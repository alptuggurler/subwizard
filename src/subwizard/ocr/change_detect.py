"""Skip OCR when the region has not changed since the last tick (SPEC 3.3). Qt-free.

Compares a small thumbnail of the region with the previous one; this costs well under
a millisecond, while an OCR call costs ~100 ms.

With `bright_min` set, the thumbnail is built from the bright pixels only (where
subtitle letters are), so a game scene moving behind the subtitle is not a change.

Two measures, either one is a change:
- the mean difference over the whole thumbnail (a big change anywhere), and
- the largest mean difference of one small cell (`CELL_PX` thumbnail pixels square).
  A short line ("Get down!") in a wide region barely moves the whole-region mean
  (0.6 in a 1920x540 region, measured) but fills a few cells completely (~28).
"""

from __future__ import annotations

import cv2
import numpy as np

CELL_PX = 8  # thumbnail pixels per cell side (160 px thumbnail -> 20 cells across)


class ChangeDetector:
    def __init__(
        self, threshold: float, thumb_width: int, bright_min: int | None = None,
        cell_threshold: float | None = None,
    ) -> None:  # fmt: skip
        self._threshold = threshold  # mean absolute difference, 0..255
        self._cell_threshold = cell_threshold  # None = whole-region mean only
        self._thumb_width = thumb_width
        self._bright_min = bright_min
        self._previous: np.ndarray | None = None
        self.last_diff = 0.0
        self.last_cell_diff = 0.0

    def reset(self) -> None:
        """Forget the previous thumbnail; the next region always counts as changed."""
        self._previous = None

    def _thumb(self, image: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        if self._bright_min is not None:
            _, gray = cv2.threshold(gray, self._bright_min - 1, 255, cv2.THRESH_BINARY)
        height, width = gray.shape[:2]
        thumb_h = max(1, round(height * self._thumb_width / max(1, width)))
        return cv2.resize(gray, (self._thumb_width, thumb_h), interpolation=cv2.INTER_AREA)

    def changed(self, image: np.ndarray) -> bool:
        thumb = self._thumb(image)
        previous, self._previous = self._previous, thumb
        if previous is None or previous.shape != thumb.shape:
            self.last_diff = self.last_cell_diff = 255.0
            return True
        diff = cv2.absdiff(thumb, previous)
        self.last_diff = float(diff.mean())
        height, width = diff.shape[:2]
        cells = cv2.resize(diff.astype(np.float32),
                           (max(1, width // CELL_PX), max(1, height // CELL_PX)),
                           interpolation=cv2.INTER_AREA)  # fmt: skip
        self.last_cell_diff = float(cells.max())
        if self.last_diff >= self._threshold:
            return True
        return self._cell_threshold is not None and self.last_cell_diff >= self._cell_threshold
