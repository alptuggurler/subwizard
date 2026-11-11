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


class TranslationError(Exception):
    """Engine failed (timeout, quota, blocked/empty reply); the caller falls back."""


class BaseTranslator(ABC):
    name: str

    @abstractmethod
    def translate(self, request: TranslationRequest) -> TranslationResult:
        """Blocking; runs on the translator worker thread. Raises TranslationError."""

    def close(self) -> None:
        return None
