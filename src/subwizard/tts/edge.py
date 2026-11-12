"""Microsoft Edge online neural voices via `edge-tts` (SPEC 3.11). Qt-free.

Needs internet; sends only the line being read. Good Turkish out of the box
(tr-TR-EmelNeural), where Windows SAPI often has no Turkish voice installed. The MP3 it
returns is decoded with miniaudio and played on the chosen output device.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from subwizard.tts.base import BaseTTS, TTSError

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger(__name__)

VOICES = {"tr": "tr-TR-EmelNeural", "en": "en-US-AriaNeural"}
SAMPLE_RATE = 24000  # what the service sends; decoded at this rate, mono
TIMEOUT_S = 10


def rate_text(rate: float) -> str:
    """1.0 -> "+0%", 1.25 -> "+25%", 0.8 -> "-20%" (edge-tts's format)."""
    return f"{round((rate - 1.0) * 100):+d}%"


async def _synthesize(text: str, voice: str, rate: float) -> bytes:
    import edge_tts

    audio = bytearray()
    communicate = edge_tts.Communicate(text, voice, rate=rate_text(rate),
                                       connect_timeout=TIMEOUT_S, receive_timeout=TIMEOUT_S)  # fmt: skip
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
    return bytes(audio)


def decode_mp3(data: bytes) -> np.ndarray:
    """MP3 bytes -> float32 mono samples at SAMPLE_RATE."""
    import miniaudio
    import numpy as np

    sound = miniaudio.decode(data, output_format=miniaudio.SampleFormat.FLOAT32,
                             nchannels=1, sample_rate=SAMPLE_RATE)  # fmt: skip
    return np.frombuffer(sound.samples, dtype=np.float32)


def _play(samples: np.ndarray, sample_rate: int, device: int | None) -> None:
    import sounddevice as sd

    sd.play(samples, sample_rate, device=device, blocking=True)


def _stop_playback() -> None:
    import sounddevice as sd

    sd.stop()


class EdgeTTS(BaseTTS):
    name = "edge"

    def __init__(self, voice: str = "", device: int | None = None,
                 synthesize: Callable[[str, str, float], bytes] | None = None,
                 player: Callable[[np.ndarray, int, int | None], None] = _play) -> None:  # fmt: skip
        self._voice = voice
        self._device = device
        self._synthesize = synthesize or self._online
        self._player = player
        self._loop: asyncio.AbstractEventLoop | None = None  # the TTS thread's own loop
        self._stopped = threading.Event()

    def _online(self, text: str, voice: str, rate: float) -> bytes:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(_synthesize(text, voice, rate))

    def speak(self, text: str, language: str, rate: float) -> None:
        voice = self._voice or VOICES.get(language, VOICES["en"])
        try:
            data = self._synthesize(text, voice, rate)
        except Exception as exc:  # network down, service changed, timeout
            raise TTSError(f"edge-tts: {exc}") from exc
        if not data:
            raise TTSError("edge-tts: no audio")
        if self._stopped.is_set():
            return
        self._player(decode_mp3(data), SAMPLE_RATE, self._device)

    def stop(self) -> None:
        self._stopped.set()
        if self._player is _play:
            _stop_playback()

    def close(self) -> None:
        if self._loop is not None:
            self._loop.close()
