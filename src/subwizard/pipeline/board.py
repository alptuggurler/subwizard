"""SubtitleBoard: what translation is on screen right now (SPEC 3.5, 3.7). Qt-free.

Fed by the state machine (OCR thread) and the translator (its own thread), so every
method takes the lock. Methods return True when the shown subtitle changed; the caller
then tells the UI.

- A translation is shown only if its line is still the current one; a late result for
  a line that was replaced or has left the screen is not shown.
- Until the new line's translation arrives, the previous one stays (no blank flicker).
- When the line leaves the screen, its translation stays at least `min_display_ms`
  after it appeared, then clears.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from subwizard.pipeline.subtitle_state import SubtitleLine
from subwizard.translator.base import TranslationResult


@dataclass(frozen=True, slots=True)
class SubtitleView:
    line_id: int
    text: str  # the translation
    speaker: str | None
    source: str
    engine: str
    fallback: bool
    shown_at: float
    latency_ms: float  # first seen on screen -> translation ready


class SubtitleBoard:
    def __init__(self, min_display_ms: float, ms_per_char: float = 0.0,
                 max_display_ms: float = 0.0) -> None:  # fmt: skip
        self._min_display_s = min_display_ms / 1000.0
        self._per_char_s = ms_per_char / 1000.0
        self._max_display_s = max(max_display_ms, min_display_ms) / 1000.0
        self._lock = threading.Lock()
        self._current_id: int | None = None
        self._shown: SubtitleView | None = None
        self._clear_at: float | None = None

    @property
    def shown(self) -> SubtitleView | None:
        with self._lock:
            return self._shown

    def line_stable(self, line: SubtitleLine) -> None:
        with self._lock:
            self._current_id = line.id
            self._clear_at = None  # a new line arrived: whatever is shown stays until replaced

    def translated(self, line: SubtitleLine, result: TranslationResult, now: float) -> bool:
        with self._lock:
            if line.id != self._current_id:
                return False
            self._shown = SubtitleView(
                line.id, result.text, line.speaker, line.text, result.engine,
                result.fallback, now, (now - line.seen_at) * 1000.0,
            )  # fmt: skip
            self._clear_at = None
            return True

    def cleared(self, line_id: int, now: float) -> bool:
        with self._lock:
            if line_id == self._current_id:
                self._current_id = None
            if self._shown is None or self._current_id is not None:
                return False
            # A long translation needs longer to read than a short one.
            hold = min(self._max_display_s,
                       self._min_display_s + len(self._shown.text) * self._per_char_s)  # fmt: skip
            self._clear_at = max(now, self._shown.shown_at + hold)
            return self._expire(now)

    def tick(self, now: float) -> bool:
        with self._lock:
            return self._expire(now)

    def _expire(self, now: float) -> bool:
        if self._clear_at is None or now < self._clear_at:
            return False
        self._shown, self._clear_at = None, None
        return True

    def reset(self) -> bool:
        with self._lock:
            changed = self._shown is not None
            self._current_id, self._shown, self._clear_at = None, None, None
            return changed
