"""M1 UI: the pipeline behind Start/Stop, F9 overlay, Device & Audio, Diagnostics."""

import time

import pytest

pytest.importorskip("PySide6")
pytestmark = pytest.mark.ui

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from subwizard.app import create_app  # noqa: E402
from subwizard.config import load_config  # noqa: E402
from subwizard.core.metrics import M  # noqa: E402
from subwizard.sources.base import VideoMode  # noqa: E402
from subwizard.sources.runner import SourceState  # noqa: E402
from tests.conftest import CLIP_FPS  # noqa: E402


def wait_until(condition, timeout=5.0):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return False


def _window(tmp_path, overrides):
    path = tmp_path / "config.yaml"
    config = load_config(path, {"ui": {"language": "tr", "close_to_tray": False}, **overrides})
    app, win = create_app(config, path)
    if win.tray is not None:
        win.tray.hide()
    win.config_path = path
    return app, win


@pytest.fixture
def window(tmp_path, clip):
    app, win = _window(tmp_path, {"source": {"kind": "file", "path": str(clip)}})
    yield win
    win.controller.shutdown()
    win.close()
    win.deleteLater()
    app.processEvents()
    # deleteLater needs a DeferredDelete pass; without it every old window stays alive,
    # and each new one restyles them all (the suite slowed down test by test).
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _page(win, key):
    return next(p for p in win.pages if p.key == key)


def test_start_plays_the_clip_and_stop_clears_it(window):
    c = window.controller
    home = window.home
    home._toggle.click()
    assert c.running
    assert wait_until(lambda: c.latest_frame() is not None)
    assert wait_until(lambda: c.source_state == SourceState.RUNNING)

    home.video.grab()  # forces a paint of the current frame
    assert wait_until(lambda: c.latest_frame().index > 3)
    home.video.grab()
    snap = c.snapshot()
    assert snap.rate(M.RENDER_FPS).long > 0
    assert snap.stat(M.RENDER_FRAME_AGE_MS).count > 0
    assert str(snap.gauge(M.SOURCE_ACTUAL)).startswith(f"320×180@{CLIP_FPS}")
    assert home._toggle.text() == "Durdur"

    home._toggle.click()
    assert not c.running and c.latest_frame() is None
    assert wait_until(lambda: c.source_state == SourceState.STOPPED)
    assert home._toggle.text() == "Başlat"


def test_missing_file_fails_with_a_helpful_message_and_resets_start(tmp_path):
    app, win = _window(tmp_path, {"source": {"kind": "file", "path": str(tmp_path / "no.mp4")}})
    try:
        c = win.controller
        c.start()
        assert wait_until(lambda: c.source_state == SourceState.FAILED)
        assert wait_until(lambda: not c.running)
        assert "no.mp4" in c.source_problem
        assert win.home._toggle.text() == "Başlat"
        assert win.home._notice.isVisibleTo(win.home)
    finally:
        win.controller.shutdown()
        win.close()


def test_no_file_selected_does_not_start(tmp_path):
    app, win = _window(tmp_path, {"source": {"kind": "file"}})
    try:
        win.controller.start()
        assert not win.controller.running
        assert win.controller.source_state == SourceState.FAILED
        assert "Cihaz & Ses" in win.controller.source_problem
    finally:
        win.controller.shutdown()
        win.close()


def test_f9_cycles_the_perf_overlay_and_remembers_it(window):
    c = window.controller
    seen = []
    c.perfOverlayChanged.connect(seen.append)
    for _ in range(3):
        c.cycle_perf_overlay()
    assert seen == ["compact", "detailed", "off"]
    c.cycle_perf_overlay()
    assert load_config(window.config_path).ui.perf_overlay == "compact"


def test_overlay_paints_without_and_with_frames(window):
    c = window.controller
    c.cycle_perf_overlay()
    c.cycle_perf_overlay()  # detailed
    window.home.video._on_metrics(c.snapshot())
    assert not window.home.video.grab().isNull()  # no frame yet: placeholder + overlay
    c.start()
    assert wait_until(lambda: c.latest_frame() is not None)
    window.home.video._on_metrics(c.snapshot())
    assert not window.home.video.grab().isNull()


