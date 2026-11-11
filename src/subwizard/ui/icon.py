"""App icon drawn in code, so no binary asset is needed until real artwork exists."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPixmap

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
_TOP = QColor("#7C5CFF")
_BOTTOM = QColor("#3A2A9E")


def _draw(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    gradient = QLinearGradient(0, 0, 0, size)
    gradient.setColorAt(0, _TOP)
    gradient.setColorAt(1, _BOTTOM)
    p.setBrush(gradient)
    p.setPen(Qt.PenStyle.NoPen)
    radius = size * 0.22
    p.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    font = QFont()
    font.setBold(True)
    font.setPixelSize(int(size * 0.5))
    p.setFont(font)
    p.setPen(QColor("white"))
    p.drawText(QRectF(0, 0, size, size * 0.74), Qt.AlignmentFlag.AlignCenter, "S")

    # A subtitle bar under the letter.
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 230))
    bar_h = size * 0.09
    p.drawRoundedRect(
        QRectF(size * 0.2, size * 0.74, size * 0.6, bar_h), bar_h / 2, bar_h / 2
    )
    p.end()
    return pixmap


def make_app_icon() -> QIcon:
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(_draw(size))
    return icon
