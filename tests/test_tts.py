"""M7 TTS (SPEC 3.11): worker queue, catch-up modes, session wiring. No real speech."""

import threading
import time

from subwizard.tts.base import BaseTTS
from subwizard.tts.worker import MAX_SPEEDUP, TTSWorker, Utterance


class FakeTTS(BaseTTS):
    name = "fake"

    def __init__(self, hold=None):
        self.said = []
        self.hold = hold  # threading.Event: block the first utterance until set

    def speak(self, text, language, rate):
        self.said.append((text, language, round(rate, 2)))
        if self.hold is not None:
            self.hold.wait(2)
            self.hold = None


def _wait(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_queue_behaviour_with_release():
    gate = threading.Event()
    engine = FakeTTS(hold=gate)
    w = TTSWorker(engine, catch_up="skip")
    w.start()
    w.say(Utterance(1, "one", "tr"))
    assert _wait(lambda: engine.said)
    w.say(Utterance(2, "two", "tr"))
    w.say(Utterance(3, "three", "tr"))
    gate.set()
    assert _wait(lambda: len(engine.said) == 2)
    assert [t for t, *_ in engine.said] == ["one", "three"] and w.dropped == 1
    w.stop()


def test_speedup_talks_faster_instead_of_dropping_and_grown_lines_replace():
    gate = threading.Event()
    engine = FakeTTS(hold=gate)
    w = TTSWorker(engine, rate=1.0, catch_up="speedup")
    w.start()
    w.say(Utterance(1, "one", "en"))
    assert _wait(lambda: engine.said)
    w.say(Utterance(2, "two", "en"))
    w.say(Utterance(3, "three", "en"))
    w.say(Utterance(4, "three and more", "en"), replaces=3)  # the line grew
    gate.set()
    assert _wait(lambda: len(engine.said) == 3)
    assert [t for t, *_ in engine.said] == ["one", "two", "three and more"]
    assert engine.said[1][2] == 1.25 and engine.said[2][2] == 1.0  # one waiting -> +25 %
    assert w.dropped == 0
    assert w.rate_for(100) == MAX_SPEEDUP
    w.stop()


def test_engine_errors_do_not_stop_the_worker():
    class Broken(FakeTTS):
        def speak(self, text, language, rate):
            super().speak(text, language, rate)
            if text == "bad":
                raise OSError("device busy")

    engine = Broken()
    w = TTSWorker(engine)
    w.start()
    w.say(Utterance(1, "bad", "tr"))
    assert _wait(lambda: len(engine.said) == 1)
    w.say(Utterance(2, "good", "tr"))
    assert _wait(lambda: w.spoken == 1)
    w.stop()


def test_say_voice_listing_and_sapi_voice_pick():
    from subwizard.platform.windows.tts_sapi import pick_voice
    from subwizard.tts.say import parse_voices

    voices = parse_voices("Yelda               tr_TR    # Merhaba\nGood News  en_US  # Hi\n")
    assert voices == {"Yelda": "tr_TR", "Good News": "en_US"}
    sapi = [("HKEY\\TTS_MS_EN-US_ZIRA_11.0", "Microsoft Zira Desktop - English (United States)"),
            ("HKEY\\TTS_MS_TR-TR_TOLGA_11.0", "Microsoft Tolga - Turkish (Turkey)")]
    assert pick_voice(sapi, "tr").endswith("TOLGA_11.0")
    assert pick_voice(sapi, "en").endswith("ZIRA_11.0")
    assert pick_voice(sapi, "de") == ""


def test_build_tts_without_an_engine_is_off():
    from subwizard.tts import build_tts

    assert build_tts("sapi") is None or __import__("sys").platform == "win32"


def _session_said(clip, mode):
    from subwizard.config import load_config
    from subwizard.core.metrics import MetricsHub
    from subwizard.core.session import Session
    from tests.test_pipeline_m3 import LINES, ScriptedOCR

    config = load_config(None, {"source": {"kind": "file", "path": str(clip), "loop": True},
                                "ocr": {"hz": 20, "change_mode": "gray"},
                                "subtitle": {"min_display_ms": 0}, "tts": {"mode": mode}})  # fmt: skip
    engine, translated = FakeTTS(), []
    session = Session(config, MetricsHub(), on_frame=lambda: None,
                      on_source_state=lambda s, m: None, on_audio_state=lambda s, m: None,
                      ocr_factory=lambda cfg: ScriptedOCR(), on_translated=translated.append,
                      tts_factory=lambda engine_name, voice: engine)  # fmt: skip
    session.start()
    try:
        _wait(lambda: len(engine.said) >= 2, timeout=10)
    finally:
        session.stop()
    return engine.said, translated, LINES


def test_translation_mode_reads_the_turkish(clip):
    said, translated, lines = _session_said(clip, "translation")
    assert said[:2] == [(f"[tr] {lines[0]}", "tr", 1.0), (f"[tr] {lines[1]}", "tr", 1.0)]


def test_source_mode_reads_the_english_and_does_not_translate(clip):
    said, translated, lines = _session_said(clip, "source")
    assert said[:2] == [(lines[0], "en", 1.0), (lines[1], "en", 1.0)]
    assert translated == []
