"""Support bundle (SPEC M5 "log paketleme"): one zip a user can attach to a bug report.
Qt-free.

Contents: the newest session logs and subtitle logs, config.yaml with secrets masked,
the Diagnostics report (metrics), and system/version info. API keys never enter it:
they live only in the OS credential store, and anything key-shaped found in the logs is
masked again here as a safety net.
"""

from __future__ import annotations

import platform
import re
import time
import zipfile
from pathlib import Path

import yaml

from subwizard import APP_NAME, __version__
from subwizard.config import AppConfig
from subwizard.core.secrets import mask

MAX_LOG_FILES = 5  # newest session logs (and as many subtitle logs)
MAX_LOG_BYTES = 5 * 1024 * 1024  # tail of each file; a long session log can be large
# Google API keys ("AIza" + 35 chars) and anything after key=/token= in a URL or log line.
SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"(?i)((?:api[_-]?key|token|t)=)([^\s&\"']{6,})"),
)
SECRET_CONFIG_KEYS = {("web", "token")}


def scrub(text: str) -> str:
    text = SECRET_PATTERNS[0].sub(lambda m: mask(m.group(0)), text)
    return SECRET_PATTERNS[1].sub(lambda m: m.group(1) + mask(m.group(2)), text)


def _config_text(config: AppConfig) -> str:
    data = config.model_dump(mode="json")
    for section, key in SECRET_CONFIG_KEYS:
        if data.get(section, {}).get(key):
            data[section][key] = mask(data[section][key])
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _newest(log_dir: Path, pattern: str) -> list[Path]:
    files = sorted(log_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:MAX_LOG_FILES]


def _tail(path: Path) -> str:
    with path.open("rb") as f:
        size = f.seek(0, 2)
        f.seek(max(0, size - MAX_LOG_BYTES))
        return f.read().decode("utf-8", errors="replace")


def system_info() -> str:
    return "\n".join([
        f"{APP_NAME} {__version__}",
        f"OS: {platform.platform()}",
        f"Python: {platform.python_version()} ({platform.machine()})",
        f"Created: {time.strftime('%Y-%m-%d %H:%M:%S')}",
    ])  # fmt: skip


def make_support_bundle(dest: Path, config: AppConfig, log_dir: Path,
                        diagnostics: str = "") -> Path:  # fmt: skip
    """Write the zip to `dest` and return it. Missing logs are fine (fresh install)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("system.txt", system_info())
        z.writestr("config.yaml", _config_text(config))
        if diagnostics:
            z.writestr("diagnostics.txt", scrub(diagnostics))
        if log_dir.is_dir():
            for pattern in ("*.log", "subtitles-*.srt"):
                for path in _newest(log_dir, pattern):
                    z.writestr(f"logs/{path.name}", scrub(_tail(path)))
    return dest
