"""Turkish subtitle drawn over the video (SPEC 2, 3.7) — the M3 version.

SPEC 2: the subtitle is rendered into a QPixmap only when the text (or the video size)
changes; every frame just blits that pixmap. M4 extends this (fonts, colors, positions,
replace mode); the sizes here are proportions of the video so they scale with it.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap

from subwizard.pipeline.board import SubtitleView

FONT_FAMILIES = ["Segoe UI Variable", "Segoe UI", "Arial"]  # SPEC 3.7 fallback order
FONT_RATIO = 0.045  # font pixel size / video height
SPEAKER_RATIO = 0.75  # speaker / fallback marker size relative to the text
MAX_WIDTH_RATIO = 0.8  # word wrap at 80% of the video width (SPEC 3.7)
GAP_RATIO = 0.015  # gap between our box and the original subtitle region
PADDING_EM = 0.45  # box padding, in font heights
RADIUS_EM = 0.35
BACKGROUND = QColor(0, 0, 0, 165)
TEXT_COLOR = QColor(255, 255, 255)
OUTLINE = QColor(0, 0, 0, 220)
OUTLINE_EM = 0.08
SPEAKER_COLOR = QColor(255, 210, 74)
FALLBACK_COLOR = QColor(190, 190, 190)


@dataclass(frozen=True, slots=True)
class PaintKey:
    line_id: int
    text: str
    width: int
    height: int
    dpr: float


def _font(px: int, bold: bool = False) -> QFont:
    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    font.setPixelSize(max(1, px))
    font.setBold(bold)
    return font


def render_subtitle(view: SubtitleView, video_w: int, video_h: int, dpr: float,
                    fallback_label: str) -> QPixmap:  # fmt: skip
    """The whole subtitle box as one pixmap (logical size ~ box size)."""
    px = round(video_h * FONT_RATIO)
    text_font, small_font = _font(px, bold=True), _font(round(px * SPEAKER_RATIO), bold=True)
    fm, small_fm = QFontMetrics(text_font), QFontMetrics(small_font)
    pad = round(px * PADDING_EM)
    max_w = round(video_w * MAX_WIDTH_RATIO) - 2 * pad
    lines = wrap_lines(view.text, fm, max_w)
    text_w = max((fm.horizontalAdvance(line) for line in lines), default=0)
    text_h = fm.height() * len(lines)
    speaker_h = small_fm.height() if view.speaker else 0
    marker = fallback_label if view.fallback else ""
    width = max(text_w, small_fm.horizontalAdvance(view.speaker or ""),
                small_fm.horizontalAdvance(marker)) + 2 * pad  # fmt: skip
    height = text_h + speaker_h + (small_fm.height() if marker else 0) + 2 * pad

    pixmap = QPixmap(round(width * dpr), round(height * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(BACKGROUND)
    p.drawRoundedRect(QRectF(0, 0, width, height), px * RADIUS_EM, px * RADIUS_EM)

    y = pad
    if view.speaker:
        p.setFont(small_font)
        p.setPen(SPEAKER_COLOR)
        p.drawText(QRect(0, y, width, speaker_h), Qt.AlignmentFlag.AlignHCenter, view.speaker)
        y += speaker_h
    # Text with a thin outline (QPainterPath), centred line by line.
    path = QPainterPath()
    baseline = y + fm.ascent()
    for line in lines:
        path.addText(pad + (width - 2 * pad - fm.horizontalAdvance(line)) / 2, baseline,
                     text_font, line)  # fmt: skip
        baseline += fm.height()
    p.setPen(QPen(OUTLINE, max(1.0, px * OUTLINE_EM)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(TEXT_COLOR)
    p.drawPath(path)
    if marker:
        p.setFont(small_font)
        p.setPen(FALLBACK_COLOR)
        p.drawText(QRect(0, y + text_h, width - pad, small_fm.height()),
                   Qt.AlignmentFlag.AlignRight, marker)  # fmt: skip
    p.end()
    return pixmap


def wrap_lines(text: str, fm: QFontMetrics, max_w: int) -> list[str]:
    """Greedy word wrap (QPainterPath has none); one word wider than max_w gets its own line."""
    lines: list[str] = []
    for word in text.split():
        if lines and fm.horizontalAdvance(f"{lines[-1]} {word}") <= max_w:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    return lines


def place(box_w: float, box_h: float, video: QRectF, roi_top: float, roi_bottom: float) -> QRectF:
    """Just above the original subtitle region (SPEC 3.7 "bottom"); below it if there
    is no room above (a region at the top of the frame)."""
    gap = video.height() * GAP_RATIO
    x = video.left() + (video.width() - box_w) / 2
    above = video.top() + roi_top * video.height() - gap - box_h
    if above >= video.top():
        return QRectF(x, above, box_w, box_h)
    below = video.top() + roi_bottom * video.height() + gap
    return QRectF(x, min(below, video.bottom() - box_h), box_w, box_h)
