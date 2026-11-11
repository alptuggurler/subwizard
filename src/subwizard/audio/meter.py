"""LevelMeter: peak level per channel in dBFS, clipping and "no audio" detection
(SPEC 3.2.1). Qt-free, device-free."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from subwizard.core.metrics import M, MetricsHub
from subwizard.utils import timing

FLOOR_DB = -120.0  # reported for digital silence instead of -inf


def to_db(amplitude: float) -> float:
    if amplitude <= 0:
        return FLOOR_DB
    return max(FLOOR_DB, 20.0 * float(np.log10(amplitude)))


def db_to_gain(db: float) -> float:
    return float(10.0 ** (db / 20.0))


class LevelMeter:
    def __init__(
        self,
        metrics: MetricsHub,
        silence_db: float,
        silence_s: float,
        clip_db: float,
        clock: Callable[[], float] = timing.now,
    ) -> None:
        self._metrics = metrics
        self._silence_db = silence_db
        self._silence_s = silence_s
        self._clip_db = clip_db
        self._clock = clock
        self._quiet_since: float | None = None

    def process(self, block: np.ndarray) -> None:
        """Feed one input block (frames × channels, float32 in [-1, 1])."""
        peaks = np.abs(block).max(axis=0) if len(block) else np.zeros(block.shape[1])
        left = to_db(float(peaks[0]))
        right = to_db(float(peaks[-1]))  # mono: both meters show the one channel
        loudest = max(left, right)

        now = self._clock()
        if loudest < self._silence_db:
            if self._quiet_since is None:
                self._quiet_since = now
        else:
            self._quiet_since = None
        silent = self._quiet_since is not None and now - self._quiet_since >= self._silence_s

        m = self._metrics
        m.set_gauge(M.AUDIO_LEVEL_L, left)
        m.set_gauge(M.AUDIO_LEVEL_R, right)
        m.set_gauge(M.AUDIO_SILENT, silent)
        m.set_gauge(M.AUDIO_CLIPPING, loudest >= self._clip_db)
