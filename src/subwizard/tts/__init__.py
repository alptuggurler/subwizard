"""Text-to-speech (SPEC 3.11, M7, optional). Qt-free."""

from __future__ import annotations

import logging
import sys

from subwizard.tts.base import BaseTTS

log = logging.getLogger(__name__)


def build_tts(engine: str, voice: str = "", output_device: str | None = None) -> BaseTTS | None:
    """`auto`: SAPI on Windows, `say` on macOS (development); `edge`: online neural voices
    anywhere. None when unavailable."""
    if engine == "auto":
        engine = {"win32": "sapi", "darwin": "say"}.get(sys.platform, "")
    if engine == "edge":
        from subwizard.audio.devices import find_device
        from subwizard.tts.edge import EdgeTTS

        return EdgeTTS(voice, find_device(output_device, "output"))
    if engine == "sapi" and sys.platform == "win32":
        from subwizard.platform.windows.tts_sapi import SapiTTS

        return SapiTTS(voice)
    if engine == "say" and sys.platform == "darwin":
        from subwizard.tts.say import SayTTS

        return SayTTS(voice)
    log.warning("Sesli okuma motoru '%s' bu sistemde yok; kapalı.", engine)
    return None
