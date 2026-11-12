"""A/V sync "Otomatik ölç" (SPEC 3.12 Cihaz & Ses), the measuring part. Qt-free.

While something with sharp audiovisual events plays (an A/V sync test video on the
console: white flash + beep), the app records the picture's mean brightness per frame
and the audio level per block, each with its capture time. Rising edges ("onsets") are
found in both; each video onset is paired with the nearest audio onset, and the median
difference is the offset. Audio can only be delayed (config `audio.delay_ms` >= 0), so
the suggestion is how much to delay audio when the picture arrives later.

Not yet wired to the UI: needs the capture card on Windows to be checked end to end.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

JUMP_RATIO = 0.5  # an onset rises at least half of the signal's full range at once
MIN_GAP_S = 0.25  # one event = one onset (a flash spans several frames)
MAX_OFFSET_S = 0.5  # pairs further apart are different events
MIN_PAIRS = 3  # fewer matched events = not trustworthy


@dataclass(frozen=True, slots=True)
class SyncEstimate:
    offset_ms: float  # audio onset - video onset; > 0: sound comes later than picture
    pairs: int
    spread_ms: float  # max - min of the paired differences: how consistent

    @property
    def reliable(self) -> bool:
        return self.pairs >= MIN_PAIRS

    def suggested_delay_ms(self, current_ms: int, limit_ms: int) -> int:
        """New `audio.delay_ms`: picture late (offset < 0) -> delay audio more; sound
        already late -> delay less (never below 0, audio cannot be advanced)."""
        return max(0, min(limit_ms, round(current_ms - self.offset_ms)))


def onsets(times: Sequence[float], values: Sequence[float], jump_ratio: float = JUMP_RATIO,
           min_gap_s: float = MIN_GAP_S) -> list[float]:  # fmt: skip
    """Times where the signal jumps up by at least `jump_ratio` of its range."""
    if len(values) < 2:
        return []
    span = max(values) - min(values)
    if span <= 0:
        return []
    found: list[float] = []
    for i in range(1, len(values)):
        if values[i] - values[i - 1] >= jump_ratio * span and (
                not found or times[i] - found[-1] >= min_gap_s):  # fmt: skip
            found.append(times[i])
    return found


def estimate(video: Sequence[float], audio: Sequence[float],
             max_offset_s: float = MAX_OFFSET_S) -> SyncEstimate | None:  # fmt: skip
    """Pair onsets (seconds, same clock); None when nothing pairs up."""
    diffs = []
    for v in video:
        nearest = min(audio, key=lambda a: abs(a - v), default=None)
        if nearest is not None and abs(nearest - v) <= max_offset_s:
            diffs.append((nearest - v) * 1000)
    if not diffs:
        return None
    return SyncEstimate(statistics.median(diffs), len(diffs), max(diffs) - min(diffs))
