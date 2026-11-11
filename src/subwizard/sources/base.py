"""Video source contract (SPEC 3.1). Qt-free."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


@dataclass(frozen=True, slots=True)
class VideoMode:
    width: int
    height: int
    fps: float
    fourcc: str

    def __str__(self) -> str:
        return f"{self.width}×{self.height}@{self.fps:g} {self.fourcc}"


@dataclass(frozen=True, slots=True)
class Frame:
    image: np.ndarray  # BGR, HxWx3, uint8
    timestamp: float  # time.perf_counter() when the frame was read
    index: int


class SourceError(Exception):
    """The source could not be opened or read (message is user-facing)."""


class BaseSource(ABC):
    """A producer of frames. `read()` blocks until the next frame and is called
    from the source thread only; consumers get frames via a latest-only slot."""

    @abstractmethod
    def open(self) -> None:
        """Open the device/file. Raises SourceError with a solution-oriented message."""

    @abstractmethod
    def read(self) -> Frame | None:
        """Next frame, or None if none could be read (disconnect / end of file)."""

    @abstractmethod
    def close(self) -> None: ...

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    @property
    @abstractmethod
    def requested_mode(self) -> VideoMode: ...

    @property
    @abstractmethod
    def actual_mode(self) -> VideoMode | None:
        """What the device really delivers, read back after open()."""

    def __enter__(self) -> BaseSource:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
