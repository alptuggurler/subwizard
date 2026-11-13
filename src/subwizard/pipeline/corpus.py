"""SubtitleCorpus: every translated line, kept per game across sessions. Qt-free.

`<user data>/subtitles.sqlite`, one row per (game, English text): the translation, the
speaker, how often it was seen and when. The game is the active profile's name. It is
the user's own material (e.g. for studying English); nothing is sent anywhere.

Written from the translator thread; the lock is for the UI reading counts.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS lines ("
    "id INTEGER PRIMARY KEY, game TEXT NOT NULL, source TEXT NOT NULL, "
    "translation TEXT NOT NULL, speaker TEXT, engine TEXT NOT NULL, "
    "complete INTEGER NOT NULL, times INTEGER NOT NULL DEFAULT 1, "
    "first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, UNIQUE(game, source))"
)
UPSERT = (
    "INSERT INTO lines (game, source, translation, speaker, engine, complete, first_seen, "
    "last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(game, source) DO UPDATE SET translation = excluded.translation, "
    "speaker = COALESCE(excluded.speaker, speaker), engine = excluded.engine, "
    "times = times + 1, last_seen = excluded.last_seen"
)


class SubtitleCorpus:
    def __init__(self, path: Path, game: str, clock=time.time) -> None:  # noqa: ANN001
        self._game = game
        self._clock = clock
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(path, check_same_thread=False)
            self._db.execute(SCHEMA)
            self._db.commit()
        except (sqlite3.Error, OSError):
            # A broken file must never stop translation; the session runs without it.
            log.exception("Altyazı kaydı açılamadı (%s); satırlar kaydedilmeyecek", path)
            self._db = None

    def record(self, source: str, translation: str, speaker: str | None, engine: str,
               complete: bool) -> None:  # fmt: skip
        source, translation = " ".join(source.split()), translation.strip()
        if self._db is None or not source or not translation:
            return
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._clock()))
        with self._lock:
            try:
                self._db.execute(UPSERT, (self._game, source, translation, speaker, engine,
                                          int(complete), stamp, stamp))  # fmt: skip
                self._db.commit()
            except sqlite3.Error:
                log.exception("Altyazı satırı kaydedilemedi")

    def count(self, game: str | None = None) -> int:
        if self._db is None:
            return 0
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) FROM lines WHERE game = ?",
                                   (game or self._game,)).fetchone()  # fmt: skip
        return int(row[0])

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
