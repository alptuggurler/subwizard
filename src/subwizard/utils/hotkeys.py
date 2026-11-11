"""Hotkey names and the global listener (SPEC 3.8). Qt-free.

Keys are stored in Qt's portable text form ("F5", "Ctrl+Shift+S"). The global listener
(pynput) runs on its own thread and only calls back with the action name; the UI side
moves that onto the Qt main thread with a signal.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

log = logging.getLogger(__name__)

ACTIONS = (
    "calibrate", "copy_last", "mute", "retranslate", "pause_translation",
    "perf_overlay", "toggle_subtitles", "fullscreen", "history_panel",
)  # fmt: skip
MODIFIERS = {"ctrl": "<ctrl>", "shift": "<shift>", "alt": "<alt>", "meta": "<cmd>"}


def normalize_key(text: str) -> str:
    """"f5" / " ctrl + s " -> "F5" / "Ctrl+S"."""
    out = []
    for part in (p.strip() for p in text.split("+")):
        low = part.lower()
        if not part:
            continue
        if low in MODIFIERS:
            out.append(low.capitalize())
        elif (low.startswith("f") and low[1:].isdigit()) or len(part) == 1:
            out.append(part.upper())
        else:
            out.append(part.capitalize())
    return "+".join(out)


def conflicts(keys: dict[str, str]) -> dict[str, list[str]]:
    """Keys bound to more than one action: {key: [actions]}; empty bindings are ignored."""
    by_key: dict[str, list[str]] = {}
    for action, key in keys.items():
        if key:
            by_key.setdefault(normalize_key(key), []).append(action)
    return {k: v for k, v in by_key.items() if len(v) > 1}


def to_pynput(key: str) -> str | None:
    """"Ctrl+F5" -> "<ctrl>+<f5>", "Ctrl+S" -> "<ctrl>+s"; None if it cannot be expressed."""
    out = []
    for part in normalize_key(key).split("+"):
        low = part.lower()
        if low in MODIFIERS:
            out.append(MODIFIERS[low])
        elif low.startswith("f") and low[1:].isdigit():
            out.append(f"<{low}>")
        elif len(part) == 1:
            out.append(low)
        else:
            return None
    return "+".join(out) if out else None


class GlobalHotkeys:
    """System-wide keys while another window (the game) has focus. Windows only: on
    macOS pynput needs accessibility permission, and the app is not targeted there."""

    def __init__(self) -> None:
        self._listener = None

    @staticmethod
    def supported() -> bool:
        return sys.platform == "win32"

    def start(self, keys: dict[str, str], on_action: Callable[[str], None]) -> bool:
        self.stop()
        if not self.supported():
            return False
        mapping = {}
        for action, key in keys.items():
            combo = to_pynput(key) if key else None
            if combo is None:
                if key:
                    log.warning("Kısayol '%s' (%s) global olarak dinlenemiyor.", key, action)
                continue
            mapping[combo] = lambda a=action: on_action(a)
        try:
            from pynput import keyboard

            self._listener = keyboard.GlobalHotKeys(mapping)
            self._listener.daemon = True
            self._listener.start()
        except Exception:  # no keyboard access: in-app shortcuts still work
            log.exception("Global kısayollar başlatılamadı")
            self._listener = None
            return False
        log.info("Global kısayollar etkin: %s", ", ".join(sorted(mapping)))
        return True

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
