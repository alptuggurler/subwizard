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
from subwizard.core.secrets import ANTHROPIC, OPENROUTER, load_api_keys
from subwizard.translator.base import (
    BaseTranslator,
    TranslationError,
    TranslationRequest,
    TranslationResult,
)
from subwizard.profiles import DEFAULT, GameProfile, glossary_violations, load_profile
from subwizard.ocr.vision import HANDOFF
from subwizard.translator.cache import CachedTranslation, TranslationCache, cache_key, loose_key
from subwizard.translator.prompt import is_chatter, system_prompt
from subwizard.translator.ollama import FallbackChain
from subwizard.translator.spend import SpendLedger
from subwizard.utils import timing
from subwizard.utils.paths import user_data_dir

log = logging.getLogger(__name__)

TOKENS_PER_PRICE_UNIT = 1_000_000
FALLBACK_ENGINES = ("deep", "ollama")  # weaker than Gemini: retried when Gemini is back
PRICED_ENGINES = ("gemini", "openrouter", "anthropic")  # cost counted, daily limit applies
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
        ledger: SpendLedger | None = None,
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
        self._quota = False  # Gemini is refusing for quota right now
        self.ledger = ledger or SpendLedger(None)
        self._limited = False  # today's cost reached translator.daily_limit_usd
        self._publish()  # today's / all-time numbers are known before the first request

    def at_limit(self) -> bool:
        """Today's cost reached the daily limit (read live: the limit can change any time,
        and a new day starts at zero)."""
        limit = self._config.translator.daily_limit_usd
        reached = limit > 0 and self.ledger.today().cost_usd >= limit
        if reached != self._limited:
            self._limited = reached
            self._metrics.set_gauge(M.TR_LIMIT, reached)
            if reached:
                log.warning("Günlük harcama sınırına ulaşıldı ($%.4f / $%.2f); ücretli motor "
                            "bugün kullanılmıyor.", self.ledger.today().cost_usd, limit)  # fmt: skip
            else:
                log.info("Günlük harcama sınırının altında; ücretli motor yeniden kullanılıyor.")
        return reached

    def _set_quota(self, on: bool) -> None:
        if on != self._quota:
            self._quota = on
            self._metrics.set_gauge(M.TR_QUOTA, on)
            if not on:
                log.info("Gemini kotası yeniden kullanılabilir.")

    def _price(self, result: TranslationResult) -> float:
        tr = self._config.translator
        model = tr.anthropic_model if result.engine == "anthropic" else tr.model
        price = tr.prices.get(model)
        if price is None:
            return 0.0
        cached = min(result.cached_tokens, result.input_tokens)
        return ((result.input_tokens - cached) * price.input + cached * price.cached()
                + result.output_tokens * price.output) / TOKENS_PER_PRICE_UNIT  # fmt: skip

    def _account(self, result: TranslationResult) -> TranslationResult:
        """Tokens and cost of one paid answer into the session usage, the day's ledger
        and metrics. OpenRouter reports what it charged; Gemini is priced locally."""
        cost = result.cost_usd if result.cost_usd > 0 else self._price(result)
        self.ledger.add(cost, result.input_tokens, result.output_tokens, result.cached_tokens)
        with self._lock:
            self.usage.input_tokens += result.input_tokens
            self.usage.output_tokens += result.output_tokens
            self.usage.cost_usd += cost
        self._metrics.incr(M.TR_TOKENS_IN, result.input_tokens)
        self._metrics.incr(M.TR_TOKENS_OUT, result.output_tokens)
        return replace(result, cost_usd=cost)

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
        loose = loose_key(request.text, scope) if tr.loose_cache else None
        handed = None if force else HANDOFF.take(request.text)
        if handed is not None:  # vision mode: read and translated in one call already
            handed = self._account(handed)
            m.observe(M.TR_MS, handed.latency_ms)
            self.cache.put(key, CachedTranslation(handed.text, handed.engine), loose)
            self._publish()
            return handed
        hit = None if force else self.cache.get(key)
        if hit is None and not force:
            # The same line as an earlier one, read a little differently by OCR.
            hit = self.cache.get_loose(loose)
            if hit is not None:
                log.debug("Önbellek (yakın eşleşme): %r", request.text)
        # A fallback translation is cached too, but when the main engine is available it
        # gets a chance to do better next time instead of the weaker result living forever.
        weaker = (hit is not None and hit.engine in FALLBACK_ENGINES
                  and self.primary is not None and hit.engine != self.primary.name)  # fmt: skip
        if hit is not None and not weaker:
            with self._lock:
                self.usage.cache_hits += 1
            m.incr(M.TR_CACHE_HITS)
            return TranslationResult(hit.text, hit.engine, 0.0, cached=True,
                                     fallback=hit.engine in FALLBACK_ENGINES
                                     and hit.engine != tr.engine)  # fmt: skip

        result: TranslationResult | None = None
        reasons: list[str] = []  # why each engine failed, for the "Test" button
        primary = self.primary
        if primary is not None and primary.name in PRICED_ENGINES and self.at_limit():
            reasons.append(f"{primary.name}: daily_limit")
            primary = None
        if primary is not None:
            with self._lock:
                self.usage.requests += 1
            m.incr(M.TR_REQUESTS)
            try:
                result = self.primary.translate(request)
            except TranslationError as exc:
                quota = str(exc).startswith("quota")
                # While every key rests each line fails the same way: say it once.
                if not (quota and self._quota):
                    log.warning("%s çeviremedi (%s); yedeğe geçiliyor.", self.primary.name, exc)
                self._set_quota(quota)
                reasons.append(f"{self.primary.name}: {exc}")
            else:
                self._set_quota(False)
                if primary.name in PRICED_ENGINES:  # the local model and test mode are free
                    result = self._account(result)
                if is_chatter(request.text, result.text):
                    log.warning("%s çeviri yerine açıklama yazdı, satır olduğu gibi "
                                "gösteriliyor: %r -> %r", result.engine, request.text,
                                result.text)  # fmt: skip
                    result = replace(result, text=request.text)
                cost = result.cost_usd
                log.info("Çeviri (%s, %.0f ms, %d+%d token, $%.5f): %r -> %r",
                         result.engine, result.latency_ms, result.input_tokens,
                         result.output_tokens, cost, request.text, result.text)  # fmt: skip

        if result is None and self.fallback is not None:
            try:
                result = self.fallback.translate(request)
                result = replace(result, fallback=self.primary is not None
                                 or result.engine != tr.engine)  # fmt: skip
            except TranslationError as exc:
                log.warning("Yedek çevirmen de çeviremedi (%s): %r", exc, request.text)
                reasons.append(f"{self.fallback.name}: {exc}")
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
            raise TranslationError("; ".join(["all_engines_failed", *reasons]))

        missed = glossary_violations(request.text, result.text, self.profile)
        if missed:
            m.incr(M.TR_GLOSSARY_MISSES)
            log.warning("Sözlük ihlali (%s, profil %s): %s; %r -> %r", result.engine,
                        self.profile.name, ", ".join(f"{t} → {self.profile.expected(t)}"
                                                     for t in missed),
                        request.text, result.text)  # fmt: skip
        m.observe(M.TR_MS, result.latency_ms)
        self.cache.put(key, CachedTranslation(result.text, result.engine), loose)
        self._publish()
        return result

    def _publish(self) -> None:
        with self._lock:
            cost = self.usage.cost_usd
        elapsed = self._clock() - self._started
        self._metrics.set_gauge(M.TR_COST, cost)
        self._metrics.set_gauge(M.TR_COST_TODAY, self.ledger.today().cost_usd)
        self._metrics.set_gauge(M.TR_COST_TOTAL, self.ledger.total().cost_usd)
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
        self.ledger.close()


