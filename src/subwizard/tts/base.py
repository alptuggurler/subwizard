"""Text-to-speech contract (SPEC 3.11, M7). Qt-free."""

from __future__ import annotations

from abc import ABC, abstractmethod


class TTSError(Exception):
    """The engine could not speak (no voice, device busy, process failed)."""


class BaseTTS(ABC):
    name = "base"

    @abstractmethod
    def speak(self, text: str, language: str, rate: float) -> None:
        """Blocking. `language`: "tr" or "en"; `rate`: 1.0 = the engine's normal speed."""

    def stop(self) -> None:  # noqa: B027 - optional: interrupt speech in progress
        """Cut off the current utterance (called from another thread)."""

    def close(self) -> None:  # noqa: B027
        pass
