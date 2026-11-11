"""M3 end to end: video -> OCR -> state machine -> translator -> subtitle shown.

Without an API key the fallback (deep-translator) does the work (M3 acceptance).
The default run uses an offline stand-in for Google Translate (conftest); the
`network` test calls the real service: `uv run pytest -m network`.
"""

import time

import pytest

from subwizard.config import load_config
from subwizard.core.metrics import M, MetricsHub
from subwizard.core.session import Session
from subwizard.ocr.base import BaseOCR, OCRLine, OCRResult
from subwizard.translator.test_run import run_test

LINES = ["We need to get out of here, now.", "Not without the money."]


class ScriptedOCR(BaseOCR):
    """Reads LINES[0] for a while, then nothing, then LINES[1], whatever the frame shows."""

    name = "rapid"

    def __init__(self):
        self.calls = 0

    def recognize(self, image, scale=1.0):
        self.calls += 1
        phase = (self.calls // 6) % 3  # 6 reads per phase
        text = {0: LINES[0], 1: "", 2: LINES[1]}[phase]
        return OCRResult((OCRLine(text, 0.95, (0, 0, 10, 10)),) if text else ())


def run_session(clip, overrides, until, timeout=10.0):
    config = load_config(None, {"source": {"kind": "file", "path": str(clip), "loop": True},
                                "ocr": {"hz": 20, "change_mode": "gray"},
                                "subtitle": {"min_display_ms": 0}, **overrides})  # fmt: skip
    metrics = MetricsHub()
    shown, translated = [], []
    session = Session(
        config, metrics,
        on_frame=lambda: None, on_source_state=lambda s, m: None,
        on_audio_state=lambda s, m: None,
        ocr_factory=lambda cfg: ScriptedOCR(),
        on_subtitle=shown.append, on_translated=translated.append,
    )  # fmt: skip
    session.start()
    deadline = time.time() + timeout
    try:
        while time.time() < deadline and not until(shown):
            time.sleep(0.02)
    finally:
        session.stop()
    return shown, translated, metrics.snapshot()


def texts(shown):
    return [v.text if v else None for v in shown]


def test_without_api_key_lines_are_translated_by_the_fallback(clip):
    shown, translated, snap = run_session(
        clip, {}, until=lambda s: any(v and v.source == LINES[1] for v in s))
    views = [v for v in shown if v is not None]
    assert [v.source for v in views][:2] == LINES
    assert views[0].text == f"[tr] {LINES[0]}"
    assert views[0].engine == "deep" and views[0].fallback  # "(yedek)" marker
    assert None in shown[1:]  # the first line cleared when the region went empty
    assert snap.counter(M.TR_FALLBACKS) >= 2
    assert snap.counter(M.TR_REQUESTS) == 0  # no key: Gemini never called
    assert snap.stat(M.SUB_LATENCY_MS).count or snap.stat(M.SUB_LATENCY_MS).mean_long > 0


def test_each_line_is_translated_once_per_appearance(clip):
    shown, translated, _ = run_session(
        clip, {}, until=lambda s: sum(1 for v in s if v and v.source == LINES[1]) >= 1)
    sources = [t.line.text for t in translated]
    # Line 1 stays on screen for 6 reads but is translated once.
    assert sources.count(LINES[0]) == 1


def test_deep_engine_setting_skips_gemini_even_with_a_key(clip, isolated_user_state):
    from subwizard.core import secrets

    secrets.save_api_keys(["k-1234567890123"])
    shown, _, snap = run_session(clip, {"translator": {"engine": "deep"}},
                                 until=lambda s: any(v for v in s))  # fmt: skip
    view = next(v for v in shown if v)
    assert view.engine == "deep" and not view.fallback  # chosen, not a fallback
    assert snap.counter(M.TR_REQUESTS) == 0


def test_translation_errors_do_not_stop_the_pipeline(clip, monkeypatch):
    from subwizard.translator.deep import DeepTranslatorFallback

    calls = {"n": 0}

    def flaky(self, text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("offline")
        return f"ok {text}"

    monkeypatch.setattr(DeepTranslatorFallback, "_google", flaky)
    shown, _, snap = run_session(clip, {}, until=lambda s: any(v for v in s))
    assert any(v and v.text.startswith("ok ") for v in shown)
    assert snap.counter(M.TR_ERRORS) == 1


@pytest.mark.network
def test_real_google_translate_without_api_key():
    """M3 acceptance against the real fallback service (needs internet)."""
    outcome = run_test(load_config(None, {}), "Where the hell did you park the car?")
    assert outcome.engine == "deep" and outcome.text
    assert outcome.text != "Where the hell did you park the car?"
    print(f"\n{outcome.text!r} in {outcome.latency_ms:.0f} ms")
