"""M2 UI: Alan & OCR page, region editor, F2 calibration, OCR rows in metrics."""

import pytest

pytest.importorskip("PySide6")
pytestmark = pytest.mark.ui

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from subwizard.config import AppConfig  # noqa: E402
from subwizard.core.metrics import M, MetricsHub  # noqa: E402
from subwizard.ocr.base import BaseOCR, OCRLine, OCRResult  # noqa: E402
from subwizard.ocr.roi import TEMPLATES, Rect  # noqa: E402
from subwizard.ocr.worker import OCRState  # noqa: E402
from subwizard.ui import calibration as calibration_module  # noqa: E402
from subwizard.ui.calibration import CalibrationWindow  # noqa: E402
from subwizard.ui.metrics_view import DASH, metric_rows  # noqa: E402
from subwizard.ui.pages.roi import RoiPage  # noqa: E402
from subwizard.ui.widgets.roi_editor import RoiEditor  # noqa: E402
from tests.test_ui_m1 import _page, wait_until, window  # noqa: E402, F401

FAKE_TEXT = "We need to get out of here."


class FakeOCR(BaseOCR):
    name = "rapid"

    def recognize(self, image, scale=1.0):
        return OCRResult((OCRLine(FAKE_TEXT, 0.95, (4, 4, 40, 10)),))


def _start_with_fake_ocr(win):
    c = win.controller
    c._session._ocr_factory = lambda config: FakeOCR()
    c.start()
    assert wait_until(lambda: c.latest_frame() is not None)
    return c


def test_roi_page_replaces_the_placeholder(window):
    page = _page(window, "roi")
    assert isinstance(page, RoiPage)


def test_template_combo_moves_the_region(window):
    page = _page(window, "roi")
    page._template.setCurrentIndex(1)  # bottom_wide
    roi = window.controller.config.roi
    assert roi.template == "bottom_wide"
    assert roi.rect == TEMPLATES["bottom_wide"].as_list()


def _editor(window):
    """A standalone editor wired like the page's (the page layout would resize it)."""
    editor = RoiEditor(window.controller)
    editor.rectChosen.connect(_page(window, "roi")._on_rect)
    editor.resize(400, 300)  # 16:9 frame -> 400x225 at y 37.5
    editor.grab()  # paints once so the frame's on-screen box is known
    return editor


def test_dragging_on_the_editor_sets_a_custom_region(window):
    _start_with_fake_ocr(window)
    editor = _editor(window)
    QTest.mousePress(editor, Qt.MouseButton.LeftButton, pos=QPoint(100, 150))
    QTest.mouseMove(editor, QPoint(300, 220))
    QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=QPoint(300, 220))
    roi = window.controller.config.roi
    assert roi.template == "custom"
    x, y, w, h = roi.rect
    assert x == pytest.approx(0.25, abs=0.01) and w == pytest.approx(0.5, abs=0.01)
    assert y == pytest.approx((150 - 37.5) / 225, abs=0.01)
    assert h == pytest.approx(70 / 225, abs=0.01)
    assert editor.pending is None  # committed on release


def test_a_click_is_not_a_region(window):
    _start_with_fake_ocr(window)
    before = list(window.controller.config.roi.rect)
    editor = _editor(window)
    QTest.mouseClick(editor, Qt.MouseButton.LeftButton, pos=QPoint(100, 150))
    assert window.controller.config.roi.rect == before


def test_ocr_ticks_reach_the_page_and_metrics(window):
    c = _start_with_fake_ocr(window)
    page = _page(window, "roi")
    window.stackedWidget.setCurrentWidget(page)
    page.isVisible = lambda: True  # windows are never shown in tests
    assert wait_until(lambda: c.ocr_state == OCRState.RUNNING)
    assert wait_until(lambda: c.last_ocr is not None and c.last_ocr.text == FAKE_TEXT)
    assert wait_until(lambda: page.preview.text.text() == FAKE_TEXT)
    assert not page.preview.raw.pixmap().isNull()
    rows = {r.label: r for r in metric_rows(c.metrics.snapshot(), c.config)}
    assert rows["diag.ocr_text"].value == FAKE_TEXT
    assert rows["diag.ocr_confidence"].value == "95%"


def test_calibration_saves_a_template_on_enter(window):
    c = _start_with_fake_ocr(window)
    cal = CalibrationWindow(c, c.latest_frame())
    QTest.keyClick(cal, Qt.Key.Key_4)
    QTest.keyClick(cal, Qt.Key.Key_Return)
    assert c.config.roi.template == "top"


def test_calibration_saves_a_drawn_region_and_esc_cancels(window):
    c = _start_with_fake_ocr(window)
    cal = CalibrationWindow(c, c.latest_frame())
    cal.editor.set_pending(Rect(0.1, 0.2, 0.3, 0.1))
    QTest.keyClick(cal, Qt.Key.Key_Escape)
    assert c.config.roi.template == "bottom_center"
    cal = CalibrationWindow(c, c.latest_frame())
    cal.editor.set_pending(Rect(0.1, 0.2, 0.3, 0.1))
    cal.confirm()
    assert c.config.roi.template == "custom"
    assert c.config.roi.rect == [0.1, 0.2, 0.3, 0.1]


def test_calibration_keeps_the_frame_frozen(window):
    c = _start_with_fake_ocr(window)
    first = c.latest_frame()
    cal = CalibrationWindow(c, first)
    assert wait_until(lambda: c.latest_frame().index != first.index)
    assert cal.editor.frame().index == first.index


def test_f2_starts_the_source_and_opens_calibration(window, monkeypatch):
    opened = []
    monkeypatch.setattr(calibration_module.CalibrationWindow, "open_on",
                        lambda self, screen: opened.append(self))  # fmt: skip
    c = window.controller
    c._session._ocr_factory = lambda config: FakeOCR()
    assert not c.running
    c.request_calibration()  # what F2 and --calibrate call
    assert c.running
    assert wait_until(lambda: bool(opened))  # waits for the first frame, then opens
    assert window.calibration is opened[0]
    opened[0].close()
    assert wait_until(lambda: window.calibration is None)


def test_ocr_rows_show_dashes_before_anything_ran():
    rows = {r.label: r for r in metric_rows(MetricsHub().snapshot(), AppConfig())}
    for key in ("diag.ocr_rate", "diag.ocr_skipped", "diag.ocr_ms", "diag.ocr_text"):
        assert rows[key].value == DASH


def test_ocr_rows_report_skips_and_grade_slow_ocr(clock):
    hub, cfg = MetricsHub(clock=clock), AppConfig()
    for i in range(4):  # 4 frames looked at in 1 s, OCR ran on 2 of them
        hub.tick(M.OCR_TICKS)
        if i % 2 == 0:
            hub.tick(M.OCR_RUNS)
        clock.advance(0.25)
    for _ in range(3):
        hub.incr(M.OCR_SKIPPED)
    hub.observe(M.OCR_MS, cfg.metrics.ocr_ms_bad + 1)
    hub.set_gauge(M.OCR_TEXT, "x" * 200)
    rows = {r.label: r for r in metric_rows(hub.snapshot(), cfg)}
    # Rates count events after the stream's first one: 3 ticks/s, 1 run/s -> 67 % skipped.
    assert rows["diag.ocr_skipped"].value.startswith("67%")
    assert rows["diag.ocr_ms"].grade == "bad"
    assert len(rows["diag.ocr_text"].value) <= 70
