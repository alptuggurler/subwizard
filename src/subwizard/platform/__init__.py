"""Platform abstraction: everything OS-specific sits behind these functions.

Only Windows is implemented (SPEC 0); elsewhere device listing is empty, and a
capture device can still be opened by index for development.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from subwizard.sources.base import VideoMode

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class VideoDevice:
    index: int
    name: str


def list_video_devices() -> list[VideoDevice]:
    if sys.platform == "win32":
        from subwizard.platform.windows import devices

        return devices.list_video_devices()
    return []


def list_video_modes(index: int) -> list[VideoMode]:
    """Every resolution/FPS/format the device offers, best first."""
    if sys.platform == "win32":
        from subwizard.platform.windows import devices

        return devices.list_video_modes(index)
    return []


def find_video_device(name: str) -> int | None:
    devices = list_video_devices()
    for device in devices:
        if device.name == name:
            return device.index
    lowered = name.lower()
    for device in devices:
        if lowered in device.name.lower():
            return device.index
    return None


def exclude_from_capture(window_id: int) -> bool:
    """Keep a window (the PC-game overlay) out of screen captures, so OCR never reads
    our own text. Windows and macOS; False elsewhere."""
    if sys.platform == "win32":
        from subwizard.platform.windows import window

        return window.exclude_from_capture(window_id)
    if sys.platform == "darwin":
        from subwizard.platform.macos import window as mac_window

        return mac_window.exclude_from_capture(window_id)
    return False


def float_over_fullscreen(window_id: int) -> bool:
    """macOS: keep the overlay window visible over fullscreen apps (their own Space).
    Elsewhere always-on-top is enough: False, nothing to do."""
    if sys.platform == "darwin":
        from subwizard.platform.macos import window as mac_window

        return mac_window.float_over_fullscreen(window_id)
    return False


def set_dock_visible(visible: bool) -> bool:
    """macOS: show or hide the Dock icon (the menu bar item stays). False elsewhere."""
    if sys.platform == "darwin":
        from subwizard.platform.macos import dock

        return dock.set_dock_visible(visible)
    return False


def activate_app() -> None:
    """macOS: bring the app to the front (needed when it has no Dock icon)."""
    if sys.platform == "darwin":
        from subwizard.platform.macos import dock

        dock.activate()


def screen_capture_allowed() -> bool:
    """macOS: may this app see other apps' windows in screen grabs (Screen Recording
    permission)? Asks for it (system prompt, once) when not. True elsewhere, and when
    macOS cannot tell."""
    if sys.platform != "darwin":
        return True
    from subwizard.platform.macos import screen_access

    global _screen_access_asked
    if screen_access.allowed() is not False:
        return True
    if not _screen_access_asked:  # the source retries; the prompt is shown once
        _screen_access_asked = True
        screen_access.request()
    return False


_screen_access_asked = False
