"""M4.5 UI: display modes, Web Overlay page (QR, URLs, clients), Mode B overlay off."""

import pytest

pytest.importorskip("PySide6")
pytestmark = pytest.mark.ui

from PySide6.QtGui import QGuiApplication  # noqa: E402

from subwizard.pipeline.board import SubtitleView  # noqa: E402
from subwizard.ui.pages.home import web_state  # noqa: E402
from subwizard.ui.pages.web import WebPage  # noqa: E402
from tests.test_ui_m1 import _page, _window  # noqa: E402


@pytest.fixture
def web_window(tmp_path, clip):
    app, win = _window(tmp_path, {"source": {"kind": "file", "path": str(clip)},
                                  "display": {"mode": "both"},
                                  "web": {"host": "127.0.0.1", "port": 0}})  # fmt: skip
    yield win
    win.controller.shutdown()
    win.close()
    win.deleteLater()
    app.processEvents()


def test_web_page_replaces_the_placeholder(web_window):
    assert isinstance(_page(web_window, "web"), WebPage)


def test_both_mode_starts_the_server_and_shows_the_address(web_window):
    c = web_window.controller
    page = _page(web_window, "web")
    assert c.web.running
    assert page.url_label.text() == c.web.url()
    assert page.qr.text == c.web.url()
    assert web_state(c) == ("0 cihaz bağlı", "warn")
    page._copy_obs.click()
    assert QGuiApplication.clipboard().text().endswith("/obs")
    page.qr.resize(184, 184)
    assert not page.qr.grab().isNull()


def test_switching_to_window_mode_stops_the_server(web_window):
    c = web_window.controller
    _page(web_window, "web")._mode.setCurrentIndex(0)
    assert c.config.display.mode == "window" and not c.web.running
    assert "kapalı" in _page(web_window, "web")._notice._text.text()


def test_subtitles_reach_the_web_server_from_the_translator_thread(web_window):
    c = web_window.controller
    view = SubtitleView(3, "Arabaya bin.", None, "Get in the car.", "gemini", False, 0.0, 0.0)
    c._publish_subtitle(view)
    import time

    deadline = time.time() + 2
    while c.web._current is None and time.time() < deadline:
        time.sleep(0.01)
    assert c.web._current["translation"] == "Arabaya bin."


def test_web_only_mode_does_not_draw_on_the_pc_video(web_window):
    c = web_window.controller
    c.set_display_mode("web")
    c._on_subtitle(SubtitleView(1, "Koş!", None, "Run!", "gemini", False, 0.0, 0.0))
    video = web_window.home.video
    video.resize(640, 360)
    assert not video.grab().isNull()


def test_port_change_restarts_on_the_new_port(web_window, tmp_path):
    import socket

    c = web_window.controller
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    c.set_web_option("port", free)
    assert c.web.running and c.web.port == free
