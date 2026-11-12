"""M4.8: setup wizard. Acceptance: a clean user (no config file) finishes the wizard
without a terminal and sees a translation; the wizard's Test button shows a real
translation and latency."""

import pytest

pytest.importorskip("PySide6")
pytestmark = pytest.mark.ui

from subwizard.app import create_app  # noqa: E402
from subwizard.config import load_config  # noqa: E402
from subwizard.ui.pages.device import SOURCE_KINDS  # noqa: E402
from subwizard.ui.wizard import STEPS, SetupWizard  # noqa: E402
from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from tests.test_pipeline_m3 import ScriptedOCR  # noqa: E402
from tests.test_ui_m1 import _page, wait_until  # noqa: E402


def click(widget):
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)


def scripted_game(win, clip):
    """The test clip has no subtitles: OCR "reads" a scripted line instead, so the rest
    of the pipeline (state machine, translation, overlay) runs for real."""
    c = win.controller
    c._session._ocr_factory = lambda cfg: ScriptedOCR()
    c.config.ocr.change_mode = "gray"
    c.config.subtitle.min_display_ms = 0
    c.config.source.loop = True


@pytest.fixture
def fresh(tmp_path):
    """No config file at all, like a first start."""
    path = tmp_path / "config.yaml"
    assert not path.exists()
    config = load_config(path, {"ui": {"language": "tr", "close_to_tray": False}})
    app, win = create_app(config, path)
    if win.tray is not None:
        win.tray.hide()
    win.config_path = path
    yield win
    if win.wizard is not None:
        win.wizard.close()
    win.controller.shutdown()
    win.close()
    win.deleteLater()
    app.processEvents()
    # deleteLater needs a DeferredDelete pass; without it every old window stays alive,
    # and each new one restyles them all (the suite slowed down test by test).
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_first_start_needs_the_wizard(fresh):
    assert fresh.controller.config.ui.wizard_done is False


def test_clean_user_finishes_the_wizard_and_sees_a_translation(fresh, clip):
    c = fresh.controller
    scripted_game(fresh, clip)
    fresh.open_wizard()
    w = fresh.wizard
    assert isinstance(w, SetupWizard) and w.current == "welcome"

    w.next()  # -> display mode
    click(w.mode_cards["window"])
    assert c.config.display.mode == "window"
    w.next()  # -> capture card: no hardware here, pick the video file source instead
    assert w.current == "capture"
    w.device_video._kind.setCurrentIndex(SOURCE_KINDS.index("file"))
    c.set_source_path(clip)
    w.next()  # -> audio
    assert w.current == "audio"
    w.next()  # -> translation
    w.translate._test_button.click()  # SPEC: "Test et" shows a real translation + latency
    assert wait_until(lambda: w.translate._test_button.isEnabled())
    assert w.translate._test_result.text().startswith("[tr]")
    assert "ms" in w.translate._test_meta.text()
    w._no_key.click()  # "continue without a key" -> next step
    assert c.config.translator.engine == "deep" and w.current == "profile"
    click(w.profile_cards["gta6"])
    assert c.config.profile == "gta6" and c.config.roi.template == "bottom_center"
    w.next()  # -> region
    assert w.current == "region"
    w.next()  # -> summary
    assert w.current == "summary" and "GTA 6" in w._summary.text()
    w.next()  # Start

    assert c.config.ui.wizard_done and c.running
    c.flush()
    saved = load_config(fresh.config_path)
    assert saved.ui.wizard_done and saved.source.kind == "file"
    assert wait_until(lambda: c.subtitle is not None, timeout=20)
    assert c.subtitle.text.startswith("[tr]")


def test_mode_b_skips_audio_and_shows_the_qr(fresh, tmp_path):
    c = fresh.controller
    c.config.web.host, c.config.web.port = "127.0.0.1", 0
    fresh.open_wizard()
    w = fresh.wizard
    w.next()
    click(w.mode_cards["web"])
    w.next()
    assert w.current == "capture"
    w.next()
    assert w.current == "translate"  # audio skipped: the sound comes from the TV
    w.back()
    assert w.current == "capture"
    w.go(STEPS.index("summary"))
    assert c.web.running and w.qr.text == c.web.url()


def test_closing_early_does_not_reopen_it_every_start(fresh):
    fresh.open_wizard()
    fresh.wizard.close()
    assert fresh.controller.config.ui.wizard_done


def test_settings_can_reopen_the_wizard(fresh):
    fresh.controller.finish_wizard()
    _page(fresh, "settings")._wizard.click()
    assert fresh.wizard is not None


def test_wait_for_subtitle_freezes_on_text(fresh, clip):
    scripted_game(fresh, clip)
    c = fresh.controller
    c.set_source_kind("file")
    c.set_source_path(clip)
    roi = _page(fresh, "roi")
    roi.wait_and_freeze()
    assert c.running
    assert wait_until(lambda: roi._freeze.isChecked(), timeout=15)
    assert not roi._wait_freeze.isChecked()
