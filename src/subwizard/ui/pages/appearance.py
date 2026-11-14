"""Görünüm: font, size, colors, position of the Turkish subtitle, with a live preview
(SPEC 3.7, 3.12). The preview uses the same renderer as the video, on a fake scene."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QLinearGradient, QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget
from qfluentwidgets import (
    CardWidget,
    ColorPickerButton,
    ComboBox,
    EditableComboBox,
    FluentIcon,
    PushButton,
    SwitchButton,
)

from subwizard.controller import OVERLAY_PRESETS, AppController
from subwizard.i18n import t
from subwizard.pipeline.board import SubtitleView
from subwizard.ui.pages.base import BasePage, SettingRow
from subwizard.ui.subtitle_paint import TURKISH_SAMPLE, Style, place, render_subtitle
from subwizard.ui.widgets.value_slider import ValueSlider

POSITIONS = ("bottom", "top", "replace")
COMBO_WIDTH = 220
PREVIEW_HEIGHT = 240
PREVIEW_ROI = (0.80, 0.95)  # where the "English subtitle" sits in the preview scene
PREVIEW_SCENE = (QColor(46, 70, 92), QColor(150, 110, 70))  # sky -> ground
PREVIEW_ENGLISH = QColor(235, 235, 235)
# Sliders work in integer steps: tenths of a percent, percent of opacity.
SIZE_RANGE = (20, 100)  # 2.0 % .. 10.0 % of the video height
WIDTH_RANGE = (30, 100)
OPACITY_RANGE = (0, 100)
SWITCHES = ("bold", "outline", "auto_bg", "show_source")
# Effect switches: row name -> (overlay option, value when on, value when off).
EFFECTS = {"smooth": ("fade_ms", 150, 0), "previous_line": ("stack_lines", 2, 1)}
COLORS = ("text_color", "background_color", "speaker_color")


def _switch() -> SwitchButton:
    switch = SwitchButton()
    switch.setOnText("")
    switch.setOffText("")
    return switch


class SubtitlePreview(QWidget):
    """A 16:9 fake game frame with the English line and our Turkish subtitle on it."""

    def __init__(self, controller: AppController):
        super().__init__()
        self._controller = controller
        self.setFixedHeight(PREVIEW_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        controller.overlayChanged.connect(self.update)
        controller.settingsChanged.connect(self.update)

    def sample(self) -> SubtitleView:
        return SubtitleView(0, t("appearance.preview.text", sample=TURKISH_SAMPLE), "LUCIA",
                            t("appearance.preview.source"), "gemini", False, 0.0, 0.0)  # fmt: skip

    def paintEvent(self, event: QPaintEvent) -> None:
        cfg = self._controller.config.overlay
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = min(self.width(), self.height() * 16 / 9)
        video = QRectF((self.width() - w) / 2, 0, w, self.height())
        gradient = QLinearGradient(video.topLeft(), video.bottomLeft())
        gradient.setColorAt(0, PREVIEW_SCENE[0])
        gradient.setColorAt(1, PREVIEW_SCENE[1])
        p.fillRect(video, gradient)
        english_y = video.top() + video.height() * PREVIEW_ROI[0]
        english_h = video.height() * (PREVIEW_ROI[1] - PREVIEW_ROI[0])
        english = QRectF(video.left(), english_y, video.width(), english_h)
        font = p.font()
        font.setPixelSize(max(8, round(english_h * 0.6)))
        p.setFont(font)
        if cfg.position == "replace":
            p.fillRect(english, PREVIEW_SCENE[1])  # what auto_bg would sample
        else:
            p.setPen(PREVIEW_ENGLISH)
            p.drawText(english, Qt.AlignmentFlag.AlignCenter, t("appearance.preview.source"))
        if not cfg.visible:
            return
        dpr = self.devicePixelRatioF()
        if cfg.position == "replace":
            bg = (PREVIEW_SCENE[1].red(), PREVIEW_SCENE[1].green(), PREVIEW_SCENE[1].blue(), 255)
            style = Style.from_config(cfg, round(w), round(video.height()),
                                      bg if cfg.auto_bg else None,
                                      round(english_h * 0.8), round(w * 0.9))  # fmt: skip
        else:
            style = Style.from_config(cfg, round(w), round(video.height()))
        pixmap = render_subtitle(self.sample(), style, dpr, t("subtitle.fallback"))
        bw, bh = pixmap.width() / dpr, pixmap.height() / dpr
        if cfg.position == "replace":
            box = QRectF(english.center().x() - bw / 2, english.center().y() - bh / 2, bw, bh)
        else:
            box = place(cfg.position, bw, bh, video, *PREVIEW_ROI, cfg.margin_pct)
        p.drawPixmap(box.topLeft(), pixmap)
        p.end()


class AppearancePage(BasePage):
    key = "appearance"

    def __init__(self, controller: AppController, parent: QWidget | None = None):
        super().__init__(controller, parent)
        c = controller
        self.preview = SubtitlePreview(controller)
        card = CardWidget()
        preview_layout = QHBoxLayout(card)
        preview_layout.setContentsMargins(12, 12, 12, 12)
        preview_layout.addWidget(self.preview)
        self.body.addWidget(card)

        presets = QWidget()
        presets_layout = QHBoxLayout(presets)
        presets_layout.setContentsMargins(0, 0, 0, 0)
        self._preset_buttons: dict[str, PushButton] = {}
        for name in OVERLAY_PRESETS:
            button = PushButton()
            button.clicked.connect(lambda _=False, n=name: c.apply_overlay_preset(n))
            presets_layout.addWidget(button)
            self._preset_buttons[name] = button
        self._presets_row = SettingRow(FluentIcon.BRUSH, presets)

        self._visible = _switch()
        self._visible.checkedChanged.connect(lambda v: c.set_overlay_option("visible", v))
        self._visible_row = SettingRow(FluentIcon.VIEW, self._visible)

        self._position = ComboBox()
        self._position.setMinimumWidth(COMBO_WIDTH)
        self._position.currentIndexChanged.connect(
            lambda i: c.set_overlay_option("position", POSITIONS[i]) if i >= 0 else None)
        self._position_row = SettingRow(FluentIcon.ALIGNMENT, self._position)

        self._font = EditableComboBox()
        self._font.setMinimumWidth(COMBO_WIDTH)
        self._font.addItems(sorted(set(QFontDatabase.families())))
        self._font.currentTextChanged.connect(self._on_font)
        self._font_row = SettingRow(FluentIcon.FONT, self._font)

        self._size = ValueSlider(SIZE_RANGE, lambda v: f"%{v / 10:.1f}")
        self._size.slider.valueChanged.connect(
            lambda v: c.set_overlay_option("font_size_pct", v / 10))
        self._size_row = SettingRow(FluentIcon.FONT_SIZE, self._size)

        self._width = ValueSlider(WIDTH_RANGE, lambda v: f"%{v}")
        self._width.slider.valueChanged.connect(
            lambda v: c.set_overlay_option("max_width_pct", float(v)))
        self._width_row = SettingRow(FluentIcon.FIT_PAGE, self._width)

        self._opacity = ValueSlider(OPACITY_RANGE, lambda v: f"%{v}")
        self._opacity.slider.valueChanged.connect(
            lambda v: c.set_overlay_option("background_opacity", v / 100))
        self._opacity_row = SettingRow(FluentIcon.TRANSPARENT, self._opacity)

        self._colors: dict[str, ColorPickerButton] = {}
        colors = QWidget()
        colors_layout = QHBoxLayout(colors)
        colors_layout.setContentsMargins(0, 0, 0, 0)
        for key in COLORS:
            button = ColorPickerButton(QColor("#FFFFFF"), "")
            button.colorChanged.connect(lambda col, k=key: c.set_overlay_option(k, col.name().upper()))
            colors_layout.addWidget(button)
            self._colors[key] = button
        self._colors_row = SettingRow(FluentIcon.PALETTE, colors)

        self._switches: dict[str, SwitchButton] = {}
        self._switch_rows: dict[str, SettingRow] = {}
        for key in SWITCHES:
            switch = _switch()
            switch.checkedChanged.connect(lambda v, k=key: c.set_overlay_option(k, v))
            self._switches[key] = switch
            self._switch_rows[key] = SettingRow(FluentIcon.EDIT, switch)
        self._effects: dict[str, SwitchButton] = {}
        for name, (option, on, off) in EFFECTS.items():
            switch = _switch()
            switch.checkedChanged.connect(
                lambda v, o=option, a=on, b=off: c.set_overlay_option(o, a if v else b))
            self._effects[name] = switch
            self._switch_rows[name] = SettingRow(FluentIcon.VIDEO, switch)

        for row in (self._presets_row, self._visible_row, self._position_row, self._font_row,
                    self._size_row, self._width_row, self._colors_row, self._opacity_row,
                    *self._switch_rows.values()):  # fmt: skip
            self.body.addWidget(row)

        controller.overlayChanged.connect(self._load)
        controller.settingsChanged.connect(self._load)
        self.retranslate()

    def _on_font(self, family: str) -> None:
        if family != self.controller.config.overlay.font_family:
            self.controller.set_overlay_option("font_family", family.strip())

    def _load(self) -> None:
        cfg = self.controller.config.overlay
        widgets = [self._visible, self._position, self._font, *self._colors.values(),
                   *self._switches.values(), *self._effects.values()]  # fmt: skip
        for widget in widgets:
            widget.blockSignals(True)
        self._visible.setChecked(cfg.visible)
        self._position.setCurrentIndex(POSITIONS.index(cfg.position))
        self._font.setText(cfg.font_family)
        for key, button in self._colors.items():
            button.setColor(QColor(getattr(cfg, key)))
        for key, switch in self._switches.items():
            switch.setChecked(getattr(cfg, key))
        for name, switch in self._effects.items():
            switch.setChecked(getattr(cfg, EFFECTS[name][0]) > EFFECTS[name][2])
        for widget in widgets:
            widget.blockSignals(False)
        self._size.set_value(round(cfg.font_size_pct * 10))
        self._width.set_value(round(cfg.max_width_pct))
        self._opacity.set_value(round(cfg.background_opacity * 100))
        self.preview.update()

    def retranslate(self) -> None:
        super().retranslate()
        self._font.setPlaceholderText(t("appearance.font.default"))
        for name, button in self._preset_buttons.items():
            button.setText(t(f"appearance.preset.{name}"))
        self._position.blockSignals(True)
        self._position.clear()
        self._position.addItems([t(f"appearance.position.{p}") for p in POSITIONS])
        self._position.blockSignals(False)
        for key, button in self._colors.items():
            button.setToolTip(t(f"appearance.color.{key}"))
            button.title = t(f"appearance.color.{key}")
        rows = {"presets": self._presets_row, "visible": self._visible_row,
                "position": self._position_row, "font": self._font_row,
                "size": self._size_row, "width": self._width_row, "colors": self._colors_row,
                "opacity": self._opacity_row,
                **self._switch_rows}  # fmt: skip
        for name, row in rows.items():
            row.set_texts(t(f"appearance.{name}"), t(f"appearance.{name}.desc"))
        self._load()
