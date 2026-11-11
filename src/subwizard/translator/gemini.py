"""Gemini translator via the `google-genai` SDK (SPEC 3.6). Qt-free.

Checked against google-genai 3.2 (source) and ai.google.dev docs on 2026-11-12:
- `HttpOptions.timeout` is in milliseconds.
- The SDK retries 5 times by default on 408/429/5xx; that would hide a 429 from the key
  rotation and multiply the timeout, so SDK retries are off (`attempts=1`) and the
  single retry below is ours.
- Gemini 3.x takes `ThinkingConfig(thinking_level=...)`; "minimal" is the fastest.
- `temperature`/`top_p`/`top_k` are not sent (SPEC: deprecated on new models).
- Safety: every text category at `OFF`; a blocked or empty reply is a TranslationError.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from subwizard.core.secrets import mask
from subwizard.translator.base import (
    BaseTranslator,
    TranslationError,
    TranslationRequest,
    TranslationResult,
)
from subwizard.translator.prompt import clean_reply, system_prompt, user_prompt
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
        quota_cooldown_s: float = 60.0,
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
        self._system = system_prompt(prompt)
        self._cooldown_s = quota_cooldown_s
        self._client_factory = client_factory or _make_client
        self._clock = clock
        self._clients: dict[int, Any] = {}
        self._resting_until: dict[int, float] = {}  # key index -> time it may be used again
        self._current = 0
        self._config: Any = None

    @property
    def model(self) -> str:
        return self._model

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
        contents = user_prompt(request)
        attempts_left = self._retries + 1
        started = timing.now()
        while True:
            index = self._next_key()
            if index is None:
                raise TranslationError("quota")  # every key is resting: caller falls back
            try:
                response = self._client(index).models.generate_content(
                    model=self._model, contents=contents, config=self._generate_config()
                )
            except Exception as exc:  # noqa: BLE001 - classified below
                code = getattr(exc, "code", None)
                if code in QUOTA_CODES:
                    log.warning("Gemini kotası doldu (anahtar %s); sıradaki anahtar deneniyor.",
                                mask(self._keys[index]))  # fmt: skip
                    self._resting_until[index] = self._clock() + self._cooldown_s
                    continue  # rotating keys does not use up a retry
                if is_timeout(exc):
                    # SPEC: on timeout go to the fallback at once; a retry would double it.
                    raise TranslationError("timeout") from exc
                attempts_left -= 1
                if attempts_left > 0 and (code in RETRYABLE_CODES or code is None):
                    log.warning("Gemini hatası (%s), tekrar deneniyor: %s", code, exc)
                    continue
                raise TranslationError(f"api_error:{code}") from exc
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
        return TranslationResult(
            clean_reply(text), self.name, latency_ms,
            input_tokens=input_tokens, output_tokens=output_tokens,
        )  # fmt: skip


def _make_client(api_key: str, timeout_s: float) -> Any:
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=int(timeout_s * 1000),  # milliseconds
            retry_options=types.HttpRetryOptions(attempts=1),  # retries are ours
        ),
    )
