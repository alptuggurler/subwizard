"""Tray / macOS menu bar menu: status lines, toggles and settings submenus drive the
controller, and the menu is rebuilt with current state every time it opens."""

import pytest

pytest.importorskip("PySide6")
pytestmark = pytest.mark.ui

from PySide6.QtWidgets import QMenu  # noqa: E402

from subwizard import platform  # noqa: E402
from subwizard.app import create_app  # noqa: E402
from subwizard.config import load_config  # noqa: E402
from subwizard.ui import tray as tray_module  # noqa: E402
from subwizard.ui.tray import TrayIcon, short  # noqa: E402


@pytest.fixture
def setup(tmp_path, monkeypatch):
    dock_calls: list[bool] = []
    monkeypatch.setattr(platform, "set_dock_visible", lambda v: dock_calls.append(v) or True)
    monkeypatch.setattr(platform, "activate_app", lambda: None)
    path = tmp_path / "config.yaml"
    config = load_config(path, {"ui": {"language": "en"}, "translator": {"engine": "test"}})
    app, win = create_app(config, path)
    tray = win.tray or TrayIcon(win.controller, win)  # also where the platform has no tray
    tray.hide()
    yield win, tray, path, dock_calls
    tray.hide()
    tray.deleteLater()
    win.controller.config.ui.close_to_tray = False
    win.close()
    win.deleteLater()
    app.processEvents()


def _all_actions(menu: QMenu):
    for action in menu.actions():
        yield action
        if action.menu() is not None:
            yield from _all_actions(action.menu())


def _find(tray: TrayIcon, text: str, under: str | None = None):
    menu = tray.contextMenu()
    if under is not None:
        menu = next(a.menu() for a in menu.actions() if a.menu() and a.text() == under)
    return next(a for a in _all_actions(menu) if a.text() == text)


def test_status_lines_and_start_item(setup):
    win, tray, _, _ = setup
    texts = [a.text() for a in tray.contextMenu().actions()]
    assert texts[0] == "Ready · Capture card"
    assert not tray.contextMenu().actions()[0].isEnabled()  # read-only status line
    assert "Start" in texts and "Quit" in texts


def test_settings_submenus_change_config_and_save(setup):
    win, tray, path, _ = setup
    _find(tray, "Top of the screen", under="Subtitle position").trigger()
    _find(tray, "Both", under="Display mode").trigger()
    _find(tray, "Google Translate (free)", under="Translation engine").trigger()
    saved = load_config(path)
    assert saved.overlay.position == "top"
    assert saved.display.mode == "both"
    assert saved.translator.engine == "deep"

    tray.rebuild()  # opening the menu again shows the new choice checked
    assert _find(tray, "Top of the screen", under="Subtitle position").isChecked()
    assert not _find(tray, "Right over the English text", under="Subtitle position").isChecked()


def test_toggles_follow_the_controller(setup):
    win, tray, _, _ = setup
    c = win.controller
    assert _find(tray, "Translation on").isChecked()
    _find(tray, "Translation on").trigger()
    assert c.translation_paused
    tray.rebuild()
    assert not _find(tray, "Translation on").isChecked()

    _find(tray, "Show subtitles").trigger()
    assert not c.config.overlay.visible


def test_pages_menu_opens_the_page(setup, monkeypatch):
    win, tray, _, _ = setup
    shown = []
    monkeypatch.setattr(win, "showNormal", lambda: shown.append(True))  # tests never show()
    _find(tray, "Translation", under="Go to page").trigger()
    assert shown and win.stackedWidget.currentWidget().key == "translate"


def test_dock_switch(setup, monkeypatch):
    win, tray, path, dock_calls = setup
    monkeypatch.setattr(tray_module, "IS_MAC", True)
    tray.rebuild()
    _find(tray, "Show in Dock").trigger()
    assert dock_calls == [False]
    assert load_config(path).ui.show_in_dock is False
    win.controller.set_show_in_dock(True)
    assert dock_calls == [False, True]


def test_language_change_rebuilds(setup):
    win, tray, _, _ = setup
    win.controller.set_language("tr")
    assert _find(tray, "Çıkış") is not None


def test_short_cuts_long_notices():
    assert short("Can't reach the service. Check your internet.") == "Can't reach the service"
    assert len(short("x" * 200)) == 60 and short("x" * 200).endswith("…")


def test_spend_line_reads_the_ledger_when_stopped(setup):
    from subwizard.translator.service import spend_path
    from subwizard.translator.spend import SpendLedger

    win, tray, _, _ = setup
    ledger = SpendLedger(spend_path())  # the test user data dir (conftest)
    ledger.add(0.0123, 100, 50)
    ledger.close()
    win.controller.config.translator.daily_limit_usd = 1.0
    tray.rebuild()
    assert _find(tray, "Gemini today: $0.0123 / $1.00") is not None
