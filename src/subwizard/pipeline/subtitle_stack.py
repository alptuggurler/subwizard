"""SubtitleStack: how the translation enters and leaves the screen. Qt-free.

The board says *what* is shown; this says *how*: a new line fades in, the line before it
stays above for a moment, smaller and dimmed, then fades away, and a cleared line fades
out instead of vanishing. Fast dialogue stays readable and nothing pops. Time comes from
the caller (seconds), so the views animate it and tests step it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from subwizard.pipeline.board import SubtitleView

PREVIOUS_HOLD_S = 2.5  # an older line stays this long before it fades
PREVIOUS_OPACITY = 0.55
PREVIOUS_SCALE = 0.78


@dataclass(slots=True)
class _Entry:
    view: SubtitleView
    born: float
    demoted: float | None = None  # when a newer line took its place
    leaving: float | None = None  # when it started to fade out


@dataclass(frozen=True, slots=True)
class StackItem:
    view: SubtitleView
    opacity: float  # 0..1
    scale: float  # 1 = the current line, smaller = an older one
    current: bool


def _same_line(old: SubtitleView, new: SubtitleView) -> bool:
    """The same line again (retranslated), the line it grew from (typewriter, joined
    sentence), or one contained in the other (a second speaker's line appeared above or
    went away): it is replaced in place, not pushed up as an older line, so the same
    words are never on screen twice."""
    if old.line_id == new.line_id:
        return True
    a, b = old.source.casefold().strip(), new.source.casefold().strip()
    if a and b.startswith(a):
        return True
    wa, wb = _WORD.findall(a), _WORD.findall(b)
    shorter = min(len(wa), len(wb))
    if shorter and wa[:shorter] == wb[:shorter]:
        return True  # the top speaker's line stayed, the one below came or went
    return _contains(wb, wa) or _contains(wa, wb)


_WORD = re.compile(r"\w+(?:'\w+)?")
MIN_CONTAINED_WORDS = 2  # "No." inside "I know no one." is not the same line


def _contains(words: list[str], part: list[str]) -> bool:
    """`part` appears in `words` as whole words, in order."""
    n = len(part)
    if n < MIN_CONTAINED_WORDS or n >= len(words):
        return False
    return any(words[i:i + n] == part for i in range(len(words) - n + 1))


class SubtitleStack:
    def __init__(self, max_lines: int = 2, fade_ms: float = 150.0) -> None:
        self.max_lines = max(1, max_lines)
        self.fade_s = max(0.0, fade_ms) / 1000.0
        self._entries: list[_Entry] = []  # oldest first; the last one may be current

    def configure(self, max_lines: int, fade_ms: float) -> None:
        self.max_lines = max(1, max_lines)
        self.fade_s = max(0.0, fade_ms) / 1000.0

    def _current(self) -> _Entry | None:
        if self._entries and self._entries[-1].demoted is None and self._entries[-1].leaving is None:
            return self._entries[-1]
        return None

    def show(self, view: SubtitleView, now: float) -> None:
        current = self._current()
        if current is not None:
            if current.view == view:
                return
            if _same_line(current.view, view):
                current.view = view  # same place, no new fade-in
                return
            current.demoted = now
        self._entries.append(_Entry(view, now))
        self._prune(now)

    def clear(self, now: float) -> None:
        """Nothing is shown any more: everything fades out together."""
        for entry in self._entries:
            if entry.leaving is None:
                entry.leaving = now
        self._prune(now)

    def reset(self) -> None:
        self._entries.clear()

    def _opacity(self, entry: _Entry, now: float) -> float:
        fade = self.fade_s
        value = 1.0 if fade <= 0 else min(1.0, (now - entry.born) / fade)
        if entry.demoted is not None:
            if now - entry.demoted >= PREVIOUS_HOLD_S and entry.leaving is None:
                entry.leaving = entry.demoted + PREVIOUS_HOLD_S
            # Dims over one fade instead of jumping to the older-line look.
            dim = 1.0 if fade <= 0 else min(1.0, (now - entry.demoted) / fade)
            value *= 1.0 - (1.0 - PREVIOUS_OPACITY) * dim
        if entry.leaving is not None:
            value *= 0.0 if fade <= 0 else max(0.0, 1.0 - (now - entry.leaving) / fade)
        return value

    def _scale(self, entry: _Entry, now: float) -> float:
        if entry.demoted is None:
            return 1.0
        done = 1.0 if self.fade_s <= 0 else min(1.0, (now - entry.demoted) / self.fade_s)
        return 1.0 - (1.0 - PREVIOUS_SCALE) * done

    def _prune(self, now: float) -> None:
        kept = []
        for entry in self._entries:
            opacity = self._opacity(entry, now)
            if entry.leaving is not None and opacity <= 0.0:
                continue
            kept.append(entry)
        # Only the newest older lines fit; the rest go at once.
        older = [e for e in kept if e.demoted is not None]
        for entry in older[: max(0, len(older) - (self.max_lines - 1))]:
            kept.remove(entry)
        self._entries = kept

    def items(self, now: float) -> list[StackItem]:
        """What to draw, oldest first (the current line last)."""
        self._prune(now)
        return [
            StackItem(e.view, self._opacity(e, now), self._scale(e, now),
                      e.demoted is None and e.leaving is None)  # fmt: skip
            for e in self._entries
        ]

    def animating(self, now: float) -> bool:
        """Something is still fading or waiting to fade: keep repainting."""
        self._prune(now)
        for entry in self._entries:
            if entry.demoted is not None or entry.leaving is not None:
                return True
            if self.fade_s > 0 and now - entry.born < self.fade_s:
                return True
        return False
