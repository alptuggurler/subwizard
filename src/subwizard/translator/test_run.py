"""One-off translation for the "Test" button (SPEC 3.12): shows the engine, the result
and the latency with the current settings, without touching the session's cache.
Qt-free; runs on a helper thread."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from subwizard.config import AppConfig
from subwizard.core.metrics import MetricsHub
from subwizard.translator.base import TranslationRequest
from subwizard.translator.cache import TranslationCache
from subwizard.translator.service import TranslationService, build_engines

log = logging.getLogger(__name__)

SAMPLE_TEXT = "Hey, keep your head down. The cops are all over this place."


@dataclass(frozen=True, slots=True)
class TestOutcome:
    source: str
    text: str  # "" when nothing translated it
    engine: str
    fallback: bool
    latency_ms: float
    cost_usd: float
    note: str  # i18n-free reason code when Gemini was not used ("no_api_key", "")

    __test__ = False  # not a pytest test class


def run_test(config: AppConfig, text: str = SAMPLE_TEXT) -> TestOutcome:
    try:
        primary, fallback, note = build_engines(config)
        service = TranslationService(config, MetricsHub(), primary, fallback, TranslationCache(0))
        try:
            result = service.translate(TranslationRequest(text))
        finally:
            service.close()
    except Exception as exc:  # noqa: BLE001 - the button reports, it never crashes
        log.warning("Çeviri testi başarısız: %s", exc)
        return TestOutcome(text, "", "", False, 0.0, 0.0, str(exc))
    return TestOutcome(text, result.text, result.engine, result.fallback,
                       result.latency_ms, result.cost_usd, note)  # fmt: skip
