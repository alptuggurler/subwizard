"""Prompt text for LLM translation (SPEC 3.6). Qt-free."""

from __future__ import annotations

from subwizard.translator.base import TranslationRequest

DEFAULT_SYSTEM_PROMPT = (
    "Sen profesyonel bir oyun yerelleştirme çevirmenisin. Sana İngilizce bir oyun altyazısı "
    "ve önceki birkaç satırın bağlamı verilecek. Yalnızca son satırı doğal, akıcı, konuşma "
    "diline uygun Türkçeye çevir. Argo, küfür ve esprileri sansürlemeden, Türkçede karşılığı "
    "olan en doğal ifadeyle aktar. Özel isimleri (karakter, mekân, marka) çevirme. Açıklama, "
    "tırnak veya ek metin ekleme; yalnızca çeviriyi döndür."
)


def system_prompt(override: str) -> str:
    return override.strip() or DEFAULT_SYSTEM_PROMPT


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
