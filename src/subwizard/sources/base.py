"""Video source contract (SPEC 3.1). Qt-free."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from subwizard.core.errors import UserError

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
    timestamp: float  # timing.now() when the frame was read
    index: int


class SourceError(UserError):
    """The source could not be opened (carries a solution-oriented i18n message)."""


class LatestSlot:
    """Latest-only hand-off between threads (SPEC 2): a newer frame replaces an
    unread one, so stale frames can never queue up behind a slow consumer."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: Frame | None = None

    def put(self, frame: Frame | None) -> None:
        with self._lock:
            self._frame = frame

    def get(self) -> Frame | None:
        with self._lock:
            return self._frame


class BaseSource(ABC):
    """A producer of frames. `read()` blocks until the next frame and is called
    from the source thread only; consumers get frames via a LatestSlot."""

    @abstractmethod
    def open(self) -> None:
        """Open the device/file. Raises SourceError."""

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
    def requested_mode(self) -> VideoMode | None:
        """What was asked of the device (None when there is nothing to ask, e.g. a file)."""

    @property
    @abstractmethod
    def actual_mode(self) -> VideoMode | None:
        """What the source really delivers, read back after open()."""

    @property
    def backend(self) -> str:
        return ""

    @property
    def ended(self) -> bool:
        """True once a finite source is exhausted; a live device never ends."""
        return False

    @property
    def can_reconnect(self) -> bool:
        """Whether a failed open is worth retrying (a device may come back; a
        missing file will not)."""
        return True

    def __enter__(self) -> BaseSource:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
