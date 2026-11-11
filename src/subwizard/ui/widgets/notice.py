"""Notice: an inline warning/error line with a colored marker. Hidden when empty."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget
from qfluentwidgets import BodyLabel

from subwizard.core.metrics import Grade
from subwizard.ui.metrics_view import GRADE_COLORS

MARKER_SIZE = 10


class StatusDot(QLabel):
    """A small colored circle: green / yellow / red, or gray for "nothing to report"."""

    NEUTRAL = "#8A8A8A"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(MARKER_SIZE, MARKER_SIZE)
        self.set_grade(None)

    def set_grade(self, grade: Grade | None) -> None:
        color = GRADE_COLORS[grade] or self.NEUTRAL
        self.setStyleSheet(f"background:{color};border-radius:{MARKER_SIZE // 2}px")


class Notice(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._dot = StatusDot()
        self._text = BodyLabel()
        self._text.setWordWrap(True)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(self._dot)
        row.addWidget(self._text, 1)
        self.hide()

    def show_message(self, text: str, grade: Grade = "warn") -> None:
        """Empty text hides the notice."""
        self._dot.set_grade(grade)
        self._text.setText(text)
        self.setVisible(bool(text))
