"""Claude translator through the official `anthropic` SDK (Claude API). Qt-free.

Prepaid credits bought on platform.claude.com, no middleman fee. Same prompts as the
Gemini engine (system prompt with the game profile, context lines, speaker).

Checked against platform.claude.com/docs and anthropic 2.10 on 2026-11-18:
- Default model `claude-haiku-5-5`: $0.10 in / $0.50 out / $0.01 cache read per 1M tokens
  (prompts under 100K). Adaptive thinking is ON by default for it; one subtitle line does
  not need it, so `thinking: {"type": "disabled"}` (faster, cheaper).
- The system prompt is marked for prompt caching: from 512 tokens on (a profile with a
  glossary gets there) repeats are read from the cache at a tenth of the price; shorter
  prompts are simply not cached, no error.
- `usage.input_tokens` excludes cached tokens; here input_tokens = all prompt tokens and
  cached_tokens = the part read from the cache, like the Gemini engine reports them.
- SDK retries are off (`max_retries=0`); the single retry below is ours, so a 429 or a
  timeout goes to the fallback at once (SPEC 3.6).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from subwizard.profiles import GameProfile
from subwizard.translator.base import (
    BaseTranslator,
    TranslationError,
    TranslationRequest,
    TranslationResult,
    brief,
)
from subwizard.translator.prompt import clean_reply, system_prompt, user_prompt
from subwizard.utils import timing

log = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 512  # one subtitle line
RETRYABLE_CODES = (408, 500, 502, 503, 504, 529)  # 529 = overloaded
LOW_CREDIT_WORDS = ("credit balance", "billing", "spend limit")


class AnthropicTranslator(BaseTranslator):
    name = "anthropic"

    def __init__(self, api_keys: list[str], *, model: str, timeout_s: float, retries: int,
                 prompt: str = "", profile: GameProfile | None = None,
                 client_factory: Callable[[str, float], Any] | None = None) -> None:  # fmt: skip
        if not api_keys:
            raise TranslationError("no_api_key")
        self.model = model
        self._retries = retries
        self._system = system_prompt(prompt, profile)
        self._client = (client_factory or _make_client)(api_keys[0], timeout_s)

    def translate(self, request: TranslationRequest) -> TranslationResult:
        import anthropic

        started = timing.now()
        attempts_left = self._retries + 1
        while True:
            attempts_left -= 1
            try:
                message = self._client.messages.create(
                    model=self.model,
                    max_tokens=MAX_OUTPUT_TOKENS,
                    system=[{"type": "text", "text": self._system,
                             "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": user_prompt(request)}],
                    thinking={"type": "disabled"},
                )  # fmt: skip
            except anthropic.APITimeoutError as exc:
                raise TranslationError("timeout") from exc  # SPEC: no retry on timeout
            except anthropic.APIConnectionError as exc:
                if attempts_left > 0:
                    continue
                raise TranslationError(f"network ({brief(exc)})") from exc
            except anthropic.APIStatusError as exc:
                if exc.status_code in RETRYABLE_CODES and attempts_left > 0:
                    log.warning("Claude API hatası (%s), tekrar deneniyor.", exc.status_code)
                    continue
                raise status_error(exc.status_code, _message(exc)) from exc
            return self._result(message, timing.ms_since(started))

    def _result(self, message: Any, latency_ms: float) -> TranslationResult:
        text = "".join(block.text for block in message.content if block.type == "text")
        text = clean_reply(text)
        if not text:
            log.warning("Claude boş yanıt döndü (stop=%s)", message.stop_reason)
            raise TranslationError("empty")
        usage = message.usage
        cached = usage.cache_read_input_tokens or 0
        prompt_tokens = usage.input_tokens + cached + (usage.cache_creation_input_tokens or 0)
        return TranslationResult(
            text, self.name, latency_ms,
            input_tokens=prompt_tokens, output_tokens=usage.output_tokens, cached_tokens=cached,
        )  # fmt: skip

    def close(self) -> None:
        self._client.close()


def _message(exc: Any) -> str:
    body = getattr(exc, "body", None)
    error = body.get("error") if isinstance(body, dict) else None
    text = error.get("message") if isinstance(error, dict) else None
    return str(text or getattr(exc, "message", "") or exc)[:200]


def status_error(code: int, message: str) -> TranslationError:
    """A reason the service and the Test button understand. Out of credit and rate limits
    start with "quota" so the Home card warns and the fallback takes over."""
    if code == 429:
        return TranslationError(f"quota (rate_limited: {message})")
    if code == 402 or (code in (400, 403) and any(w in message.lower() for w in LOW_CREDIT_WORDS)):
        return TranslationError(f"quota (no_credit: {message})")
    if code == 401:
        return TranslationError(f"bad_key ({message})")
    return TranslationError(f"api_error:{code} ({message})")


def _make_client(api_key: str, timeout_s: float) -> Any:
    import anthropic

    return anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=0)
