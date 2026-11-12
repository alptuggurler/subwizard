"""TTS worker (SPEC 3.11): speaks on its own thread so the pipeline never waits. Qt-free.

Lines arrive faster than speech when dialogue is quick. `catch_up`:
- `skip`: at most MAX_WAITING lines wait; older ones are dropped (newest is what is on
  screen),
- `speedup`: nothing is dropped below HARD_LIMIT, the rate rises with the backlog.
A line that grew (`replaces`) takes the place of its shorter version if still waiting.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass

from subwizard.tts.base import BaseTTS

log = logging.getLogger(__name__)

MAX_WAITING = 1  # skip: lines waiting behind the one being spoken
HARD_LIMIT = 6  # speedup: beyond this even speedup drops the oldest
SPEEDUP_STEP = 0.25  # +25 % per waiting line
MAX_SPEEDUP = 1.75


@dataclass(frozen=True, slots=True)
class Utterance:
    line_id: int
    text: str
    language: str


class TTSWorker:
    def __init__(self, engine: BaseTTS, rate: float = 1.0, catch_up: str = "skip") -> None:
        self._engine = engine
        self._rate = rate
        self._catch_up = catch_up
        self._queue: deque[Utterance] = deque()
        self._cond = threading.Condition()
        self._stopping = False
        self.spoken = 0
        self.dropped = 0
        self._thread = threading.Thread(target=self._run, name="tts", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def say(self, item: Utterance, replaces: int | None = None) -> None:
        with self._cond:
            stale = {item.line_id, replaces}
            self._queue = deque(u for u in self._queue if u.line_id not in stale)
            self._queue.append(item)
            limit = MAX_WAITING if self._catch_up == "skip" else HARD_LIMIT
            while len(self._queue) > limit:
                gone = self._queue.popleft()
                self.dropped += 1
                log.debug("TTS satırı atlandı (sıra birikti): %r", gone.text)
            self._cond.notify()

    def rate_for(self, waiting: int) -> float:
        if self._catch_up != "speedup":
            return self._rate
        return self._rate * min(MAX_SPEEDUP, 1.0 + SPEEDUP_STEP * waiting)

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._stopping:
                    self._cond.wait()
                if self._stopping:
                    return
                item = self._queue.popleft()
                rate = self.rate_for(len(self._queue))
            try:
                self._engine.speak(item.text, item.language, rate)
                self.spoken += 1
            except Exception as exc:  # engine failure must not kill the worker
                log.warning("Sesli okuma başarısız (%s): %s", self._engine.name, exc)

    def stop(self) -> None:
        with self._cond:
            self._stopping = True
            self._queue.clear()
            self._cond.notify()
        self._engine.stop()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._engine.close()
