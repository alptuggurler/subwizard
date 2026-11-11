"""CaptureCardSource: a UVC capture card through OpenCV (SPEC 3.1).

Windows: DirectShow first, Media Foundation as fallback. Other platforms use
OpenCV's default backend so the code can at least be exercised during development.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

import cv2

from subwizard.sources.base import BaseSource, Frame, SourceError, VideoMode
from subwizard.sources.video_file import fourcc_to_str
from subwizard.utils import timing

log = logging.getLogger(__name__)


def default_backends() -> list[tuple[str, int]]:
    if sys.platform == "win32":
        return [("DSHOW", cv2.CAP_DSHOW), ("MSMF", cv2.CAP_MSMF)]
    return [("default", cv2.CAP_ANY)]


class CaptureCardSource(BaseSource):
    def __init__(
        self,
        device: int | str,
        mode: VideoMode,
        buffer_size: int = 1,
        *,
        backends: list[tuple[str, int]] | None = None,
        resolve_name: Callable[[str], int | None] | None = None,
        capture_factory: Callable[[int, int], cv2.VideoCapture] = cv2.VideoCapture,
    ) -> None:
        self._device = device
        self._requested = mode
        self._buffer_size = buffer_size
        self._backends = backends if backends is not None else default_backends()
        self._resolve_name = resolve_name
        self._factory = capture_factory  # injectable so tests need no hardware
        self._cap: cv2.VideoCapture | None = None
        self._actual: VideoMode | None = None
        self._backend = ""
        self._index = 0

    def _device_index(self) -> int:
        if isinstance(self._device, int):
            return self._device
        index = self._resolve_name(self._device) if self._resolve_name else None
        if index is None:
            raise SourceError("error.capture_name_not_found", name=self._device)
        return index

    def _configure(self, cap: cv2.VideoCapture) -> VideoMode:
        # Order matters (SPEC 3.1): the pixel format must be chosen first, because
        # the resolutions and frame rates a card offers depend on it.
        want = self._requested
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*want.fourcc))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, want.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, want.height)
        cap.set(cv2.CAP_PROP_FPS, want.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, self._buffer_size)
        return VideoMode(
            int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            round(cap.get(cv2.CAP_PROP_FPS), 2),
            fourcc_to_str(cap.get(cv2.CAP_PROP_FOURCC)),
        )

    def open(self) -> None:
        index = self._device_index()
        for name, backend in self._backends:
            cap = self._factory(index, backend)
            if not cap.isOpened():
                cap.release()
                log.debug("Cihaz %s, %s ile açılamadı.", index, name)
                continue
            actual = self._configure(cap)
            # Some cards open fine while another program holds them and only fail
            # on the first read, so prove a frame actually arrives.
            ok, _ = cap.read()
            if not ok:
                cap.release()
                log.debug("Cihaz %s, %s ile açıldı ama kare vermedi.", index, name)
                continue
            self._cap, self._actual, self._backend = cap, actual, name
            if actual == self._requested:
                log.info("Capture card açıldı (%s): %s", name, actual)
            else:
                log.warning(
                    "Capture card istenen modu vermedi (%s): istenen %s → gerçekleşen %s",
                    name, self._requested, actual,
                )  # fmt: skip
            return
        raise SourceError("error.capture_not_found")

    def read(self) -> Frame | None:
        if self._cap is None:
            return None
        ok, image = self._cap.read()
        if not ok or image is None:
            return None
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
    def requested_mode(self) -> VideoMode:
        return self._requested

    @property
    def actual_mode(self) -> VideoMode | None:
        return self._actual

    @property
    def backend(self) -> str:
        return self._backend


def mode_hints(requested: VideoMode | None, actual: VideoMode | None) -> list[str]:
    """i18n keys explaining a requested/actual mismatch, most likely cause first."""
    if requested is None or actual is None or requested == actual:
        return []
    hints = []
    if actual.fourcc.upper() != requested.fourcc.upper():
        hints.append("hint.select_mjpg")
    if actual.fps < requested.fps or (actual.width, actual.height) != (
        requested.width, requested.height
    ):  # fmt: skip
        hints.append("hint.usb3_port")
    return hints or ["hint.mode_unsupported"]
