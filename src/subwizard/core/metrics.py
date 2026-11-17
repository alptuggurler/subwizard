"""MetricsHub: every stage reports here; the UI reads snapshots (SPEC 3.2.1). Qt-free.

Four kinds of metric, all thread-safe:
  tick(name)            an event happened now       -> rate per second (FPS)
  observe(name, value)  a measurement               -> mean / max / jitter over the windows
  incr(name)            a monotonically rising count (dropped frames, underruns)
  set_gauge(name, v)    the latest value of something (mode string, audio level)
"""

from __future__ import annotations

import math
import statistics
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from subwizard.utils import timing

Grade = Literal["ok", "warn", "bad"]


class M:
    """Metric names, so producers and the UI cannot drift apart."""

    SOURCE_FPS = "source.fps"
    SOURCE_GAME_FPS = "source.game_fps"  # frames that differ from the one before
    SOURCE_INTERVAL_MS = "source.interval_ms"
    SOURCE_DROPPED = "source.dropped"
    SOURCE_REPEATED = "source.repeated"
    SOURCE_REQUESTED = "source.requested_mode"
    SOURCE_ACTUAL = "source.actual_mode"
    SOURCE_BACKEND = "source.backend"
    SOURCE_RECONNECTS = "source.reconnects"
    RENDER_FPS = "render.fps"
    RENDER_FRAME_AGE_MS = "render.frame_age_ms"
    AUDIO_LEVEL_L = "audio.level_l_db"
    AUDIO_LEVEL_R = "audio.level_r_db"
    AUDIO_SILENT = "audio.silent"
    AUDIO_CLIPPING = "audio.clipping"
    AUDIO_UNDERRUN = "audio.underrun"
    AUDIO_OVERRUN = "audio.overrun"
    AUDIO_LATENCY_MS = "audio.latency_ms"
    AUDIO_SAMPLE_RATE = "audio.sample_rate"
    AUDIO_BLOCK = "audio.block"
    OCR_TICKS = "ocr.ticks"  # new frames looked at
    OCR_RUNS = "ocr.runs"  # OCR calls actually made
    OCR_SKIPPED = "ocr.skipped"  # ticks skipped because the region did not change
    OCR_MS = "ocr.ms"
    OCR_TEXT = "ocr.text"
    OCR_CONFIDENCE = "ocr.confidence"
    SUB_LINES = "sub.lines"  # lines that became stable
    SUB_LATENCY_MS = "sub.latency_ms"  # subtitle first seen -> translation ready
    TR_MS = "tr.ms"  # one engine call (cache misses only)
    TR_REQUESTS = "tr.requests"  # calls to the primary engine (Gemini)
    TR_CACHE_HITS = "tr.cache_hits"
    TR_FALLBACKS = "tr.fallbacks"
    TR_ERRORS = "tr.errors"  # nothing could translate the line
    TR_GLOSSARY_MISSES = "tr.glossary_misses"  # a glossary term not translated as listed
    TR_TOKENS_IN = "tr.tokens_in"
    TR_TOKENS_OUT = "tr.tokens_out"
    TR_COST = "tr.cost_usd"  # gauge: session total
    TR_COST_PER_HOUR = "tr.cost_per_hour"  # gauge
    TR_COST_TODAY = "tr.cost_today"  # gauge: today's Gemini cost, all sessions (SpendLedger)
    TR_COST_TOTAL = "tr.cost_total"  # gauge: all-time Gemini cost
    TR_LIMIT = "tr.limit"  # gauge: True while today's cost is at the daily limit
    TR_ENGINE = "tr.engine"  # gauge: engine of the last translation
    TR_TEXT = "tr.text"  # gauge: last translation
    TR_OFFLINE = "tr.offline"  # gauge: True after translator.offline_after failures in a row
    TR_QUOTA = "tr.quota"  # gauge: True while Gemini refuses for quota (fallback in use or not)
    WEB_CLIENTS = "web.clients"  # gauge: connected web overlay pages
    SYS_CPU_PROC = "sys.cpu_proc_pct"
    SYS_CPU_TOTAL = "sys.cpu_total_pct"
    SYS_RAM_PROC_MB = "sys.ram_proc_mb"
    SYS_RAM_TOTAL = "sys.ram_total_pct"


@dataclass(frozen=True, slots=True)
class Rate:
    short: float = 0.0  # per second over the short window
    long: float = 0.0


@dataclass(frozen=True, slots=True)
class Stat:
    last: float = 0.0
    mean: float = 0.0  # short window
    worst: float = 0.0  # max over the short window
    jitter: float = 0.0  # standard deviation over the short window
    mean_long: float = 0.0
    worst_long: float = 0.0
    count: int = 0  # samples in the short window (0 = nothing measured recently)
    p50_long: float = 0.0  # median over the long window (sparse events: latencies)
    count_long: int = 0