def cache_path() -> str:
    return str(user_data_dir() / "cache" / "translations.sqlite")


def build_engines(
    config: AppConfig, profile: GameProfile | None = None,
) -> tuple[BaseTranslator | None, BaseTranslator | None, str]:  # fmt: skip
    """(primary, fallback, note). The note says why Gemini is not used, if it is not."""
    from subwizard.translator.deep import DeepTranslatorFallback

    tr = config.translator
    if tr.engine == "test":
        from subwizard.translator.echo import EchoTranslator

        log.info("Çeviri test modunda: istek atılmıyor, okunan metin gösteriliyor.")
        return EchoTranslator(), None, ""
    profile = profile or load_profile(config.profile)
    log.info("Oyun profili: %s (%d sözlük terimi, %d yok sayılan ifade)", profile.name,
             len(profile.glossary), len(profile.ignore_phrases))  # fmt: skip
    log.debug("Sistem prompt'u (profil %s):\n%s", profile.name,
              system_prompt(tr.system_prompt, profile))  # fmt: skip
    deep = DeepTranslatorFallback(tr.source_language, tr.target_language, tr.fallback_timeout_s,
                                  profile=profile)  # fmt: skip
    if tr.engine == "deep":
        return None, deep, ""
    if tr.engine == "ollama":
        local = make_ollama(config, profile)
        log.info("Yerel model çeviriyor (Ollama: %s).", tr.ollama_model)
        return local, deep if tr.fallback else None, ""
    # Gemini's / OpenRouter's fallback: the local model first (if asked for), then Google / MyMemory.
    chain: list[BaseTranslator] = []
    if tr.local_fallback:
        chain.append(make_ollama(config, profile))
    if tr.fallback:
        chain.append(deep)
    fallback = None if not chain else chain[0] if len(chain) == 1 else FallbackChain(chain)
    if tr.engine == "openrouter":
        keys, _source = load_api_keys(OPENROUTER)
        if not keys:
            log.warning("OpenRouter API anahtarı yok; yedek çevirmen kullanılacak.")
            return None, fallback, "no_api_key"
        from subwizard.translator.openrouter import OpenRouterTranslator

        log.info("OpenRouter çeviriyor (%s).", tr.openrouter_model)
        return OpenRouterTranslator(
            keys, model=tr.openrouter_model, timeout_s=tr.timeout_s, retries=tr.retries,
            prompt=tr.system_prompt, profile=profile,
        ), fallback, ""  # fmt: skip
    if tr.engine == "anthropic":
        keys, _source = load_api_keys(ANTHROPIC)
        if not keys:
            log.warning("Claude API anahtarı yok; yedek çevirmen kullanılacak.")
            return None, fallback, "no_api_key"
        from subwizard.translator.anthropic_api import AnthropicTranslator

        log.info("Claude API çeviriyor (%s).", tr.anthropic_model)
        claude = AnthropicTranslator(
            keys, model=tr.anthropic_model, timeout_s=tr.timeout_s, retries=tr.retries,
            prompt=tr.system_prompt, profile=profile,
        )  # fmt: skip
        claude.warm()
        return claude, fallback, ""
    keys, _source = load_api_keys()
    if not keys:
        log.warning("Gemini API anahtarı yok; yedek çevirmen kullanılacak.")
        return None, fallback, "no_api_key"
    from subwizard.translator.gemini import GeminiTranslator

    gemini = GeminiTranslator(
        keys, model=tr.model, timeout_s=tr.timeout_s, retries=tr.retries,
        thinking_level=tr.thinking_level, prompt=tr.system_prompt, profile=profile,
        quota_cooldown_s=tr.quota_cooldown_s,
    )  # fmt: skip
    return gemini, fallback, ""


def make_ollama(config: AppConfig, profile: GameProfile) -> BaseTranslator:
    from subwizard.translator.ollama import OllamaTranslator

    tr = config.translator
    local = OllamaTranslator(tr.ollama_model, tr.ollama_url, tr.ollama_timeout_s,
                             tr.source_language, tr.target_language, profile)  # fmt: skip
    local.warm()
    return local


def open_cache(config: AppConfig) -> TranslationCache:
    from pathlib import Path

    tr = config.translator
    return TranslationCache(tr.cache_size, Path(cache_path()) if tr.disk_cache else None)


def build_service(config: AppConfig, metrics: MetricsHub) -> TranslationService:
    profile = load_profile(config.profile)
    primary, fallback, _note = build_engines(config, profile)
    # Test mode keeps its echoes out of the real cache (and real translations out of it).
    cache = TranslationCache(0) if config.translator.engine == "test" else open_cache(config)
    return TranslationService(config, metrics, primary, fallback, cache, profile=profile,
                              ledger=SpendLedger(spend_path()))  # fmt: skip


def spend_path():  # noqa: ANN201
    from pathlib import Path

    return Path(user_data_dir() / "usage.sqlite")
