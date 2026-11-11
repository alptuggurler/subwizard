"""Where user data (config, logs, cache) lives. Qt-free."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from subwizard import APP_NAME

CONFIG_FILENAME = "config.yaml"


def user_data_dir() -> Path:
    """Per-user data directory (%APPDATA%/SubWizard on Windows)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME.lower()


def default_config_path() -> Path:
    """`./config.yaml` if present (developer / portable use), else the user data dir."""
    local = Path.cwd() / CONFIG_FILENAME
    if local.is_file():
        return local
    return user_data_dir() / CONFIG_FILENAME


def default_log_dir() -> Path:
    return user_data_dir() / "logs"
