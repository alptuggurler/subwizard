"""PC game mode (Faz 2) UI: Device page, wizard card, click-through overlay window.
No real screen is captured: the session reads the test clip instead."""

import pytest

pytest.importorskip("PySide6")
pytestmark = pytest.mark.ui

from subwizard.sources.video_file import VideoFileSource  # noqa: E402
from tests.test_ui_m1 import _page, wait_until, window  # noqa: E402, F401

MONITORS = [{"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 0, "width": 2560, "height": 1440}]


@pytest.fixture
def screen_mode(window, monkeypatch):
    from subwizard.ui import screen_overlay
    from subwizard.sources import screen

    c = window.controller
    clip = c.config.source.path
    monkeypatch.setattr(screen, "list_monitors", lambda *a: MONITORS)
    monkeypatch.setattr(c._session, "_source_factory", lambda cfg: VideoFileSource(clip))
    shown = []
    monkeypatch.setattr(screen_overlay.ScreenOverlay, "show", lambda self: shown.append(self))
    return window, shown


def test_device_page_offers_the_screen_and_hides_audio(screen_mode):
    window, _ = screen_mode
    c = window.controller
    page = _page(window, "device")
    page.reload_devices()
    page._kind.setCurrentIndex(1)
    assert c.config.source.kind == "screen"
    assert not page._monitor_row.isHidden() and page._device_row.isHidden()
    assert page._input_row.isHidden() and page._delay_row.isHidden()
    assert page._monitor.count() == 2 and "2560" in page._monitor.itemText(1)
    page._monitor.setCurrentIndex(1)
    assert c.config.source.monitor == 2
    page._kind.setCurrentIndex(0)
    assert c.config.source.kind == "capture" and not page._input_row.isHidden()


def test_overlay_window_covers_the_game_and_draws_only_on_subtitle_changes(screen_mode):
    from PySide6.QtCore import Qt

    window, shown = screen_mode
    c = window.controller
    c.set_source_kind("screen")
    c.start()
    assert wait_until(lambda: c.latest_frame() is not None)
    overlay = window.screen_overlay
    assert overlay is not None and shown == [overlay]
    flags = overlay.windowFlags()
    for flag in (Qt.WindowType.WindowTransparentForInput, Qt.WindowType.WindowStaysOnTopHint,
                 Qt.WindowType.FramelessWindowHint, Qt.WindowType.Tool):
        assert flags & flag
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert not overlay.grab().isNull()  # paints (subtitle only) without a video frame fill
    c.stop()
    assert overlay.isHidden()


def test_web_only_mode_does_not_open_the_overlay(screen_mode):
    window, shown = screen_mode
    c = window.controller
    c.set_source_kind("screen")
    c.set_display_mode("web")
    c.start()
    assert window.screen_overlay is None and shown == []
    c.stop()


def test_wizard_pc_game_card_switches_to_the_screen_and_skips_audio(window):
    from subwizard.ui.wizard import PC_GAME, SetupWizard

    c = window.controller
    wizard = SetupWizard(c)
    wizard.mode_cards[PC_GAME].chosen.emit(PC_GAME)
    assert c.config.source.kind == "screen" and c.config.display.mode == "window"
    assert wizard.mode_cards[PC_GAME].selected and not wizard.mode_cards["window"].selected
    assert wizard._skipped("audio")
    from subwizard.i18n import t

    assert wizard.steps["capture"].title.text() == t("wizard.screen.title")
    wizard.mode_cards["web"].chosen.emit("web")
    assert c.config.source.kind == "capture" and c.config.display.mode == "web"
    wizard.deleteLater()
