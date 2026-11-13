"""What the first real Gemini session on Windows showed (free-tier quota after ~20 lines,
Google refusing the network, a sentence shown in pieces, console menus sent for
translation): fallback chain with resting providers, quota notice, fragment joining,
foreign-text filter. No network."""

import pytest

from subwizard.config import SubtitleConfig, load_config
from subwizard.core.metrics import M, MetricsHub
from subwizard.pipeline.subtitle_state import SubtitleLine, SubtitleStateMachine
from subwizard.pipeline.translate_worker import (
    MERGE_WINDOW_S,
    FragmentJoiner,
    TranslateWorker,
    continues,
    sentence_open,
)
from subwizard.translator import deep as deep_module
from subwizard.translator.base import TranslationError, TranslationRequest, TranslationResult
from subwizard.translator.cache import TranslationCache
from subwizard.translator.deep import DeepTranslatorFallback, mymemory_code
from subwizard.translator.service import TranslationService
from tests.test_translation_core import FakeEngine


class TooManyRequests(Exception):  # named like deep_translator's
    pass


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def chain(google, mymemory, clock):
    d = DeepTranslatorFallback("en", "tr", 1.0, clock=clock)
    d._providers = [("google", google), ("mymemory", mymemory)]
    return d


# --- fallback chain -------------------------------------------------------------------
def test_refused_provider_rests_and_the_next_one_translates():
    clock, asked = Clock(), []

    def google(text):
        asked.append("google")
        raise TooManyRequests("Server Error: You made too many requests to the server.")

    d = chain(google, lambda text: asked.append("mymemory") or "Selam", clock)
    assert d.translate(TranslationRequest("Hello")).text == "Selam"
    assert d.translate(TranslationRequest("Hello")).text == "Selam"
    assert asked == ["google", "mymemory", "mymemory"] and d.resting() == ["google"]
    clock.now += deep_module.RATE_LIMIT_REST_S + 1
    d.translate(TranslationRequest("Hello"))
    assert asked[-2:] == ["google", "mymemory"]  # asked again after the rest
    d.close()


def test_every_provider_failing_reports_each_reason():
    clock = Clock()

    def used_up(text):
        raise deep_module.DailyLimit("MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS")

    def offline(text):
        raise ConnectionError("no route")

    d = chain(offline, used_up, clock)
    with pytest.raises(TranslationError) as raised:
        d.translate(TranslationRequest("Hello"))
    text = str(raised.value)
    assert text.startswith("fallback_failed (google: ConnectionError: no route; mymemory: Daily")
    assert d.resting() == ["mymemory"]  # a plain network error does not rest a provider
    clock.now += deep_module.RATE_LIMIT_REST_S + 1
    assert d.resting() == ["mymemory"]  # the daily allowance rests much longer
    with pytest.raises(TranslationError, match="mymemory: resting"):
        d.translate(TranslationRequest("Hello"))
    d.close()


def test_mymemory_language_codes():
    assert (mymemory_code("en"), mymemory_code("tr"), mymemory_code("de")) == (
        "en-GB", "tr-TR", "de-DE")  # fmt: skip
    assert mymemory_code("tr-TR") == "tr-TR" and mymemory_code("xx") == "xx"


# --- quota notice ---------------------------------------------------------------------
def test_quota_is_a_gauge_and_is_logged_once(caplog):
    config = load_config(None, {"translator": {"disk_cache": False}})
    metrics = MetricsHub()
    gemini = FakeEngine(fail=TranslationError("quota (ClientError: 429 RESOURCE_EXHAUSTED)"))
    svc = TranslationService(config, metrics, gemini, FakeEngine("deep"), TranslationCache(0))
    with caplog.at_level("WARNING"):
        for text in ("One.", "Two.", "Three."):
            assert svc.translate(TranslationRequest(text)).fallback
    assert metrics.snapshot().gauge(M.TR_QUOTA) is True
    assert sum("yedeğe geçiliyor" in r.message for r in caplog.records) == 1
    gemini.fail = None
    assert svc.translate(TranslationRequest("Four.")).engine == "gemini"
    assert metrics.snapshot().gauge(M.TR_QUOTA) is False


def test_home_card_names_the_quota(tmp_path, clip):
    pytest.importorskip("PySide6")
    from subwizard.i18n import t
    from subwizard.ui.pages.home import translator_state
    from tests.test_ui_m1 import _window

    app, win = _window(tmp_path, {"source": {"kind": "file", "path": str(clip)}})
    try:
        hub = win.controller.metrics
        hub.set_gauge(M.TR_QUOTA, True)
        assert translator_state(win.controller) == (t("home.translator.quota_fallback"), "warn")
        hub.set_gauge(M.TR_OFFLINE, True)
        assert translator_state(win.controller) == (t("home.translator.quota_stopped"), "bad")
    finally:
        win.controller.shutdown()
        win.close()


# --- a sentence shown in pieces -------------------------------------------------------
def line(n, text, at, speaker=None, replaces=None):
    return SubtitleLine(n, text, speaker, at, replaces)


def test_sentence_open_and_continues():
    assert sentence_open("Created by our own brilliant scientists, the most")
    assert sentence_open("Yara did not elect me to")
    for done in ("has ever seen.", 'the "Draft to Paradise".', "Dani, say something!",
                 "be drafted tomorrow--", 'taking "True Yarans"?', ""):  # fmt: skip
        assert not sentence_open(done), done
    assert continues("Yara did not elect me to", "do what's easy.")
    assert continues("the most,", "Effective treatment")  # comma: goes on even in upper case
    assert not continues("These lucky Yarans will be chosen", "Even my own son Diego could")


