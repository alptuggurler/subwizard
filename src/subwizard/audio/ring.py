"""DelayBuffer: the FIFO between the capture-card input stream and the output
stream. Its fill level *is* the audio delay, which is how `audio.delay_ms` and the
latency profiles are applied live (SPEC 3.2). Qt-free, device-free, thread-safe.
"""

from __future__ import annotations

import threading

import numpy as np

CAPACITY_HEADROOM_S = 1.0  # ring capacity beyond the largest allowed target


class DelayBuffer:
    def __init__(
        self,
        channels: int,
        sample_rate: int,
        target_ms: float,
        slack_ms: float,
        max_target_ms: float,
    ) -> None:
        self._rate = sample_rate
        self._channels = channels
        capacity = int((max_target_ms / 1000.0 + CAPACITY_HEADROOM_S) * sample_rate)
        self._ring = np.zeros((capacity, channels), dtype=np.float32)
        self._capacity = capacity
        self._read = 0
        self._fill = 0
        self._primed = False
        self._lock = threading.Lock()
        self._slack = self._frames(slack_ms)
        self._target = self._frames(target_ms)
        self.underruns = 0
        self.overruns = 0

    def _frames(self, ms: float) -> int:
        return max(0, int(ms / 1000.0 * self._rate))

    def set_target_ms(self, target_ms: float) -> None:
        """Change the delay while running. Lowering it drops the excess at once;
        raising it plays silence until the buffer has refilled."""
        with self._lock:
            self._target = min(self._frames(target_ms), self._capacity - self._slack - 1)
            if self._fill < self._target:
                self._primed = False
            elif self._fill > self._target:
                self._drop(self._fill - self._target)

    @property
    def fill_ms(self) -> float:
        with self._lock:
            return self._fill * 1000.0 / self._rate

    @property
    def target_ms(self) -> float:
        return self._target * 1000.0 / self._rate

    def _drop(self, frames: int) -> None:
        self._read = (self._read + frames) % self._capacity
        self._fill -= frames

    def write(self, block: np.ndarray) -> None:
        frames = len(block)
        with self._lock:
            if frames >= self._capacity:  # cannot happen with sane block sizes
                block = block[-(self._capacity - 1) :]
                frames = len(block)
            # The output clock running slower than the input clock makes the delay
            # creep up; once past the slack, drop back to the target.
            if self._fill > self._target + self._slack:
                self._drop(self._fill - self._target)
                self.overruns += 1
            if self._fill + frames > self._capacity:
                self._drop(self._fill + frames - self._capacity)
            start = (self._read + self._fill) % self._capacity
            first = min(frames, self._capacity - start)
            self._ring[start : start + first] = block[:first]
            if first < frames:
                self._ring[: frames - first] = block[first:]
            self._fill += frames

    def read(self, frames: int) -> np.ndarray:
        """Always returns exactly `frames` frames; silence where audio is missing."""
        out = np.zeros((frames, self._channels), dtype=np.float32)
        with self._lock:
            if not self._primed:
                if self._fill < max(self._target, frames):
                    return out  # still filling up to the target delay
                self._primed = True
            if self._fill < frames:
                # Input fell behind: play what is left, then rebuild the delay.
                self.underruns += 1
                self._primed = False
                frames = self._fill
            first = min(frames, self._capacity - self._read)
            out[:first] = self._ring[self._read : self._read + first]
            if first < frames:
                out[first:frames] = self._ring[: frames - first]
            self._drop(frames)
        return out
