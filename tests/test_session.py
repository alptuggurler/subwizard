import threading
import time

import pytest

from subwizard.audio.base import AudioError, BaseAudioPassthrough
from subwizard.config import load_config
from subwizard.core.metrics import M, MetricsHub
from subwizard.core.session import AudioState, Session, build_source
from subwizard.sources.base import SourceError
from subwizard.sources.capture_card import CaptureCardSource
from subwizard.sources.runner import SourceState
from subwizard.sources.video_file import VideoFileSource
from tests.conftest import CLIP_FPS


class FakeAudio(BaseAudioPassthrough):
    def __init__(self, fail=False):
        self.fail, self.running, self.calls = fail, False, []

    def start(self):
        if self.fail:
            raise AudioError("error.audio_open", detail="device busy")
        self.running = True

    def stop(self):
        self.running = False

    is_running = property(lambda self: self.running)

    def set_delay_ms(self, delay_ms):
        self.calls.append(("delay", delay_ms))

    def set_gain_db(self, gain_db):
        self.calls.append(("gain", gain_db))

    def set_muted(self, muted):
        self.calls.append(("muted", muted))


def _session(overrides, audio=None):
    config = load_config(None, overrides)
    metrics = MetricsHub()
    events = {"frames": 0, "source": [], "audio": [], "got_frame": threading.Event()}

    def on_frame():
        events["frames"] += 1
        events["got_frame"].set()

    session = Session(
        config, metrics,
        on_frame=on_frame,
        on_source_state=lambda state, key: events["source"].append(state),
        on_audio_state=lambda state, msg: events["audio"].append((state, msg)),
        audio_factory=lambda cfg, m: audio,
    )  # fmt: skip
    return session, metrics, events


def test_file_source_runs_end_to_end(clip):
    session, metrics, events = _session({"source": {"kind": "file", "path": str(clip)}})
    session.start()
    try:
        assert events["got_frame"].wait(3)
        time.sleep(1.0)
        snap = metrics.snapshot()
        frame = session.latest_frame()
    finally:
        session.stop()
    assert frame is not None and frame.image.shape == (180, 320, 3)
    assert snap.rate(M.SOURCE_FPS).short == pytest.approx(CLIP_FPS, abs=3)
    assert snap.counter(M.SOURCE_REPEATED) == 0
    assert SourceState.RUNNING in events["source"]
    assert events["source"][-1] == SourceState.STOPPED
    assert session.latest_frame() is None and not session.running
    assert events["audio"][0] == (AudioState.OFF, "")  # a video file has no passthrough


def test_start_twice_and_stop_twice_are_harmless(clip):
    session, _, events = _session({"source": {"kind": "file", "path": str(clip)}})
    session.start()
    session.start()
    session.stop()
    session.stop()
    assert events["source"].count(SourceState.CONNECTING) == 1


def test_build_source_picks_the_right_kind(clip):
    file_cfg = load_config(None, {"source": {"kind": "file", "path": str(clip)}})
    assert isinstance(build_source(file_cfg), VideoFileSource)
    capture = build_source(load_config(None, {"source": {"device": 1}}))
    assert isinstance(capture, CaptureCardSource)
    assert str(capture.requested_mode) == "1920×1080@60 MJPG"
    with pytest.raises(SourceError) as exc:
        build_source(load_config(None, {"source": {"kind": "file"}}))
    assert exc.value.key == "error.file_not_set"


@pytest.mark.parametrize(
    ("audio_cfg", "fake", "expected"),
    [
        ({"mode": "off", "input_device": "USB"}, FakeAudio(), AudioState.OFF),
        ({"mode": "passthrough"}, FakeAudio(), AudioState.NOT_CONFIGURED),
        ({"mode": "passthrough", "input_device": "USB"}, FakeAudio(), AudioState.RUNNING),
        ({"mode": "passthrough", "input_device": "USB"}, FakeAudio(fail=True), AudioState.ERROR),
    ],
)
def test_audio_states(audio_cfg, fake, expected):
    session, _, events = _session({"audio": audio_cfg}, audio=fake)
    session.start_audio()
    state, message = events["audio"][-1]
    assert state == expected
    assert (session.audio is fake) == (expected == AudioState.RUNNING)
    if expected == AudioState.ERROR:
        assert "device busy" in message
    session.stop_audio()
    assert not fake.running and session.audio is None
