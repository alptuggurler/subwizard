"""Application config: `config.yaml` + env, validated with pydantic-settings. Qt-free.

Priority: explicit overrides (CLI) > config.yaml > environment (SUBWIZARD_*) > defaults.
Sections grow with each milestone; unknown keys are ignored so an older or newer
config file never prevents the app from starting.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

Language = Literal["auto", "tr", "en"]
ThemeMode = Literal["auto", "dark", "light"]


class ConfigError(Exception):
    """config.yaml could not be read or failed validation (message is user-facing)."""


class _Section(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="ignore")


class UIConfig(_Section):
    language: Language = "auto"
    theme: ThemeMode = "dark"
    close_to_tray: bool = True


class LoggingConfig(_Section):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    dir: Path | None = None


class SourceConfig(_Section):
    kind: Literal["capture", "file", "screen"] = "capture"
    device: int | str = 0
    path: Path | None = None
    width: int = Field(1920, gt=0)
    height: int = Field(1080, gt=0)
    fps: int = Field(60, gt=0)
    fourcc: str = Field("MJPG", min_length=4, max_length=4)
    buffer_size: int = Field(1, ge=1)


class AudioConfig(_Section):
    mode: Literal["passthrough", "off"] = "passthrough"
    input_device: str | None = None
    delay_ms: int = Field(0, ge=-200, le=500)


class TranslatorConfig(_Section):
    engine: Literal["gemini", "deep"] = "gemini"
    model: str = "gemini-3.5-flash-lite"
    context_lines: int = Field(5, ge=0)
    timeout_s: float = Field(2.5, gt=0)


class DisplayConfig(_Section):
    mode: Literal["window", "web", "both"] = "window"


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SUBWIZARD_",
        env_nested_delimiter="__",
        validate_assignment=True,
        extra="ignore",
    )

    ui: UIConfig = UIConfig()
    logging: LoggingConfig = LoggingConfig()
    source: SourceConfig = SourceConfig()
    audio: AudioConfig = AudioConfig()
    translator: TranslatorConfig = TranslatorConfig()
    display: DisplayConfig = DisplayConfig()
    profile: str = "default"
    debug: bool = False


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _describe(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        where = ".".join(str(part) for part in err["loc"])
        lines.append(f"  - {where}: {err['msg']}")
    return "\n".join(lines)


def load_config(path: Path | None = None, overrides: dict[str, Any] | None = None) -> AppConfig:
    """Load and validate config. A missing file just means defaults."""
    data: dict[str, Any] = {}
    if path is not None and path.is_file():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigError(f"Ayar dosyası okunamadı: {path}\n  {exc}") from exc
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ConfigError(f"Ayar dosyası geçersiz (en üst düzey bir sözlük olmalı): {path}")
        data = loaded
    if overrides:
        data = _deep_merge(data, overrides)
    try:
        return AppConfig(**data)
    except ValidationError as exc:
        source = path if path is not None else "ayarlar"
        raise ConfigError(f"Ayarlar geçersiz ({source}):\n{_describe(exc)}") from exc


def save_config(config: AppConfig, path: Path) -> None:
    """Write config atomically so a crash mid-save cannot corrupt the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        config.model_dump(mode="json"), sort_keys=False, allow_unicode=True
    )
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
