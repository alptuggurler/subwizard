"""OCRWorker: reads the subtitle region a few times per second on its own thread
(SPEC 3.4). Qt-free; results leave through a callback the controller turns into a signal.

Each tick: newest frame -> crop region -> skip if unchanged -> preprocess -> OCR ->
drop low-confidence lines -> normalise. The render loop never waits on any of it.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np

from subwizard.config import AppConfig
from subwizard.core.metrics import M, MetricsHub
from subwizard.ocr.base import BaseOCR, OCRLine, OCRResult
from subwizard.ocr.change_detect import ChangeDetector
from subwizard.ocr.preprocess import preprocess
from subwizard.ocr.roi import Rect, crop_regions
from subwizard.pipeline.text_utils import join_lines
from subwizard.sources.base import Frame
from subwizard.utils import timing

log = logging.getLogger(__name__)

SKIP_LOG_EVERY_S = 10.0  # summary line "N of M ticks skipped" at INFO


class OCRState(StrEnum):
    OFF = "off"
    LOADING = "loading"  # models are loading (first start takes a moment)
    RUNNING = "running"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class OCRTick:
    frame_index: int
    frame_timestamp: float
    text: str  # normalised text currently in the region ("" = no subtitle)
    confidence: float
    lines: tuple[OCRLine, ...]
    skipped: bool  # region unchanged: previous result reused, no OCR call
    ms: float  # time spent on this tick (crop + OCR), 0 when skipped
    crop: np.ndarray = field(repr=False)
    processed: np.ndarray | None = field(default=None, repr=False)


def filter_lines(result: OCRResult, min_confidence: float) -> tuple[OCRLine, ...]:
    return tuple(line for line in result.lines if line.confidence >= min_confidence)


class OCRWorker(threading.Thread):
    def __init__(
        self,
        config: AppConfig,
        metrics: MetricsHub,
        latest_frame: Callable[[], Frame | None],
        *,
        engine_factory: Callable[[AppConfig], BaseOCR],
        on_tick: Callable[[OCRTick], None],
        on_state: Callable[[OCRState, str], None],
    ) -> None:
        super().__init__(name="ocr", daemon=True)
        self._config = config
        self._metrics = metrics
        self._latest_frame = latest_frame
        self._engine_factory = engine_factory
        self._on_tick = on_tick
        self._on_state = on_state
        self._stop_event = threading.Event()
        self._force = threading.Event()
        self._last_index = -1
        self._last_rect: tuple[object, ...] | None = None
        self._lines: tuple[OCRLine, ...] = ()
        self._ticks = self._skips = 0
        self._summary_at = timing.now()
        ocr = config.ocr
        self._detector = ChangeDetector(
            ocr.change_threshold, ocr.change_thumb_width,
            ocr.change_bright_min if ocr.change_mode == "bright" else None,
        )  # fmt: skip

    def stop(self) -> None:
        self._stop_event.set()

    def force(self) -> None:
        """Run OCR on the next tick even if the region looks unchanged."""
        self._force.set()

    def run(self) -> None:
        self._on_state(OCRState.LOADING, "")
        try:
            with timing.Stopwatch() as sw:
                engine = self._engine_factory(self._config)
        except Exception as exc:  # missing model files, onnxruntime load errors
            log.exception("OCR motoru başlatılamadı")
            self._on_state(OCRState.ERROR, str(exc))
            return
        log.info("OCR motoru hazır (%s, %.0f ms)", engine.name, sw.ms)
        self._on_state(OCRState.RUNNING, "")
        try:
            while not self._stop_event.is_set():
                started = timing.now()
                try:
                    self._tick(engine)
                except Exception:
                    # One bad frame must not end OCR for the session.
                    log.exception("OCR adımı başarısız")
                period = 1.0 / self._config.ocr.hz
                self._stop_event.wait(max(0.0, period - (timing.now() - started)))
        finally:
            engine.close()
            self._on_state(OCRState.OFF, "")

    def _tick(self, engine: BaseOCR) -> None:
        frame = self._latest_frame()
        if frame is None or frame.index == self._last_index:
            return  # no new picture since the last tick
        self._last_index = frame.index
        cfg = self._config
        m = self._metrics
        m.tick(M.OCR_TICKS)

        roi = cfg.roi
        key = (repr(roi.rect), repr(roi.extra_rects), repr(roi.exclude_rects), roi.padding_pct)
        if key != self._last_rect:
            self._last_rect = key
            self._detector.reset()
        region = crop_regions(frame.image, Rect(*roi.rect), [Rect(*r) for r in roi.extra_rects],
                              [Rect(*r) for r in roi.exclude_rects], roi.padding_pct)  # fmt: skip

        forced = self._force.is_set()
        self._force.clear()
        self._ticks += 1
        if not forced and not self._detector.changed(region):
            self._skips += 1
            m.incr(M.OCR_SKIPPED)
            self._summarise()
            self._emit(frame, region, None, skipped=True, ms=0.0)
            return

        with timing.Stopwatch() as sw:
            prep_cfg = cfg.ocr.preprocess.get(engine.name) or next(iter(cfg.ocr.preprocess.values()))
            processed = preprocess(region, prep_cfg)
            scale = processed.shape[0] / region.shape[0] if region.shape[0] else 1.0
            result = engine.recognize(processed, scale=scale if scale > 1 else 1.0)
            self._lines = filter_lines(result, cfg.ocr.min_confidence)
        m.tick(M.OCR_RUNS)
        m.observe(M.OCR_MS, sw.ms)
        self._summarise()
        self._emit(frame, region, processed, skipped=False, ms=sw.ms)

    def _emit(
        self, frame: Frame, region: np.ndarray, processed: np.ndarray | None, *,
        skipped: bool, ms: float,
    ) -> None:  # fmt: skip
        lines = self._lines
        text = join_lines([line.text for line in lines])
        confidence = sum(l.confidence for l in lines) / len(lines) if lines else 0.0  # noqa: E741
        self._metrics.set_gauge(M.OCR_TEXT, text)
        self._metrics.set_gauge(M.OCR_CONFIDENCE, confidence)
        if not skipped:
            log.debug("OCR %.0f ms, güven %.2f: %r", ms, confidence, text)
        self._on_tick(OCRTick(frame.index, frame.timestamp, text, confidence, lines, skipped,
                              ms, region, processed))  # fmt: skip

    def _summarise(self) -> None:
        now = timing.now()
        if now - self._summary_at < SKIP_LOG_EVERY_S:
            return
        if self._ticks:
            log.info(
                "OCR: son %.0f sn'de bakılan kare %d, değişim olmadığı için atlanan %d (%%%.0f)",
                now - self._summary_at, self._ticks, self._skips, 100 * self._skips / self._ticks,
            )  # fmt: skip
        self._summary_at, self._ticks, self._skips = now, 0, 0


def build_ocr(config: AppConfig) -> BaseOCR:
    from subwizard.ocr.rapid import RapidOCREngine

    ocr = config.ocr
    return RapidOCREngine(det_max_side=ocr.det_max_side, threads=ocr.threads)


__all__ = ["OCRState", "OCRTick", "OCRWorker", "build_ocr", "filter_lines"]
