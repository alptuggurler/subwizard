"""Home: big Start/Stop button, mode indicator, status cards (SPEC 3.12)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    IconWidget,
    PrimaryPushButton,
    StrongBodyLabel,
)

from subwizard.controller import AppController
from subwizard.i18n import t
from subwizard.ui.pages.base import BasePage

START_BUTTON_SIZE = (200, 48)
CARD_ICON_SIZE = 22


class StatusCard(CardWidget):
    """Title + current state of one subsystem. Colors come with real state in M1."""

    def __init__(self, icon: FluentIcon, title_key: str, state_key: str):
        super().__init__()
        self._title_key = title_key
        self._state_key = state_key
        self._title = StrongBodyLabel()
        self._state = CaptionLabel()

        icon_widget = IconWidget(icon)
        icon_widget.setFixedSize(CARD_ICON_SIZE, CARD_ICON_SIZE)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(self._title)
        text.addWidget(self._state)

        row = QHBoxLayout(self)
        row.setContentsMargins(20, 16, 20, 16)
        row.setSpacing(14)
        row.addWidget(icon_widget)
        row.addLayout(text, 1)

    def retranslate(self) -> None:
        self._title.setText(t(self._title_key))
        self._state.setText(t(self._state_key))


class HomePage(BasePage):
    key = "home"

    def __init__(self, controller: AppController, parent: QWidget | None = None):
        super().__init__(controller, parent)

        self._toggle = PrimaryPushButton()
        self._toggle.setFixedSize(*START_BUTTON_SIZE)
        self._toggle.clicked.connect(controller.toggle_running)
        self._status = StrongBodyLabel()
        self._mode = BodyLabel()

        state = QVBoxLayout()
        state.setSpacing(2)
        state.addWidget(self._status)
        state.addWidget(self._mode)
        top = QHBoxLayout()
        top.setSpacing(20)
        top.addWidget(self._toggle)
        top.addLayout(state, 1)
        self.body.addLayout(top)

        self._pending = CaptionLabel()
        self._pending.setWordWrap(True)
        self.body.addWidget(self._pending)
        self.body.addSpacing(8)

        self._cards = [
            StatusCard(FluentIcon.VIDEO, "home.card.capture", "home.state.not_connected"),
            StatusCard(FluentIcon.LANGUAGE, "home.card.translator", "home.state.not_configured"),
            StatusCard(FluentIcon.GLOBE, "home.card.web", "home.state.off"),
        ]
        cards = QHBoxLayout()
        cards.setSpacing(12)
        for card in self._cards:
            cards.addWidget(card)
        self.body.addLayout(cards)
        self.body.setAlignment(cards, Qt.AlignmentFlag.AlignTop)

        controller.runningChanged.connect(self.retranslate)
        self.retranslate()

    def retranslate(self, *_: object) -> None:
        super().retranslate()
        running = self.controller.running
        self._toggle.setText(t("home.stop") if running else t("home.start"))
        self._toggle.setIcon(FluentIcon.PAUSE if running else FluentIcon.PLAY)
        self._status.setText(t("home.status.running") if running else t("home.status.idle"))
        mode = t(f"home.mode.{self.controller.config.display.mode}")
        self._mode.setText(t("home.mode", mode=mode))
        self._pending.setText(t("home.pipeline_pending"))
        for card in self._cards:
            card.retranslate()
