"""macOS `say` (development machine only; the target is Windows SAPI). Qt-free."""

from __future__ import annotations

import logging
import subprocess
import threading

from subwizard.tts.base import BaseTTS, TTSError

log = logging.getLogger(__name__)

WORDS_PER_MINUTE = 180  # `say`'s own default speed
LOCALES = {"tr": "tr_TR", "en": "en_"}


def parse_voices(listing: str) -> dict[str, str]:
    """`say -v '?'` lines ("Yelda   tr_TR   # Merhaba…") -> {voice: locale}."""
    voices: dict[str, str] = {}
    for line in listing.splitlines():
        head = line.split("#", 1)[0].split()
        if len(head) >= 2:
            voices[" ".join(head[:-1])] = head[-1]
    return voices


class SayTTS(BaseTTS):
    name = "say"

    def __init__(self, voice: str = "") -> None:
        self._voice = voice
        self._voices: dict[str, str] | None = None
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None

    def _voice_for(self, language: str) -> str:
        if self._voice:
            return self._voice
        if self._voices is None:
            listing = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
            self._voices = parse_voices(listing)
        prefix = LOCALES.get(language, language)
        return next((v for v, loc in self._voices.items() if loc.startswith(prefix)), "")

    def speak(self, text: str, language: str, rate: float) -> None:
        cmd = ["say", "-r", str(round(WORDS_PER_MINUTE * rate))]
        voice = self._voice_for(language)
        if voice:
            cmd += ["-v", voice]
        with self._lock:
            self._proc = subprocess.Popen([*cmd, "--", text])
        code = self._proc.wait()
        if code not in (0, -15):  # -15: stop() terminated it
            raise TTSError(f"say exited with {code}")

    def stop(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
