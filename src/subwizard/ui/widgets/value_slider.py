"""ValueSlider: a horizontal slider with its formatted value shown next to it."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QWidget
from qfluentwidgets import BodyLabel, Slider

SLIDER_WIDTH = 240
VALUE_WIDTH = 64


class ValueSlider(QWidget):
    def __init__(self, value_range: tuple[int, int], fmt: Callable[[int], str]):
        super().__init__()
        self._fmt = fmt
        self.slider = Slider(Qt.Orientation.Horizontal)
        self.slider.setRange(*value_range)
        self.slider.setFixedWidth(SLIDER_WIDTH)
        self._value = BodyLabel()
        self._value.setFixedWidth(VALUE_WIDTH)
        self._value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.slider)
        row.addWidget(self._value)
        self.slider.valueChanged.connect(self._show)

    def _show(self, value: int) -> None:
        self._value.setText(self._fmt(value))

    def set_value(self, value: int) -> None:
        """Set without emitting valueChanged (used when refreshing from config)."""
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self._show(value)
