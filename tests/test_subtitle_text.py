"""Text handling added after the first paid sessions were planned: finished sentences go
on their first sighting, OCR garbage is dropped, long lines stay longer."""

import pytest

from subwizard.config import SubtitleConfig
from subwizard.pipeline.board import SubtitleBoard
from subwizard.pipeline.subtitle_state import (
    LineStable,
    SubtitleLine,
    SubtitleStateMachine,
    ends_sentence,
    strip_noise,
)
from subwizard.translator.base import TranslationResult


def stable(events):
    return [e.line for e in events if isinstance(e, LineStable)]


# --- a finished sentence does not wait for a second OCR tick --------------------------
def test_ends_sentence():
    for done in ("Get down!", "Really?", 'He said "no."', "Wait…", "(Sighs.)"):
        assert ends_sentence(done), done
    for half in ("Created by our own scientists, the most", "Yara did not elect me to", ""):
        assert not ends_sentence(half), half


def test_finished_sentence_is_sent_on_the_first_sighting():
    sm = SubtitleStateMachine(SubtitleConfig())
    first = stable(sm.feed("Turn that off.", 1.0))
    assert [line.text for line in first] == ["Turn that off."]
    assert sm.feed("Turn that off.", 1.25) == []  # seen again: nothing new
    assert sm.feed("Turn that off.", 1.5) == []
    # An unfinished line still needs its second sighting.
    assert sm.feed("Yara did not elect me to", 2.0) == []
    assert [line.text for line in stable(sm.feed("Yara did not elect me to", 2.25))] == [
        "Yara did not elect me to"]  # fmt: skip


def test_a_misread_first_sighting_is_corrected_once():
    sm = SubtitleStateMachine(SubtitleConfig())
    first = stable(sm.feed("Tum that off.", 1.0))[0]  # the subtitle was still fading in
    fixed = stable(sm.feed("Turn that off.", 1.25))
    assert [(line.text, line.replaces, line.seen_at) for line in fixed] == [
        ("Turn that off.", first.id, 1.0)]  # fmt: skip
    assert sm.feed("Tum that off.", 1.5) == []  # later flicker is the same line


def test_first_sighting_rule_can_be_turned_off():
    sm = SubtitleStateMachine(SubtitleConfig(fast_punctuation=False))
    assert sm.feed("Turn that off.", 1.0) == []
    assert len(stable(sm.feed("Turn that off.", 1.25))) == 1


# --- OCR garbage ----------------------------------------------------------------------
@pytest.mark.parametrize(("raw", "clean"), [
    ("We need ou$3e to move now.", "We need to move now."),
    ("The waters2aljd, are rising.", "The waters, are rising."),
    ("23m CESARE: Here, take this!", "CESARE: Here, take this!"),
    ("9? We'll lose them all.", "We'll lose them all."),
    # Ordinary mixes of letters and digits stay.
    ("Press R2 on your PS5 to aim.", "Press R2 on your PS5 to aim."),
    ("It's 100m away, the 2nd door, at 10:30pm.", "It's 100m away, the 2nd door, at 10:30pm."),
    ("That cost me $500 in the 1980s.", "That cost me $500 in the 1980s."),
    ("R2-D2 is 50% done, *sigh*.", "R2-D2 is 50% done, *sigh*."),
    ("100 dollars is not enough.", "100 dollars is not enough."),  # the number is the sentence's
])  # fmt: skip
def test_strip_noise(raw, clean):
    assert strip_noise(raw) == clean


def test_noise_cleaning_is_part_of_the_machine_and_optional():
    assert SubtitleStateMachine(SubtitleConfig()).clean("23m CESARE: Here, take this!") == (
        "CESARE: Here, take this!")
    off = SubtitleStateMachine(SubtitleConfig(clean_noise=False))
    assert off.clean("23m CESARE: Here, take this!") == "23m CESARE: Here, take this!"


# --- a long translation stays longer --------------------------------------------------
def _shown(board, text):
    line = SubtitleLine(1, "source", None, 0.0, None)
    board.line_stable(line)
    assert board.translated(line, TranslationResult(text, "gemini", 5.0), 10.0)
    assert not board.cleared(line.id, 10.2)  # the English left almost at once


def test_long_translation_stays_on_screen_longer():
    short, long = (SubtitleBoard(1500, 50, 7000) for _ in range(2))
    _shown(short, "Kapat.")  # 6 chars: 1.5 + 0.3 s
    _shown(long, "x" * 60)  # 1.5 + 3.0 s
    assert not short.tick(11.7) and short.tick(11.9)
    assert not long.tick(14.4) and long.tick(14.6)
    capped = SubtitleBoard(1500, 50, 7000)
    _shown(capped, "x" * 400)
    assert not capped.tick(16.9) and capped.tick(17.1)  # never beyond max_display_ms
    fixed = SubtitleBoard(1500)  # ms_per_char 0: the old fixed time
    _shown(fixed, "x" * 400)
    assert fixed.tick(11.6)


# --- the same line, read a little differently, comes from the cache -------------------
def test_loose_key_folds_ocr_mixups_but_not_meaning():
    from subwizard.translator.cache import loose_key

    same = ["I'll kill him, Lucia.", "l'll kill him, Lucia", "I'II kiII him,Lucia.", "1'll kill him , Lucia."]
    assert len({loose_key(text, "tr") for text in same}) == 1
    different = [
        ("I can go there now.", "I can't go there now."),  # a missing letter is another word
        ("You have 5 minutes left.", "You have 6 minutes left."),
        ("You did it all alone.", "You did it all alone?"),
        ("We need to leave now.", "We need to live now."),
    ]
    for a, b in different:
        assert loose_key(a, "tr") != loose_key(b, "tr"), (a, b)
    assert loose_key("I'll kill him, Lucia.", "tr") != loose_key("I'll kill him, Lucia.", "tr@gta6")
    assert loose_key("I'll.", "tr") is None  # too short to match loosely


def test_a_reread_line_is_not_sent_again(tmp_path):
    from subwizard.config import load_config
    from subwizard.core.metrics import M, MetricsHub
    from subwizard.translator.base import TranslationRequest
    from subwizard.translator.cache import TranslationCache
    from subwizard.translator.service import TranslationService
    from tests.test_translation_core import FakeEngine

    def service(loose=True):
        config = load_config(None, {"translator": {"loose_cache": loose}})
        engine, metrics = FakeEngine(), MetricsHub()
        cache = TranslationCache(100, tmp_path / "cache.sqlite")
        return TranslationService(config, metrics, engine, None, cache), engine, metrics

    svc, engine, _ = service()
    first = svc.translate(TranslationRequest("I'll kill him, Lucia."))
    svc.close()
    svc, engine, metrics = service()  # another session: the alias is on disk
    again = svc.translate(TranslationRequest("l'll kiII him, Lucia"))
    assert (again.text, again.cached) == (first.text, True) and engine.calls == []
    assert metrics.snapshot().counter(M.TR_CACHE_HITS) == 1
    other = svc.translate(TranslationRequest("I can't kill him, Lucia."))
    assert not other.cached and len(engine.calls) == 1
    forced = svc.translate(TranslationRequest("l'll kiII him, Lucia"), force=True)  # F7
    assert not forced.cached
    svc.cache.clear()
    assert not svc.translate(TranslationRequest("l'll kiII him, Lucia")).cached
    svc.close()
    svc, engine, _ = service(loose=False)
    svc.translate(TranslationRequest("Where did you park the car?"))
    assert not svc.translate(TranslationRequest("Where did you park the car")).cached
    svc.close()
