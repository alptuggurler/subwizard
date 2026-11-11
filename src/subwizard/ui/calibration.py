"""F2 calibration (SPEC 3.3): a full-screen frozen frame; drag a rectangle over the
subtitle, Enter saves it, Esc cancels, 1-4 pick a ready-made template."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeyEvent
from PySide6.QtWidgets import QVBoxLayout, QWidget

from subwizard.controller import AppController
from subwizard.i18n import t
from subwizard.ocr.roi import TEMPLATES
from subwizard.sources.base import Frame
from subwizard.ui.widgets.roi_editor import RoiEditor

TEMPLATE_KEYS = {
    Qt.Key.Key_1: "bottom_center",
    Qt.Key.Key_2: "bottom_wide",
    Qt.Key.Key_3: "lower_half",
    Qt.Key.Key_4: "top",
}


class CalibrationWindow(QWidget):
    closed = Signal()

    def __init__(self, controller: AppController, frame: Frame):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._controller = controller
        self.editor = RoiEditor(controller, commit_on_release=False, parent=self)
        self.editor.freeze(frame)
        self.editor.set_hint(t("calib.hint"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.editor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def open_on(self, screen: QRect) -> None:
        # Same frameless screen-sized window as the video fullscreen (see VideoView).
        self.setGeometry(screen)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def confirm(self) -> None:
        """Save the drawn (or template) region; with nothing drawn, keep the current one."""
        rect = self.editor.pending
        if rect is not None:
            name = next((n for n, r in TEMPLATES.items() if r == rect), None)
            if name is not None:
                self._controller.set_roi_template(name)
            elif rect.usable:
                self._controller.set_roi_rect(rect)
        self.close()

    def pick_template(self, name: str) -> None:
        self.editor.set_pending(TEMPLATES[name])

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirm()
        elif key == Qt.Key.Key_Escape:
            self.close()
        elif key in TEMPLATE_KEYS:
            self.pick_template(TEMPLATE_KEYS[key])
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._controller.force_ocr()  # show the result for the (possibly new) region at once
        super().closeEvent(event)
        self.closed.emit()

