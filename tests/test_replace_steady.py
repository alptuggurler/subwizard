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
