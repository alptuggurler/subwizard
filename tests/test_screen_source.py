"""PC game mode (Faz 2): ScreenSource with a fake mss; no real screen is touched."""

import numpy as np
import pytest

from subwizard.sources.base import SourceError
from subwizard.sources.screen import ScreenSource, list_monitors

MONITORS = [{"left": 0, "top": 0, "width": 3840, "height": 1080},  # all, as mss reports
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 0, "width": 1920, "height": 1080}]


class FakeMSS:
    def __init__(self, fail=False):
        self.monitors = MONITORS
        self.grabbed = []
        self.closed = False
        self.fail = fail

    def grab(self, region):
        if self.fail:
            raise RuntimeError("display changed")
        self.grabbed.append(region)
        image = np.zeros((region["height"] // 10, region["width"] // 10, 4), np.uint8)
        image[..., 2] = 200  # red in BGRA
        image[..., 3] = 255
        return image

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def test_second_monitor_is_captured_as_bgr_at_the_set_rate():
    fake = FakeMSS()
    src = ScreenSource(monitor=2, fps=50, factory=lambda: fake)
    src.open()
    assert src.region == MONITORS[2] and src.backend == "mss" and src.requested_mode is None
    assert src.actual_mode.width == 192 and src.actual_mode.fps == 50.0
    frames = [src.read() for _ in range(6)]
    assert [f.index for f in frames] == [1, 2, 3, 4, 5, 6]
    image = frames[0].image
    assert image.shape == (108, 192, 3) and image.flags["C_CONTIGUOUS"]
    assert image[0, 0].tolist() == [0, 0, 200]
    span = frames[-1].timestamp - frames[0].timestamp
    assert 0.08 <= span <= 0.4  # 5 intervals of 20 ms, paced
    src.close()
    assert fake.closed and not src.is_open and src.read() is None


def test_missing_monitor_and_failed_grabs():
    with pytest.raises(SourceError) as err:
        ScreenSource(monitor=5, factory=FakeMSS).open()
    assert err.value.key == "error.screen_not_found"
    with pytest.raises(SourceError):
        ScreenSource(factory=lambda: FakeMSS(fail=True)).open()
    src = ScreenSource(factory=FakeMSS)
    src.open()
    src._grabber.fail = True
    assert src.read() is None  # the runner counts it and reconnects after enough failures


def test_monitor_list_skips_the_combined_entry():
    assert list_monitors(FakeMSS) == MONITORS[1:]

    def broken():
        raise OSError("no display")

    assert list_monitors(broken) == []


def test_session_builds_a_screen_source_from_config():
    from subwizard.config import AppConfig
    from subwizard.core.session import build_source

    config = AppConfig()
    config.source.kind = "screen"
    config.source.monitor = 2
    source = build_source(config)
    assert isinstance(source, ScreenSource) and source._monitor_no == 2


def test_missing_macos_permission_is_said_plainly(monkeypatch):
    from subwizard import platform

    monkeypatch.setattr(platform, "screen_capture_allowed", lambda: False)
    opened = []
    with pytest.raises(SourceError) as raised:
        ScreenSource(factory=lambda: opened.append(1) or FakeMSS()).open()
    assert raised.value.key == "error.screen_permission" and not opened  # never grabbed


def test_permission_check_asks_only_when_denied(monkeypatch):
    import sys

    from subwizard import platform
    from subwizard.platform.macos import screen_access

    monkeypatch.undo()  # the real wrapper, with the CoreGraphics calls faked
    monkeypatch.setattr(sys, "platform", "darwin")
    asked = []
    monkeypatch.setattr(screen_access, "request", lambda: asked.append(1))
    for state, expected in ((True, True), (None, True), (False, False)):
        monkeypatch.setattr(screen_access, "allowed", lambda s=state: s)
        assert platform.screen_capture_allowed() is expected
    assert asked == [1]
