"""Audio device enumeration via PortAudio (`sounddevice`). Qt-free.

On Windows only WASAPI devices are offered: it is the low-latency API the
passthrough uses, and listing every host API would show each device 4–5 times.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)

PREFERRED_HOSTAPI = "WASAPI" if sys.platform == "win32" else None
Kind = Literal["input", "output"]


@dataclass(frozen=True, slots=True)
class AudioDevice:
    index: int
    name: str
    hostapi: str
    inputs: int
    outputs: int
    sample_rate: int
    is_default: bool = False


def list_audio_devices(kind: Kind) -> list[AudioDevice]:
    import sounddevice as sd  # lazy: importing loads PortAudio

    try:
        hostapis = sd.query_hostapis()
        raw = sd.query_devices()
        default_in, default_out = sd.default.device
    except Exception as exc:  # PortAudio raises its own error types
        log.warning("Ses cihazları listelenemedi: %s", exc)
        return []

    default_index = default_in if kind == "input" else default_out
    devices = []
    for info in raw:
        api = hostapis[info["hostapi"]]["name"]
        channels = info["max_input_channels" if kind == "input" else "max_output_channels"]
        if channels <= 0:
            continue
        if PREFERRED_HOSTAPI and PREFERRED_HOSTAPI not in api:
            continue
        devices.append(
            AudioDevice(
                index=info["index"],
                name=info["name"],
                hostapi=api,
                inputs=info["max_input_channels"],
                outputs=info["max_output_channels"],
                sample_rate=int(info["default_samplerate"]),
                is_default=info["index"] == default_index,
            )
        )
    return devices


def find_device(name: str | None, kind: Kind, devices: list[AudioDevice] | None = None) -> int | None:
    """Device index for a saved name. None means "system default" — also returned
    when the saved device is no longer plugged in (the caller decides what to do)."""
    if not name:
        return None
    devices = devices if devices is not None else list_audio_devices(kind)
    for device in devices:
        if device.name == name:
            return device.index
    lowered = name.lower()
    for device in devices:  # partial match, e.g. --audio-device "USB Capture"
        if lowered in device.name.lower():
            return device.index
    return None
