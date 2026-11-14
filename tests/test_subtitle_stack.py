"""How the translation enters and leaves the screen (pipeline/subtitle_stack.py)."""

import pytest

from subwizard.pipeline.board import SubtitleView
from subwizard.pipeline.subtitle_stack import (
    PREVIOUS_HOLD_S,
    PREVIOUS_OPACITY,
    PREVIOUS_SCALE,
    SubtitleStack,
)


def view(n, text, source=None):
    return SubtitleView(n, text, None, source or f"source {n}", "gemini", False, 0.0, 0.0)


def shown(stack, now):
    return [(i.view.text, round(i.opacity, 2), round(i.scale, 2), i.current) for i in stack.items(now)]


def test_a_line_fades_in_and_out():
    stack = SubtitleStack(max_lines=2, fade_ms=200)
    stack.show(view(1, "Selam"), 10.0)
    assert shown(stack, 10.0) == [("Selam", 0.0, 1.0, True)]
    assert shown(stack, 10.1) == [("Selam", 0.5, 1.0, True)]
    assert shown(stack, 11.0) == [("Selam", 1.0, 1.0, True)] and not stack.animating(11.0)
    stack.clear(11.0)
    assert shown(stack, 11.1) == [("Selam", 0.5, 1.0, False)] and stack.animating(11.1)
    assert shown(stack, 11.3) == [] and not stack.animating(11.3)


def test_the_previous_line_stays_above_small_and_dim_then_goes():
    stack = SubtitleStack(max_lines=2, fade_ms=200)
    stack.show(view(1, "Bir"), 0.0)
    stack.show(view(2, "İki"), 5.0)
    assert shown(stack, 6.0) == [("Bir", PREVIOUS_OPACITY, PREVIOUS_SCALE, False),
                                 ("İki", 1.0, 1.0, True)]  # fmt: skip
    stack.show(view(3, "Üç"), 6.0)  # only one older line fits
    assert [text for text, *_ in shown(stack, 6.5)] == ["İki", "Üç"]
    late = 6.0 + PREVIOUS_HOLD_S + 0.1
    assert shown(stack, late)[0][1] == pytest.approx(PREVIOUS_OPACITY / 2, abs=0.02)
    assert [text for text, *_ in shown(stack, late + 0.2)] == ["Üç"]
    assert not stack.animating(late + 0.2)


def test_the_same_line_is_replaced_in_place():
    stack = SubtitleStack(max_lines=3, fade_ms=200)
    stack.show(view(1, "Yara beni", "Yara did not elect me to"), 0.0)
    # The joined sentence (another line id, its source starts with the old one) and a
    # retranslation (same id) take the place of the line; nothing is pushed up.
    stack.show(view(2, "Yara beni kolayı yapmam için seçmedi.",
                    "Yara did not elect me to do what's easy."), 2.0)  # fmt: skip
    stack.show(view(2, "Yara beni kolay olanı yapmam için seçmedi.",
                    "Yara did not elect me to do what's easy."), 3.0)  # fmt: skip
    assert shown(stack, 3.0) == [("Yara beni kolay olanı yapmam için seçmedi.", 1.0, 1.0, True)]


def test_effects_can_be_turned_off():
    stack = SubtitleStack(max_lines=1, fade_ms=0)
    stack.show(view(1, "Bir"), 0.0)
    assert shown(stack, 0.0) == [("Bir", 1.0, 1.0, True)]
    stack.show(view(2, "İki"), 1.0)
    assert shown(stack, 1.0) == [("İki", 1.0, 1.0, True)]
    stack.clear(2.0)
    assert shown(stack, 2.0) == [] and not stack.animating(2.0)
    stack.configure(3, 100)
    assert (stack.max_lines, stack.fade_s) == (3, 0.1)
