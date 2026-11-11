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
