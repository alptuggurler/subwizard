"""Replace mode: the cover over the English text does not shake between OCR reads."""

from types import SimpleNamespace

from subwizard.ui.widgets.video_view import VideoView

steady = VideoView._steady_bounds


def test_same_line_only_grows():
    view = SimpleNamespace(_steady=None)
    assert steady(view, 1, (100, 900, 800, 950), 1) == ((100, 900, 800, 950), 1)
    # The next read is a few pixels off and the line half faded: the box stays put.
    assert steady(view, 1, (104, 903, 600, 948), 1) == ((100, 900, 800, 950), 1)
    # A second speaker line appears above it: the box grows to cover both.
    assert steady(view, 1, (100, 850, 800, 950), 2) == ((100, 850, 800, 950), 2)


def test_new_line_or_other_place_starts_afresh():
    view = SimpleNamespace(_steady=None)
    steady(view, 1, (100, 850, 800, 950), 2)
    assert steady(view, 2, (100, 900, 800, 950), 1) == ((100, 900, 800, 950), 1)
    # Same line id, but the text is now somewhere else (no vertical overlap).
    assert steady(view, 2, (100, 100, 800, 150), 1) == ((100, 100, 800, 150), 1)


pick = VideoView._pick_cover


def test_translation_after_english_left_stays_in_place():
    view = SimpleNamespace(_last_cover=None)
    assert pick(view, 1, "over line 1", True, "replace") == "over line 1"
    # Line 2's translation arrives after its English left the screen: no OCR boxes.
    assert pick(view, 2, None, True, "replace") == "over line 1"
    assert pick(view, 3, "over line 3", True, "replace") == "over line 3"


def test_above_mode_keeps_old_rule():
    view = SimpleNamespace(_last_cover=None)
    pick(view, 1, "over line 1", True, "above")
    assert pick(view, 2, None, True, "above") is None
    assert pick(view, 2, None, False, "above") == "over line 1"  # fading out


def test_no_cover_yet():
    assert pick(SimpleNamespace(_last_cover=None), 1, None, True, "replace") is None


def test_only_the_subtitle_lines_make_the_cover():
    from subwizard.ui.subtitle_paint import matches_source

    source = "Lucia Hands up, this is a robbery!"
    assert matches_source("Lucia: Hands up, this is a", source)
    assert matches_source("robbery!", source)
    assert not matches_source("PHOTO STUDIO CFIF", source)
    assert not matches_source("eciyaovey-kern michael Wiafe", source)
    assert not matches_source("0 0", source)