"""Fallback translator: Google Translate via `deep-translator` (SPEC 3.6). Qt-free.

No API key, no cost, lower quality, no context. The speaker name is not sent (SPEC 3.5:
names are not translated). deep-translator calls `requests.get`
without a timeout, so the call runs on a helper thread and is abandoned after
`timeout_s`; a hung request then cannot stall the subtitle pipeline.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from subwizard.translator.base import (
    BaseTranslator,
    TranslationError,
    TranslationRequest,
    TranslationResult,
    brief,
)
from subwizard.profiles import GameProfile
from subwizard.translator.prompt import protect, restore
from subwizard.utils import timing

log = logging.getLogger(__name__)

POOL_THREADS = 2  # one may be stuck on a dead connection while the next line goes out


class DeepTranslatorFallback(BaseTranslator):
    name = "deep"

    def __init__(
        self, source: str, target: str, timeout_s: float,
        backend: Callable[[str], str] | None = None,
        profile: GameProfile | None = None,
    ) -> None:  # fmt: skip
        self._source, self._target = source, target
        self._profile = profile  # keep terms are shielded from Google (no prompt here)
        self._timeout_s = timeout_s
        self._backend = backend
        self._pool = ThreadPoolExecutor(POOL_THREADS, thread_name_prefix="deep")

    def _call(self, text: str) -> str:
        if self._backend is not None:
            return self._backend(text)
        return self._google(text)

    def _google(self, text: str) -> str:
        from deep_translator import GoogleTranslator

        return GoogleTranslator(source=self._source, target=self._target).translate(text)

    def translate(self, request: TranslationRequest) -> TranslationResult:
        started = timing.now()
        text, originals = protect(request.text, self._profile)
        future = self._pool.submit(self._call, text)
        try:
            translated = future.result(timeout=self._timeout_s)
        except FutureTimeout as exc:
            raise TranslationError("timeout") from exc
        except Exception as exc:  # network, HTML changes on Google's side
            log.warning("Yedek çevirmen başarısız: %s", exc)
            raise TranslationError(f"fallback_failed ({brief(exc)})") from exc
        if not translated or not translated.strip():
            raise TranslationError("empty")
        translated = restore(translated.strip(), originals)
        return TranslationResult(translated, self.name, timing.ms_since(started))

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
