"""SpendLedger: what Gemini cost, per day, kept across sessions. Qt-free, thread-safe.

The session total alone said nothing about the prepaid balance; this adds every answer's
cost (tokens x the configured price, the same numbers Google bills) to the local day, so
"today" and "all time" are known at once and a daily limit can be enforced.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Spend:
    cost_usd: float = 0.0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


def local_day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t))


class SpendLedger:
    def __init__(self, path: Path | None, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._days: dict[str, Spend] = {}
        self._db: sqlite3.Connection | None = None
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._db = sqlite3.connect(path, check_same_thread=False)
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS days (day TEXT PRIMARY KEY, cost REAL, "
                    "requests INTEGER, input INTEGER, output INTEGER, cached INTEGER)")  # fmt: skip
                for row in self._db.execute("SELECT * FROM days"):
                    self._days[row[0]] = Spend(*row[1:])
            except sqlite3.Error:
                log.exception("Harcama kaydı açılamadı (%s); yalnızca bellekte tutuluyor", path)
                self._db = None

    def add(self, cost_usd: float, input_tokens: int, output_tokens: int,
            cached_tokens: int = 0) -> None:  # fmt: skip
        day = local_day(self._clock())
        with self._lock:
            old = self._days.get(day, Spend())
            new = Spend(old.cost_usd + cost_usd, old.requests + 1,
                        old.input_tokens + input_tokens, old.output_tokens + output_tokens,
                        old.cached_tokens + cached_tokens)  # fmt: skip
            self._days[day] = new
            if self._db is not None:
                try:
                    self._db.execute("INSERT OR REPLACE INTO days VALUES (?, ?, ?, ?, ?, ?)",
                                     (day, new.cost_usd, new.requests, new.input_tokens,
                                      new.output_tokens, new.cached_tokens))  # fmt: skip
                    self._db.commit()
                except sqlite3.Error:
                    log.exception("Harcama kaydı yazılamadı")

    def today(self) -> Spend:
        with self._lock:
            return self._days.get(local_day(self._clock()), Spend())

    def total(self) -> Spend:
        with self._lock:
            days = list(self._days.values())
        return Spend(sum(d.cost_usd for d in days), sum(d.requests for d in days),
                     sum(d.input_tokens for d in days), sum(d.output_tokens for d in days),
                     sum(d.cached_tokens for d in days))  # fmt: skip

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
