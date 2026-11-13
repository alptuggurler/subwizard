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

# Games show one sentence in pieces ("...scientists, the most" / "effective treatment for
# cancer the world" / "has ever seen."). A piece alone cannot be translated into a
# verb-final language ("has ever seen." -> "gördüğü"), so the unfinished sentence so far is
# sent together with the new piece and the translation of the whole is shown.
SENTENCE_END = ".!?…"
CLOSERS = "\"”’')]"  # may follow the final punctuation
CUT_OFF = ("--", "—", "–")  # an interrupted line is not continued by the next one
CONTINUES_AFTER = ",;:"
MERGE_MAX_FRAGMENTS = 4
MERGE_MAX_CHARS = 240
MERGE_WINDOW_S = 8.0  # pieces further apart than this are separate sentences


def sentence_open(text: str) -> bool:
    """True when the line stops in the middle of a sentence."""
    text = text.rstrip().rstrip(CLOSERS).rstrip()
    return bool(text) and text[-1] not in SENTENCE_END and not text.endswith(CUT_OFF)


def continues(previous: str, text: str) -> bool:
    """`text` goes on where the unfinished `previous` stopped: it starts in lower case, or
    the previous piece ended with a comma."""
    first = next((c for c in text if c.isalpha()), "")
    return first.islower() or previous.rstrip().endswith(tuple(CONTINUES_AFTER))


class FragmentJoiner:
    """Keeps the pieces of the sentence being shown; `text_for` returns what to translate
    for a new line: the line itself, or the open sentence so far plus the line."""

    def __init__(self) -> None:
        self._fragments: list[str] = []
        self._last_id: int | None = None
        self._last_speaker: str | None = None
        self._last_seen = 0.0

    def text_for(self, line: SubtitleLine) -> str:
        fragments = self._fragments
        grown = line.replaces is not None and line.replaces == self._last_id
        if fragments and grown:
            fragments.pop()  # the same piece, grown (typewriter): not a new one
        if fragments and (
            (not grown and line.id != (self._last_id or 0) + 1)  # a piece was skipped
            or line.speaker != self._last_speaker
            or line.seen_at - self._last_seen > MERGE_WINDOW_S
            or not continues(fragments[-1], line.text)
        ):
            fragments.clear()
        joined = " ".join([*fragments, line.text])
        if len(fragments) + 1 > MERGE_MAX_FRAGMENTS or len(joined) > MERGE_MAX_CHARS:
            fragments.clear()
            joined = line.text
        if sentence_open(line.text):
            fragments.append(line.text)
        else:
            fragments.clear()
        self._last_id, self._last_speaker, self._last_seen = line.id, line.speaker, line.seen_at
        return joined


@dataclass(frozen=True, slots=True)
class Translated:
    line: SubtitleLine
    result: TranslationResult | None  # None: nothing could translate it
    source: str = ""  # the text that was translated: the line, or the joined sentence


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
        self._joiner = FragmentJoiner()
        self._sent: tuple[int, str] | None = None  # (line id, text sent for it): F7 resends it
        self._partial_in_history = False  # the last context entry is an unfinished sentence

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
        text = line.text
        if force and self._sent is not None and self._sent[0] == line.id:
            text = self._sent[1]  # F7: the same (possibly joined) text again
        elif not force and self._config.translator.merge_fragments:
            text = self._joiner.text_for(line)
        merged = text != line.text
        if merged:
            log.debug("Yarım cümle birleştirildi: %r", text)
            if self._partial_in_history and self._history:
                self._history.pop()  # the shorter version of this sentence is not context
        self._sent = (line.id, text)
        request = TranslationRequest(text, self._context(), line.speaker)
        try:
            result: TranslationResult | None = self.service.translate(request, force=force)
        except TranslationError:
            result = None
        except Exception:  # one bad line must not end translation for the session
            log.exception("Çeviri sırasında beklenmeyen hata")
            result = None
        self._track_connection(result is not None)
        if result is not None:
            if self._history and self._history[-1][0] == text:
                self._history.pop()  # F7: the new translation replaces the old as context
            self._history.append((text, result.text))
            self._partial_in_history = sentence_open(line.text)
            self._metrics.set_gauge(M.TR_ENGINE, result.engine + (" (önbellek)" if result.cached else ""))
            self._metrics.set_gauge(M.TR_TEXT, result.text)
        else:
            self._partial_in_history = False
        self._on_translated(Translated(line, result, text))

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
