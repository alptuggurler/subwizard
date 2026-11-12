"""Translator contract (SPEC 3.6). Qt-free."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TranslationRequest:
    text: str
    # Previous (source, translation) pairs, oldest first, for tone/name consistency.
    context: tuple[tuple[str, str], ...] = ()
    speaker: str | None = None


@dataclass(frozen=True, slots=True)
class TranslationResult:
    text: str
    engine: str
    latency_ms: float
    cached: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    fallback: bool = False  # produced by the fallback engine (overlay shows "(yedek)")
    cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class VisionResult:
    """Vision mode: what the model read in the image and its translation."""

    source: str  # "" = no subtitle in the image
    translation: str
    result: TranslationResult  # latency, tokens, cost


class TranslationError(Exception):
    """Engine failed (timeout, quota, blocked/empty reply); the caller falls back."""


BRIEF_CHARS = 200


def brief(exc: BaseException) -> str:
    """One line of an underlying error for the "Test" button and the log: the type and the
    start of the message, with API keys masked (SDK errors may echo request URLs)."""
    from subwizard.core.support import scrub

    text = " ".join(str(exc).split()) or type(exc).__name__
    text = scrub(f"{type(exc).__name__}: {text}")
    return text if len(text) <= BRIEF_CHARS else text[: BRIEF_CHARS - 1] + "…"


class BaseTranslator(ABC):
    name: str

    @abstractmethod
    def translate(self, request: TranslationRequest) -> TranslationResult:
        """Blocking; runs on the translator worker thread. Raises TranslationError."""

    def close(self) -> None:
        return None
