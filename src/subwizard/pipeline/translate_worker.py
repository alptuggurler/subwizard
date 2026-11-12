"""TranslateWorker: translates stable subtitle lines on its own thread (SPEC 2, 3.6). Qt-free.

Latest-only like the frame slot: if two lines become stable while one request is in
flight, only the newest waits; the skipped one has already left the screen. The last
`translator.context_lines` translated lines go along as context.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from subwizard.config import AppConfig
from subwizard.core.metrics import M, MetricsHub
from subwizard.pipeline.subtitle_state import SubtitleLine
from subwizard.translator.base import TranslationError, TranslationRequest, TranslationResult
from subwizard.translator.service import TranslationService

log = logging.getLogger(__name__)

IDLE_WAIT_S = 0.5  # wake-up period while idle, to notice stop()


@dataclass(frozen=True, slots=True)
class Translated:
    line: SubtitleLine
    result: TranslationResult | None  # None: nothing could translate it


class TranslateWorker(threading.Thread):
    def __init__(
        self,
        config: AppConfig,
        metrics: MetricsHub,
        service_factory: Callable[[AppConfig, MetricsHub], TranslationService],
        on_translated: Callable[[Translated], None],
        on_ready: Callable[[TranslationService], None] = lambda service: None,
    ) -> None:
        super().__init__(name="translate", daemon=True)
        self._config = config
        self._metrics = metrics
        self._service_factory = service_factory
        self._on_translated = on_translated
        self._on_ready = on_ready
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop_event = threading.Event()
        self._pending: tuple[SubtitleLine, bool] | None = None
        self._history: deque[tuple[str, str]] = deque(maxlen=max(1, config.translator.context_lines))
        self.service: TranslationService | None = None
        self._failures = 0  # lines in a row nothing could translate (network down?)

    def submit(self, line: SubtitleLine, force: bool = False) -> None:
        """`force` skips the cache (F7: retranslate)."""
        with self._lock:
            if self._pending is not None:
                log.debug("Çeviri sırası: %r atlandı, yerine %r", self._pending[0].text, line.text)
            self._pending = (line, force)
        self._wake.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake.set()

    def _context(self) -> tuple[tuple[str, str], ...]:
        n = self._config.translator.context_lines
        return tuple(self._history)[-n:] if n else ()

    def run(self) -> None:
        try:
            self.service = self._service_factory(self._config, self._metrics)
        except Exception:
            log.exception("Çeviri motoru hazırlanamadı")
            return
        self._on_ready(self.service)
        try:
            while not self._stop_event.is_set():
                self._wake.wait(IDLE_WAIT_S)
                self._wake.clear()
                with self._lock:
                    pending, self._pending = self._pending, None
                if pending is None or self._stop_event.is_set():
                    continue
                self._translate(*pending)
        finally:
            log.info("Çeviri oturum özeti: %s", self.service.summary())
            self.service.close()

    def _translate(self, line: SubtitleLine, force: bool = False) -> None:
        request = TranslationRequest(line.text, self._context(), line.speaker)
        try:
            result: TranslationResult | None = self.service.translate(request, force=force)
        except TranslationError:
            result = None
        except Exception:  # one bad line must not end translation for the session
            log.exception("Çeviri sırasında beklenmeyen hata")
            result = None
        self._track_connection(result is not None)
        if result is not None:
            self._history.append((line.text, result.text))
            self._metrics.set_gauge(M.TR_ENGINE, result.engine + (" (önbellek)" if result.cached else ""))
            self._metrics.set_gauge(M.TR_TEXT, result.text)
        self._on_translated(Translated(line, result))

    def _track_connection(self, ok: bool) -> None:
        """SPEC M5: a network outage becomes one clear notice, and clears by itself on
        the next line that translates (requests keep going, no restart needed)."""
        limit = self._config.translator.offline_after
        was_offline = self._failures >= limit
        self._failures = 0 if ok else self._failures + 1
        offline = self._failures >= limit
        if offline and not was_offline:
            log.warning("Çeviri servisine ulaşılamıyor (%d satır çevrilemedi)", self._failures)
        elif was_offline and not offline:
            log.warning("Çeviri bağlantısı geri geldi")
        self._metrics.set_gauge(M.TR_OFFLINE, offline)
