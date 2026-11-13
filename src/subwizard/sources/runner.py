"""SourceRunner: the source thread. Reads frames into a LatestSlot, measures frame
timing, and reconnects with exponential backoff when the device drops (SPEC 3.1)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from enum import StrEnum

from subwizard.core.metrics import M, MetricsHub
from subwizard.i18n import t
from subwizard.sources.base import BaseSource, Frame, LatestSlot, SourceError

log = logging.getLogger(__name__)

HASH_STRIDE = 64  # sample every 64th pixel in both axes for the repeated-frame check
LATE_FACTOR = 1.5  # an interval this many times the nominal one counts as dropped frames


class SourceState(StrEnum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    ENDED = "ended"
    FAILED = "failed"


class SourceRunner(threading.Thread):
    def __init__(
        self,
        source: BaseSource,
        slot: LatestSlot,
        metrics: MetricsHub,
        *,
        on_frame: Callable[[], None],
        on_state: Callable[[SourceState, str], None],  # state, user-facing problem ("" if none)
        reconnect_initial_s: float,
        reconnect_max_s: float,
        max_read_failures: int,
    ) -> None:
        super().__init__(name="source", daemon=True)
        self._source = source
        self._slot = slot
        self._metrics = metrics
        self._on_frame = on_frame
        self._on_state = on_state
        self._initial = reconnect_initial_s
        self._max = reconnect_max_s
        self._max_failures = max_read_failures
        self._stop_event = threading.Event()
        self._last_ts: float | None = None
        self._last_hash: int | None = None

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        backoff = self._initial
        self._on_state(SourceState.CONNECTING, "")
        while not self._stop_event.is_set():
            try:
                self._source.open()
            except SourceError as exc:
                if not self._source.can_reconnect:
                    log.error("%s", exc.message)
                    self._on_state(SourceState.FAILED, exc.message)
                    return
                self._on_state(SourceState.RECONNECTING, exc.message)
                log.warning("%s (%.1f sn sonra tekrar denenecek)", exc.message, backoff)
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, self._max)
                continue

            backoff = self._initial
            self._publish_modes()
            self._on_state(SourceState.RUNNING, "")
            self._read_until_lost()
            self._source.close()
            self._slot.put(None)
            if self._source.ended:
                self._on_state(SourceState.ENDED, "")
                return
            if not self._stop_event.is_set():
                self._metrics.incr(M.SOURCE_RECONNECTS)
                self._on_state(SourceState.RECONNECTING, t("error.capture_lost"))
                log.warning("Kaynak koptu; yeniden bağlanılıyor.")
        self._on_state(SourceState.STOPPED, "")

    def _publish_modes(self) -> None:
        m = self._metrics
        requested, actual = self._source.requested_mode, self._source.actual_mode
        m.set_gauge(M.SOURCE_REQUESTED, requested)
        m.set_gauge(M.SOURCE_ACTUAL, actual)
        m.set_gauge(M.SOURCE_BACKEND, self._source.backend)
        self._last_ts = None
        self._last_hash = None

    def _read_until_lost(self) -> None:
        failures = 0
        while not self._stop_event.is_set():
            frame = self._source.read()
            if frame is None:
                if self._source.ended:
                    return
                failures += 1
                if failures >= self._max_failures:
                    return
                continue
            failures = 0
            self._measure(frame)
            self._slot.put(frame)
            self._on_frame()

    def _measure(self, frame: Frame) -> None:
        m = self._metrics
        m.tick(M.SOURCE_FPS)
        if self._last_ts is not None:
            interval = frame.timestamp - self._last_ts
            m.observe(M.SOURCE_INTERVAL_MS, interval * 1000.0)
            actual = self._source.actual_mode
            if actual is not None and actual.fps > 0:
                nominal = 1.0 / actual.fps
                if interval > LATE_FACTOR * nominal:
                    m.incr(M.SOURCE_DROPPED, round(interval / nominal) - 1)
        self._last_ts = frame.timestamp

        # A capture card that cannot keep up re-delivers the previous frame; a
        # sparse sample of the pixels is enough to notice that cheaply.
        digest = hash(frame.image[::HASH_STRIDE, ::HASH_STRIDE].tobytes())
        if digest == self._last_hash:
            m.incr(M.SOURCE_REPEATED)
        else:
            # A console sends a 30 FPS game as 60 Hz with every frame doubled: the frames
            # that really change are the game's own frame rate.
            m.tick(M.SOURCE_GAME_FPS)
        self._last_hash = digest
