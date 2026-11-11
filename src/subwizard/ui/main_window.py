"""Main window: Fluent shell with left navigation (SPEC 3.12)."""

from __future__ import annotations

from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentWindow, NavigationItemPosition

from subwizard import APP_NAME
from subwizard.controller import AppController
from subwizard.i18n import t
from subwizard.ui.icon import make_app_icon
from subwizard.ui.pages import PAGES
from subwizard.ui.pages.base import BasePage

DEFAULT_SIZE = (1180, 760)
MIN_SIZE = (900, 600)
NAV_EXPAND_WIDTH = 240


class MainWindow(FluentWindow):
    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller
        self.tray = None  # set by app.create_app when a system tray exists
        self.pages: list[BasePage] = []
        self._nav_items = []
        self._shown_once = False

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(make_app_icon())
        self.resize(*DEFAULT_SIZE)
        self.setMinimumSize(*MIN_SIZE)
        self.navigationInterface.setExpandWidth(NAV_EXPAND_WIDTH)

        for spec in PAGES:
            page = spec.cls(controller, self)
            position = (
                NavigationItemPosition.BOTTOM if spec.bottom else NavigationItemPosition.SCROLL
            )
            item = self.addSubInterface(page, spec.icon, t(f"nav.{page.key}"), position)
            self.pages.append(page)
            self._nav_items.append(item)

        controller.languageChanged.connect(self.retranslate)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._shown_once:
            # Start with labels visible; the user can still collapse to icons.
            self._shown_once = True
            self.navigationInterface.expand(useAni=False)

    def retranslate(self, *_: object) -> None:
        for page, item in zip(self.pages, self._nav_items, strict=True):
            item.setText(t(f"nav.{page.key}"))
            page.retranslate()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.tray is not None and self.controller.config.ui.close_to_tray:
            event.ignore()
            self.hide()
            return
        super().closeEvent(event)
        QApplication.quit()
