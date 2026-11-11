from pathlib import Path

import pytest
import yaml

from subwizard.config import AppConfig, ConfigError, load_config, save_config

REPO = Path(__file__).resolve().parent.parent


def test_missing_file_gives_defaults(tmp_path):
    config = load_config(tmp_path / "nope.yaml")
    assert config == AppConfig()
    assert config.source.fps == 60
    assert config.ui.theme == "dark"


def test_yaml_values_and_partial_sections(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("ui:\n  language: en\nsource:\n  fps: 30\n", encoding="utf-8")
    config = load_config(path)
    assert config.ui.language == "en"
    assert config.ui.theme == "dark"  # untouched keys keep defaults
    assert config.source.fps == 30


def test_overrides_beat_yaml_without_dropping_siblings(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("source:\n  kind: capture\n  fps: 30\n", encoding="utf-8")
    config = load_config(path, {"source": {"kind": "file", "path": "clip.mp4"}})
    assert config.source.kind == "file"
    assert config.source.path == Path("clip.mp4")
    assert config.source.fps == 30


def test_invalid_value_names_the_field(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("audio:\n  delay_ms: 9000\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"audio\.delay_ms"):
        load_config(path)


@pytest.mark.parametrize("text", ["ui: [unclosed", "- just\n- a list\n"])
def test_broken_yaml_is_a_config_error(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_empty_file_and_unknown_keys_are_tolerated(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("", encoding="utf-8")
    assert load_config(path) == AppConfig()
    path.write_text("future_section:\n  x: 1\nui:\n  future_key: 2\n", encoding="utf-8")
    assert load_config(path) == AppConfig()


def test_save_round_trip(tmp_path):
    path = tmp_path / "nested" / "config.yaml"
    config = AppConfig()
    config.ui.language = "tr"
    config.source.path = Path("clip.mp4")
    save_config(config, path)
    assert load_config(path) == config
    assert not path.with_name("config.yaml.tmp").exists()


def test_assignment_is_validated():
    config = AppConfig()
    with pytest.raises(ValueError):
        config.ui.theme = "neon"


def test_example_config_matches_defaults():
    example = yaml.safe_load((REPO / "config.example.yaml").read_text(encoding="utf-8"))
    assert AppConfig(**example) == AppConfig()
    # The example must document every key, so new settings are not forgotten there.
    assert example == AppConfig().model_dump(mode="json")
