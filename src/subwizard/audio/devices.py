"""Audio device enumeration via PortAudio (`sounddevice`). Qt-free.

On Windows only WASAPI devices are offered: it is the low-latency API the
passthrough uses, and listing every host API would show each device 4–5 times.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import numpy as np

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


def guess_capture_input(video_device_name: str, devices: list[AudioDevice]) -> str | None:
    """The capture card's own audio input, recognised by the card's name inside it,
    e.g. video "V-Z632" -> audio "Dijital Ses Arabirimi (V-Z632)". This is what OBS
    users pick by hand as "Use custom audio device"."""
    needle = video_device_name.strip().lower()
    if not needle:
        return None
    for device in devices:
        if needle in device.name.lower():
            return device.name
    return None


def wasapi_settings():
    """Shared-mode WASAPI with the system sample-rate converter enabled.

    Without it a stream only opens at the device mixer's own rate: an output whose
    Windows format is 96 kHz (e.g. SteelSeries Sonar) rejects a 48 kHz stream.
    None on other platforms.
    """
    if sys.platform != "win32":
        return None
    import sounddevice as sd

    return sd.WasapiSettings(auto_convert=True)


def play_blocking(samples: np.ndarray, sample_rate: int, device: int | None) -> None:
    """Play a whole buffer on an output device (test tone, TTS). Same WASAPI converter as
    the passthrough, or a 96 kHz output rejects anything else ("Invalid sample rate")."""
    import sounddevice as sd

    sd.play(samples, sample_rate, device=device, blocking=True,
            extra_settings=wasapi_settings())  # fmt: skip
