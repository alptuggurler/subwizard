"""Gemini translator via the `google-genai` SDK (SPEC 3.6). Qt-free.

Checked against google-genai 3.2 (source) and ai.google.dev docs on 2026-11-12:
- `HttpOptions.timeout` is in milliseconds.
- The SDK retries 5 times by default on 408/429/5xx; that would hide a 429 from the key
  rotation and multiply the timeout, so SDK retries are off (`attempts=1`) and the
  single retry below is ours.
- Gemini 3.x takes `ThinkingConfig(thinking_level=...)`; "minimal" is the fastest.
- The SDK also sends the timeout as the server deadline (`X-Server-Timeout`, whole
  seconds), and the API rejects deadlines under 10 s with a 400 ("Manually set deadline
  3s is too short", found on Windows). The header is set to at least 10 s ourselves (the
  SDK keeps a header that is already there); the client-side timeout stays `timeout_s`,
  so a slow reply still falls back after 2.5 s.
- `temperature`/`top_p`/`top_k` are not sent (SPEC: deprecated on new models).
- Safety: every text category at `OFF`; a blocked or empty reply is a TranslationError.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any

from subwizard.core.secrets import mask
from subwizard.profiles import GameProfile
from subwizard.translator.base import (
    BaseTranslator,
    TranslationError,
    TranslationRequest,
    TranslationResult,
    VisionResult,
    brief,
)
from subwizard.translator.prompt import (
    VISION_INSTRUCTION,
    clean_reply,
    parse_vision_reply,
    system_prompt,
    user_prompt,
    vision_prompt,
)
from subwizard.utils import timing

log = logging.getLogger(__name__)

SAFETY_CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
)
QUOTA_CODES = (429,)
RETRYABLE_CODES = (408, 500, 502, 503, 504)
MAX_OUTPUT_TOKENS = 512  # a subtitle line; thinking is "minimal" so this is not eaten by it


def is_timeout(exc: BaseException) -> bool:
    """httpx / httpx2 timeouts (the SDK may use either) without importing them here."""
    return any("Timeout" in cls.__name__ for cls in type(exc).__mro__)


class GeminiTranslator(BaseTranslator):
    name = "gemini"

    def __init__(
        self,
        api_keys: list[str],
        *,
        model: str,
        timeout_s: float,
        retries: int,
        thinking_level: str,
        prompt: str = "",
        profile: GameProfile | None = None,
        quota_cooldown_s: float = 60.0,
        media_resolution: str = "medium",
        client_factory: Callable[..., Any] | None = None,
        clock: Callable[[], float] = timing.now,
    ) -> None:
        if not api_keys:
            raise TranslationError("no_api_key")
        self._keys = list(api_keys)
        self._model = model
        self._timeout_s = timeout_s
        self._retries = retries
        self._thinking_level = thinking_level
        self._system = system_prompt(prompt, profile)
        self._cooldown_s = quota_cooldown_s
        self._client_factory = client_factory or _make_client
        self._clock = clock
        self._clients: dict[int, Any] = {}
        self._resting_until: dict[int, float] = {}  # key index -> time it may be used again
        self._current = 0
        self._last_quota = ""  # the API's own words for the last quota error
        self._config: Any = None
        self._vision: Any = None
        self._media_resolution = media_resolution

    @property
    def model(self) -> str:
        return self._model

    def key_states(self) -> list[tuple[str, float]]:
        """Per key (in order): "active" (used for the next line), "ready", or "resting"
        (hit its quota), with the seconds left to rest. Read by the UI thread."""
        now = self._clock()
        states = []
        for index in range(len(self._keys)):
            left = self._resting_until.get(index, 0.0) - now
            if left > 0:
                states.append(("resting", left))
            else:
                states.append(("active" if index == self._current else "ready", 0.0))
        return states

    def _client(self, index: int) -> Any:
        if index not in self._clients:
            self._clients[index] = self._client_factory(self._keys[index], self._timeout_s)
        return self._clients[index]

    def _generate_config(self) -> Any:
        if self._config is None:
            from google.genai import types

            self._config = types.GenerateContentConfig(
                system_instruction=self._system,
                thinking_config=types.ThinkingConfig(thinking_level=self._thinking_level.upper()),
                safety_settings=[
                    types.SafetySetting(category=c, threshold="OFF") for c in SAFETY_CATEGORIES
                ],
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )
        return self._config

    def _next_key(self) -> int | None:
        """The current key if usable, else the next one that is not resting."""
        now = self._clock()
        for step in range(len(self._keys)):
            index = (self._current + step) % len(self._keys)
            if self._resting_until.get(index, 0.0) <= now:
                self._current = index
                return index
        return None

    def translate(self, request: TranslationRequest) -> TranslationResult:
        return self._generate(user_prompt(request), self._generate_config())

    def translate_image(self, image: bytes, mime_type: str = "image/png",
                        context: tuple[tuple[str, str], ...] = ()) -> VisionResult:  # fmt: skip
        """Vision mode (SPEC 3.6, M7, experimental): the model reads the subtitle in the
        cropped region and translates it in one call. Empty source = no subtitle seen."""
        from google.genai import types

        parts = [types.Part.from_bytes(data=image, mime_type=mime_type), vision_prompt(context)]
        result = self._generate(parts, self._vision_config())
        source, translation = parse_vision_reply(result.text)
        return VisionResult(source, translation, result)

    def _vision_config(self) -> Any:
        if self._vision is None:
            from google.genai import types

            base = self._generate_config()
            self._vision = base.model_copy(update={
                "system_instruction": f"{self._system}\n\n{VISION_INSTRUCTION}",
                "media_resolution": f"MEDIA_RESOLUTION_{self._media_resolution.upper()}",
            })  # fmt: skip
            assert isinstance(self._vision, types.GenerateContentConfig)
        return self._vision

    def _generate(self, contents: Any, config: Any) -> TranslationResult:
        attempts_left = self._retries + 1
        started = timing.now()
        while True:
            index = self._next_key()
            if index is None:
                # Every key is resting: the caller falls back.
                detail = f" ({self._last_quota})" if self._last_quota else ""
                raise TranslationError(f"quota{detail}")
            try:
                response = self._client(index).models.generate_content(
                    model=self._model, contents=contents, config=config
                )
            except Exception as exc:  # noqa: BLE001 - classified below
                code = getattr(exc, "code", None)
                if code in QUOTA_CODES:
                    # The reply says which limit it was (per minute or per day) and how
                    # long to wait; without it a daily limit looks like a short hiccup.
                    self._last_quota = brief(exc)
                    log.warning("Gemini kotası doldu (anahtar %s): %s; sıradaki anahtar "
                                "deneniyor.", mask(self._keys[index]), self._last_quota)  # fmt: skip
                    self._resting_until[index] = self._clock() + self._cooldown_s
                    continue  # rotating keys does not use up a retry
                if is_timeout(exc):
                    # SPEC: on timeout go to the fallback at once; a retry would double it.
                    raise TranslationError("timeout") from exc
                attempts_left -= 1
                if attempts_left > 0 and (code in RETRYABLE_CODES or code is None):
                    log.warning("Gemini hatası (%s), tekrar deneniyor: %s", code, exc)
                    continue
                raise TranslationError(f"api_error:{code} ({brief(exc)})") from exc
            return self._result(response, timing.ms_since(started))

    def _result(self, response: Any, latency_ms: float) -> TranslationResult:
        text = None
        try:
            text = response.text
        except Exception:  # noqa: BLE001 - blocked candidates may raise on .text
            text = None
        if not text or not text.strip():
            feedback = getattr(response, "prompt_feedback", None)
            reason = getattr(feedback, "block_reason", None)
            candidates = getattr(response, "candidates", None) or []
            finish = getattr(candidates[0], "finish_reason", None) if candidates else None
            log.warning("Gemini boş/engelli yanıt döndü (block=%s, finish=%s)", reason, finish)
            raise TranslationError("empty")
        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", None) or 0
        output_tokens = (getattr(usage, "candidates_token_count", None) or 0) + (
            getattr(usage, "thoughts_token_count", None) or 0
        )
        cached_tokens = getattr(usage, "cached_content_token_count", None) or 0
        return TranslationResult(
            clean_reply(text), self.name, latency_ms,
            input_tokens=input_tokens, output_tokens=output_tokens, cached_tokens=cached_tokens,
        )  # fmt: skip


MIN_SERVER_DEADLINE_S = 10  # the API refuses shorter deadlines with HTTP 400


def http_options_kwargs(timeout_s: float) -> dict[str, Any]:
    """Arguments for `types.HttpOptions`: client timeout as configured, server deadline at
    least MIN_SERVER_DEADLINE_S."""
    deadline = max(MIN_SERVER_DEADLINE_S, math.ceil(timeout_s))
    return {
        "timeout": int(timeout_s * 1000),  # milliseconds, client side
        "headers": {"X-Server-Timeout": str(deadline)},
    }


def _make_client(api_key: str, timeout_s: float) -> Any:
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            **http_options_kwargs(timeout_s),
            retry_options=types.HttpRetryOptions(attempts=1),  # retries are ours
        ),
    )
