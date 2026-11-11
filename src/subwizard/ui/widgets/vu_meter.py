"""VUMeter: stereo peak meter in dBFS with a clipping segment (SPEC 3.2.1)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget
from qfluentwidgets import isDarkTheme

from subwizard.ui.metrics_view import GRADE_COLORS

MIN_DB = -60.0  # left edge of the scale
WARN_DB = -12.0  # green below, yellow above
CLIP_DB = -1.0  # red from here to 0 dBFS
BAR_HEIGHT = 8
BAR_GAP = 4
RADIUS = 3
FALL_DB_PER_UPDATE = 3.0  # meter falls back gradually so peaks are readable


class VUMeter(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._levels = [MIN_DB, MIN_DB]
        self.setFixedHeight(2 * BAR_HEIGHT + BAR_GAP)
        self.setMinimumWidth(160)

    def set_levels(self, left: float | None, right: float | None) -> None:
        for i, level in enumerate((left, right)):
            level = MIN_DB if level is None else max(MIN_DB, min(0.0, level))
            self._levels[i] = max(level, self._levels[i] - FALL_DB_PER_UPDATE)
        self.update()

    def _x(self, db: float) -> float:
        return (db - MIN_DB) / -MIN_DB * self.width()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        track = QColor(255, 255, 255, 30) if isDarkTheme() else QColor(0, 0, 0, 25)
        segments = [
            (MIN_DB, WARN_DB, GRADE_COLORS["ok"]),
            (WARN_DB, CLIP_DB, GRADE_COLORS["warn"]),
            (CLIP_DB, 0.0, GRADE_COLORS["bad"]),
        ]
        for i, level in enumerate(self._levels):
            y = i * (BAR_HEIGHT + BAR_GAP)
            painter.setBrush(track)
            painter.drawRoundedRect(QRectF(0, y, self.width(), BAR_HEIGHT), RADIUS, RADIUS)
            for start, end, color in segments:
                if level <= start:
                    break
                right = self._x(min(level, end))
                painter.setBrush(QColor(color))
                painter.drawRoundedRect(
                    QRectF(self._x(start), y, right - self._x(start), BAR_HEIGHT), RADIUS, RADIUS
                )
        painter.end()
