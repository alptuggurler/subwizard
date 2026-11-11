"""M0 acceptance for the UI shell: language and theme apply instantly and persist."""

import pytest

pytest.importorskip("PySide6")
pytestmark = pytest.mark.ui

from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from qfluentwidgets import isDarkTheme  # noqa: E402

from subwizard.app import create_app  # noqa: E402
from subwizard.config import load_config  # noqa: E402
from subwizard.ui.pages import PAGES  # noqa: E402


@pytest.fixture
def window(tmp_path):
    path = tmp_path / "config.yaml"
    config = load_config(path, {"ui": {"language": "tr", "theme": "dark"}})
    app, win = create_app(config, path)
    if win.tray is not None:
        win.tray.hide()
    win.config_path = path
    yield win
    win.controller.config.ui.close_to_tray = False
    win.close()
    win.deleteLater()
    app.processEvents()


def _nav_texts(win) -> list[str]:
    return [item.text() for item in win._nav_items]


def test_all_spec_pages_present(window):
    keys = [page.key for page in window.pages]
    assert keys == [
        "home", "history", "roi", "translate", "appearance", "profiles",
        "web", "device", "diagnostics", "hotkeys", "settings", "about",
    ]  # fmt: skip
    assert len(keys) == len(PAGES)


def test_language_switch_applies_instantly_and_saves(window):
    assert _nav_texts(window)[0] == "Ana Sayfa"
    home = window.pages[0]
    assert home._toggle.text() == "Başlat"

    window.controller.set_language("en")

    assert _nav_texts(window)[:2] == ["Home", "Subtitle History"]
    assert home._title.text() == "Home"
    assert home._toggle.text() == "Start"
    assert window.tray is None or window.tray._quit.text() == "Quit"
    assert load_config(window.config_path).ui.language == "en"


def test_language_combo_drives_the_controller(window):
    settings = next(p for p in window.pages if p.key == "settings")
    settings._language.setCurrentIndex(settings._language_codes.index("en"))
    assert window.controller.config.ui.language == "en"
    assert settings._title.text() == "Settings"
    # Rebuilding the combo on retranslate must not bounce the selection back.
    assert settings._language.currentIndex() == settings._language_codes.index("en")


def test_theme_switch_applies_instantly_and_saves(window):
    assert isDarkTheme()
    window.controller.set_theme("light")
    assert not isDarkTheme()
    assert load_config(window.config_path).ui.theme == "light"
    window.controller.set_theme("dark")
    assert isDarkTheme()


def test_start_stop_updates_button(window):
    home = window.pages[0]
    home._toggle.click()
    assert window.controller.running
    assert home._toggle.text() == "Durdur"
    home._toggle.click()
    assert not window.controller.running


def test_close_hides_to_tray_only_when_enabled(window, monkeypatch):
    quits = []
    if window.tray is None:
        pytest.skip("no system tray on this platform")
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()  # hidden to tray, app keeps running

    window.controller.set_close_to_tray(False)
    monkeypatch.setattr(QApplication, "quit", lambda: quits.append(True))
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted() and quits == [True]
