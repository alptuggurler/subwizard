"""SubtitleRenderer: the Turkish subtitle over the video (SPEC 2, 3.7).

SPEC 2: the subtitle is rendered into a QPixmap only when its text, style or the video
size changes; every frame just blits that pixmap. Sizes come from `overlay.*` as
percentages of the video, so the look is the same in the window and full screen.

Positions: `bottom` = just above the original subtitle region (below it if there is
no room), `top` = top of the video, `replace` = over the English text itself, using
the OCR line boxes, on a background sampled from around it (`auto_bg`).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap

from subwizard.config import OverlayConfig
from subwizard.pipeline.board import SubtitleView

DEFAULT_FAMILIES = ["Segoe UI Variable", "Segoe UI", "Arial"]  # SPEC 3.7 fallback order
SMALL_RATIO = 0.75  # speaker / source / fallback marker size relative to the text
PADDING_EM = 0.45  # box padding, in font heights
RADIUS_EM = 0.35
OUTLINE_EM = 0.08
OUTLINE_COLOR = QColor(0, 0, 0, 220)
MARKER_COLOR = QColor(190, 190, 190)
SOURCE_COLOR = QColor(205, 205, 205)
REPLACE_MIN_FONT_PX = 10  # replace mode shrinks the font to fit the English box
REPLACE_SAMPLE_PX = 6  # ring around the English text sampled for its background color
TURKISH_SAMPLE = "ğüşıöçİĞÜŞÖÇ"


@dataclass(frozen=True, slots=True)
class Style:
    """Everything that changes how the pixmap looks (part of the cache key)."""

    families: tuple[str, ...]
    font_px: int
    bold: bool
    max_width: int
    text_color: str
    background: tuple[int, int, int, int]  # RGBA
    speaker_color: str
    outline: bool
    show_source: bool

    @classmethod
    def from_config(cls, cfg: OverlayConfig, video_w: int, video_h: int,
                    background: tuple[int, int, int, int] | None = None,
                    font_px: int | None = None, max_width: int | None = None) -> Style:  # fmt: skip
        families = (cfg.font_family, *DEFAULT_FAMILIES) if cfg.font_family else tuple(DEFAULT_FAMILIES)
        bg = QColor(cfg.background_color)
        return cls(
            families=families,
            font_px=font_px or max(1, round(video_h * cfg.font_size_pct / 100)),
            bold=cfg.bold,
            max_width=max_width or round(video_w * cfg.max_width_pct / 100),
            text_color=cfg.text_color,
            background=background or (bg.red(), bg.green(), bg.blue(),
                                      round(255 * cfg.background_opacity)),  # fmt: skip
            speaker_color=cfg.speaker_color,
            outline=cfg.outline,
            show_source=cfg.show_source,
        )


def make_font(families: tuple[str, ...], px: int, bold: bool) -> QFont:
    font = QFont()
    font.setFamilies(list(families))
    font.setPixelSize(max(1, px))
    font.setBold(bold)
    return font


def wrap_lines(text: str, fm: QFontMetrics, max_w: int) -> list[str]:
    """Greedy word wrap (QPainterPath has none); one word wider than max_w gets its own line."""
    lines: list[str] = []
    for word in text.split():
        if lines and fm.horizontalAdvance(f"{lines[-1]} {word}") <= max_w:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    return lines


def render_subtitle(view: SubtitleView, style: Style, dpr: float, fallback_label: str) -> QPixmap:
    """The whole subtitle box as one pixmap (logical size = box size)."""
    px = style.font_px
    text_font = make_font(style.families, px, style.bold)
    small_font = make_font(style.families, round(px * SMALL_RATIO), True)
    fm, small_fm = QFontMetrics(text_font), QFontMetrics(small_font)
    pad = round(px * PADDING_EM)
    inner_w = max(1, style.max_width - 2 * pad)
    lines = wrap_lines(view.text, fm, inner_w)
    source_lines = wrap_lines(view.source, small_fm, inner_w) if style.show_source else []
    marker = fallback_label if view.fallback else ""
    small_h = small_fm.height()

    text_w = max((fm.horizontalAdvance(line) for line in lines), default=0)
    width = max([text_w, small_fm.horizontalAdvance(view.speaker or ""),
                 small_fm.horizontalAdvance(marker)]
                + [small_fm.horizontalAdvance(line) for line in source_lines]) + 2 * pad  # fmt: skip
    height = (fm.height() * len(lines) + (small_h if view.speaker else 0)
              + small_h * len(source_lines) + (small_h if marker else 0) + 2 * pad)  # fmt: skip

    pixmap = QPixmap(max(1, round(width * dpr)), max(1, round(height * dpr)))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    if style.background[3] > 0:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(*style.background))
        p.drawRoundedRect(QRectF(0, 0, width, height), px * RADIUS_EM, px * RADIUS_EM)

    y = pad
    if view.speaker:
        p.setFont(small_font)
        p.setPen(QColor(style.speaker_color))
        p.drawText(QRect(0, y, width, small_h), Qt.AlignmentFlag.AlignHCenter, view.speaker)
        y += small_h
    path = QPainterPath()
    baseline = y + fm.ascent()
    for line in lines:
        path.addText(pad + (width - 2 * pad - fm.horizontalAdvance(line)) / 2, baseline,
                     text_font, line)  # fmt: skip
        baseline += fm.height()
    if style.outline:
        p.setPen(QPen(OUTLINE_COLOR, max(1.0, px * OUTLINE_EM)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(style.text_color))
    p.drawPath(path)
    y += fm.height() * len(lines)
    p.setFont(small_font)
    for line in source_lines:
        p.setPen(SOURCE_COLOR)
        p.drawText(QRect(0, y, width, small_h), Qt.AlignmentFlag.AlignHCenter, line)
        y += small_h
    if marker:
        p.setPen(MARKER_COLOR)
        p.drawText(QRect(0, y, width - pad, small_h), Qt.AlignmentFlag.AlignRight, marker)
    p.end()
    return pixmap


def place(position: str, box_w: float, box_h: float, video: QRectF, roi_top: float,
          roi_bottom: float, margin_pct: float) -> QRectF:  # fmt: skip
    """Where the pixmap goes for `bottom` and `top` (replace mode uses `replace_rect`)."""
    gap = video.height() * margin_pct / 100
    x = video.left() + (video.width() - box_w) / 2
    if position == "top":
        return QRectF(x, video.top() + gap, box_w, box_h)
    above = video.top() + roi_top * video.height() - gap - box_h
    if above >= video.top():
        return QRectF(x, above, box_w, box_h)
    below = video.top() + roi_bottom * video.height() + gap
    return QRectF(x, min(below, video.bottom() - box_h), box_w, box_h)


def text_bounds(boxes: list[tuple[int, int, int, int]], origin: tuple[int, int]
                ) -> tuple[int, int, int, int] | None:  # fmt: skip
    """Union of OCR line boxes (crop pixels) as frame pixels (x0, y0, x1, y1)."""
    if not boxes:
        return None
    ox, oy = origin
    return (ox + min(b[0] for b in boxes), oy + min(b[1] for b in boxes),
            ox + max(b[0] + b[2] for b in boxes), oy + max(b[1] + b[3] for b in boxes))  # fmt: skip


def surrounding_color(image: np.ndarray, bounds: tuple[int, int, int, int],
                      ring: int = REPLACE_SAMPLE_PX) -> tuple[int, int, int]:  # fmt: skip
    """Median BGR->RGB color of a thin ring just outside the text: what the scene behind
    the subtitle looks like, so the cover blends in (SPEC 3.7 auto_bg)."""
    h, w = image.shape[:2]
    x0, y0, x1, y1 = bounds
    ox0, oy0 = max(0, x0 - ring), max(0, y0 - ring)
    ox1, oy1 = min(w, x1 + ring), min(h, y1 + ring)
    outer = image[oy0:oy1, ox0:ox1].reshape(-1, image.shape[2]).astype(np.int32)
    mask = np.ones((oy1 - oy0, ox1 - ox0), dtype=bool)
    mask[max(0, y0 - oy0):y1 - oy0, max(0, x0 - ox0):x1 - ox0] = False
    pixels = outer[mask.reshape(-1)]
    if not len(pixels):
        return (0, 0, 0)
    b, g, r = (int(v) for v in np.median(pixels, axis=0)[:3])
    return (r, g, b)
