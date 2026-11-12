"""Prompt text for LLM translation (SPEC 3.6). Qt-free."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from subwizard.profiles import KEEP
from subwizard.translator.base import TranslationRequest

if TYPE_CHECKING:
    from subwizard.profiles import GameProfile

DEFAULT_SYSTEM_PROMPT = (
    "Sen profesyonel bir oyun yerelleştirme çevirmenisin. Sana İngilizce bir oyun altyazısı "
    "ve önceki birkaç satırın bağlamı verilecek. Yalnızca son satırı doğal, akıcı, konuşma "
    "diline uygun Türkçeye çevir. Argo, küfür ve esprileri sansürlemeden, Türkçede karşılığı "
    "olan en doğal ifadeyle aktar. Özel isimleri (karakter, mekân, marka) çevirme. Açıklama, "
    "tırnak veya ek metin ekleme; yalnızca çeviriyi döndür."
)


def system_prompt(override: str, profile: GameProfile | None = None) -> str:
    """The base prompt plus the game profile: game info, glossary, extra instruction."""
    parts = [override.strip() or DEFAULT_SYSTEM_PROMPT]
    if profile is None:
        return parts[0]
    if profile.game_info.strip():
        parts.append(f"Oyun hakkında: {profile.game_info.strip()}")
    if profile.glossary:
        lines = ["Bu terimleri her zaman böyle çevir:"]
        for term, target in profile.glossary.items():
            if target.lower() == KEEP:
                lines.append(f"- {term} → {term} (çevirme, olduğu gibi bırak)")
            else:
                lines.append(f"- {term} → {target}")
        parts.append("\n".join(lines))
    if profile.prompt_extra.strip():
        parts.append(profile.prompt_extra.strip())
    return "\n\n".join(parts)


# --- keep terms for engines without a prompt (Google Translate) -------------------------
# A keep term is swapped for a token that machine translation leaves alone, then put
# back. Digits inside a rare letter pattern survive Google Translate as one "word".
TOKEN = "ZQX{}"
_TOKEN_RE = re.compile(r"ZQX(\d+)", re.IGNORECASE)


def protect(text: str, profile: GameProfile | None) -> tuple[str, list[str]]:
    """`text` with keep terms replaced by tokens, and the original spellings in order."""
    if profile is None or not profile.keep_terms:
        return text, []
    originals: list[str] = []
    # Longest first, so "Vice City" is taken before a shorter keep term inside it.
    for term in sorted(profile.keep_terms, key=len, reverse=True):
        pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)

        def swap(match: re.Match[str]) -> str:
            originals.append(match.group(0))
            return TOKEN.format(len(originals) - 1)

        text = pattern.sub(swap, text)
    return text, originals


def restore(text: str, originals: list[str]) -> str:
    if not originals:
        return text

    def back(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return originals[index] if index < len(originals) else match.group(0)

    return _TOKEN_RE.sub(back, text)


def user_prompt(request: TranslationRequest) -> str:
    """Context lines first (oldest first), then the line to translate."""
    parts: list[str] = []
    if request.context:
        parts.append("Önceki satırlar (yalnızca bağlam, çevirme):")
        for source, translation in request.context:
            parts.append(f"EN: {source}\nTR: {translation}")
        parts.append("")
    if request.speaker:
        parts.append(f"Konuşan: {request.speaker}")
    parts.append(f"Çevrilecek satır:\n{request.text}")
    return "\n".join(parts)


QUOTES = "\"'“”‘’«»"


def clean_reply(text: str) -> str:
    """Strip what models add despite the prompt: surrounding quotes, a "TR:" label."""
    text = text.strip()
    for label in ("TR:", "Türkçe:", "Çeviri:"):
        if text.startswith(label):
            text = text[len(label):].strip()
    if len(text) >= 2 and text[0] in QUOTES and text[-1] in QUOTES:
        text = text[1:-1].strip()
    return text
