import importlib.util
import os
import sys
from pathlib import Path

import pytest

# UI tests must not need a display. On macOS the frameless-window helper needs the
# native platform plugin (it segfaults under "offscreen"); tests never show a window there.
if sys.platform != "darwin":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
CLIP_FPS = 30
CLIP_SECONDS = 1.0


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def clip(tmp_path_factory) -> Path:
    """A tiny synthetic clip: 320×180, 30 fps, 1 s, every frame unique."""
    path = tmp_path_factory.mktemp("clip") / "clip.mp4"
    _load_script("make_test_clip").make_clip(path, 320, 180, CLIP_FPS, CLIP_SECONDS)
    return path


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
