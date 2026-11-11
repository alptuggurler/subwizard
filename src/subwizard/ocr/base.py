"""OCR engine contract (SPEC 3.3–3.4). Qt-free."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


@dataclass(frozen=True, slots=True)
class OCRLine:
    text: str
    confidence: float  # 0..1
    box: tuple[int, int, int, int]  # x, y, w, h in ROI pixels (used by `replace` overlay mode)


@dataclass(frozen=True, slots=True)
class OCRResult:
    lines: tuple[OCRLine, ...] = field(default=())

    @property
    def text(self) -> str:
        return " ".join(line.text for line in self.lines)

    @property
    def confidence(self) -> float:
        if not self.lines:
            return 0.0
        return sum(line.confidence for line in self.lines) / len(self.lines)


class BaseOCR(ABC):
    name: str

    @abstractmethod
    def recognize(self, image: np.ndarray) -> OCRResult:
        """Read text from an already cropped + preprocessed ROI image."""

    def close(self) -> None:  # engines holding native resources override this
        return None
