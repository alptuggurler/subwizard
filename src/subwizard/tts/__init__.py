"""Text-to-speech (SPEC 3.11, M7, optional). Qt-free."""

from __future__ import annotations

import logging
import sys

from subwizard.tts.base import BaseTTS

log = logging.getLogger(__name__)


def build_tts(engine: str, voice: str = "") -> BaseTTS | None:
    """`auto`: SAPI on Windows, `say` on macOS (development); None when unavailable."""
    if engine == "auto":
        engine = {"win32": "sapi", "darwin": "say"}.get(sys.platform, "")
    if engine == "sapi" and sys.platform == "win32":
        from subwizard.platform.windows.tts_sapi import SapiTTS

        return SapiTTS(voice)
    if engine == "say" and sys.platform == "darwin":
        from subwizard.tts.say import SayTTS

        return SayTTS(voice)
    log.warning("Sesli okuma motoru '%s' bu sistemde yok; kapalı.", engine)
    return None
