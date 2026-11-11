"""Wires the components together and runs the Qt event loop."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import signal

from PySide6.QtCore import QCoreApplication, QTimer
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


def run_gui(config: AppConfig, config_path: Path, calibrate: bool = False) -> int:
    app, window = create_app(config, config_path)
    window.show()
    if not config.ui.wizard_done:  # first run: the setup wizard on top of the window
        QTimer.singleShot(0, window.open_wizard)
    if calibrate:  # --calibrate: start the source and open F2 calibration right away
        QTimer.singleShot(0, window.controller.request_calibration)
    return app.exec()


SIGNAL_POLL_MS = 200  # lets Python see Ctrl+C while the Qt loop runs


def run_headless(config: AppConfig, config_path: Path) -> int:
    """`--headless`: no window; capture + OCR + translation, translations on the web
    overlay only (SPEC 5). Ctrl+C stops."""
    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    if config.display.mode == "window":
        config.display.mode = "web"  # without a window, the web page is the only display
    controller = AppController(config, config_path, parent=app)
    if not controller.web.running:
        log.error("Web overlay başlatılamadı: %s", controller.web.error)
        controller.shutdown()
        return 1
    controller.start()
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    poll = QTimer()
    poll.timeout.connect(lambda: None)
    poll.start(SIGNAL_POLL_MS)
    print("Ctrl+C: çıkış", flush=True)
    return app.exec()
