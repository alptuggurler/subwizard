"""Fallback translator: Google Translate, then MyMemory, via `deep-translator` (SPEC 3.6).
Qt-free.

No API key, no cost, lower quality, no context. The speaker name is not sent (SPEC 3.5:
names are not translated). deep-translator calls `requests.get`
without a timeout, so the call runs on a helper thread and is abandoned after
`timeout_s`; a hung request then cannot stall the subtitle pipeline.

Google's free endpoint refuses some networks outright ("too many requests" on the very
first call, seen on Windows and macOS). A provider that says so rests for a while instead
of being asked again for every line, and the next one (MyMemory: free, a daily character
allowance) is tried.
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
RATE_LIMIT_REST_S = 60.0  # a provider that refused us is not asked again for this long
DAILY_LIMIT_REST_S = 3600.0  # MyMemory's daily allowance is used up
MYMEMORY_WARNING = "MYMEMORY WARNING"  # it answers 200 with this text when the day is used up


class DailyLimit(Exception):
    """The provider's free allowance for today is gone."""


def is_rate_limit(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return "toomanyrequests" in text or "too many requests" in text or "429" in text


def mymemory_code(language: str) -> str:
    """MyMemory wants a region ("en-GB", "tr-TR"); the config has "en", "tr"."""
    from deep_translator.constants import MY_MEMORY_LANGUAGES_TO_CODES

    codes = list(MY_MEMORY_LANGUAGES_TO_CODES.values())
    if language in codes:
        return language
    prefix = language.lower() + "-"
    matches = [c for c in codes if c.lower().startswith(prefix)]
    same = [c for c in matches if c.lower() == f"{language.lower()}-{language.lower()}"]
    preferred = [c for c in matches if c == "en-GB"]
    for group in (same, preferred, matches):
        if group:
            return group[0]
    return language


class DeepTranslatorFallback(BaseTranslator):
    name = "deep"

    def __init__(
        self, source: str, target: str, timeout_s: float,
        backend: Callable[[str], str] | None = None,
        profile: GameProfile | None = None,
        clock: Callable[[], float] = timing.now,
    ) -> None:  # fmt: skip
        self._source, self._target = source, target
        self._profile = profile  # keep terms are shielded from Google (no prompt here)
        self._timeout_s = timeout_s
        self._clock = clock
        # (name, call) in the order they are tried; a test backend replaces them all.
        self._providers: list[tuple[str, Callable[[str], str]]] = (
            [("backend", backend)] if backend is not None
            else [("google", self._google), ("mymemory", self._mymemory)]
        )  # fmt: skip
        self._resting_until: dict[str, float] = {}
        self._pool = ThreadPoolExecutor(POOL_THREADS, thread_name_prefix="deep")

    def _google(self, text: str) -> str:
        from deep_translator import GoogleTranslator

        return GoogleTranslator(source=self._source, target=self._target).translate(text)

    def _mymemory(self, text: str) -> str:
        from deep_translator import MyMemoryTranslator

        translator = MyMemoryTranslator(source=mymemory_code(self._source),
                                        target=mymemory_code(self._target))  # fmt: skip
        translated = translator.translate(text)
        if isinstance(translated, str) and MYMEMORY_WARNING in translated.upper():
            raise DailyLimit(translated)
        return translated

    def resting(self) -> list[str]:
        """Providers that refused us and are not being asked right now."""
        now = self._clock()
        return [name for name, until in self._resting_until.items() if until > now]

    def translate(self, request: TranslationRequest) -> TranslationResult:
        started = timing.now()
        text, originals = protect(request.text, self._profile)
        resting = self.resting()
        reasons: list[str] = []
        for name, call in self._providers:
            if name in resting:
                reasons.append(f"{name}: resting")
                continue
            future = self._pool.submit(call, text)
            try:
                translated = future.result(timeout=self._timeout_s)
            except FutureTimeout:
                reasons.append(f"{name}: timeout")
                continue
            except Exception as exc:  # network, HTML changes on the provider's side
                if isinstance(exc, DailyLimit) or is_rate_limit(exc):
                    rest = DAILY_LIMIT_REST_S if isinstance(exc, DailyLimit) else RATE_LIMIT_REST_S
                    self._resting_until[name] = self._clock() + rest
                    log.warning("Yedek çevirmen (%s) isteği reddetti, %d sn denenmeyecek: %s",
                                name, rest, brief(exc))  # fmt: skip
                else:
                    log.warning("Yedek çevirmen (%s) başarısız: %s", name, brief(exc))
                reasons.append(f"{name}: {brief(exc)}")
                continue
            if not translated or not translated.strip():
                reasons.append(f"{name}: empty")
                continue
            translated = restore(translated.strip(), originals)
            return TranslationResult(translated, self.name, timing.ms_since(started))
        only = reasons[0].split(": ", 1)[1] if len(reasons) == 1 else ""
        if only in ("timeout", "empty"):
            raise TranslationError(only)
        raise TranslationError(f"fallback_failed ({'; '.join(reasons)})")

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
