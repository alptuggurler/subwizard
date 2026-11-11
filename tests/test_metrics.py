import threading

import pytest

from subwizard.core.metrics import MetricsHub, Snapshot, grade


def _hub(clock) -> MetricsHub:
    return MetricsHub(short_window_s=2.0, long_window_s=60.0, clock=clock)


def _tick_at_fps(hub, clock, fps, seconds, name="fps"):
    for _ in range(int(fps * seconds)):
        clock.advance(1 / fps)
        hub.tick(name)


def test_steady_stream_reads_as_its_fps(clock):
    hub = _hub(clock)
    _tick_at_fps(hub, clock, 60, 5)
    rate = hub.snapshot().rate("fps")
    assert rate.short == pytest.approx(60, abs=0.6)
    assert rate.long == pytest.approx(60, abs=0.6)


def test_young_stream_is_not_diluted_by_the_window(clock):
    hub = _hub(clock)
    _tick_at_fps(hub, clock, 60, 0.5)
    assert hub.snapshot().rate("fps").long == pytest.approx(60, abs=2.5)


def test_short_window_reacts_to_a_drop_while_long_window_lags(clock):
    hub = _hub(clock)
    _tick_at_fps(hub, clock, 60, 10)
    _tick_at_fps(hub, clock, 30, 3)
    rate = hub.snapshot().rate("fps")
    assert rate.short == pytest.approx(30, abs=0.6)
    assert 45 < rate.long < 60


def test_rate_falls_to_zero_when_the_stream_stops(clock):
    hub = _hub(clock)
    _tick_at_fps(hub, clock, 60, 3)
    clock.advance(2.5)
    assert hub.snapshot().rate("fps").short == 0
    clock.advance(61)
    assert hub.snapshot().rate("fps").long == 0


def test_stat_mean_worst_jitter(clock):
    hub = _hub(clock)
    for value in (10.0, 20.0, 30.0):
        clock.advance(0.1)
        hub.observe("ms", value)
    stat = hub.snapshot().stat("ms")
    assert (stat.last, stat.mean, stat.worst) == (30.0, 20.0, 30.0)
    assert stat.jitter == pytest.approx(8.165, abs=0.001)


def test_stat_short_window_forgets_but_long_remembers(clock):
    hub = _hub(clock)
    hub.observe("ms", 100.0)
    clock.advance(5)
    hub.observe("ms", 10.0)
    stat = hub.snapshot().stat("ms")
    assert (stat.mean, stat.worst) == (10.0, 10.0)
    assert (stat.mean_long, stat.worst_long) == (55.0, 100.0)
    clock.advance(61)
    assert hub.snapshot().stat("ms").worst_long == 0


def test_counters_gauges_and_defaults(clock):
    hub = _hub(clock)
    hub.incr("dropped")
    hub.incr("dropped", 3)
    hub.set_gauge("mode", "1080p")
    snap = hub.snapshot()
    assert snap.counter("dropped") == 4
    assert snap.gauge("mode") == "1080p"
    assert snap.counter("nope") == 0 and snap.gauge("nope") is None
    assert snap.rate("nope").short == 0 and snap.stat("nope").mean == 0
    assert Snapshot().counter("x") == 0


def test_clear_by_prefix(clock):
    hub = _hub(clock)
    hub.tick("audio.x")
    hub.set_gauge("audio.level", -3)
    hub.set_gauge("source.mode", "m")
    hub.clear("audio.")
    snap = hub.snapshot()
    assert "audio.x" not in snap.rates and "audio.level" not in snap.gauges
    assert snap.gauge("source.mode") == "m"
    hub.tick("audio.x")  # a cleared stream can start again
    assert "audio.x" in hub.snapshot().rates


@pytest.mark.parametrize(
    ("value", "warn", "bad", "expected"),
    [
        (60, 58, 48, "ok"), (57, 58, 48, "warn"), (40, 58, 48, "bad"),  # higher is better
        (10, 20, 50, "ok"), (30, 20, 50, "warn"), (80, 20, 50, "bad"),  # lower is better
    ],
)  # fmt: skip
def test_grade(value, warn, bad, expected):
    assert grade(value, warn, bad) == expected


def test_invalid_windows_rejected():
    with pytest.raises(ValueError):
        MetricsHub(short_window_s=10, long_window_s=5)


def test_concurrent_producers_lose_nothing():
    hub = MetricsHub()

    def work():
        for _ in range(2000):
            hub.incr("n")
            hub.tick("t")
            hub.observe("o", 1.0)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for _ in range(50):
        hub.snapshot()
    for thread in threads:
        thread.join()
    assert hub.snapshot().counter("n") == 8000
