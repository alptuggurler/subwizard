"""A small line chart of the last N seconds (Diagnostics, SPEC 3.2.1). Plain QPainter:
a few dozen points once a second do not need a plotting library."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget
from qfluentwidgets import isDarkTheme

HEIGHT = 96
PAD = 6
LABEL_WIDTH = 64
LINE_WIDTH = 1.6
GRID = {True: QColor(255, 255, 255, 28), False: QColor(0, 0, 0, 26)}
TEXT = {True: QColor(200, 200, 200), False: QColor(90, 90, 90)}


class Sparkline(QWidget):
    """Lines (one per series) sharing a y-axis; gaps (None) break a line."""

    def __init__(self, colors: list[QColor], fixed_range: tuple[float, float] | None = None,
                 unit: str = "") -> None:  # fmt: skip
        super().__init__()
        self.setFixedHeight(HEIGHT)
        self._colors = colors
        self._fixed = fixed_range
        self._unit = unit
        self.series: list[list[float | None]] = [[] for _ in colors]
        self.capacity = 60

    def set_series(self, series: list[list[float | None]], capacity: int) -> None:
        self.series = series
        self.capacity = capacity
        self.update()

    def value_range(self) -> tuple[float, float]:
        values = [v for line in self.series for v in line if v is not None]
        if self._fixed is not None:
            low, high = self._fixed
            if values:  # grow past the usual range instead of clipping a spike
                low, high = min(low, min(values)), max(high, max(values))
            return low, high
        if not values:
            return 0.0, 1.0
        low, high = min(values), max(values)
        if high - low < 1e-6:
            low, high = low - 1, high + 1
        return min(0.0, low), high

    def paintEvent(self, event: QPaintEvent) -> None:
        dark = isDarkTheme()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = QRectF(LABEL_WIDTH, PAD, self.width() - LABEL_WIDTH - PAD, self.height() - 2 * PAD)
        low, high = self.value_range()

        p.setPen(QPen(GRID[dark], 1))
        for frac in (0.0, 0.5, 1.0):
            y = plot.top() + plot.height() * frac
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        p.setPen(TEXT[dark])
        for frac, value in ((0.0, high), (1.0, low)):
            y = plot.top() + plot.height() * frac
            p.drawText(QRectF(0, y - 9, LABEL_WIDTH - 8, 18),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{value:.0f}{self._unit}")  # fmt: skip

        step = plot.width() / max(1, self.capacity - 1)
        span = high - low or 1.0
        for color, line in zip(self._colors, self.series):
            offset = self.capacity - len(line)  # newest value at the right edge
            path, drawing = QPainterPath(), False
            for i, value in enumerate(line):
                if value is None:
                    drawing = False
                    continue
                point = QPointF(plot.left() + (offset + i) * step,
                                plot.bottom() - (value - low) / span * plot.height())  # fmt: skip
                if drawing:
                    path.lineTo(point)
                else:
                    path.moveTo(point)
                    drawing = True
            p.setPen(QPen(color, LINE_WIDTH))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        p.end()
