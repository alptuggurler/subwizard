"""Windows' built-in OCR (Windows.Media.Ocr via pywinrt; SPEC M7, experimental).

No model download, uses the OCR language packs Windows already has. Not run on macOS:
unverified until checked on Windows. Windows OCR gives no confidence, so every line
gets 1.0 (`ocr.min_confidence` has no effect with this engine).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from subwizard.ocr.base import BaseOCR, OCRLine, OCRResult

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger(__name__)

LANGUAGES = ("en-US", "en-GB")  # subtitles are English; else the user's profile languages


class WindowsOCRUnavailable(Exception):
    """No WinRT or no usable OCR language pack."""


def to_bgra(image: np.ndarray) -> np.ndarray:
    """Gray / BGR / BGRA uint8 -> contiguous BGRA (what the SoftwareBitmap is made from)."""
    import cv2
    import numpy as np

    if image.ndim == 2:
        out = cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
    elif image.shape[2] == 3:
        out = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    else:
        out = image
    return np.ascontiguousarray(out)


def fit(image: np.ndarray, max_side: int) -> tuple[np.ndarray, float]:
    """Shrink to the engine's limit; returns the image and the factor applied (<= 1)."""
    import cv2

    h, w = image.shape[:2]
    if max(h, w) <= max_side:
        return image, 1.0
    f = max_side / max(h, w)
    return cv2.resize(image, (max(1, round(w * f)), max(1, round(h * f))),
                      interpolation=cv2.INTER_AREA), f  # fmt: skip


def line_box(rects: list[tuple[float, float, float, float]], scale: float
             ) -> tuple[int, int, int, int]:  # fmt: skip
    """Union of word rects (x, y, width, height) -> (x0, y0, x1, y1) in crop pixels."""
    if not rects:
        return (0, 0, 0, 0)
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    return (round(x0 / scale), round(y0 / scale), round(x1 / scale), round(y1 / scale))


class WindowsOCR(BaseOCR):
    name = "windows"

    def __init__(self) -> None:
        try:
            from winrt.windows.globalization import Language
            from winrt.windows.media.ocr import OcrEngine
        except ImportError as exc:
            raise WindowsOCRUnavailable(f"pywinrt yok: {exc}") from exc
        engine = None
        for tag in LANGUAGES:
            lang = Language(tag)
            if OcrEngine.is_language_supported(lang):
                engine = OcrEngine.try_create_from_language(lang)
                if engine is not None:
                    break
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise WindowsOCRUnavailable("Windows'ta OCR dil paketi yok")
        self._engine = engine
        self._max_side = int(OcrEngine.max_image_dimension)
        self._loop = asyncio.new_event_loop()
        log.info("Windows OCR: %s", engine.recognizer_language.language_tag)

    def recognize(self, image: np.ndarray, scale: float = 1.0) -> OCRResult:
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter

        bgra, f = fit(to_bgra(image), self._max_side)
        writer = DataWriter()
        writer.write_bytes(bgra.tobytes())
        h, w = bgra.shape[:2]
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.BGRA8, w, h
        )

        async def run():  # noqa: ANN202 - WinRT OcrResult
            return await self._engine.recognize_async(bitmap)

        result = self._loop.run_until_complete(run())
        lines = []
        for line in result.lines:
            rects = [(r.x, r.y, r.width, r.height) for r in (wd.bounding_rect for wd in line.words)]
            lines.append(OCRLine(line.text, 1.0, line_box(rects, scale * f)))
        return OCRResult(tuple(lines))

    def close(self) -> None:
        self._loop.close()
