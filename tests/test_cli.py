from pathlib import Path

import pytest

from subwizard import cli
from subwizard.cli import build_parser, overrides_from_args


def _overrides(*argv: str) -> dict:
    return overrides_from_args(build_parser().parse_args(list(argv)))


def test_no_flags_no_overrides():
    assert _overrides() == {}


def test_spec_examples_parse():
    assert _overrides("--source", "capture", "--device", "1", "--audio-device", "USB Capture") == {
        "source": {"kind": "capture", "device": 1},
        "audio": {"input_device": "USB Capture"},
    }
    assert _overrides("--source", "file", "--path", "tests/fixtures/clip.mp4") == {
        "source": {"kind": "file", "path": Path("tests/fixtures/clip.mp4")}
    }
    assert _overrides("--translator", "deep", "--profile", "gta6", "--display", "both") == {
        "translator": {"engine": "deep"},
        "display": {"mode": "both"},
        "profile": "gta6",
    }


def test_device_by_name_stays_a_string():
    assert _overrides("--device", "USB Video")["source"]["device"] == "USB Video"


def test_debug_raises_log_level():
    assert _overrides("--debug") == {"debug": True, "logging": {"level": "DEBUG"}}


def test_file_source_requires_path(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--source", "file"])
    assert "--path" in capsys.readouterr().err


def test_invalid_config_exits_with_message(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    path.write_text("source:\n  fps: -1\n", encoding="utf-8")
    assert cli.main(["--config", str(path)]) == cli.EXIT_CONFIG_ERROR
    assert "source.fps" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--calibrate", "--headless"])
def test_unbuilt_flags_fail_loudly_instead_of_opening_gui(tmp_path, monkeypatch, flag):
    monkeypatch.setattr(cli, "default_log_dir", lambda: tmp_path / "logs")
    assert cli.main(["--config", str(tmp_path / "config.yaml"), flag]) == cli.EXIT_NOT_IMPLEMENTED