def test_audio_sliders_apply_and_save_after_settling(window):
    c = window.controller
    device = _page(window, "device")
    device._delay.slider.setValue(120)
    device._gain.slider.setValue(-6)
    assert (c.config.audio.delay_ms, c.config.audio.gain_db) == (120, -6.0)
    c.flush()
    saved = load_config(window.config_path).audio
    assert (saved.delay_ms, saved.gain_db) == (120, -6.0)

    device._delay.slider.setValue(-50)  # audio cannot be advanced: explain, do not pretend
    assert "geciktirilebilir" in device._audio_notice._text.text()


def test_device_page_switches_between_capture_and_file(window):
    c = window.controller
    device = _page(window, "device")
    assert device._file_row.isVisibleTo(device) and not device._mode_row.isVisibleTo(device)
    device._kind.setCurrentIndex(0)  # capture card
    assert c.config.source.kind == "capture"
    assert device._mode_row.isVisibleTo(device) and not device._file_row.isVisibleTo(device)

    c.set_source_mode(VideoMode(1920, 1080, 30.0, "YUY2"))
    src = load_config(window.config_path).source
    assert (src.width, src.height, src.fps, src.fourcc) == (1920, 1080, 30, "YUY2")
    assert device._mode.currentText() == "1920×1080@30 YUY2"


def test_changing_the_source_while_running_restarts_it(window, clip):
    c = window.controller
    c.start()
    assert wait_until(lambda: c.latest_frame() is not None)
    states = []
    c.runningChanged.connect(states.append)
    c.set_source_path(clip)
    assert states == [False, True]
    assert wait_until(lambda: c.latest_frame() is not None)


def test_diagnostics_report_is_pasteable_text(window):
    c = window.controller
    c.start()
    assert wait_until(lambda: c.latest_frame() is not None and c.latest_frame().index > 5)
    diag = _page(window, "diagnostics")
    report = diag.report()
    assert report.startswith("SubWizard ")
    assert "[Görüntü]" in report and "[Ses]" in report and "[Sistem]" in report
    assert "Kaynak FPS" in report and "320×180" in report
    diag.copy_report()
    assert QApplication.clipboard().text() == report or diag._copy.text() == "Kopyalandı"


def test_metrics_reach_the_ui_on_a_timer(window):
    got = []
    window.controller.metricsUpdated.connect(got.append)
    assert wait_until(lambda: len(got) >= 2, timeout=2.0)
    assert got[-1].gauge(M.SYS_RAM_PROC_MB) > 0


def test_start_picks_the_capture_cards_own_audio_input(tmp_path, monkeypatch):
    from subwizard import controller as controller_module
    from subwizard.audio.devices import AudioDevice
    from subwizard.platform import VideoDevice

    monkeypatch.setattr(controller_module.platform, "list_video_devices",
                        lambda: [VideoDevice(0, "Webcam"), VideoDevice(1, "V-Z632")])  # fmt: skip
    monkeypatch.setattr(controller_module, "list_audio_devices", lambda kind: [
        AudioDevice(46, "Dijital Ses Arabirimi (V-Z632)", "Windows WASAPI", 2, 0, 48000)
    ])  # fmt: skip
    app, win = _window(tmp_path, {"source": {"kind": "capture", "device": 1}})
    try:
        c = win.controller
        c._autoselect_capture_audio()
        assert c.config.audio.input_device == "Dijital Ses Arabirimi (V-Z632)"
        assert load_config(win.config_path).audio.input_device == c.config.audio.input_device

        c.config.audio.input_device = "Mikrofon"  # a user's own choice is never replaced
        c._autoselect_capture_audio()
        assert c.config.audio.input_device == "Mikrofon"
    finally:
        win.controller.shutdown()
        win.close()
