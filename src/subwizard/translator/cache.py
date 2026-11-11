"""Translation cache: in-memory LRU + optional SQLite file kept across sessions (SPEC 3.6).
Qt-free and thread-safe (the translator worker writes, the UI may read sizes / clear).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CachedTranslation:
    text: str
    engine: str


def cache_key(text: str, target: str) -> str:
    """Normalised source text: case and spacing differences from OCR are the same line."""
    return f"{target}\x1f{' '.join(text.split()).casefold()}"


class TranslationCache:
    def __init__(self, size: int, path: Path | None = None) -> None:
        self._size = size
        self._lock = threading.Lock()
        self._memory: OrderedDict[str, CachedTranslation] = OrderedDict()
        self._db: sqlite3.Connection | None = None
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._db = sqlite3.connect(path, check_same_thread=False)
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS translations "
                    "(key TEXT PRIMARY KEY, text TEXT NOT NULL, engine TEXT NOT NULL)"
                )
                self._db.commit()
            except sqlite3.Error:
                # A broken cache file must never stop translation; run memory-only.
                log.exception("Çeviri önbelleği açılamadı (%s); yalnızca bellek kullanılacak", path)
                self._db = None

    def get(self, key: str) -> CachedTranslation | None:
        with self._lock:
            hit = self._memory.get(key)
            if hit is not None:
                self._memory.move_to_end(key)
                return hit
            if self._db is None:
                return None
            row = self._db.execute(
                "SELECT text, engine FROM translations WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            hit = CachedTranslation(row[0], row[1])
            self._remember(key, hit)
            return hit

    def put(self, key: str, value: CachedTranslation) -> None:
        with self._lock:
            self._remember(key, value)
            if self._db is not None:
                self._db.execute(
                    "INSERT OR REPLACE INTO translations (key, text, engine) VALUES (?, ?, ?)",
                    (key, value.text, value.engine),
                )
                self._db.commit()

    def _remember(self, key: str, value: CachedTranslation) -> None:
        if self._size <= 0:
            return
        self._memory[key] = value
        self._memory.move_to_end(key)
        while len(self._memory) > self._size:
            self._memory.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            if self._db is not None:
                return self._db.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
            return len(self._memory)

    def clear(self) -> None:
        with self._lock:
            self._memory.clear()
            if self._db is not None:
                self._db.execute("DELETE FROM translations")
                self._db.commit()

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
