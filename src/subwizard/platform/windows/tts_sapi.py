"""Windows SAPI voices through pyttsx3 (SPEC 3.11). Not run on macOS: unverified until
checked on Windows. pyttsx3 must be created and driven on one thread, so the engine is
built lazily on the TTS worker thread."""

from __future__ import annotations

import logging

from subwizard.tts.base import BaseTTS, TTSError

log = logging.getLogger(__name__)

WORDS_PER_MINUTE = 200  # pyttsx3's default rate
NAMES = {"tr": ("turkish", "tr-tr", "tr_tr"), "en": ("english", "en-us", "en-gb", "en_")}


def pick_voice(voices: list[tuple[str, str]], language: str) -> str:
    """(id, name) pairs -> the id of the first voice for `language`, else ""."""
    hints = NAMES.get(language, (f"{language}-", f"{language}_"))
    for voice_id, name in voices:
        text = f"{voice_id} {name}".lower()
        if any(h in text for h in hints):
            return voice_id
    return ""


class SapiTTS(BaseTTS):
    name = "sapi"

    def __init__(self, voice: str = "") -> None:
        self._voice = voice
        self._engine = None

    def _get(self):  # noqa: ANN202 - pyttsx3.Engine
        if self._engine is None:
            try:
                import pyttsx3

                self._engine = pyttsx3.init("sapi5")
            except Exception as exc:  # COM / missing SAPI
                raise TTSError(str(exc)) from exc
        return self._engine

    def speak(self, text: str, language: str, rate: float) -> None:
        engine = self._get()
        voice = self._voice or pick_voice(
            [(v.id, v.name) for v in engine.getProperty("voices")], language
        )
        if voice:
            engine.setProperty("voice", voice)
        else:
            log.warning("SAPI'de '%s' sesi yok; varsayılan ses kullanılıyor.", language)
        engine.setProperty("rate", round(WORDS_PER_MINUTE * rate))
        engine.say(text)
        engine.runAndWait()

    def stop(self) -> None:
        if self._engine is not None:
            self._engine.stop()
