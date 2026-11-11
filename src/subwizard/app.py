"""Wires the components together and runs the Qt event loop."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from subwizard import APP_NAME
from subwizard.config import AppConfig
from subwizard.controller import AppController
from subwizard.ui.icon import make_app_icon
from subwizard.ui.main_window import MainWindow
from subwizard.ui.theme import apply_theme
from subwizard.ui.tray import TrayIcon

log = logging.getLogger(__name__)


def create_app(config: AppConfig, config_path: Path) -> tuple[QApplication, MainWindow]:
    """Build the application without entering the event loop (also used by scripts/tests)."""
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(make_app_icon())

    controller = AppController(config, config_path, parent=app)
    apply_theme(config.ui.theme)
    controller.themeChanged.connect(apply_theme)

    window = MainWindow(controller)
    if QSystemTrayIcon.isSystemTrayAvailable():
        # With a tray icon the window can be closed while the app keeps running.
        app.setQuitOnLastWindowClosed(False)
        window.tray = TrayIcon(controller, window)
        window.tray.show()
    else:
        log.warning("Sistem tepsisi kullanılamıyor; kapatma butonu uygulamadan çıkar.")
    return app, window


def run_gui(config: AppConfig, config_path: Path) -> int:
    app, window = create_app(config, config_path)
    window.show()
    return app.exec()
