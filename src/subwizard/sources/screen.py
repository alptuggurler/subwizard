"""ScreenSource: a monitor of this PC as the video source (PC games; SPEC Faz 2). Qt-free.

The game itself draws the picture, so frames are only for OCR (and the Home page
preview): a modest rate is enough and keeps the CPU free for the game. mss grabs
through GDI on Windows; our own overlay window is excluded from capture there
(`platform.windows.capture_exclusion`), so OCR never reads the Turkish text back.
macOS (development only) needs the Screen Recording permission, otherwise only the
wallpaper comes through.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from subwizard.sources.base import BaseSource, Frame, SourceError, VideoMode
from subwizard.utils import timing

log = logging.getLogger(__name__)

RESYNC_AFTER_FRAMES = 5  # fell this far behind (slow grab): restart the schedule


def _mss() -> Any:  # noqa: ANN401 - mss.MSS
    import mss

    return mss.MSS() if hasattr(mss, "MSS") else mss.mss()


def list_monitors(factory: Callable[[], Any] = _mss) -> list[dict[str, int]]:
    """Physical monitors (left, top, width, height); index 0 here = monitor 1."""
    try:
        with factory() as grabber:
            return [dict(m) for m in grabber.monitors[1:]]
    except Exception as exc:  # no display, permission
        log.warning("Ekranlar listelenemedi: %s", exc)
        return []


class ScreenSource(BaseSource):
    def __init__(self, monitor: int = 1, fps: float = 10.0,
                 factory: Callable[[], Any] = _mss) -> None:  # fmt: skip
        self._monitor_no = monitor  # 1-based, like the monitor numbers Windows shows
        self._fps = fps
        self._factory = factory
        self._grabber: Any = None
        self._region: dict[str, int] | None = None
        self._mode: VideoMode | None = None
        self._interval = 1.0 / fps
        self._next_due: float | None = None
        self._index = 0

    def open(self) -> None:
        # mss keeps per-thread handles: opened (and read) on the source thread.
        try:
            grabber = self._factory()
        except Exception as exc:
            raise SourceError("error.screen_unavailable", error=exc) from exc
        monitors = grabber.monitors[1:]
        if not 1 <= self._monitor_no <= len(monitors):
            grabber.close()
            raise SourceError("error.screen_not_found", monitor=self._monitor_no,
                              count=len(monitors))  # fmt: skip
        self._region = dict(monitors[self._monitor_no - 1])
        self._grabber = grabber
        first = self._grab()
        if first is None:
            self.close()
            raise SourceError("error.screen_unavailable", error="grab failed")
        h, w = first.shape[:2]
        self._mode = VideoMode(w, h, float(self._fps), "BGRA")
        self._next_due = None
        log.info("Ekran kaynağı açıldı: ekran %d %s (%s)", self._monitor_no, self._region,
                 self._mode)  # fmt: skip

    def _grab(self):  # noqa: ANN202 - np.ndarray | None
        import cv2
        import numpy as np

        try:
            shot = self._grabber.grab(self._region)
        except Exception as exc:  # mss.ScreenShotError: display changed, session locked
            log.warning("Ekran yakalanamadı: %s", exc)
            return None
        return cv2.cvtColor(np.asarray(shot), cv2.COLOR_BGRA2BGR)  # contiguous BGR

    def read(self) -> Frame | None:
        if self._grabber is None:
            return None
        now = timing.now()
        if self._next_due is None or now - self._next_due > RESYNC_AFTER_FRAMES * self._interval:
            self._next_due = now
        delay = self._next_due - now
        if delay > 0:
            time.sleep(delay)
        self._next_due += self._interval
        image = self._grab()
        if image is None:
            return None
        self._index += 1
        return Frame(image, timing.now(), self._index)

    def close(self) -> None:
        if self._grabber is not None:
            try:
                self._grabber.close()
            finally:
                self._grabber = None

    @property
    def is_open(self) -> bool:
        return self._grabber is not None

    @property
    def requested_mode(self) -> VideoMode | None:
        return None  # nothing is asked of a monitor

    @property
    def actual_mode(self) -> VideoMode | None:
        return self._mode

    @property
    def backend(self) -> str:
        return "mss"

    @property
    def region(self) -> dict[str, int] | None:
        """The captured monitor in physical pixels (the overlay window covers it)."""
        return self._region
