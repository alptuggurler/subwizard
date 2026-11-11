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
