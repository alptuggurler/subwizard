"""CPU / RAM usage of this process and the whole machine (SPEC 3.2.1). Qt-free."""

from __future__ import annotations

import psutil

from subwizard.core.metrics import M, MetricsHub

_BYTES_PER_MB = 1024 * 1024


class SystemMonitor:
    def __init__(self, metrics: MetricsHub) -> None:
        self._metrics = metrics
        self._process = psutil.Process()
        self._cpus = psutil.cpu_count() or 1
        # First call only primes psutil's internal counters (it returns 0.0).
        self._process.cpu_percent(None)
        psutil.cpu_percent(None)

    def sample(self) -> None:
        """Non-blocking; call a few times per second."""
        m = self._metrics
        # Process CPU is reported per core by psutil; normalise to the whole machine
        # so it is comparable with the total.
        m.set_gauge(M.SYS_CPU_PROC, self._process.cpu_percent(None) / self._cpus)
        m.set_gauge(M.SYS_CPU_TOTAL, psutil.cpu_percent(None))
        m.set_gauge(M.SYS_RAM_PROC_MB, self._process.memory_info().rss / _BYTES_PER_MB)
        m.set_gauge(M.SYS_RAM_TOTAL, psutil.virtual_memory().percent)
