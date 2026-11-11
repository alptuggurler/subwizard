"""Web Overlay: display mode, the phone URL + QR code, OBS URL, port / token, connected
pages, page defaults (SPEC 3.10, 3.12)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIntValidator, QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    LineEdit,
    PushButton,
    StrongBodyLabel,
    SwitchButton,
)

from subwizard.controller import AppController
from subwizard.i18n import t
from subwizard.ui.pages.base import BasePage, SettingRow
from subwizard.ui.pages.home import web_state
from subwizard.ui.widgets.notice import Notice
from subwizard.ui.widgets.value_slider import ValueSlider
from subwizard.web.qr import qr_matrix

MODES = ("window", "web", "both")
COMBO_WIDTH = 260
QR_SIZE = 184
FIELD_WIDTH = 160
PORT_RANGE = (1024, 65535)
FONT_RANGE = (16, 120)
LINES_RANGE = (1, 6)


class QrView(QWidget):
    """Paints a QR matrix crisply at any size (black on white, as phone cameras expect)."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(QR_SIZE, QR_SIZE)
        self.text = ""
        self._matrix: list[list[bool]] = []

    def set_text(self, text: str) -> None:
        if text != self.text:
            self.text = text
            self._matrix = qr_matrix(text) if text else []
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        if not self._matrix:
            return
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.GlobalColor.white)
        n = len(self._matrix)
        cell = min(self.width(), self.height()) / n
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0))
        for y, row in enumerate(self._matrix):
            for x, dark in enumerate(row):
                if dark:
                    p.drawRect(QRectF(x * cell, y * cell, cell + 0.5, cell + 0.5))
        p.end()


def _switch() -> SwitchButton:
    switch = SwitchButton()
    switch.setOnText("")
    switch.setOffText("")
    return switch


