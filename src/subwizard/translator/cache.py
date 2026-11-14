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


# The same line read a little differently on another day ("I'll" / "l'll" / "I'II") is a
# cache miss by the exact key. The loose key folds what OCR mixes up and drops spacing and
# punctuation, but nothing that carries meaning: digits other than 0/1 stay, a question
# stays a question, and words are never compared by similarity ("can" is not "can't").
_LOOSE_FOLD = str.maketrans({"l": "i", "1": "i", "|": "i", "ı": "i", "0": "o"})
LOOSE_MIN_CHARS = 8  # shorter texts collide too easily ("Ill" / "I'll")


def loose_key(text: str, target: str) -> str | None:
    """None when the text is too short to be matched loosely."""
    folded = text.casefold().translate(_LOOSE_FOLD)
    core = "".join(c for c in folded if c.isalnum())
    if len(core) < LOOSE_MIN_CHARS:
        return None
    return f"{target}\x1f{core}{'?' if text.rstrip().endswith('?') else ''}"


class TranslationCache:
    def __init__(self, size: int, path: Path | None = None) -> None:
        self._size = size
        self._lock = threading.Lock()
        self._memory: OrderedDict[str, CachedTranslation] = OrderedDict()
        self._aliases: OrderedDict[str, str] = OrderedDict()  # loose key -> exact key
        self._db: sqlite3.Connection | None = None
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._db = sqlite3.connect(path, check_same_thread=False)
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS translations "
                    "(key TEXT PRIMARY KEY, text TEXT NOT NULL, engine TEXT NOT NULL)"
                )
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS aliases (loose TEXT PRIMARY KEY, key TEXT NOT NULL)"
                )
                self._db.commit()
            except sqlite3.Error:
                # A broken cache file must never stop translation; run memory-only.
                log.exception("Çeviri önbelleği açılamadı (%s); yalnızca bellek kullanılacak", path)
                self._db = None

    def get_loose(self, loose: str | None) -> CachedTranslation | None:
        """The translation of a line that differs only by what OCR mixes up."""
        if loose is None:
            return None
        with self._lock:
            key = self._aliases.get(loose)
            if key is None and self._db is not None:
                row = self._db.execute("SELECT key FROM aliases WHERE loose = ?", (loose,)).fetchone()
                key = row[0] if row else None
        return self.get(key) if key is not None else None

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

    def put(self, key: str, value: CachedTranslation, loose: str | None = None) -> None:
        with self._lock:
            self._remember(key, value)
            if loose is not None and self._size > 0:
                self._aliases[loose] = key
                self._aliases.move_to_end(loose)
                while len(self._aliases) > self._size:
                    self._aliases.popitem(last=False)
            if self._db is not None:
                self._db.execute(
                    "INSERT OR REPLACE INTO translations (key, text, engine) VALUES (?, ?, ?)",
                    (key, value.text, value.engine),
                )
                if loose is not None:
                    self._db.execute(
                        "INSERT OR REPLACE INTO aliases (loose, key) VALUES (?, ?)", (loose, key))
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
            self._aliases.clear()
            if self._db is not None:
                self._db.execute("DELETE FROM translations")
                self._db.execute("DELETE FROM aliases")
                self._db.commit()

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
