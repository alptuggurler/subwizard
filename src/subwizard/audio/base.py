"""Audio passthrough contract (SPEC 3.2). Qt-free."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

from subwizard.core.errors import UserError


class AudioError(UserError):
    """Audio device could not be opened (carries a solution-oriented i18n message)."""


class BaseAudioPassthrough(ABC):
    """Routes the capture card's audio input to an output device."""

    # Called with every input block on the audio thread (A/V "Otomatik ölç"); keep it cheap.
    tap: Callable[[np.ndarray], None] | None = None

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
