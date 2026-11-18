"""Local translator through Ollama (http://127.0.0.1:11434). Qt-free.

Free, no quota, works offline; quality and speed depend on the model and the GPU.
Default model: TranslateGemma (Google's translation-tuned Gemma, 55 languages), asked with
the single-turn prompt from its model card. Any other Ollama model (qwen3, gemma3, ...)
gets the same prompt. No context lines and no speaker name: TranslateGemma is trained on
one text at a time, and names are not translated (SPEC 3.5).

Only the standard library is used (urllib): the request goes to this PC, not the internet.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.error
import urllib.request

from subwizard.profiles import GameProfile
from subwizard.translator.base import (
    BaseTranslator,
    TranslationError,
    TranslationRequest,
    TranslationResult,
    brief,
)
from subwizard.utils import timing

log = logging.getLogger(__name__)

KEEP_ALIVE = "30m"  # the model stays in (GPU) memory between lines
WARM_TIMEOUT_S = 120.0  # first load of a model from disk can take a while
LANGUAGE_NAMES = {
    "en": "English", "tr": "Turkish", "de": "German", "fr": "French", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "ru": "Russian", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese", "ar": "Arabic", "pl": "Polish", "nl": "Dutch",
}  # fmt: skip
THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)  # reasoning models (qwen3)


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code.lower(), code)


def build_prompt(text: str, source: str, target: str, profile: GameProfile | None) -> str:
    """TranslateGemma's prompt (model card), plus the profile's keep terms if any."""
    src, tgt = language_name(source), language_name(target)
    prompt = (
        f"You are a professional {src} ({source}) to {tgt} ({target}) translator. Your goal "
        f"is to accurately convey the meaning and nuances of the original {src} text while "
        f"adhering to {tgt} grammar, vocabulary, and cultural sensitivities.\n"
        f"Produce only the {tgt} translation, without any additional explanations or "
        f"commentary."
    )
    if profile is not None and profile.keep_terms:
        prompt += " Keep these names exactly as written: " + ", ".join(profile.keep_terms) + "."
    return prompt + f" Please translate the following {src} text into {tgt}:\n\n\n{text}"


def clean_reply(reply: str) -> str:
    """The translation alone: no reasoning block, no surrounding quotes or blank lines."""
    text = THINK_BLOCK.sub("", reply).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'“”":
        text = text[1:-1].strip()
    return text


class OllamaTranslator(BaseTranslator):
    name = "ollama"

    def __init__(self, model: str, url: str = "http://127.0.0.1:11434", timeout_s: float = 4.0,
                 source: str = "en", target: str = "tr",
                 profile: GameProfile | None = None) -> None:  # fmt: skip
        self.model = model
        self._url = url.rstrip("/")
        self._timeout_s = timeout_s
        self._source, self._target = source, target
        self._profile = profile

    def _post(self, path: str, body: dict, timeout_s: float) -> dict:
        request = urllib.request.Request(
            self._url + path, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )  # fmt: skip
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - local URL from config
            return json.loads(response.read().decode("utf-8"))

    def translate(self, request: TranslationRequest) -> TranslationResult:
        started = timing.now()
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": build_prompt(
                request.text, self._source, self._target, self._profile)}],
            "stream": False,
            "keep_alive": KEEP_ALIVE,
            "options": {"temperature": 0},
        }  # fmt: skip
        try:
            reply = self._post("/api/chat", body, self._timeout_s)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            if exc.code == 404:  # "model 'x' not found, try pulling it first"
                raise TranslationError(f"model_not_found ({self.model})") from exc
            raise TranslationError(f"http_{exc.code}: {detail}") from exc
        except TimeoutError as exc:
            raise TranslationError("timeout") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TranslationError("timeout") from exc
            raise TranslationError(f"ollama_unreachable ({brief(exc)})") from exc
        except (OSError, ValueError) as exc:  # connection reset, bad JSON
            raise TranslationError(f"ollama_error ({brief(exc)})") from exc
        text = clean_reply(str(reply.get("message", {}).get("content", "")))
        if not text:
            raise TranslationError("empty")
        return TranslationResult(
            text, self.name, timing.ms_since(started),
            input_tokens=int(reply.get("prompt_eval_count") or 0),
            output_tokens=int(reply.get("eval_count") or 0),
        )  # fmt: skip

    def warm(self) -> threading.Thread:
        """Load the model in the background, so the first subtitle does not wait for it
        (a cold load takes seconds; the per-line timeout is short)."""

        def run() -> None:
            try:
                self._post("/api/generate", {"model": self.model, "keep_alive": KEEP_ALIVE},
                           WARM_TIMEOUT_S)  # fmt: skip
                log.info("Yerel model hazır (Ollama: %s).", self.model)
            except Exception as exc:  # noqa: BLE001 - only a log line; translate() reports
                log.warning("Yerel model yüklenemedi (Ollama: %s): %s", self.model, brief(exc))

        thread = threading.Thread(target=run, name="ollama-warm", daemon=True)
        thread.start()
        return thread


class FallbackChain(BaseTranslator):
    """Tries each fallback in order (Ollama first, then Google/MyMemory)."""

    name = "chain"

    def __init__(self, engines: list[BaseTranslator]) -> None:
        self.engines = engines

    def translate(self, request: TranslationRequest) -> TranslationResult:
        reasons: list[str] = []
        for engine in self.engines:
            try:
                return engine.translate(request)
            except TranslationError as exc:
                log.info("Yedek (%s) çeviremedi: %s", engine.name, exc)
                reasons.append(f"{engine.name}: {exc}")
        raise TranslationError("; ".join(reasons) or "no_fallback")

    def close(self) -> None:
        for engine in self.engines:
            engine.close()
