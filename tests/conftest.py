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


class MemoryKeyring:
    """Stands in for the OS credential store: tests never touch the real Keychain."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, password):
        self.store[(service, user)] = password

    def delete_password(self, service, user):
        from keyring.errors import PasswordDeleteError

        if self.store.pop((service, user), None) is None:
            raise PasswordDeleteError("not set")


def fake_google_translate(self, text: str) -> str:
    return f"[tr] {text}"


@pytest.fixture(autouse=True)
def isolated_user_state(tmp_path, monkeypatch, request):
    """Every test gets its own user data dir (config, logs, translation cache), no real
    API keys, an in-memory keyring and an offline fallback translator. Tests marked
    `network` keep the real fallback."""
    import keyring

    data = tmp_path / "userdata"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(data))
    monkeypatch.setenv("APPDATA", str(data))
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEYS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # a developer's ./.env must not leak into tests
    memory = MemoryKeyring()
    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, getattr(memory, name))
    if request.node.get_closest_marker("network") is None:
        from subwizard.translator.deep import DeepTranslatorFallback

        monkeypatch.setattr(DeepTranslatorFallback, "_google", fake_google_translate)
    return memory
