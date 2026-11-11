import threading
import time

import cv2
import numpy as np
import pytest

from subwizard.core.metrics import M, MetricsHub
from subwizard.i18n import t
from subwizard.sources.base import BaseSource, Frame, LatestSlot, SourceError, VideoMode
from subwizard.sources.capture_card import CaptureCardSource, mode_hints
from subwizard.sources.runner import SourceRunner, SourceState
from subwizard.sources.video_file import VideoFileSource, fourcc_to_str
from tests.conftest import CLIP_FPS, CLIP_SECONDS

WANT = VideoMode(1920, 1080, 60.0, "MJPG")


# --- VideoFileSource ---------------------------------------------------------
def test_file_plays_at_real_time_speed(clip):
    frames = 15
    with VideoFileSource(clip) as source:
        assert source.actual_mode == VideoMode(320, 180, float(CLIP_FPS), source.actual_mode.fourcc)
        assert source.requested_mode is None
        start = time.perf_counter()
        read = [source.read() for _ in range(frames)]
        elapsed = time.perf_counter() - start
    assert [f.index for f in read] == list(range(frames))
    assert read[0].image.shape == (180, 320, 3)
    # 15 frames = 14 intervals of 1/30 s; generous bounds for a busy CI machine.
    assert 0.4 < elapsed < 0.9


def test_file_loops_or_ends(clip):
    total = int(CLIP_FPS * CLIP_SECONDS)
    with VideoFileSource(clip, loop=True) as source:
        frames = [source.read() for _ in range(total + 3)]
        assert all(f is not None for f in frames) and not source.ended
    with VideoFileSource(clip, loop=False) as source:
        frames = [source.read() for _ in range(total + 3)]
        assert sum(f is not None for f in frames) == total
        assert source.ended


def test_file_errors_are_user_facing(tmp_path):
    with pytest.raises(SourceError) as exc:
        VideoFileSource(tmp_path / "missing.mp4").open()
    assert exc.value.key == "error.file_not_found"
    assert "missing.mp4" in exc.value.message

    garbage = tmp_path / "garbage.mp4"
    garbage.write_bytes(b"not a video")
    with pytest.raises(SourceError) as exc:
        VideoFileSource(garbage).open()
    assert exc.value.key == "error.file_unreadable"


def test_fourcc_decoding():
    assert fourcc_to_str(cv2.VideoWriter_fourcc(*"MJPG")) == "MJPG"
    assert fourcc_to_str(0) == "?"
    assert fourcc_to_str(-1) == "?"


# --- CaptureCardSource (fake device) -------------------------------------------
class FakeCapture:
    """Stands in for cv2.VideoCapture; `delivers` is what the 'device' really gives."""

    def __init__(self, opened=True, delivers=WANT, frames=True):
        self.opened, self.delivers, self.frames = opened, delivers, frames
        self.set_calls = []
        self.released = False

    def isOpened(self):
        return self.opened

    def set(self, prop, value):
        self.set_calls.append(prop)
        return True

    def get(self, prop):
        d = self.delivers
        return {
            cv2.CAP_PROP_FRAME_WIDTH: d.width,
            cv2.CAP_PROP_FRAME_HEIGHT: d.height,
            cv2.CAP_PROP_FPS: d.fps,
            cv2.CAP_PROP_FOURCC: cv2.VideoWriter_fourcc(*d.fourcc),
        }[prop]

    def read(self):
        return (True, np.zeros((4, 4, 3), np.uint8)) if self.frames else (False, None)

    def release(self):
        self.released = True


def _capture(fakes: dict, device=0, **kwargs):
    backends = [(name, i) for i, name in enumerate(fakes)]
    by_id = dict(zip(range(len(fakes)), fakes.values(), strict=True))
    return CaptureCardSource(
        device, WANT, backends=backends, capture_factory=lambda idx, b: by_id[b], **kwargs
    )


def test_capture_sets_properties_in_the_required_order():
    fake = FakeCapture()
    source = _capture({"DSHOW": fake})
    source.open()
    assert fake.set_calls == [
        cv2.CAP_PROP_FOURCC,
        cv2.CAP_PROP_FRAME_WIDTH,
        cv2.CAP_PROP_FRAME_HEIGHT,
        cv2.CAP_PROP_FPS,
        cv2.CAP_PROP_BUFFERSIZE,
    ]
    assert source.actual_mode == WANT and source.backend == "DSHOW"
    assert source.read().index == 0
    source.close()
    assert fake.released and not source.is_open


def test_capture_reports_what_the_device_really_delivers():
    got = VideoMode(1920, 1080, 30.0, "YUY2")
    source = _capture({"DSHOW": FakeCapture(delivers=got)})
    source.open()
    assert source.requested_mode == WANT and source.actual_mode == got


@pytest.mark.parametrize("first", [FakeCapture(opened=False), FakeCapture(frames=False)])
def test_capture_falls_back_to_the_next_backend(first):
    source = _capture({"DSHOW": first, "MSMF": FakeCapture()})
    source.open()
    assert source.backend == "MSMF"
    assert first.released


def test_capture_not_found_has_a_helpful_message():
    with pytest.raises(SourceError) as exc:
        _capture({"DSHOW": FakeCapture(opened=False), "MSMF": FakeCapture(frames=False)}).open()
    assert exc.value.key == "error.capture_not_found"
    assert "USB 3.0" in exc.value.message


