"""About: version and license. Update check and log packaging arrive in M5."""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CardWidget, StrongBodyLabel

from subwizard import APP_NAME, __version__
from subwizard.controller import AppController
from subwizard.i18n import t
from subwizard.ui.pages.base import BasePage


class AboutPage(BasePage):
    key = "about"

    def __init__(self, controller: AppController, parent: QWidget | None = None):
        super().__init__(controller, parent)
        self._name = StrongBodyLabel(APP_NAME)
        self._version = BodyLabel()
        self._license = BodyLabel()

        card = CardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(4)
        for label in (self._name, self._version, self._license):
            layout.addWidget(label)
        self.body.addWidget(card)
        self.retranslate()

    def retranslate(self) -> None:
        super().retranslate()
        self._version.setText(t("about.version", version=__version__))
        self._license.setText(t("about.license"))
