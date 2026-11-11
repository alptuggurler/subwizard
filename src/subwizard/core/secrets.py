"""Gemini API keys (SPEC 3.6, 3.12). Qt-free.

Read in this order, first non-empty wins:
1. environment `GEMINI_API_KEYS` (comma separated) or `GEMINI_API_KEY`,
2. a `.env` file in the working directory or the user data directory (same names),
3. the OS credential store via `keyring` (Windows Credential Manager, macOS Keychain),
   which is where the UI saves them.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from subwizard import APP_NAME
from subwizard.utils.paths import user_data_dir

log = logging.getLogger(__name__)

KEYRING_SERVICE = APP_NAME
KEYRING_USER = "gemini_api_keys"
ENV_NAMES = ("GEMINI_API_KEYS", "GEMINI_API_KEY")


def split_keys(value: str) -> list[str]:
    keys: list[str] = []
    for part in value.replace("\n", ",").split(","):
        key = part.strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        values[name.removeprefix("export ").strip()] = value.strip().strip("'\"")
    return values


def env_file_paths() -> list[Path]:
    return [Path.cwd() / ".env", user_data_dir() / ".env"]


def keys_from_environment() -> list[str]:
    for name in ENV_NAMES:
        if os.environ.get(name):
            return split_keys(os.environ[name])
    for path in env_file_paths():
        values = _read_env_file(path)
        for name in ENV_NAMES:
            if values.get(name):
                return split_keys(values[name])
    return []


def keys_from_keyring() -> list[str]:
    try:
        import keyring

        return split_keys(keyring.get_password(KEYRING_SERVICE, KEYRING_USER) or "")
    except Exception:  # no backend, locked store: treat as "no keys saved"
        log.warning("Kimlik deposundan API anahtarı okunamadı", exc_info=True)
        return []


def load_api_keys() -> tuple[list[str], str]:
    """(keys, where they came from: "env" | "keyring" | "")."""
    keys = keys_from_environment()
    if keys:
        return keys, "env"
    keys = keys_from_keyring()
    return (keys, "keyring") if keys else ([], "")


def save_api_keys(keys: list[str]) -> None:
    """Store in the OS credential store; an empty list deletes the entry."""
    import keyring
    from keyring.errors import PasswordDeleteError

    if keys:
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, ",".join(keys))
        return
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    except PasswordDeleteError:
        pass


def mask(key: str) -> str:
    """For logs and the UI: never show a whole key."""
    return f"{key[:4]}…{key[-4:]}" if len(key) > 12 else "…"
