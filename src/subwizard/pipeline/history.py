"""Subtitle history (SPEC 3.9, 3.12): every line of the session with its translation,
for the History page, the F12 panel, exports and the session subtitle log. Qt-free,
thread-safe (OCR and translator threads write, the UI reads).

The log file `logs/subtitles-YYYYMMDD-HHMM.srt` gets each line when it leaves the
screen (its end time is known then), so it is readable while the session runs.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from subwizard.pipeline.subtitle_state import SubtitleLine
from subwizard.translator.base import TranslationResult

log = logging.getLogger(__name__)

MIN_SRT_DURATION_S = 1.0  # a line that flashed by still gets a readable SRT cue
MAX_ENTRIES = 5000  # a long session; older lines leave memory (the log file keeps them)


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    line_id: int
    start_s: float  # seconds since the session started (first sighting)
    end_s: float | None  # when it left the screen; None while still shown
    clock: str  # wall clock "HH:MM:SS" of the first sighting
    source: str  # English, speaker included
    speaker: str | None
    translation: str = ""
    engine: str = ""
    fallback: bool = False
    bad: bool = False  # user marked "bad translation" (SPEC 3.12)


def srt_time(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(entries: list[HistoryEntry], with_source: bool = True) -> str:
    blocks = []
    for n, e in enumerate(entries, 1):
        end = e.end_s if e.end_s is not None else e.start_s + MIN_SRT_DURATION_S
        end = max(end, e.start_s + MIN_SRT_DURATION_S)
        text = e.translation or e.source
        if with_source and e.translation:
            text = f"{e.translation}\n{e.source}"
        blocks.append(f"{n}\n{srt_time(e.start_s)} --> {srt_time(end)}\n{text}\n")
    return "\n".join(blocks)


def to_text(entries: list[HistoryEntry]) -> str:
    return "\n".join(f"[{e.clock}] {e.source}\n           {e.translation}" for e in entries)


class SubtitleHistory:
    def __init__(self, started_at: float, log_path: Path | None = None,
                 clock=time.perf_counter) -> None:  # noqa: ANN001  # fmt: skip
        self._started = started_at
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[int, HistoryEntry] = {}
        self._log_path = log_path
        self._logged = 0

    def _offset(self, t: float) -> float:
        return max(0.0, t - self._started)

    def add(self, line: SubtitleLine) -> None:
        with self._lock:
            if line.replaces is not None and line.replaces in self._entries:
                # Typewriter text: the long form takes the short one's place.
                old = self._entries.pop(line.replaces)
                start, clock = old.start_s, old.clock
            else:
                start, clock = self._offset(line.seen_at), time.strftime("%H:%M:%S")
            self._entries[line.id] = HistoryEntry(line.id, start, None, clock, line.full_text,
                                                  line.speaker)  # fmt: skip
            while len(self._entries) > MAX_ENTRIES:
                self._entries.pop(next(iter(self._entries)))

    def translated(self, line_id: int, result: TranslationResult) -> None:
        self.set_translation(line_id, result.text, result.engine, result.fallback)

    def set_translation(self, line_id: int, text: str, engine: str, fallback: bool) -> None:
        with self._lock:
            entry = self._entries.get(line_id)
            if entry is not None:
                self._entries[line_id] = replace(entry, translation=text, engine=engine,
                                                 fallback=fallback, bad=False)  # fmt: skip
                if entry.end_s is not None:
                    self._write(self._entries[line_id])

    def get(self, line_id: int) -> HistoryEntry | None:
        with self._lock:
            return self._entries.get(line_id)

    def close(self, line_id: int, t: float | None = None) -> None:
        with self._lock:
            entry = self._entries.get(line_id)
            if entry is None or entry.end_s is not None:
                return
            entry = replace(entry, end_s=self._offset(self._clock() if t is None else t))
            self._entries[line_id] = entry
            if entry.translation:
                self._write(entry)

    def close_all(self) -> None:
        with self._lock:
            open_ids = [i for i, e in self._entries.items() if e.end_s is None]
        for line_id in open_ids:
            self.close(line_id)

    def mark_bad(self, line_id: int, bad: bool = True) -> None:
        with self._lock:
            if line_id in self._entries:
                self._entries[line_id] = replace(self._entries[line_id], bad=bad)

    def entries(self) -> list[HistoryEntry]:
        with self._lock:
            return list(self._entries.values())

    def last(self, n: int) -> list[HistoryEntry]:
        with self._lock:
            return list(self._entries.values())[-n:]

    def search(self, query: str) -> list[HistoryEntry]:
        q = query.casefold().strip()
        return [e for e in self.entries()
                if not q or q in e.source.casefold() or q in e.translation.casefold()]  # fmt: skip

    def _write(self, entry: HistoryEntry) -> None:
        """Append one SRT cue to the session log (lock held by the caller)."""
        if self._log_path is None:
            return
        self._logged += 1
        block = to_srt([entry]).replace("1\n", f"{self._logged}\n", 1)
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(block + "\n")
        except OSError:
            log.exception("Altyazı logu yazılamadı (%s); kayıt kapatılıyor", self._log_path)
            self._log_path = None
