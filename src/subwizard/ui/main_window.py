"""Main window: Fluent shell with left navigation (SPEC 3.12)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QGuiApplication, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentWindow, NavigationItemPosition

from subwizard import APP_NAME
from subwizard.controller import AppController
from subwizard.i18n import t
from subwizard.ui.calibration import CalibrationWindow
from subwizard.ui.icon import make_app_icon
from subwizard.ui.pages import PAGES
from subwizard.ui.pages.base import BasePage
from subwizard.utils.hotkeys import GlobalHotkeys

DEFAULT_SIZE = (1180, 760)
MIN_SIZE = (900, 600)
NAV_EXPAND_WIDTH = 240


class MainWindow(FluentWindow):
    _globalHotkey = Signal(str)  # from the pynput thread to the Qt main thread

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

        self.home = self.pages[0]
        self._actions = {
            "calibrate": controller.request_calibration,
            "copy_last": self.copy_last_translation,
            "mute": controller.toggle_mute,
            "retranslate": controller.retranslate_current,
            "pause_translation": controller.toggle_translation_pause,
            "perf_overlay": controller.cycle_perf_overlay,
            "toggle_subtitles": controller.toggle_subtitles,
            "fullscreen": self.home.toggle_fullscreen,
            "history_panel": self.toggle_history_panel,
        }
        self._shortcuts: list[QShortcut] = []
        self._global = GlobalHotkeys()
        self._globalHotkey.connect(self._on_global_hotkey)
        controller.hotkeysChanged.connect(self.bind_hotkeys)
        self.bind_hotkeys()

        self.calibration: CalibrationWindow | None = None
        self._calibrate_on_frame = False
        controller.calibrateRequested.connect(self.open_calibration)
        self.wizard = None  # SetupWizard while open
        controller.wizardRequested.connect(self.open_wizard)
        controller.frameReady.connect(self._frame_for_calibration)
        controller.runningChanged.connect(self._on_running)

    # --- hotkeys (SPEC 3.8) ---------------------------------------------------------------
    def bind_hotkeys(self) -> None:
        """(Re)create the in-app shortcuts and the global listener from config."""
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self._shortcuts = []
        keys = self.controller.config.hotkeys.keys
        for action, key in keys.items():
            handler = self._actions.get(action)
            if not key or handler is None:
                continue
            shortcut = QShortcut(QKeySequence(key), self)
            # Application-wide so they also work in the detached fullscreen video window.
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(lambda h=handler: h())
            self._shortcuts.append(shortcut)
        if self.controller.config.hotkeys.global_hotkeys:
            self._global.start(dict(keys), self._globalHotkey.emit)
        else:
            self._global.stop()

    def _on_global_hotkey(self, action: str) -> None:
        # While one of our windows has focus the QShortcut already fired.
        if QApplication.activeWindow() is not None:
            return
        handler = self._actions.get(action)
        if handler is not None:
            handler()

    def trigger(self, action: str) -> None:
        self._actions[action]()

    def copy_last_translation(self) -> None:
        text = self.controller.last_translation()
        if text:
            QGuiApplication.clipboard().setText(text)

    def toggle_history_panel(self) -> None:
        self.home.video.show_history = not self.home.video.show_history
        self.home.video.update()

    # --- setup wizard (SPEC 3.12) -------------------------------------------------------
    def open_wizard(self) -> None:
        from subwizard.ui.wizard import SetupWizard

        if self.wizard is not None:
            self.wizard.raise_()
            self.wizard.activateWindow()
            return
        self.wizard = SetupWizard(self.controller)
        self.wizard.destroyed.connect(lambda: setattr(self, "wizard", None))
        self.wizard.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.wizard.show()

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
        self._global.stop()
        super().closeEvent(event)
        QApplication.quit()
