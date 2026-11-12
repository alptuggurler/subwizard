"""Alan & OCR: pick the subtitle region and tune OCR while watching what it reads
(SPEC 3.3, 3.4, 3.12).

Top: template, freeze, read now, F2 calibration. Middle: the video with the region
(drag to redraw). Below: what OCR saw (raw crop, preprocessed image) and read
(text, confidence, time), then the settings that change it, all applied live.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    PushButton,
    StrongBodyLabel,
    SwitchButton,
    TogglePushButton,
)

from subwizard.controller import AppController
from subwizard.i18n import t
from subwizard.ocr.roi import TEMPLATE_NAMES
from subwizard.ocr.worker import OCRState, OCRTick
from subwizard.sources.runner import SourceState
from subwizard.ui.pages.base import BasePage, SettingRow
from subwizard.ui.widgets.notice import Notice
from subwizard.ui.widgets.roi_editor import RoiEditor, frame_to_qimage
from subwizard.ui.widgets.value_slider import ValueSlider

OCR_ENGINES = ("rapid", "windows")
COMBO_WIDTH = 180  # the toolbar must fit the default window (1180 px) without scrolling
DRAW_MODES = ("main", "extra", "exclude")
EDITOR_MIN_HEIGHT = 360
PREVIEW_HEIGHT = 72  # crop previews are scaled to this height (width follows)
PREVIEW_MAX_WIDTH = 640
PADDING_RANGE = (0, 20)  # %
HZ_RANGE = (1, 10)
CONFIDENCE_RANGE = (0, 100)  # %


def to_pixmap(image: np.ndarray, height: int, max_width: int) -> QPixmap:
    image = np.ascontiguousarray(image)
    pixmap = QPixmap.fromImage(frame_to_qimage(image).copy())  # copy: own the pixels
    return pixmap.scaled(max_width, height, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)  # fmt: skip


def _switch() -> SwitchButton:
    switch = SwitchButton()
    switch.setOnText("")  # the row title says what it is; no "On/Off" text to translate
    switch.setOffText("")
    return switch


class _PreviewCard(CardWidget):
    """What OCR saw and read on the last tick."""

    def __init__(self) -> None:
        super().__init__()
        self._title = StrongBodyLabel()
        self._raw_caption, self._processed_caption = CaptionLabel(), CaptionLabel()
        self.raw, self.processed = QLabel(), QLabel()
        for label in (self.raw, self.processed):
            label.setMinimumHeight(PREVIEW_HEIGHT)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.text = BodyLabel()
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.meta = CaptionLabel()

        # Stacked, not side by side: two wide strips would not fit a narrow window.
        images = QVBoxLayout()
        images.setSpacing(4)
        for caption, label in ((self._raw_caption, self.raw),
                               (self._processed_caption, self.processed)):  # fmt: skip
            images.addWidget(caption)
            images.addWidget(label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(self._title)
        layout.addLayout(images)
        layout.addWidget(self.text)
        layout.addWidget(self.meta)

    def retranslate(self) -> None:
        self._title.setText(t("roi.preview"))
        self._raw_caption.setText(t("roi.preview.raw"))
        self._processed_caption.setText(t("roi.preview.processed"))


class RoiPage(BasePage):
    key = "roi"

    def __init__(self, controller: AppController, parent: QWidget | None = None):
        super().__init__(controller, parent)

        # --- toolbar
        self._template = ComboBox()
        self._template.setMinimumWidth(COMBO_WIDTH)
        self._template.addItems(list(TEMPLATE_NAMES))
        self._template.currentIndexChanged.connect(self._on_template)
        self._freeze = TogglePushButton()
        self._freeze.setIcon(FluentIcon.PAUSE)
        self._freeze.toggled.connect(self._on_freeze)
        self._read_now = PushButton()
        self._read_now.setIcon(FluentIcon.SYNC)
        self._read_now.clicked.connect(controller.force_ocr)
        self._calibrate = PushButton()
        self._calibrate.setIcon(FluentIcon.FULL_SCREEN)
        self._calibrate.clicked.connect(controller.request_calibration)
        # SPEC 3.12 wizard step 7: freeze on the first frame where OCR finds text.
        self._wait_freeze = TogglePushButton()
        self._wait_freeze.setIcon(FluentIcon.STOP_WATCH)
        self._wait_freeze.toggled.connect(self._on_wait_freeze)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        for widget in (self._template, self._freeze, self._wait_freeze, self._read_now,
                       self._calibrate):  # fmt: skip
            toolbar.addWidget(widget)
        toolbar.addStretch(1)
        self.body.addLayout(toolbar)

        # What a drag in the editor draws: the main region, one more region read with
        # it, or an area to black out (minimap, HUD).
        self._draw_mode = ComboBox()
        self._draw_mode.setMinimumWidth(COMBO_WIDTH)
        self._draw_mode.addItems(list(DRAW_MODES))
        self._clear_regions = PushButton()
        self._clear_regions.setIcon(FluentIcon.ERASE_TOOL)
        self._clear_regions.clicked.connect(self._on_clear_regions)
        self._regions_info = CaptionLabel()
        self._regions_info.setWordWrap(True)
        self.regions_bar = QWidget()
        regions = QHBoxLayout(self.regions_bar)
        regions.setContentsMargins(0, 0, 0, 0)
        regions.setSpacing(8)
        regions.addWidget(self._draw_mode)
        regions.addWidget(self._clear_regions)
        regions.addWidget(self._regions_info, 1)
        self.body.addWidget(self.regions_bar)

        self._notice = Notice()
        self.body.addWidget(self._notice)

        self.editor = RoiEditor(controller)
        self.editor.setMinimumHeight(EDITOR_MIN_HEIGHT)
        self.editor.rectChosen.connect(self._on_rect)
        self.body.addWidget(self.editor)

        self.preview = _PreviewCard()
        self.body.addWidget(self.preview)

        # --- settings
        self._padding = ValueSlider(PADDING_RANGE, lambda v: f"{v}%")
        self._padding.slider.valueChanged.connect(lambda v: controller.set_roi_padding(float(v)))
        self._padding_row = SettingRow(FluentIcon.FIT_PAGE, self._padding)
        self._hz = ValueSlider(HZ_RANGE, lambda v: f"{v} Hz")
        self._hz.slider.valueChanged.connect(lambda v: controller.set_ocr_hz(float(v)))
        self._hz_row = SettingRow(FluentIcon.SPEED_HIGH, self._hz)
        self._confidence = ValueSlider(CONFIDENCE_RANGE, lambda v: f"{v}%")
        self._confidence.slider.valueChanged.connect(
            lambda v: controller.set_ocr_min_confidence(v / 100.0))
        self._confidence_row = SettingRow(FluentIcon.ACCEPT, self._confidence)
        self._white_mask = _switch()
        self._white_mask.checkedChanged.connect(
            lambda on: controller.set_preprocess_option("white_text_mask", on))
        self._white_mask_row = SettingRow(FluentIcon.PALETTE, self._white_mask)
        self._contrast = _switch()
        self._contrast.checkedChanged.connect(
            lambda on: controller.set_preprocess_option("contrast_stretch", on))
        self._contrast_row = SettingRow(FluentIcon.BRIGHTNESS, self._contrast)
        self._engine = ComboBox()
        self._engine.setMinimumWidth(COMBO_WIDTH)
        self._engine.addItems(list(OCR_ENGINES))
        self._engine.currentIndexChanged.connect(
            lambda i: controller.set_ocr_engine(OCR_ENGINES[i]) if i >= 0 else None)
        self._engine_row = SettingRow(FluentIcon.SEARCH, self._engine)
        self._settings_title = StrongBodyLabel()
        self.body.addSpacing(8)
        self.body.addWidget(self._settings_title)
        for row in (self._padding_row, self._hz_row, self._confidence_row,
                    self._white_mask_row, self._contrast_row, self._engine_row):  # fmt: skip
            self.body.addWidget(row)
        self.settings_section = [self.regions_bar, self._settings_title, self._padding_row, self._hz_row,
                                 self._confidence_row, self._white_mask_row,
                                 self._contrast_row, self._engine_row]  # fmt: skip

        controller.settingsChanged.connect(self._load_settings)
        controller.ocrTick.connect(self._on_tick)
        controller.ocrStateChanged.connect(self._refresh_notice)
        controller.sourceStateChanged.connect(self._refresh_notice)
        controller.runningChanged.connect(self._on_running)
        self._load_settings()
        self.retranslate()

    # --- reactions ----------------------------------------------------------------------
    def _on_template(self, index: int) -> None:
        self.controller.set_roi_template(TEMPLATE_NAMES[index])
        self.controller.force_ocr()

    @property
    def draw_mode(self) -> str:
        return DRAW_MODES[max(0, self._draw_mode.currentIndex())]

    def _on_rect(self, rect: object) -> None:
        if self.draw_mode == "extra":
            self.controller.add_roi_region(rect, exclude=False)
        elif self.draw_mode == "exclude":
            self.controller.add_roi_region(rect, exclude=True)
        else:
            self.controller.set_roi_rect(rect)
        self.controller.force_ocr()

    def _on_clear_regions(self) -> None:
        self.controller.clear_roi_regions()
        self.controller.force_ocr()

    def _on_freeze(self, frozen: bool) -> None:
        self.editor.freeze(self.controller.latest_frame() if frozen else None)
        if frozen and self._wait_freeze.isChecked():
            self._wait_freeze.setChecked(False)

    def _on_wait_freeze(self, waiting: bool) -> None:
        if waiting:
            if self._freeze.isChecked():
                self._freeze.setChecked(False)
            if not self.controller.running:
                self.controller.start()
            self.controller.force_ocr()

    def wait_and_freeze(self) -> None:
        self._wait_freeze.setChecked(True)

    def _on_running(self, running: bool) -> None:
        if not running and self._freeze.isChecked():
            self._freeze.setChecked(False)
        self._refresh_notice()

    def _on_tick(self, tick: OCRTick) -> None:
        if self._wait_freeze.isChecked() and any(line.text.strip() for line in tick.lines):
            self._freeze.setChecked(True)  # the subtitle is on screen right now
        if not self.isVisible():
            return
        p = self.preview
        p.raw.setPixmap(to_pixmap(tick.crop, PREVIEW_HEIGHT, PREVIEW_MAX_WIDTH))
        if tick.processed is not None:
            p.processed.setPixmap(to_pixmap(tick.processed, PREVIEW_HEIGHT, PREVIEW_MAX_WIDTH))
        p.text.setText(tick.text or t("roi.preview.no_text"))
        state = t("roi.preview.skipped") if tick.skipped else f"{tick.ms:.0f} ms"
        p.meta.setText(t("roi.preview.meta", confidence=f"{tick.confidence * 100:.0f}",
                         state=state, lines=len(tick.lines)))  # fmt: skip

    def _refresh_notice(self, *_: object) -> None:
        c = self.controller
        if not c.running:
            self._notice.show_message(t("roi.notice.stopped"), "warn")
        elif c.ocr_state == OCRState.ERROR:
            self._notice.show_message(t("roi.notice.error", error=c.ocr_message), "bad")
        elif c.ocr_state == OCRState.LOADING:
            self._notice.show_message(t("roi.notice.loading"), "warn")
        elif c.source_state not in (SourceState.RUNNING, SourceState.ENDED):
            self._notice.show_message(t("roi.notice.no_video"), "warn")
        else:
            self._notice.show_message("")

    def _load_settings(self) -> None:
        cfg = self.controller.config
        self._template.blockSignals(True)
        self._template.setCurrentIndex(TEMPLATE_NAMES.index(cfg.roi.template))
        self._template.blockSignals(False)
        self._padding.set_value(round(cfg.roi.padding_pct))
        self._hz.set_value(round(cfg.ocr.hz))
        self._confidence.set_value(round(cfg.ocr.min_confidence * 100))
        self._engine.blockSignals(True)
        self._engine.setCurrentIndex(OCR_ENGINES.index(cfg.ocr.engine))
        self._engine.blockSignals(False)
        prep = cfg.ocr.prep()
        for switch, value in ((self._white_mask, prep.white_text_mask),
                              (self._contrast, prep.contrast_stretch)):  # fmt: skip
            switch.blockSignals(True)
            switch.setChecked(value)
            switch.blockSignals(False)
        self._refresh_regions()

    def _refresh_regions(self) -> None:
        roi = self.controller.config.roi
        extra, excluded = len(roi.extra_rects), len(roi.exclude_rects)
        self._regions_info.setText(t("roi.regions.info", extra=extra, exclude=excluded))
        self._clear_regions.setEnabled(bool(extra or excluded))

    def retranslate(self, *_: object) -> None:
        super().retranslate()
        self._template.blockSignals(True)
        for i, name in enumerate(TEMPLATE_NAMES):
            self._template.setItemText(i, t(f"roi.template.{name}"))
        self._template.blockSignals(False)
        self._freeze.setText(t("roi.freeze"))
        self._read_now.setText(t("roi.read_now"))
        self._calibrate.setText(t("roi.calibrate"))
        self._wait_freeze.setText(t("roi.wait_freeze"))
        self._draw_mode.blockSignals(True)
        for i, mode in enumerate(DRAW_MODES):
            self._draw_mode.setItemText(i, t(f"roi.draw.{mode}"))
        self._draw_mode.blockSignals(False)
        self._clear_regions.setText(t("roi.regions.clear"))
        self._refresh_regions()
        self._wait_freeze.setToolTip(t("roi.wait_freeze.tip"))
        self.editor.set_placeholder(t("roi.placeholder"))
        self.preview.retranslate()
        if not self.preview.text.text():
            self.preview.text.setText(t("roi.preview.waiting"))
        self._settings_title.setText(t("roi.settings"))
        self._padding_row.set_texts(t("roi.padding"), t("roi.padding.desc"))
        self._hz_row.set_texts(t("roi.hz"), t("roi.hz.desc"))
        self._engine_row.set_texts(t("roi.engine"), t("roi.engine.desc"))
        self._engine.blockSignals(True)
        for i, engine in enumerate(OCR_ENGINES):
            self._engine.setItemText(i, t(f"roi.engine.{engine}"))
        self._engine.blockSignals(False)
        self._confidence_row.set_texts(t("roi.min_confidence"), t("roi.min_confidence.desc"))
        self._white_mask_row.set_texts(t("roi.white_mask"), t("roi.white_mask.desc"))
        self._contrast_row.set_texts(t("roi.contrast"), t("roi.contrast.desc"))
        self._refresh_notice()
