"""UI strings (SPEC 3.12): flat JSON per language, no hard-coded text in code. Qt-free.

Adding a language = dropping `<code>.json` next to this file with the same keys as
`en.json` (tests enforce key parity).
"""

from __future__ import annotations

import json
import locale
import logging
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

FALLBACK_LANGUAGE = "en"
META_NAME_KEY = "_meta.name"
_DIR = Path(__file__).parent


def _load(code: str) -> dict[str, str]:
    return json.loads((_DIR / f"{code}.json").read_text(encoding="utf-8"))


def available_languages() -> dict[str, str]:
    """Language code -> native name, e.g. {"en": "English", "tr": "Türkçe"}."""
    out = {}
    for file in sorted(_DIR.glob("*.json")):
        out[file.stem] = _load(file.stem).get(META_NAME_KEY, file.stem)
    return out


def match_language(locale_name: str | None) -> str:
    """Map a locale name ("tr_TR", "Turkish_Türkiye", "en-US") to an available language."""
    name = (locale_name or "").lower()
    codes = available_languages()
    for code in codes:
        if name.startswith(code):
            return code
    if name.startswith("turkish") and "tr" in codes:  # Windows-style locale names
        return "tr"
    return FALLBACK_LANGUAGE


def detect_system_language() -> str:
    try:
        return match_language(locale.getlocale()[0])
    except (ValueError, TypeError):
        return FALLBACK_LANGUAGE


class I18n:
    def __init__(self, language: str = FALLBACK_LANGUAGE) -> None:
        self._fallback = _load(FALLBACK_LANGUAGE)
        self._strings = self._fallback
        self._language = FALLBACK_LANGUAGE
        self._listeners: list[Callable[[str], None]] = []
        self.set_language(language)

    @property
    def language(self) -> str:
        return self._language

    def set_language(self, code: str) -> None:
        if code not in available_languages():
            log.warning("Bilinmeyen dil '%s'; %s kullanılıyor.", code, FALLBACK_LANGUAGE)
            code = FALLBACK_LANGUAGE
        if code == self._language:
            return
        self._strings = _load(code)
        self._language = code
        for listener in list(self._listeners):
            listener(code)

    def subscribe(self, listener: Callable[[str], None]) -> None:
        self._listeners.append(listener)

    def t(self, key: str, **fmt: object) -> str:
        """Translate `key`; falls back to English, then to the key itself."""
        text = self._strings.get(key)
        if text is None:
            text = self._fallback.get(key)
            if text is None:
                log.warning("Eksik çeviri anahtarı: %s", key)
                return key
        return text.format(**fmt) if fmt else text


i18n = I18n()
t = i18n.t
