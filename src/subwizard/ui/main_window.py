"""Main window: Fluent shell with left navigation (SPEC 3.12)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentWindow, NavigationItemPosition

from subwizard import APP_NAME
from subwizard.controller import AppController
from subwizard.i18n import t
from subwizard.ui.calibration import CalibrationWindow
from subwizard.ui.icon import make_app_icon
from subwizard.ui.pages import PAGES
from subwizard.ui.pages.base import BasePage

DEFAULT_SIZE = (1180, 760)
MIN_SIZE = (900, 600)
NAV_EXPAND_WIDTH = 240
HOTKEY_PERF_OVERLAY = "F9"
HOTKEY_FULLSCREEN = "F11"
HOTKEY_CALIBRATE = "F2"


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

        # Window-level keys for now; rebinding and global hotkeys (pynput) arrive in M4.
        self.home = self.pages[0]
        for key, action in (
            (HOTKEY_PERF_OVERLAY, controller.cycle_perf_overlay),
            (HOTKEY_FULLSCREEN, self.home.toggle_fullscreen),
            (HOTKEY_CALIBRATE, controller.request_calibration),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            # Application-wide so they also work in the detached fullscreen video window.
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(action)

        self.calibration: CalibrationWindow | None = None
        self._calibrate_on_frame = False
        controller.calibrateRequested.connect(self.open_calibration)
        controller.frameReady.connect(self._frame_for_calibration)
        controller.runningChanged.connect(self._on_running)

    # --- F2 calibration ---------------------------------------------------------------
    def open_calibration(self) -> None:
        """Freeze the current frame full screen; if none has arrived yet (the source is
        still connecting), open as soon as the first one does."""
        if self.calibration is not None:
            return
        frame = self.controller.latest_frame()
        if frame is None:
            self._calibrate_on_frame = True
            return
        self._calibrate_on_frame = False
        if self.home.video.fullscreen:
            self.home.toggle_fullscreen()  # one full-screen window at a time
        self.calibration = CalibrationWindow(self.controller, frame)
        self.calibration.closed.connect(self._calibration_closed)
        screen = (self.screen() or QApplication.primaryScreen()).geometry()
        self.calibration.open_on(screen)

    def _frame_for_calibration(self) -> None:
        if self._calibrate_on_frame:
            self.open_calibration()

    def _on_running(self, running: bool) -> None:
        if not running:
            self._calibrate_on_frame = False

    def _calibration_closed(self) -> None:
        self.calibration = None

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
