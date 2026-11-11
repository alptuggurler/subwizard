"""VideoFileSource: plays a recorded clip at real-time speed (SPEC 3.1).

Required for development and tests: the whole pipeline runs without a console.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import cv2

from subwizard.sources.base import BaseSource, Frame, SourceError, VideoMode
from subwizard.utils import timing

log = logging.getLogger(__name__)

FALLBACK_FPS = 30.0  # for files whose container reports no frame rate
RESYNC_AFTER_FRAMES = 15  # if playback falls this far behind, restart the schedule


def fourcc_to_str(value: float) -> str:
    code = int(value)
    text = "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4))
    return text if text.isascii() and text.isprintable() and text.strip() else "?"


class VideoFileSource(BaseSource):
    def __init__(self, path: Path, loop: bool = True) -> None:
        self._path = Path(path)
        self._loop = loop
        self._cap: cv2.VideoCapture | None = None
        self._mode: VideoMode | None = None
        self._interval = 1.0 / FALLBACK_FPS
        self._next_due: float | None = None
        self._index = 0
        self._ended = False

    def open(self) -> None:
        if not self._path.is_file():
            raise SourceError("error.file_not_found", path=self._path)
        cap = cv2.VideoCapture(str(self._path))
        if not cap.isOpened():
            cap.release()
            raise SourceError("error.file_unreadable", path=self._path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps != fps or fps <= 0:  # 0 or NaN
            fps = FALLBACK_FPS
        self._mode = VideoMode(
            int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            round(fps, 2),
            fourcc_to_str(cap.get(cv2.CAP_PROP_FOURCC)),
        )
        self._interval = 1.0 / fps
        self._cap = cap
        self._next_due = None
        self._ended = False
        log.info("Video dosyası açıldı: %s (%s)", self._path, self._mode)

    def read(self) -> Frame | None:
        cap = self._cap
        if cap is None or self._ended:
            return None
        ok, image = cap.read()
        if not ok:
            if not self._loop:
                self._ended = True
                return None
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, image = cap.read()
            if not ok:
                self._ended = True
                return None

        # Pace against a schedule rather than sleeping a fixed interval, so decode
        # time does not slow playback down.
        now = timing.now()
        if self._next_due is None or now - self._next_due > RESYNC_AFTER_FRAMES * self._interval:
            self._next_due = now
        wait = self._next_due - now
        if wait > 0:
            time.sleep(wait)
        self._next_due += self._interval

        frame = Frame(image, timing.now(), self._index)
        self._index += 1
        return frame

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    def is_open(self) -> bool:
        return self._cap is not None

    @property
    def requested_mode(self) -> VideoMode | None:
        return None

    @property
    def actual_mode(self) -> VideoMode | None:
        return self._mode

    @property
    def backend(self) -> str:
        return "file"

    @property
    def ended(self) -> bool:
        return self._ended

    @property
    def can_reconnect(self) -> bool:
        return False
