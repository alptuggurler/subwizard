"""Subtitle region (ROI): templates, proportional rectangles, cropping (SPEC 3.3). Qt-free.

Rectangles are stored as fractions of the frame (x, y, w, h in 0..1), so a region
stays valid when the capture resolution changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

MIN_SIZE = 0.02  # a region smaller than 2% of the frame in either direction is a misclick


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @classmethod
    def from_points(cls, x0: float, y0: float, x1: float, y1: float) -> Rect:
        """From two corners in any order, clamped to the frame."""
        left, right = sorted((min(max(x0, 0.0), 1.0), min(max(x1, 0.0), 1.0)))
        top, bottom = sorted((min(max(y0, 0.0), 1.0), min(max(y1, 0.0), 1.0)))
        return cls(left, top, right - left, bottom - top)

    def as_list(self) -> list[float]:
        return [round(v, 4) for v in (self.x, self.y, self.w, self.h)]

    @property
    def usable(self) -> bool:
        return self.w >= MIN_SIZE and self.h >= MIN_SIZE

    def padded(self, padding_pct: float) -> Rect:
        """Grow by `padding_pct` % of the region's own size on every side, so words
        at the edge of a tight selection are not cut off; clamped to the frame."""
        dx, dy = self.w * padding_pct / 100.0, self.h * padding_pct / 100.0
        return Rect.from_points(self.x - dx, self.y - dy, self.x + self.w + dx, self.y + self.h + dy)

    def to_pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        """(x0, y0, x1, y1) pixel bounds, at least 1 px each way."""
        x0, y0 = int(self.x * width), int(self.y * height)
        x1 = max(x0 + 1, min(width, round((self.x + self.w) * width)))
        y1 = max(y0 + 1, min(height, round((self.y + self.h) * height)))
        return min(x0, width - 1), min(y0, height - 1), x1, y1


# Ready-made regions (SPEC 3.3). `custom` is whatever the user drew.
TEMPLATES: dict[str, Rect] = {
    "bottom_center": Rect(0.15, 0.78, 0.70, 0.18),
    "bottom_wide": Rect(0.05, 0.70, 0.90, 0.28),
    "lower_half": Rect(0.0, 0.50, 1.0, 0.50),
    "top": Rect(0.10, 0.02, 0.80, 0.16),
}
TEMPLATE_NAMES = (*TEMPLATES, "custom")


def crop(image: np.ndarray, rect: Rect, padding_pct: float) -> np.ndarray:
    """The padded region of a frame (a view, not a copy)."""
    height, width = image.shape[:2]
    x0, y0, x1, y1 = rect.padded(padding_pct).to_pixels(width, height)
    return image[y0:y1, x0:x1]


# --- several regions, excluded areas (SPEC 3.12 "çoklu bölge, hariç bölgeler") ---------
# Everything is still read in one OCR call, but the regions are cut out and stacked one
# under the other instead of using their bounding box. Measured on the test clip (Mac,
# RapidOCR, 20 interleaved runs, median): subtitle region alone 1452x211 px 207 ms;
# subtitle + a region at the top stacked 1659x367 px 496 ms; their bounding box
# 1536x1017 px 751 ms. An extra region still costs time (more text to recognise), so
# add one only when needed. Excluded areas (a minimap, a HUD counter) are painted black
# and cost nothing measurable (175 ms). OCR boxes map back to the frame with `to_frame`.
MASK_VALUE = 0  # black: subtitles are light text, so black reads as "nothing here"
STRIP_GAP_PX = 16  # black rows between stacked regions, so lines never merge


@dataclass(frozen=True, slots=True)
class Strip:
    """One region in the stacked OCR image: its frame pixels and where it starts."""

    x0: int
    y0: int
    x1: int
    y1: int
    top: int  # first row in the stacked image


def layout(main: Rect, extras: list[Rect], padding_pct: float, width: int,
           height: int) -> list[Strip]:  # fmt: skip
    strips, top = [], 0
    for rect in (main, *extras):
        x0, y0, x1, y1 = rect.padded(padding_pct).to_pixels(width, height)
        strips.append(Strip(x0, y0, x1, y1, top))
        top += (y1 - y0) + STRIP_GAP_PX
    return strips


def crop_regions(
    image: np.ndarray, main: Rect, extras: list[Rect], excludes: list[Rect], padding_pct: float,
) -> np.ndarray:  # fmt: skip
    """`crop()` for several regions minus excluded areas. With neither, exactly `crop()`
    (a view, no copy); otherwise a new image, so the shared frame is never changed."""
    if not extras and not excludes:
        return crop(image, main, padding_pct)
    import numpy as np

    height, width = image.shape[:2]
    strips = layout(main, extras, padding_pct, width, height)
    out_h = strips[-1].top + (strips[-1].y1 - strips[-1].y0)
    out_w = max(s.x1 - s.x0 for s in strips)
    out = np.full((out_h, out_w, *image.shape[2:]), MASK_VALUE, dtype=image.dtype)
    holes = [r.to_pixels(width, height) for r in excludes]
    for s in strips:
        part = out[s.top:s.top + (s.y1 - s.y0), :s.x1 - s.x0]
        part[:] = image[s.y0:s.y1, s.x0:s.x1]
        for hx0, hy0, hx1, hy1 in holes:
            ix0, iy0, ix1, iy1 = max(hx0, s.x0), max(hy0, s.y0), min(hx1, s.x1), min(hy1, s.y1)
            if ix0 < ix1 and iy0 < iy1:
                part[iy0 - s.y0:iy1 - s.y0, ix0 - s.x0:ix1 - s.x0] = MASK_VALUE
    return out


def config_layout(roi: object, width: int, height: int) -> list[Strip]:
    """`layout` for a RoiConfig (`rect`, `extra_rects`, `padding_pct`)."""
    return layout(Rect(*roi.rect), [Rect(*r) for r in roi.extra_rects],  # type: ignore[attr-defined]
                  roi.padding_pct, width, height)  # type: ignore[attr-defined]  # fmt: skip


def to_frame(box: tuple[int, int, int, int], strips: list[Strip]) -> tuple[int, int, int, int]:
    """An OCR box (x, y, w, h in the OCR image) in frame pixels."""
    x, y, w, h = box
    middle = y + h / 2
    strip = next((s for s in reversed(strips) if middle >= s.top), strips[0])
    return x + strip.x0, y - strip.top + strip.y0, w, h
