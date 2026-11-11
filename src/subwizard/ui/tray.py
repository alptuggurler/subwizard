"""System tray icon: start/stop, hide/show overlay, quit (SPEC 3.12)."""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from subwizard import APP_NAME
from subwizard.controller import AppController
from subwizard.i18n import t
from subwizard.ui.icon import make_app_icon


class TrayIcon(QSystemTrayIcon):
    def __init__(self, controller: AppController, window: QWidget):
        super().__init__(make_app_icon(), window)
        self._controller = controller
        self._window = window
        self.setToolTip(APP_NAME)

        self._menu = QMenu()
        self._show = QAction(self._menu, triggered=self._show_window)
        self._toggle = QAction(self._menu, triggered=controller.toggle_running)
        self._overlay = QAction(self._menu)
        self._overlay.setEnabled(False)  # subtitle overlay arrives in M4
        self._quit = QAction(self._menu, triggered=QApplication.quit)
        self._menu.addAction(self._show)
        self._menu.addSeparator()
        self._menu.addAction(self._toggle)
        self._menu.addAction(self._overlay)
        self._menu.addSeparator()
        self._menu.addAction(self._quit)
        self.setContextMenu(self._menu)

        self.activated.connect(self._on_activated)
        controller.languageChanged.connect(self.retranslate)
        controller.runningChanged.connect(self.retranslate)
        self.retranslate()

    def retranslate(self, *_: object) -> None:
        self._show.setText(t("tray.show"))
        self._toggle.setText(t("tray.stop") if self._controller.running else t("tray.start"))
        self._overlay.setText(t("tray.toggle_overlay"))
        self._quit.setText(t("tray.quit"))

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_window()

    def _show_window(self) -> None:
        self._window.showNormal()
        self._window.raise_()
        self._window.activateWindow()
