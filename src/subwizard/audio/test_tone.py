"""Sound test (SPEC 3.12 Cihaz & Ses: "Sesi test et"): a short tone on the chosen output
device, independent of the capture card. Answers "is the PC's sound working at all?"
before blaming the capture card input. Qt-free.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from subwizard.audio.devices import find_device, list_audio_devices, play_blocking

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger(__name__)

TONE_HZ = 440.0
TONE_S = 0.6
TONE_DB = -14.0  # clearly audible, not startling
FADE_S = 0.02  # no click at the start and end


def make_tone(sample_rate: int, channels: int, seconds: float = TONE_S, hz: float = TONE_HZ,
              level_db: float = TONE_DB) -> np.ndarray:  # fmt: skip
    """float32 (frames, channels) sine with short fades."""
    import numpy as np

    frames = int(sample_rate * seconds)
    t = np.arange(frames, dtype=np.float32) / sample_rate
    wave = np.sin(2 * np.pi * hz * t) * (10 ** (level_db / 20))
    fade = min(frames // 2, int(sample_rate * FADE_S))
    if fade:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        wave[:fade] *= ramp
        wave[-fade:] *= ramp[::-1]
    return np.repeat(wave.astype(np.float32)[:, None], channels, axis=1)


def play_test_tone(output_device: str | None, sample_rate: int,
                   player: Callable[[np.ndarray, int, int | None], None] = play_blocking) -> str:
    """Blocking. Returns the device name used; raises OSError-like errors from PortAudio
    (the caller shows them)."""
    outputs = list_audio_devices("output")
    index = find_device(output_device, "output", outputs)
    device = next((d for d in outputs if d.index == index), None)
    if device is None:
        device = next((d for d in outputs if d.is_default), None)
    channels = max(1, min(2, device.outputs if device else 2))
    log.info("Ses testi: %s, %d Hz, %d kanal", device.name if device else "varsayılan",
             sample_rate, channels)  # fmt: skip
    player(make_tone(sample_rate, channels), sample_rate, index)
    return device.name if device else ""