def test_capture_by_name():
    source = _capture({"DSHOW": FakeCapture()}, device="USB Video", resolve_name=lambda n: 2)
    source.open()
    with pytest.raises(SourceError) as exc:
        _capture({"DSHOW": FakeCapture()}, device="Nope", resolve_name=lambda n: None).open()
    assert exc.value.key == "error.capture_name_not_found"


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        (WANT, []),
        (None, []),
        (VideoMode(1920, 1080, 30.0, "YUY2"), ["hint.select_mjpg", "hint.usb3_port"]),
        (VideoMode(1920, 1080, 30.0, "MJPG"), ["hint.usb3_port"]),
        (VideoMode(1280, 720, 60.0, "MJPG"), ["hint.usb3_port"]),
        (VideoMode(1920, 1080, 120.0, "MJPG"), ["hint.mode_unsupported"]),
    ],
)
def test_mode_hints(actual, expected):
    assert mode_hints(WANT, actual) == expected


# --- SourceRunner ---------------------------------------------------------------
class ScriptedSource(BaseSource):
    """Yields a scripted list per connection: Frame, None (failed read) or 'end'."""

    def __init__(self, connections, open_errors=0, fps=60.0, reconnectable=True):
        self._connections = list(connections)
        self._open_errors = open_errors
        self._fps = fps
        self._reconnectable = reconnectable
        self._script = None
        self._ended = False
        self.opens = 0

    def open(self):
        self.opens += 1
        if self._open_errors:
            self._open_errors -= 1
            raise SourceError("error.capture_not_found")
        self._script = list(self._connections.pop(0)) if self._connections else []

    def read(self):
        if not self._script:
            self._ended = not self._connections
            return None
        return self._script.pop(0)

    def close(self):
        self._script = None

    is_open = property(lambda self: self._script is not None)
    requested_mode = property(lambda self: WANT)
    actual_mode = property(lambda self: VideoMode(1920, 1080, self._fps, "MJPG"))
    ended = property(lambda self: self._ended)
    can_reconnect = property(lambda self: self._reconnectable)


def _frame(index, t, value=None):
    image = np.full((128, 128, 3), index if value is None else value, np.uint8)
    return Frame(image, t, index)


def _run(source, max_failures=2):
    metrics, slot, states, frames = MetricsHub(), LatestSlot(), [], []
    runner = SourceRunner(
        source, slot, metrics,
        on_frame=lambda: frames.append(slot.get().index),
        on_state=lambda state, key: states.append((state, key)),
        reconnect_initial_s=0.01, reconnect_max_s=0.04, max_read_failures=max_failures,
    )  # fmt: skip
    runner.start()
    runner.join(5)
    assert not runner.is_alive()
    return metrics.snapshot(), states, frames, slot


def test_runner_delivers_frames_and_measures_them():
    step = 1 / 60
    script = [_frame(i, i * step) for i in range(5)]
    script.append(_frame(5, 5 * step + 3 * step))  # 3 frames went missing before this one
    script.append(_frame(6, 9 * step, value=5))  # identical pixels = repeated frame
    snap, states, frames, slot = _run(ScriptedSource([script]))

    assert frames == list(range(7))
    assert snap.counter(M.SOURCE_DROPPED) == 3
    assert snap.counter(M.SOURCE_REPEATED) == 1
    assert snap.gauge(M.SOURCE_REQUESTED) == WANT
    assert [s for s, _ in states] == [
        SourceState.CONNECTING, SourceState.RUNNING, SourceState.ENDED
    ]  # fmt: skip
    assert slot.get() is None  # no stale frame left behind after the source ends


def test_runner_reconnects_after_the_device_is_lost():
    first = [_frame(0, 0.0), None, None]  # two failed reads = lost (max_failures=2)
    second = [_frame(1, 1.0)]
    source = ScriptedSource([first, second])
    snap, states, frames, _ = _run(source)
    assert frames == [0, 1]
    assert source.opens == 2
    assert snap.counter(M.SOURCE_RECONNECTS) == 1
    assert (SourceState.RECONNECTING, t("error.capture_lost")) in states


def test_runner_retries_open_with_backoff():
    source = ScriptedSource([[_frame(0, 0.0)]], open_errors=3)
    start = time.perf_counter()
    _, states, frames, _ = _run(source)
    elapsed = time.perf_counter() - start
    assert frames == [0] and source.opens == 4
    assert states.count((SourceState.RECONNECTING, t("error.capture_not_found"))) == 3
    assert elapsed >= 0.01 + 0.02 + 0.04  # doubled each time, capped at the max


def test_runner_gives_up_on_a_source_that_cannot_come_back():
    source = ScriptedSource([], open_errors=1, reconnectable=False)
    _, states, _, _ = _run(source)
    assert states[-1] == (SourceState.FAILED, t("error.capture_not_found"))
    assert source.opens == 1


def test_runner_stops_promptly_while_waiting_to_reconnect():
    source = ScriptedSource([], open_errors=10**6)
    runner = SourceRunner(
        source, LatestSlot(), MetricsHub(), on_frame=lambda: None, on_state=lambda *a: None,
        reconnect_initial_s=30, reconnect_max_s=30, max_read_failures=2,
    )  # fmt: skip
    runner.start()
    time.sleep(0.05)
    runner.stop()
    runner.join(2)
    assert not runner.is_alive()


def test_latest_slot_keeps_only_the_newest():
    slot = LatestSlot()
    assert slot.get() is None
    for i in range(3):
        slot.put(_frame(i, float(i)))
    assert slot.get().index == 2
    done = threading.Event()
    threading.Thread(target=lambda: (slot.put(_frame(9, 9.0)), done.set())).start()
    done.wait(1)
    assert slot.get().index == 9
