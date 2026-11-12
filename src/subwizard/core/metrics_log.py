"""Metrics over time for the Diagnostics graphs and CSV export (SPEC 3.2.1). Qt-free.

The controller already publishes a Snapshot `metrics.ui_hz` times per second; this keeps
one row per second (the newest `MAX_ROWS`) so a user can export what happened during a
stutter and attach it to a bug report.
"""

from __future__ import annotations

import csv
import io
import threading
import time
from collections import deque
from collections.abc import Callable

from subwizard.core.metrics import M, Snapshot

MAX_ROWS = 3600  # one hour at one row per second
ROW_INTERVAL_S = 1.0

# column -> value from a snapshot (gauges may be None: written as empty cells)
COLUMNS: dict[str, Callable[[Snapshot], object]] = {
    "source_fps": lambda s: round(s.rate(M.SOURCE_FPS).short, 2),
    "render_fps": lambda s: round(s.rate(M.RENDER_FPS).short, 2),
    "frame_age_ms": lambda s: round(s.stat(M.RENDER_FRAME_AGE_MS).mean, 2),
    "frame_age_worst_ms": lambda s: round(s.stat(M.RENDER_FRAME_AGE_MS).worst, 2),
    "frame_interval_ms": lambda s: round(s.stat(M.SOURCE_INTERVAL_MS).mean, 2),
    "audio_db": lambda s: _loudest(s),
    "ocr_ms": lambda s: round(s.stat(M.OCR_MS).mean, 1),
    "translate_ms": lambda s: round(s.stat(M.TR_MS).mean, 1),
    "subtitle_latency_ms": lambda s: round(s.stat(M.SUB_LATENCY_MS).mean, 1),
    "cpu_proc_pct": lambda s: s.gauge(M.SYS_CPU_PROC),
    "cpu_total_pct": lambda s: s.gauge(M.SYS_CPU_TOTAL),
    "ram_proc_mb": lambda s: s.gauge(M.SYS_RAM_PROC_MB),
}


def _loudest(snap: Snapshot) -> float | None:
    levels = [v for v in (snap.gauge(M.AUDIO_LEVEL_L), snap.gauge(M.AUDIO_LEVEL_R))
              if isinstance(v, (int, float))]  # fmt: skip
    return round(max(levels), 1) if levels else None


class MetricsRecorder:
    def __init__(self, max_rows: int = MAX_ROWS, interval_s: float = ROW_INTERVAL_S,
                 clock: Callable[[], float] = time.time) -> None:  # fmt: skip
        self._rows: deque[list[object]] = deque(maxlen=max_rows)
        self._interval = interval_s
        self._clock = clock
        self._last = float("-inf")
        self._lock = threading.Lock()

    def record(self, snap: Snapshot) -> bool:
        """Keep the snapshot if a row interval has passed; returns whether it was kept."""
        now = self._clock()
        if now - self._last < self._interval:
            return False
        self._last = now
        row = [time.strftime("%H:%M:%S", time.localtime(now))]
        row += [fn(snap) for fn in COLUMNS.values()]
        with self._lock:
            self._rows.append(row)
        return True

    def series(self, column: str, rows: int) -> list[float | None]:
        """The newest `rows` values of one column, oldest first (for the graphs)."""
        index = 1 + list(COLUMNS).index(column)
        with self._lock:
            recent = list(self._rows)[-rows:]
        return [v if isinstance(v, (int, float)) else None for v in (r[index] for r in recent)]

    def __len__(self) -> int:
        return len(self._rows)

    def to_csv(self) -> str:
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["time", *COLUMNS])
        with self._lock:
            writer.writerows(["" if v is None else v for v in row] for row in self._rows)
        return out.getvalue()