def test_pieces_of_one_sentence_are_sent_together():
    j = FragmentJoiner()
    sent = [j.text_for(line(1, "Created by our own brilliant scientists, the most", 0.0)),
            j.text_for(line(2, "effective treatment for cancer the world", 4.0)),
            j.text_for(line(3, "has ever seen.", 6.0)),
            j.text_for(line(4, "Grown within our precious tobacco,", 7.0))]  # fmt: skip
    assert sent == [
        "Created by our own brilliant scientists, the most",
        "Created by our own brilliant scientists, the most effective treatment for cancer the world",
        "Created by our own brilliant scientists, the most effective treatment for cancer the "
        "world has ever seen.",
        "Grown within our precious tobacco,",  # the sentence ended: a fresh start
    ]


def test_pieces_are_not_joined_across_speakers_long_gaps_or_new_sentences():
    j = FragmentJoiner()
    j.text_for(line(1, "I was going to", 0.0, speaker="DANI"))
    assert j.text_for(line(2, "leave tonight.", 1.0, speaker="LITA")) == "leave tonight."
    j.text_for(line(3, "I was going to", 2.0))
    assert j.text_for(line(4, "leave tonight.", 2.0 + MERGE_WINDOW_S + 1)) == "leave tonight."
    j.text_for(line(5, "These lucky Yarans will be chosen", 20.0))
    assert j.text_for(line(6, "Even my own son", 21.0)) == "Even my own son"
    # A line that grew (typewriter) replaces its shorter self instead of being appended.
    j.text_for(line(7, "And so with", 30.0))
    assert j.text_for(line(8, "And so with renewed focus,", 30.5, replaces=7)) == (
        "And so with renewed focus,")
    assert j.text_for(line(9, "i must go on.", 31.0)) == "And so with renewed focus, i must go on."


def test_joining_stops_at_the_length_limit():
    j = FragmentJoiner()
    texts = [j.text_for(line(i, "and then some more words follow here,", float(i)))
             for i in range(1, 8)]  # fmt: skip
    assert max(len(t) for t in texts) <= 240 and max(t.count(",") for t in texts) <= 4


def _worker(merge=True):
    config = load_config(None, {"translator": {"disk_cache": False, "merge_fragments": merge}})
    engine = FakeEngine()
    metrics = MetricsHub()
    out = []
    worker = TranslateWorker(config, metrics, lambda c, m: None, out.append)
    worker.service = TranslationService(config, metrics, engine, None, TranslationCache(0))
    return worker, engine, out


def test_worker_translates_the_whole_sentence_and_keeps_one_context_entry():
    worker, engine, out = _worker()
    worker._translate(line(1, "Yara did not elect me to", 0.0))
    worker._translate(line(2, "do what's easy.", 1.5))
    worker._translate(line(2, "do what's easy.", 1.5), force=True)  # F7 resends the joined text
    worker._translate(line(3, "But to do what's right.", 3.0))
    whole = "Yara did not elect me to do what's easy."
    assert [r.text for r in engine.calls] == [
        "Yara did not elect me to", whole, whole, "But to do what's right."]
    assert out[1].line.id == 2 and out[1].result.text == f"TR<{whole}>"
    # The half sentence is not context for the next line, and F7 does not repeat it.
    assert [source for source, _ in engine.calls[3].context] == [whole]


def test_a_skipped_piece_breaks_the_sentence():
    j = FragmentJoiner()
    j.text_for(line(1, "Created by our own brilliant scientists, the most", 0.0))
    # Line 2 left the screen before its turn (latest-only): 3 is not joined to 1.
    assert j.text_for(line(3, "has ever seen.", 3.0)) == "has ever seen."


def test_worker_leaves_lines_alone_when_merging_is_off():
    worker, engine, _ = _worker(merge=False)
    worker._translate(line(1, "Yara did not elect me to", 0.0))
    worker._translate(line(2, "do what's easy.", 1.5))
    assert [r.text for r in engine.calls] == ["Yara did not elect me to", "do what's easy."]


# --- text that is not the game's subtitle ---------------------------------------------
def test_console_menus_and_other_scripts_are_not_subtitles():
    sm = SubtitleStateMachine(SubtitleConfig(), (), "en", "tr")
    for menu in ("Oyun Yardımı Kupalar Kullanın 人 V φ e", "Oyun Yardımı Kupalar Kullanım Kola",
                 "Oyun Yardımı Kupalar Kullanın n Et 人 门 V φ e"):  # fmt: skip
        assert sm.clean(menu) == "", menu
    assert sm.clean("Turn that shit off. 人") == "Turn that shit off."
    # Accented names and one stray Turkish-looking letter do not drop an English line.
    assert sm.clean("García's (crazy) crusade?") == "García's (crazy) crusade?"
    assert sm.clean("ENTE ANTÓN CASTILLO was hıre") == "ENTE ANTÓN CASTILLO was hıre"
    off = SubtitleStateMachine(SubtitleConfig(skip_foreign=False), (), "en", "tr")
    assert off.clean("Oyun Yardımı Kupalar Kullanım Kola") != ""
    assert SubtitleStateMachine(SubtitleConfig(), (), "tr", "en").clean("Yardımı Kullanın") != ""
