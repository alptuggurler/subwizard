"""Subtitle state machine (SPEC 3.5): decides when an OCR'd text is a finished line worth
translating, and when it has left the screen. Qt-free, pure (no threads, no I/O).

    EMPTY --text seen--> CANDIDATE --same text (>= similarity) N more times--> STABLE
    STABLE --different text--> CANDIDATE
    STABLE/CANDIDATE --region empty K ticks--> EMPTY  (the shown line is cleared)

- A STABLE line is reported once; seeing it again does nothing.
- Typewriter text: when a new candidate extends the STABLE line ("Where the" ->
  "Where the hell..."), the new line carries `replaces=<old id>` so the old request's
  result is dropped and the long form is translated.
- `ignore_phrases` (regex) are cut out first; what remains must pass the noise filter.
- A leading "NAME:" is split off as the speaker and is not translated.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from enum import StrEnum

from subwizard.config import SubtitleConfig

_SPACES = re.compile(r"\s+")
_TRAILING = " .,…!?-–—"


class State(StrEnum):
    EMPTY = "empty"
    CANDIDATE = "candidate"
    STABLE = "stable"


@dataclass(frozen=True, slots=True)
class SubtitleLine:
    id: int
    text: str  # without the speaker
    speaker: str | None
    seen_at: float  # timestamp of the frame where this text first appeared
    replaces: int | None = None  # id of the shorter line this one grew from

    @property
    def full_text(self) -> str:
        return f"{self.speaker}: {self.text}" if self.speaker else self.text


@dataclass(frozen=True, slots=True)
class LineStable:
    line: SubtitleLine


@dataclass(frozen=True, slots=True)
class LineCleared:
    line_id: int


Event = LineStable | LineCleared


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.casefold(), b.casefold(), autojunk=False).ratio()


def extends(old: str, new: str) -> bool:
    """`new` is `old` with more text after it (typewriter subtitles)."""
    base = old.casefold().rstrip(_TRAILING)
    return len(new) > len(old) and bool(base) and new.casefold().startswith(base)


@dataclass
class _Candidate:
    text: str
    seen_at: float
    repeats: int = 0
    extends_id: int | None = None


@dataclass
class SubtitleStateMachine:
    config: SubtitleConfig
    profile_ignore: tuple[str, ...] = ()  # the game profile's ignore list, on top of config's
    state: State = State.EMPTY
    current: SubtitleLine | None = None  # the STABLE line (also while a new candidate forms)
    _candidate: _Candidate | None = None
    _empty_ticks: int = 0
    _next_id: int = 1
    _ignore: list[re.Pattern[str]] = field(default_factory=list)
    _ignore_src: tuple[str, ...] = ()

    # --- text cleaning ------------------------------------------------------------
    def _patterns(self) -> list[re.Pattern[str]]:
        phrases = (*self.config.ignore_phrases, *self.profile_ignore)
        if phrases != self._ignore_src:  # settings are live; recompile when they change
            self._ignore_src = phrases
            self._ignore = []
            for phrase in phrases:
                try:
                    self._ignore.append(re.compile(phrase, re.IGNORECASE))
                except re.error:  # a broken user regex is matched literally instead
                    self._ignore.append(re.compile(re.escape(phrase), re.IGNORECASE))
        return self._ignore

    def clean(self, text: str) -> str:
        """Ignore phrases removed, whitespace collapsed; "" when what is left is noise."""
        for pattern in self._patterns():
            text = pattern.sub(" ", text)
        text = _SPACES.sub(" ", text).strip()
        if len(text) < self.config.min_chars:
            return ""
        visible = [c for c in text if not c.isspace()]
        letters = sum(c.isalpha() for c in visible)
        if not letters or letters / len(visible) < self.config.min_letter_ratio:
            return ""
        return text

    def split_speaker(self, text: str) -> tuple[str | None, str]:
        match = re.match(self.config.speaker_pattern, text)
        if match is None or not text[match.end():].strip():
            return None, text
        return match.group(1).strip(), text[match.end():].strip()

    # --- transitions --------------------------------------------------------------
    def feed(self, text: str, timestamp: float) -> list[Event]:
        """One OCR tick. `timestamp` is the frame's capture time (for latency)."""
        cleaned = self.clean(text)
        if not cleaned:
            return self._empty()
        self._empty_ticks = 0
        cfg = self.config

        if self.state == State.STABLE and self._candidate is None:
            assert self.current is not None
            if extends(self.current.full_text, cleaned):
                self._candidate = _Candidate(cleaned, timestamp, extends_id=self.current.id)
                self.state = State.CANDIDATE
                return self._maybe_promote()
            if similarity(cleaned, self.current.full_text) >= cfg.similarity:
                return []  # the same line again: never reported twice
            self._candidate = _Candidate(cleaned, timestamp)
            self.state = State.CANDIDATE
            return self._maybe_promote()

        if self._candidate is None:  # EMPTY
            self._candidate = _Candidate(cleaned, timestamp)
            self.state = State.CANDIDATE
            return self._maybe_promote()

        cand = self._candidate
        if similarity(cleaned, cand.text) >= cfg.similarity:
            cand.repeats += 1
            cand.text = cleaned  # the latest read; OCR noise differs between ticks
            return self._maybe_promote()
        # A different text before the candidate settled.
        if self.current is not None and similarity(cleaned, self.current.full_text) >= cfg.similarity:
            self._candidate = None  # flicker back to the shown line: nothing new
            self.state = State.STABLE
            return []
        extends_id = cand.extends_id
        if self.current is not None and extends(self.current.full_text, cleaned):
            extends_id = self.current.id
        elif not extends(cand.text, cleaned):
            extends_id = None
        self._candidate = _Candidate(cleaned, timestamp if extends_id is None else cand.seen_at,
                                     extends_id=extends_id)  # fmt: skip
        return self._maybe_promote()

    def _maybe_promote(self) -> list[Event]:
        cand = self._candidate
        assert cand is not None
        if cand.repeats < self.config.stable_repeats:
            return []
        speaker, text = self.split_speaker(cand.text)
        line = SubtitleLine(self._next_id, text, speaker, cand.seen_at, cand.extends_id)
        self._next_id += 1
        self.current = line
        self._candidate = None
        self.state = State.STABLE
        return [LineStable(line)]

    def _empty(self) -> list[Event]:
        if self.state == State.EMPTY:
            return []
        self._empty_ticks += 1
        if self._empty_ticks < self.config.empty_ticks:
            return []
        events: list[Event] = [LineCleared(self.current.id)] if self.current else []
        self.state = State.EMPTY
        self.current = None
        self._candidate = None
        self._empty_ticks = 0
        return events

    def reset(self) -> list[Event]:
        """Region moved or the session stopped: forget everything, clear what is shown."""
        events: list[Event] = [LineCleared(self.current.id)] if self.current else []
        self.state, self.current, self._candidate, self._empty_ticks = State.EMPTY, None, None, 0
        return events
