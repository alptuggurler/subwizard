"""Audio passthrough contract (SPEC 3.2). Qt-free."""

from __future__ import annotations

from abc import ABC, abstractmethod


class AudioError(Exception):
    """Audio device could not be opened (message is user-facing)."""


class BaseAudioPassthrough(ABC):
    """Routes the capture card's audio input to an output device."""

    @abstractmethod
    def start(self) -> None:
        """Raises AudioError with a solution-oriented message."""

    @abstractmethod
    def stop(self) -> None: ...

    @property
    @abstractmethod
    def is_running(self) -> bool: ...

    @abstractmethod
    def set_delay_ms(self, delay_ms: int) -> None:
        """Applied live. Audio can only be delayed; negative values are clamped by the caller."""

    @abstractmethod
    def set_gain_db(self, gain_db: float) -> None: ...

    @abstractmethod
    def set_muted(self, muted: bool) -> None: ...
