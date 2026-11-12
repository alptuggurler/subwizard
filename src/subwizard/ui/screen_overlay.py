"""PC game mode (Faz 2): the translation over the game itself.

A frameless, transparent, always-on-top window covering the captured monitor that
ignores the mouse and keyboard, so the game keeps every click. It draws only the
subtitle (the game draws the picture) and repaints only when the subtitle or its
style changes, never per frame, to stay out of the game's way. Works over
borderless / windowed games; exclusive fullscreen hides every other window.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QPainter, QPaintEvent, QScreen
from PySide6.QtWidgets import QWidget

from subwizard import platform
from subwizard.controller import AppController
from subwizard.ui.widgets.video_view import VideoView

log = logging.getLogger(__name__)


def pick_screen(region: dict[str, int] | None, number: int) -> QScreen | None:
    """The Qt screen showing the captured monitor: by its physical position and size
    (Windows reports physical pixels, Qt logical ones), else by number, else primary."""
    screens = QGuiApplication.screens()
    if region:
        for screen in screens:
            g, dpr = screen.geometry(), screen.devicePixelRatio()
            for f in (1.0, dpr):  # macOS: mss and Qt both in points; Windows: pixels
                if (round(g.width() * f), round(g.height() * f)) == (region["width"], region["height"]) \
                        and abs(g.x() * f - region["left"]) <= 2 and abs(g.y() * f - region["top"]) <= 2:  # fmt: skip
                    return screen
    if 1 <= number <= len(screens):
        return screens[number - 1]
    return QGuiApplication.primaryScreen()


class ScreenOverlay(VideoView):
    def __init__(self, controller: AppController, parent: QWidget | None = None) -> None:
        super().__init__(controller, measure=False, parent=parent)
        controller.frameReady.disconnect(self.update)  # subtitle changes only
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                 | Qt.WindowType.WindowDoesNotAcceptFocus)  # fmt: skip
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        if sys.platform == "darwin":  # Tool windows hide when the app loses focus there
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.excluded = False

    def cover(self, region: dict[str, int] | None, number: int) -> QRect:
        screen = pick_screen(region, number)
        geometry = screen.geometry() if screen is not None else QRect(0, 0, 1280, 720)
        self.setGeometry(geometry)
        return geometry

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        if not self.excluded:
            self.excluded = platform.exclude_from_capture(int(self.winId()))

    def paintEvent(self, event: QPaintEvent) -> None:
        frame = self._controller.latest_frame()
        if frame is None:
            return
        painter = QPainter(self)
        # The window *is* the captured monitor: frame pixels map onto all of it.
        self._draw_subtitle(painter, QRectF(self.rect()), frame)
        painter.end()

    # The base view's fullscreen / cursor / history handling does not apply here.
    def keyPressEvent(self, event) -> None:  # noqa: ANN001
        event.ignore()
