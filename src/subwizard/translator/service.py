"""TranslationService: cache -> primary engine -> fallback, with cost accounting
(SPEC 3.6). Qt-free; called from the translator worker thread only.

"The same line never goes to the API twice" (M3 acceptance) is enforced here: every
primary result is cached under the normalised source text before it is returned, and
the worker translates one line at a time, so a repeat is always a cache hit.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace

from subwizard.config import AppConfig
from subwizard.core.metrics import M, MetricsHub
from subwizard.core.secrets import load_api_keys
from subwizard.translator.base import (
    BaseTranslator,
    TranslationError,
    TranslationRequest,
    TranslationResult,
)
from subwizard.profiles import DEFAULT, GameProfile, glossary_violations, load_profile
from subwizard.translator.cache import CachedTranslation, TranslationCache, cache_key
from subwizard.translator.prompt import system_prompt
from subwizard.utils import timing
from subwizard.utils.paths import user_data_dir

log = logging.getLogger(__name__)

TOKENS_PER_PRICE_UNIT = 1_000_000
SECONDS_PER_HOUR = 3600.0


@dataclass
class Usage:
    requests: int = 0
    cache_hits: int = 0
    fallbacks: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def summary(self, elapsed_s: float) -> str:
        per_hour = self.cost_usd / elapsed_s * SECONDS_PER_HOUR if elapsed_s > 0 else 0.0
        return (
            f"{self.requests} istek, {self.cache_hits} önbellek isabeti, {self.fallbacks} yedek, "
            f"{self.errors} hata; {self.input_tokens} giriş / {self.output_tokens} çıkış token; "
            f"~${self.cost_usd:.4f} (~${per_hour:.3f}/saat)"
        )


class TranslationService:
    def __init__(
        self,
        config: AppConfig,
        metrics: MetricsHub,
        primary: BaseTranslator | None,
        fallback: BaseTranslator | None,
        cache: TranslationCache,
        clock=timing.now,  # noqa: ANN001
        profile: GameProfile | None = None,
    ) -> None:
        self._config = config
        self.profile = profile or GameProfile()
        self._metrics = metrics
        self.primary = primary
        self.fallback = fallback
        self.cache = cache
        self._clock = clock
        self._started = clock()
        self._lock = threading.Lock()  # usage is read by the UI thread
        self.usage = Usage()

    def _price(self, result: TranslationResult) -> float:
        price = self._config.translator.prices.get(self._config.translator.model)
        if price is None:
            return 0.0
        return (result.input_tokens * price.input
                + result.output_tokens * price.output) / TOKENS_PER_PRICE_UNIT  # fmt: skip

    def translate(self, request: TranslationRequest, force: bool = False) -> TranslationResult:
        """Raises TranslationError only when neither engine produced anything. `force`
        skips the cache lookup (F7); the new result replaces the cached one."""
        tr = self._config.translator
        m = self._metrics
        # A profile changes how lines are translated (glossary), so it has its own cache
        # entries; `default` keeps the plain key so existing caches stay valid.
        scope = tr.target_language if self.profile.name == DEFAULT else (
            f"{tr.target_language}@{self.profile.name}")
        key = cache_key(request.text, scope)
        hit = None if force else self.cache.get(key)
        # A fallback translation is cached too, but when Gemini is available it gets a
        # chance to do better next time instead of the weaker result living forever.
        if hit is not None and (hit.engine != "deep" or self.primary is None):
            with self._lock:
                self.usage.cache_hits += 1
            m.incr(M.TR_CACHE_HITS)
            return TranslationResult(hit.text, hit.engine, 0.0, cached=True,
                                     fallback=hit.engine == "deep" and tr.engine != "deep")  # fmt: skip

        result: TranslationResult | None = None
        if self.primary is not None:
            with self._lock:
                self.usage.requests += 1
            m.incr(M.TR_REQUESTS)
            try:
                result = self.primary.translate(request)
            except TranslationError as exc:
                log.warning("%s çeviremedi (%s); yedeğe geçiliyor.", self.primary.name, exc)
            else:
                cost = self._price(result)
                result = replace(result, cost_usd=cost)
                with self._lock:
                    self.usage.input_tokens += result.input_tokens
                    self.usage.output_tokens += result.output_tokens
                    self.usage.cost_usd += cost
                m.incr(M.TR_TOKENS_IN, result.input_tokens)
                m.incr(M.TR_TOKENS_OUT, result.output_tokens)
                log.info("Çeviri (%s, %.0f ms, %d+%d token, $%.5f): %r -> %r",
                         result.engine, result.latency_ms, result.input_tokens,
                         result.output_tokens, cost, request.text, result.text)  # fmt: skip

        if result is None and self.fallback is not None:
            try:
                result = replace(self.fallback.translate(request),
                                 fallback=self.primary is not None or tr.engine != "deep")  # fmt: skip
            except TranslationError as exc:
                log.warning("Yedek çevirmen de çeviremedi (%s): %r", exc, request.text)
            else:
                if result.fallback:
                    with self._lock:
                        self.usage.fallbacks += 1
                    m.incr(M.TR_FALLBACKS)
                log.info("Çeviri (%s, %.0f ms): %r -> %r", result.engine, result.latency_ms,
                         request.text, result.text)  # fmt: skip

        if result is None:
            with self._lock:
                self.usage.errors += 1
            m.incr(M.TR_ERRORS)
            raise TranslationError("all_engines_failed")

        missed = glossary_violations(request.text, result.text, self.profile)
        if missed:
            m.incr(M.TR_GLOSSARY_MISSES)
            log.warning("Sözlük ihlali (%s, profil %s): %s; %r -> %r", result.engine,
                        self.profile.name, ", ".join(f"{t} → {self.profile.expected(t)}"
                                                     for t in missed),
                        request.text, result.text)  # fmt: skip
        m.observe(M.TR_MS, result.latency_ms)
        self.cache.put(key, CachedTranslation(result.text, result.engine))
        self._publish()
        return result

    def _publish(self) -> None:
        with self._lock:
            cost = self.usage.cost_usd
        elapsed = self._clock() - self._started
        self._metrics.set_gauge(M.TR_COST, cost)
        if elapsed > 0:
            self._metrics.set_gauge(M.TR_COST_PER_HOUR, cost / elapsed * SECONDS_PER_HOUR)

    def summary(self) -> str:
        with self._lock:
            return self.usage.summary(self._clock() - self._started)

    def close(self) -> None:
        for engine in (self.primary, self.fallback):
            if engine is not None:
                engine.close()
        self.cache.close()


def cache_path() -> str:
    return str(user_data_dir() / "cache" / "translations.sqlite")


def build_engines(
    config: AppConfig, profile: GameProfile | None = None,
) -> tuple[BaseTranslator | None, BaseTranslator | None, str]:  # fmt: skip
    """(primary, fallback, note). The note says why Gemini is not used, if it is not."""
    from subwizard.translator.deep import DeepTranslatorFallback

    tr = config.translator
    profile = profile or load_profile(config.profile)
    log.info("Oyun profili: %s (%d sözlük terimi, %d yok sayılan ifade)", profile.name,
             len(profile.glossary), len(profile.ignore_phrases))  # fmt: skip
    log.debug("Sistem prompt'u (profil %s):\n%s", profile.name,
              system_prompt(tr.system_prompt, profile))  # fmt: skip
    deep = DeepTranslatorFallback(tr.source_language, tr.target_language, tr.fallback_timeout_s,
                                  profile=profile)  # fmt: skip
    if tr.engine == "deep":
        return None, deep, ""
    keys, _source = load_api_keys()
    if not keys:
        log.warning("Gemini API anahtarı yok; yedek çevirmen (deep-translator) kullanılacak.")
        return None, deep if tr.fallback else None, "no_api_key"
    from subwizard.translator.gemini import GeminiTranslator

    gemini = GeminiTranslator(
        keys, model=tr.model, timeout_s=tr.timeout_s, retries=tr.retries,
        thinking_level=tr.thinking_level, prompt=tr.system_prompt, profile=profile,
        quota_cooldown_s=tr.quota_cooldown_s,
    )  # fmt: skip
    return gemini, deep if tr.fallback else None, ""


def open_cache(config: AppConfig) -> TranslationCache:
    from pathlib import Path

    tr = config.translator
    return TranslationCache(tr.cache_size, Path(cache_path()) if tr.disk_cache else None)


def build_service(config: AppConfig, metrics: MetricsHub) -> TranslationService:
    profile = load_profile(config.profile)
    primary, fallback, _note = build_engines(config, profile)
    return TranslationService(config, metrics, primary, fallback, open_cache(config),
                              profile=profile)  # fmt: skip