class WebPage(BasePage):
    key = "web"

    def __init__(self, controller: AppController, parent: QWidget | None = None):
        super().__init__(controller, parent)
        c = controller
        self._notice = Notice()
        self.body.addWidget(self._notice)

        self._mode = ComboBox()
        self._mode.setMinimumWidth(COMBO_WIDTH)
        self._mode.currentIndexChanged.connect(
            lambda i: c.set_display_mode(MODES[i]) if i >= 0 else None)
        self._mode_row = SettingRow(FluentIcon.TILES, self._mode)
        self.body.addWidget(self._mode_row)

        # --- address card: QR + URLs + clients
        self.qr = QrView()
        self._address_title = StrongBodyLabel()
        self.url_label = BodyLabel()
        self.url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._copy_url = PushButton(FluentIcon.COPY, "")
        self._copy_url.clicked.connect(lambda: self._copy("/"))
        self._copy_obs = PushButton(FluentIcon.VIDEO, "")
        self._copy_obs.clicked.connect(lambda: self._copy("/obs"))
        self._clients = CaptionLabel()
        self._hint = CaptionLabel()
        self._hint.setWordWrap(True)
        buttons = QHBoxLayout()
        buttons.addWidget(self._copy_url)
        buttons.addWidget(self._copy_obs)
        buttons.addStretch(1)
        text = QVBoxLayout()
        text.setSpacing(8)
        text.addWidget(self._address_title)
        text.addWidget(self.url_label)
        text.addLayout(buttons)
        text.addWidget(self._clients)
        text.addWidget(self._hint)
        text.addStretch(1)
        self._address = CardWidget()
        address_layout = QHBoxLayout(self._address)
        address_layout.setContentsMargins(20, 16, 20, 16)
        address_layout.setSpacing(20)
        address_layout.addWidget(self.qr)
        address_layout.addLayout(text, 1)
        self.body.addWidget(self._address)

        # --- server + page settings
        # A LineEdit, not SpinBox: Fluent's SpinBox drew its value black on the dark theme.
        self._port = LineEdit()
        self._port.setValidator(QIntValidator(*PORT_RANGE, self._port))
        self._port.setFixedWidth(FIELD_WIDTH)
        self._port.editingFinished.connect(self._on_port)
        self._port_row = SettingRow(FluentIcon.CONNECT, self._port)

        self._token = LineEdit()
        self._token.setFixedWidth(FIELD_WIDTH)
        self._token.editingFinished.connect(
            lambda: c.set_web_option("token", self._token.text().strip()))
        self._token_row = SettingRow(FluentIcon.VPN, self._token)

        self._font = ValueSlider(FONT_RANGE, lambda v: f"{v} px")
        self._font.slider.valueChanged.connect(lambda v: c.set_web_option("font_px", v))
        self._font_row = SettingRow(FluentIcon.FONT_SIZE, self._font)

        self._lines = ValueSlider(LINES_RANGE, str)
        self._lines.slider.valueChanged.connect(lambda v: c.set_web_option("lines", v))
        self._lines_row = SettingRow(FluentIcon.ALIGNMENT, self._lines)

        self._source = _switch()
        self._source.checkedChanged.connect(lambda v: c.set_web_option("show_source", v))
        self._source_row = SettingRow(FluentIcon.LANGUAGE, self._source)

        self._mute = _switch()
        self._mute.checkedChanged.connect(c.set_audio_muted)
        self._mute_row = SettingRow(FluentIcon.MUTE, self._mute)

        for row in (self._port_row, self._token_row, self._font_row, self._lines_row,
                    self._source_row, self._mute_row):  # fmt: skip
            self.body.addWidget(row)

        c.webChanged.connect(self._load)
        c.settingsChanged.connect(self._load)
        c.metricsUpdated.connect(lambda _: self._refresh_clients() if self.isVisible() else None)
        self.retranslate()

    def _on_port(self) -> None:
        text = self._port.text().strip()
        if text.isdigit() and int(text) != self.controller.config.web.port:
            self.controller.set_web_option("port", int(text))

    def _copy(self, path: str) -> None:
        QGuiApplication.clipboard().setText(self.controller.web.url(path))

    def _refresh_clients(self) -> None:
        self._clients.setText(web_state(self.controller)[0])

    def _load(self) -> None:
        c = self.controller
        cfg, web = c.config.web, c.web
        self._mode.blockSignals(True)
        self._mode.setCurrentIndex(MODES.index(c.config.display.mode))
        self._mode.blockSignals(False)
        for widget, value in ((self._source, cfg.show_source), (self._mute, c.config.audio.muted)):
            widget.blockSignals(True)
            widget.setChecked(value)
            widget.blockSignals(False)
        self._port.setText(str(cfg.port))
        self._token.setText(cfg.token)
        self._font.set_value(cfg.font_px)
        self._lines.set_value(cfg.lines)

        self._address.setVisible(web.running)
        if web.running:
            self.url_label.setText(web.url())
            self.qr.set_text(web.url())
        if web.error:
            self._notice.show_message(t("web.error", port=cfg.port, error=web.error), "bad")
        elif not c.web_enabled:
            self._notice.show_message(t("web.off_hint"), None)
        else:
            self._notice.show_message("")
        self._refresh_clients()

    def retranslate(self) -> None:
        super().retranslate()
        self._mode.blockSignals(True)
        self._mode.clear()
        self._mode.addItems([t(f"web.mode.{m}") for m in MODES])
        self._mode.blockSignals(False)
        self._address_title.setText(t("web.address"))
        self._copy_url.setText(t("web.copy_url"))
        self._copy_obs.setText(t("web.copy_obs"))
        self._hint.setText(t("web.hint"))
        self._token.setPlaceholderText(t("web.token.placeholder"))
        rows = {"mode": self._mode_row, "port": self._port_row, "token": self._token_row,
                "font": self._font_row, "lines": self._lines_row, "source": self._source_row,
                "mute": self._mute_row}  # fmt: skip
        for name, row in rows.items():
            row.set_texts(t(f"web.{name}"), t(f"web.{name}.desc"))
        self._load()
