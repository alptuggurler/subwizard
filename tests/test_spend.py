"""Gemini spending: per-day ledger kept across sessions, cached-input price, the daily
limit (fallback once reached, back when raised or on a new day), live numbers. No network."""

import pytest

from subwizard.config import ModelPrice, load_config
from subwizard.core.metrics import M, MetricsHub
from subwizard.translator.base import TranslationRequest, TranslationResult
from subwizard.translator.cache import TranslationCache
from subwizard.translator.service import TranslationService
from subwizard.translator.spend import SpendLedger
from tests.test_translation_core import FakeEngine

DAY = 86_400.0


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_ledger_adds_per_day_and_survives_a_restart(tmp_path):
    clock, path = Clock(), tmp_path / "usage.sqlite"
    ledger = SpendLedger(path, clock)
    ledger.add(0.01, 100, 10, 40)
    ledger.add(0.02, 200, 20)
    assert ledger.today().cost_usd == pytest.approx(0.03) and ledger.today().requests == 2
    ledger.close()
    clock.t += DAY  # tomorrow, a new session
    again = SpendLedger(path, clock)
    assert again.today().cost_usd == 0.0
    again.add(0.05, 10, 1)
    total = again.total()
    assert total.cost_usd == pytest.approx(0.08) and total.requests == 3
    assert total.input_tokens == 310 and total.cached_tokens == 40
    again.close()


class Priced(FakeEngine):
    """Gemini answering with given token counts (the cached part included)."""

    def __init__(self, input_tokens=1_000_000, output_tokens=0, cached=0):
        super().__init__("gemini")
        self.usage = (input_tokens, output_tokens, cached)

    def translate(self, request):
        self.calls.append(request)
        i, o, c = self.usage
        return TranslationResult(f"TR<{request.text}>", "gemini", 50.0, input_tokens=i,
                                 output_tokens=o, cached_tokens=c)  # fmt: skip


def _service(engine, limit=0.0, clock=None, fallback=None):
    config = load_config(None, {"translator": {"disk_cache": False, "daily_limit_usd": limit}})
    metrics = MetricsHub()
    svc = TranslationService(config, metrics, engine, fallback, TranslationCache(0),
                             ledger=SpendLedger(None, clock or Clock()))  # fmt: skip
    return svc, metrics, config


def test_cached_input_is_priced_lower():
    # 1M input of which 600k cached: 0.4M x $0.30 + 0.6M x $0.03 = $0.138
    svc, metrics, _ = _service(Priced(cached=600_000))
    assert svc.translate(TranslationRequest("Hi.")).cost_usd == pytest.approx(0.138)
    assert metrics.snapshot().gauge(M.TR_COST_TODAY) == pytest.approx(0.138)
    assert ModelPrice(input=0.30, output=2.5).cached() == pytest.approx(0.03)  # default: a tenth


def test_numbers_are_known_before_the_first_request():
    ledger = SpendLedger(None, Clock())
    ledger.add(1.25, 0, 0)
    config = load_config(None, {"translator": {"disk_cache": False}})
    metrics = MetricsHub()
    TranslationService(config, metrics, Priced(), None, TranslationCache(0), ledger=ledger)
    assert metrics.snapshot().gauge(M.TR_COST_TOTAL) == pytest.approx(1.25)


def test_daily_limit_switches_to_the_fallback_and_back():
    clock = Clock()
    gemini = Priced()  # $0.30 per line
    svc, metrics, config = _service(gemini, limit=0.5, clock=clock, fallback=FakeEngine("deep"))
    assert svc.translate(TranslationRequest("One.")).engine == "gemini"  # $0.30
    assert svc.translate(TranslationRequest("Two.")).engine == "gemini"  # $0.60: over now
    third = svc.translate(TranslationRequest("Three."))
    assert third.engine == "deep" and third.fallback and len(gemini.calls) == 2
    assert metrics.snapshot().gauge(M.TR_LIMIT) is True
    config.translator.daily_limit_usd = 1.0  # raised by hand: applies at once
    assert svc.translate(TranslationRequest("Four.")).engine == "gemini"
    assert metrics.snapshot().gauge(M.TR_LIMIT) is False
    config.translator.daily_limit_usd = 0.5
    svc.translate(TranslationRequest("Five."))
    clock.t += DAY  # a new day starts at zero
    assert svc.translate(TranslationRequest("Six.")).engine == "gemini"


def test_spending_row_on_the_translation_page(tmp_path, clip):
    pytest.importorskip("PySide6")
    from subwizard.ui.pages.home import translator_state
    from subwizard.ui.pages.translate import TranslatePage, spend_text
    from tests.test_ui_m1 import _window

    app, win = _window(tmp_path, {"source": {"kind": "file", "path": str(clip)}})
    try:
        c = win.controller
        page = next(p for p in win.findChildren(TranslatePage))
        page._limit.setText("0.75")
        page._on_limit()
        assert c.config.translator.daily_limit_usd == 0.75
        hub = c.metrics
        hub.set_gauge(M.TR_COST_TODAY, 0.0123)
        hub.set_gauge(M.TR_COST_TOTAL, 1.5)
        hub.set_gauge(M.TR_COST, 0.002)
        text = spend_text(c.snapshot(), 0.75)
        assert "$0.0123 / $0.75" in text and "$1.50" in text and "$0.0020" in text
        hub.set_gauge(M.TR_LIMIT, True)
        assert translator_state(c)[1] == "warn" and "0.75" in translator_state(c)[0]
        page._limit.setText("")
        page._on_limit()
        assert c.config.translator.daily_limit_usd == 0.0
    finally:
        c.shutdown()
        win.close()
