"""Vision mode in the live pipeline (SPEC 3.6, M7, experimental). Qt-free.

`VisionOCR` stands in for the OCR engine: the OCR worker still crops the region and
calls it only when the change detector fires, but instead of local OCR the image goes to
Gemini, which reads *and* translates it. The English it read flows on as the OCR text
(state machine, history, overlay unchanged); the translation is handed over so the
translator thread uses it instead of making a second request.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from typing import TYPE_CHECKING

from subwizard.ocr.base import BaseOCR, OCRLine, OCRResult
from subwizard.translator.base import TranslationError, TranslationResult

if TYPE_CHECKING:
    import numpy as np

    from subwizard.translator.gemini import GeminiTranslator

log = logging.getLogger(__name__)

JPEG_QUALITY = 90  # smaller than PNG; subtitle text stays sharp
HANDOFF_MAX = 32  # translations read but not (yet) asked for; old ones are dropped


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


class VisionHandoff:
    """Translations produced together with the reading, waiting for the translator."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, TranslationResult] = {}

    def put(self, source: str, result: TranslationResult) -> None:
        with self._lock:
            self._items[_norm(source)] = result
            while len(self._items) > HANDOFF_MAX:
                self._items.pop(next(iter(self._items)))

    def take(self, source: str) -> TranslationResult | None:
        with self._lock:
            return self._items.pop(_norm(source), None)


HANDOFF = VisionHandoff()  # one per process: OCR thread puts, translator thread takes
# Set while translation is turned off (F8): the image is not sent to Gemini (it is a paid
# request that would read *and* translate), so nothing is read in vision mode meanwhile.
PAUSED = threading.Event()


class VisionOCR(BaseOCR):
    name = "vision"

    def __init__(self, gemini: GeminiTranslator, handoff: VisionHandoff = HANDOFF) -> None:
        self._gemini = gemini
        self._handoff = handoff

    def recognize(self, image: np.ndarray, scale: float = 1.0) -> OCRResult:
        if PAUSED.is_set():
            return OCRResult()
        import cv2

        ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            return OCRResult()
        try:
            read = self._gemini.translate_image(jpeg.tobytes(), "image/jpeg")
        except TranslationError as exc:
            log.warning("Görsel mod okuyamadı (%s)", exc)
            return OCRResult()
        if not read.source:
            return OCRResult()
        if read.translation:
            self._handoff.put(read.source, replace(read.result, text=read.translation))
        height, width = image.shape[:2]
        log.info("Görsel mod (%.0f ms, %d+%d token): %r -> %r", read.result.latency_ms,
                 read.result.input_tokens, read.result.output_tokens, read.source,
                 read.translation)  # fmt: skip
        return OCRResult((OCRLine(read.source, 1.0, (0, 0, width, height)),))
