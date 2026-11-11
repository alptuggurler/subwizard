"""One clock for every latency measurement (SPEC 7). Qt-free."""

from __future__ import annotations

import time
from collections.abc import Callable

now: Callable[[], float] = time.perf_counter  # seconds, monotonic, high resolution


def ms_since(start: float) -> float:
    return (now() - start) * 1000.0


class Stopwatch:
    """`with Stopwatch() as sw: ...` then `sw.ms`."""

    ms: float = 0.0

    def __enter__(self) -> Stopwatch:
        self._start = now()
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = ms_since(self._start)
