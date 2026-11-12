"""Translation on/off key (F8): optional start with translation off, on-screen notice."""

import pytest

pytest.importorskip("PySide6")
pytestmark = pytest.mark.ui

from subwizard.i18n import t  # noqa: E402
from subwizard.ocr import vision  # noqa: E402
from tests.test_ui_m1 import _page, _window, wait_until  # noqa: E402


@pytest.fixture
def paused_window(tmp_path, clip):
    from PySide6.QtCore import QEvent

    app, win = _window(tmp_path, {"source": {"kind": "file", "path": str(clip)},
                                  "translator": {"start_paused": True}})  # fmt: skip
    yield win
    win.controller.shutdown()
    win.close()
    win.deleteLater()
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    vision.PAUSED.clear()


def test_starts_off_says_which_key_turns_it_on_and_toggles_with_a_notice(paused_window):
    window = paused_window
    c = window.controller
    video = window.home.video
    assert c.translation_paused and vision.PAUSED.is_set()

    c.start()
    assert wait_until(lambda: c.latest_frame() is not None)
    assert video._toast[0] == t("toast.translation_off_hint", hotkey="F8")

    window.trigger("pause_translation")
    assert not c.translation_paused and not vision.PAUSED.is_set()
    assert video._toast[0] == t("toast.translation_on")
    window.trigger("pause_translation")
    assert c.translation_paused and video._toast[0] == t("toast.translation_off")
    video.resize(640, 360)
    assert not video.grab().isNull()  # draws the notice
    video._end_toast()
    assert video._toast is None
    c.stop()


def test_turning_off_takes_the_shown_translation_away(paused_window):
    from tests.test_ui_m4 import _view

    c = paused_window.controller
    c.toggle_translation_pause()  # on
    session = c._session
    view = _view("Kafanı eğ.")
    session._board._shown = view
    shown = []
    session._on_subtitle = shown.append
    c.toggle_translation_pause()  # off
    assert shown == [None] and session._board.shown is None


def test_hotkeys_page_switch_saves_the_setting(paused_window):
    c = paused_window.controller
    page = _page(paused_window, "hotkeys")
    assert page._start_paused.isChecked()
    page._start_paused.setChecked(False)
    assert c.config.translator.start_paused is False
    assert "start_paused: false" in paused_window.config_path.read_text()
    assert "F8" in page._start_paused_row._desc.text()
