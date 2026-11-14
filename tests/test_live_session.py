"""What the 2026-11-15 Windows session (PS5, Gemini) showed: two speakers read as one
text were translated as one (the second line lost) and their lines were shown twice;
lines replaced without an empty moment stayed open in the SRT log until the session
ended. No network."""

from subwizard.config import load_config
from subwizard.core.metrics import MetricsHub
from subwizard.pipeline.board import SubtitleView
from subwizard.pipeline.history import SubtitleHistory
from subwizard.pipeline.subtitle_stack import SubtitleStack
from subwizard.pipeline.subtitle_state import SubtitleLine
from subwizard.pipeline.translate_worker import TranslateWorker, split_speakers
from subwizard.translator.base import TranslationError, TranslationResult
from subwizard.translator.cache import TranslationCache
from subwizard.translator.service import TranslationService
from tests.test_translation_core import FakeEngine


def line(n, text, at=0.0, speaker=None, replaces=None):
    return SubtitleLine(n, text, speaker, at, replaces)


# --- two speakers in one read ---------------------------------------------------------
def test_split_speakers():
    assert split_speakers("Good. Lita: The way out is up ahead.") == [
        (None, "Good."), ("Lita", "The way out is up ahead.")]
    assert split_speakers("(What the hell?) Soldier: They went this way!") == [
        (None, "(What the hell?)"), ("Soldier", "They went this way!")]
    for one in ("Castillo shows no mercy, Dani.", "..So much blood.",
                "Remember this: we go at dawn.", "It's 5:30. Move."):  # fmt: skip
        assert split_speakers(one) == [(None, one)], one


def _worker(split=True):
    config = load_config(None, {"translator": {"disk_cache": False, "split_speakers": split}})
    engine, metrics, out = FakeEngine(), MetricsHub(), []
    worker = TranslateWorker(config, metrics, lambda c, m: None, out.append)
    worker.service = TranslationService(config, metrics, engine, None, TranslationCache(100))
    return worker, engine, out


def test_each_speaker_is_translated_alone_and_shown_one_per_line():
    worker, engine, out = _worker()
    worker._translate(line(1, "The way out is up ahead.", speaker="Lita"))
    worker._translate(line(2, "Good. Lita: The way out is up ahead.", speaker="Dani"))
    # The second speaker's line was translated a moment ago: a cache hit, one new request.
    assert [r.text for r in engine.calls] == ["The way out is up ahead.", "Good."]
    assert engine.calls[1].speaker == "Dani"
    block = out[1].result
    assert block.text == "TR<Good.>\nLita: TR<The way out is up ahead.>"
    assert out[1].line.id == 2 and out[1].source == "Good. Lita: The way out is up ahead."


def test_splitting_can_be_turned_off():
    worker, engine, _ = _worker(split=False)
    worker._translate(line(1, "Good. Lita: The way out is up ahead."))
    assert [r.text for r in engine.calls] == ["Good. Lita: The way out is up ahead."]


def test_a_failed_part_fails_the_block():
    worker, engine, out = _worker()
    engine.fail = TranslationError("down")
    worker._translate(line(1, "Good. Lita: The way out is up ahead."))
    assert out[0].result is None


# --- the same words never twice on screen ---------------------------------------------
def view(n, text, source):
    return SubtitleView(n, text, None, source, "gemini", False, 0.0, 0.0)


def texts(stack, now):
    return [i.view.text for i in stack.items(now)]


def test_a_second_speaker_appearing_or_leaving_replaces_the_line_in_place():
    stack = SubtitleStack(max_lines=2, fade_ms=0)
    stack.show(view(1, "Bu tarafa gittiler!", "They went this way!"), 1.0)
    stack.show(view(2, "(Bu da ne?)\nSoldier: Bu tarafa gittiler!",
                    "(What the hell?) Soldier: They went this way!"), 2.0)  # fmt: skip
    assert texts(stack, 2.0) == ["(Bu da ne?)\nSoldier: Bu tarafa gittiler!"]
    stack.show(view(3, "(Bu da ne?)", "(What the hell?)"), 3.0)  # the soldier's line went
    assert texts(stack, 3.0) == ["(Bu da ne?)"]
    # A different line still pushes the previous one up.
    stack.show(view(4, "Neredeyse geldik.", "Almost there."), 4.0)
    assert texts(stack, 4.0) == ["(Bu da ne?)", "Neredeyse geldik."]


def test_one_shared_word_is_not_the_same_line():
    stack = SubtitleStack(max_lines=2, fade_ms=0)
    stack.show(view(1, "Hayır.", "No."), 1.0)
    stack.show(view(2, "Kimseyi tanımıyorum.", "I know no one."), 2.0)
    assert len(texts(stack, 2.0)) == 2


# --- SRT end times --------------------------------------------------------------------
def test_a_line_replaced_without_a_gap_ends_when_the_next_one_starts(tmp_path):
    log = tmp_path / "subs.srt"
    h = SubtitleHistory(0.0, log, clock=lambda: 200.0)
    h.add(line(1, "Almost there.", 64.0))
    h.translated(1, TranslationResult("Neredeyse geldik.", "gemini", 1.0))
    h.add(line(2, "The way out is up ahead.", 66.0))  # no LineCleared in between
    first = h.get(1)
    assert first.end_s == 66.0
    assert "00:01:04,000 --> 00:01:06,000\nNeredeyse geldik." in log.read_text("utf-8")


def test_a_block_is_drawn_one_speaker_per_line():
    import pytest

    pytest.importorskip("PySide6")
    from PySide6.QtGui import QFontMetrics

    from subwizard.ui.subtitle_paint import make_font, wrap_lines
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])  # fonts need an application

    fm = QFontMetrics(make_font(("Arial",), 20, False))
    assert wrap_lines("(Bu da ne?)\nSoldier: Bu tarafa gittiler!", fm, 10_000) == [
        "(Bu da ne?)", "Soldier: Bu tarafa gittiler!"]
