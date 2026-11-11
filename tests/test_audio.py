import numpy as np
import pytest

from subwizard.audio.devices import AudioDevice, find_device
from subwizard.audio.meter import FLOOR_DB, LevelMeter, db_to_gain, to_db
from subwizard.audio.ring import DelayBuffer
from subwizard.core.metrics import M, MetricsHub

RATE = 48000
BLOCK = 480  # 10 ms


def _buffer(target_ms=30.0, slack_ms=40.0) -> DelayBuffer:
    return DelayBuffer(channels=2, sample_rate=RATE, target_ms=target_ms, slack_ms=slack_ms,
                       max_target_ms=500)  # fmt: skip


def _ramp(start: int, frames: int = BLOCK) -> np.ndarray:
    """A block whose sample values are their own frame numbers, to track position."""
    values = np.arange(start, start + frames, dtype=np.float32)
    return np.stack([values, values], axis=1)


def _pump(buf: DelayBuffer, blocks: int, start: int = 0) -> list[np.ndarray]:
    out = []
    for i in range(blocks):
        buf.write(_ramp(start + i * BLOCK))
        out.append(buf.read(BLOCK))
    return out


# --- DelayBuffer -------------------------------------------------------------------
def test_buffer_is_silent_until_primed_then_delays_by_the_target():
    buf = _buffer(target_ms=30.0)
    out = _pump(buf, 10)
    assert not out[0].any() and not out[1].any()  # 10, 20 ms buffered: still priming
    assert out[2][0, 0] == 0.0 and out[2][-1, 0] == BLOCK - 1  # playback starts at sample 0
    # Steady state: what is read lags what was just written by the target delay.
    assert buf.fill_ms == pytest.approx(20.0)  # target minus the block just read
    stream = np.concatenate(out[2:])[:, 0]
    assert np.array_equal(stream, np.arange(len(stream)))  # gapless, in order
    assert (buf.underruns, buf.overruns) == (0, 0)


def test_buffer_always_returns_the_requested_shape():
    buf = _buffer()
    assert buf.read(123).shape == (123, 2)
    assert buf.read(123).dtype == np.float32


def test_underrun_plays_the_remainder_then_rebuilds_the_delay():
    buf = _buffer(target_ms=20.0)
    _pump(buf, 5)
    buf.read(BLOCK)  # drains the 10 ms that were left
    starved = buf.read(BLOCK)
    assert not starved.any() and buf.underruns == 1
    buf.write(_ramp(0))
    assert not buf.read(BLOCK).any()  # re-priming, not stuttering along at zero delay
    assert buf.underruns == 1


def test_drift_overrun_snaps_back_to_the_target():
    buf = _buffer(target_ms=30.0, slack_ms=40.0)
    _pump(buf, 5)
    for i in range(8):  # input runs ahead: 80 ms extra with nothing read
        buf.write(_ramp(10_000 + i * BLOCK))
    assert buf.overruns >= 1
    assert buf.fill_ms <= 30.0 + 40.0 + 10.0


def test_delay_can_be_changed_live():
    buf = _buffer(target_ms=30.0)
    _pump(buf, 6)
    buf.set_target_ms(200.0)  # raise: silence while it refills
    assert not buf.read(BLOCK).any()
    for i in range(25):
        buf.write(_ramp(i * BLOCK))
    assert buf.read(BLOCK).any()

    buf.set_target_ms(10.0)  # lower: excess dropped at once, audible immediately
    assert buf.fill_ms == pytest.approx(10.0)
    assert buf.target_ms == pytest.approx(10.0)


def test_ring_wraps_without_corrupting_audio():
    buf = _buffer(target_ms=10.0)  # one block: primed by the very first write
    blocks = int(2.5 * RATE / BLOCK)  # well past the ring capacity
    stream = np.concatenate(_pump(buf, blocks))[:, 0]
    expected = np.arange(len(stream), dtype=np.float32)
    assert np.array_equal(stream, expected)


# --- LevelMeter -------------------------------------------------------------------------
def _meter(clock):
    hub = MetricsHub(clock=clock)
    return hub, LevelMeter(hub, silence_db=-60.0, silence_s=3.0, clip_db=-0.1, clock=clock)


def _tone(left: float, right: float, frames: int = 256) -> np.ndarray:
    block = np.zeros((frames, 2), np.float32)
    block[::2, 0], block[1::2, 0] = left, -left
    block[::2, 1], block[1::2, 1] = right, -right
    return block


def test_meter_reports_peak_dbfs_per_channel(clock):
    hub, meter = _meter(clock)
    meter.process(_tone(0.5, 0.1))
    snap = hub.snapshot()
    assert snap.gauge(M.AUDIO_LEVEL_L) == pytest.approx(-6.02, abs=0.01)
    assert snap.gauge(M.AUDIO_LEVEL_R) == pytest.approx(-20.0, abs=0.01)
    assert snap.gauge(M.AUDIO_CLIPPING) is False and snap.gauge(M.AUDIO_SILENT) is False


def test_meter_flags_clipping(clock):
    hub, meter = _meter(clock)
    meter.process(_tone(1.0, 0.2))
    assert hub.snapshot().gauge(M.AUDIO_CLIPPING) is True


def test_no_audio_warning_needs_three_quiet_seconds(clock):
    hub, meter = _meter(clock)
    quiet = _tone(0.0001, 0.0001)  # -80 dB
    meter.process(quiet)
    clock.advance(2.9)
    meter.process(quiet)
    assert hub.snapshot().gauge(M.AUDIO_SILENT) is False
    clock.advance(0.2)
    meter.process(quiet)
    assert hub.snapshot().gauge(M.AUDIO_SILENT) is True
    meter.process(_tone(0.3, 0.3))  # sound returns: warning clears, timer restarts
    assert hub.snapshot().gauge(M.AUDIO_SILENT) is False
    clock.advance(1.0)
    meter.process(quiet)
    assert hub.snapshot().gauge(M.AUDIO_SILENT) is False


def test_meter_handles_mono_and_digital_silence(clock):
    hub, meter = _meter(clock)
    meter.process(np.full((64, 1), 0.5, np.float32))
    snap = hub.snapshot()
    assert snap.gauge(M.AUDIO_LEVEL_L) == snap.gauge(M.AUDIO_LEVEL_R)
    meter.process(np.zeros((64, 2), np.float32))
    assert hub.snapshot().gauge(M.AUDIO_LEVEL_L) == FLOOR_DB


def test_db_conversions():
    assert to_db(1.0) == 0.0
    assert to_db(0.0) == FLOOR_DB
    assert db_to_gain(0.0) == 1.0
    assert db_to_gain(6.0) == pytest.approx(1.995, abs=0.001)
    assert to_db(db_to_gain(-12.5)) == pytest.approx(-12.5)


# --- device lookup ----------------------------------------------------------------------
DEVICES = [
    AudioDevice(3, "Speakers (Realtek)", "WASAPI", 0, 2, 48000, True),
    AudioDevice(7, "Digital Audio Interface (USB Capture HDMI)", "WASAPI", 2, 0, 48000),
]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Digital Audio Interface (USB Capture HDMI)", 7),
        ("usb capture", 7),  # partial, case-insensitive — the SPEC's --audio-device example
        ("Unplugged Device", None),
        (None, None),
        ("", None),
    ],
)
def test_find_device(name, expected):
    assert find_device(name, "input", DEVICES) == expected