@dataclass(frozen=True, slots=True)
class Snapshot:
    rates: dict[str, Rate] = field(default_factory=dict)
    stats: dict[str, Stat] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    gauges: dict[str, Any] = field(default_factory=dict)

    def rate(self, name: str) -> Rate:
        return self.rates.get(name, Rate())

    def stat(self, name: str) -> Stat:
        return self.stats.get(name, Stat())

    def counter(self, name: str) -> int:
        return self.counters.get(name, 0)

    def gauge(self, name: str, default: Any = None) -> Any:
        return self.gauges.get(name, default)


def grade(value: float, warn: float, bad: float) -> Grade:
    """Color code for a metric. Direction is implied: if `bad < warn` higher is
    better (FPS), otherwise lower is better (latency)."""
    if bad < warn:
        return "bad" if value < bad else "warn" if value < warn else "ok"
    return "bad" if value > bad else "warn" if value > warn else "ok"


class MetricsHub:
    def __init__(
        self,
        short_window_s: float = 2.0,
        long_window_s: float = 60.0,
        clock: Callable[[], float] = timing.now,
    ) -> None:
        if not 0 < short_window_s <= long_window_s:
            raise ValueError("windows must satisfy 0 < short <= long")
        self._short = short_window_s
        self._long = long_window_s
        self._clock = clock
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = {}
        self._first_event: dict[str, float] = {}
        self._samples: dict[str, deque[tuple[float, float]]] = {}
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, Any] = {}

    # --- producers ------------------------------------------------------------
    def tick(self, name: str) -> None:
        t = self._clock()
        with self._lock:
            events = self._events.get(name)
            if events is None:
                events = self._events[name] = deque()
                self._first_event[name] = t
            events.append(t)
            horizon = t - self._long
            while events and events[0] < horizon:
                events.popleft()

    def observe(self, name: str, value: float) -> None:
        t = self._clock()
        with self._lock:
            samples = self._samples.setdefault(name, deque())
            samples.append((t, value))
            horizon = t - self._long
            while samples and samples[0][0] < horizon:
                samples.popleft()

    def incr(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def set_gauge(self, name: str, value: Any) -> None:
        with self._lock:
            self._gauges[name] = value

    def clear(self, prefix: str = "") -> None:
        """Forget metrics whose name starts with `prefix` (all of them by default)."""
        with self._lock:
            for store in (
                self._events, self._first_event, self._samples, self._counters, self._gauges
            ):  # fmt: skip
                for name in [n for n in store if n.startswith(prefix)]:
                    del store[name]

    # --- consumers ------------------------------------------------------------
    def _rate(self, name: str, events: deque[float], now: float, window: float) -> float:
        # A stream younger than the window is averaged over its own age, so FPS
        # reads correctly right after start instead of ramping up for 60 s.
        age = now - self._first_event[name]
        span = min(window, age)
        if span <= 0:
            return 0.0
        horizon = now - span
        return sum(1 for t in events if t > horizon) / span

    def _stat(self, samples: deque[tuple[float, float]], now: float) -> Stat:
        long_values = [v for _, v in samples]
        horizon = now - self._short
        short_values = [v for t, v in samples if t >= horizon]
        if not long_values:
            return Stat()
        mean_long = sum(long_values) / len(long_values)
        p50_long = statistics.median(long_values)
        if not short_values:
            return Stat(last=long_values[-1], mean_long=mean_long, worst_long=max(long_values),
                        p50_long=p50_long, count_long=len(long_values))  # fmt: skip
        mean = sum(short_values) / len(short_values)
        variance = sum((v - mean) ** 2 for v in short_values) / len(short_values)
        return Stat(
            last=long_values[-1],
            mean=mean,
            worst=max(short_values),
            jitter=math.sqrt(variance),
            mean_long=mean_long,
            worst_long=max(long_values),
            count=len(short_values),
            p50_long=p50_long,
            count_long=len(long_values),
        )

    def snapshot(self) -> Snapshot:
        now = self._clock()
        with self._lock:
            long_horizon = now - self._long
            rates = {}
            for name, events in self._events.items():
                while events and events[0] < long_horizon:
                    events.popleft()
                rates[name] = Rate(
                    short=self._rate(name, events, now, self._short),
                    long=self._rate(name, events, now, self._long),
                )
            stats = {}
            for name, samples in self._samples.items():
                while samples and samples[0][0] < long_horizon:
                    samples.popleft()
                stats[name] = self._stat(samples, now)
            return Snapshot(rates, stats, dict(self._counters), dict(self._gauges))
