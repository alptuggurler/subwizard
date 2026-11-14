"""Session: one run of the pipeline, from Start to Stop. Qt-free.

Wires video source, audio passthrough, OCR, the subtitle state machine and translation.
Callbacks fire on worker threads — the controller turns them into Qt signals.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from enum import StrEnum

from subwizard import platform
from subwizard.audio.base import AudioError, BaseAudioPassthrough
from subwizard.audio.avsync import AVSyncRecorder
from subwizard.audio.devices import (
    AudioDevice,
    find_device,
    list_audio_devices,
    other_input_than_card,
)
from subwizard.audio.meter import LevelMeter
from subwizard.audio.passthrough import PassthroughSettings, SoundDevicePassthrough
from subwizard.config import AppConfig
from subwizard.core.metrics import M, MetricsHub
from subwizard.ocr import vision
from subwizard.ocr.base import BaseOCR
from subwizard.ocr.worker import OCRState, OCRTick, OCRWorker, build_ocr
from subwizard.pipeline.board import SubtitleBoard, SubtitleView
from subwizard.pipeline.corpus import SubtitleCorpus
from subwizard.pipeline.history import SubtitleHistory
from subwizard.profiles import load_profile
from subwizard.pipeline.subtitle_state import LineCleared, LineStable, SubtitleStateMachine
from subwizard.pipeline.translate_worker import TranslateWorker, Translated, sentence_open
from subwizard.translator.service import TranslationService, build_service
from subwizard.sources.base import BaseSource, Frame, LatestSlot, SourceError, VideoMode
from subwizard.sources.capture_card import CaptureCardSource
from subwizard.sources.runner import SourceRunner, SourceState
from subwizard.sources.video_file import VideoFileSource
from subwizard.tts import build_tts
from subwizard.tts.base import BaseTTS
from subwizard.tts.worker import TTSWorker, Utterance
from subwizard.utils import timing
from subwizard.utils.paths import default_log_dir, user_data_dir

log = logging.getLogger(__name__)

MAX_AUDIO_DELAY_MS = 500  # upper bound of audio.delay_ms; sizes the delay buffer
STOP_JOIN_TIMEOUT_S = 3.0


class AudioState(StrEnum):
    OFF = "off"  # disabled in settings, or not applicable (video file)
    NOT_CONFIGURED = "not_configured"  # no input device chosen yet
    RUNNING = "running"
    ERROR = "error"


def build_source(config: AppConfig) -> BaseSource:
    src = config.source
    if src.kind == "file":
        if src.path is None:
            raise SourceError("error.file_not_set")
        return VideoFileSource(src.path, loop=src.loop)
    if src.kind == "capture":
        return CaptureCardSource(
            src.device,
            VideoMode(src.width, src.height, float(src.fps), src.fourcc),
            src.buffer_size,
            resolve_name=platform.find_video_device,
        )
    if src.kind == "screen":
        from subwizard.sources.screen import ScreenSource

        return ScreenSource(src.monitor, src.screen_fps)
    raise SourceError("error.source_unsupported", kind=src.kind)


def build_passthrough(config: AppConfig, metrics: MetricsHub) -> BaseAudioPassthrough:
    audio = config.audio
    inputs, outputs = list_audio_devices("input"), list_audio_devices("output")
    input_index = find_device(audio.input_device, "input", inputs)
    if input_index is None:
        raise AudioError("error.audio_input_missing", name=audio.input_device)
    if config.source.kind == "capture" and isinstance(config.source.device, str):
        card = other_input_than_card(audio.input_device, config.source.device, inputs)
        if card:
            log.warning("Ses girişi '%s' capture card'ınki değil; kartın sesi '%s' girişinde.",
                        audio.input_device, card)  # fmt: skip
    output_index = find_device(audio.output_device, "output", outputs)
    if audio.output_device and output_index is None:
        log.warning("Ses çıkışı '%s' bulunamadı; varsayılan kullanılıyor.", audio.output_device)
    channels = _channels(audio.channels, input_index, output_index, inputs, outputs)
    if audio.delay_ms < 0:
        log.warning("audio.delay_ms negatif (%d): ses öne alınamaz, 0 uygulanıyor.", audio.delay_ms)
    profile = audio.profiles[audio.profile]
    settings = PassthroughSettings(
        input_device=input_index,
        output_device=output_index,
        sample_rate=audio.sample_rate,
        channels=channels,
        block=profile.block,
        buffer_ms=profile.buffer_ms,
        delay_ms=audio.delay_ms,
        max_delay_ms=MAX_AUDIO_DELAY_MS,
        gain_db=audio.gain_db,
        muted=audio.muted,
    )
    meter = LevelMeter(metrics, audio.silence_db, audio.silence_s, audio.clip_db)
    return SoundDevicePassthrough(settings, metrics, meter)


def _channels(
    wanted: int,
    input_index: int,
    output_index: int | None,
    inputs: list[AudioDevice],
    outputs: list[AudioDevice],
) -> int:
    """Both streams share one buffer, so they need the same channel count: the
    configured one, limited by what each device has (a mono capture input works)."""
    limits = [d.inputs for d in inputs if d.index == input_index]
    limits += [d.outputs for d in outputs if d.index == output_index or
               (output_index is None and d.is_default)]  # fmt: skip
    return max(1, min([wanted, *[n for n in limits if n > 0]]))


class Session:
    def __init__(
        self,
        config: AppConfig,
        metrics: MetricsHub,
        *,
        on_frame: Callable[[], None],
        on_source_state: Callable[[SourceState, str], None],
        on_audio_state: Callable[[AudioState, str], None],
        on_ocr_tick: Callable[[OCRTick], None] = lambda tick: None,
        on_ocr_state: Callable[[OCRState, str], None] = lambda state, msg: None,
        source_factory: Callable[[AppConfig], BaseSource] = build_source,
        audio_factory: Callable[[AppConfig, MetricsHub], BaseAudioPassthrough] = build_passthrough,
        ocr_factory: Callable[[AppConfig], BaseOCR] | None = build_ocr,
        on_subtitle: Callable[[SubtitleView | None], None] = lambda view: None,
        on_translated: Callable[[Translated], None] = lambda item: None,
        translate_factory: Callable[[AppConfig, MetricsHub], TranslationService] | None = (
            build_service
        ),
        tts_factory: Callable[[str, str, str | None], BaseTTS | None] = build_tts,
    ) -> None:
        self._config = config
        self._metrics = metrics
        self._on_frame = on_frame
        self._on_source_state = on_source_state
        self._on_audio_state = on_audio_state
        self._source_factory = source_factory
        self._audio_factory = audio_factory
        self._ocr_factory = ocr_factory  # None disables OCR (e.g. tests of video only)
        self._on_ocr_tick = on_ocr_tick
        self._on_ocr_state = on_ocr_state
        self._ocr: OCRWorker | None = None
        self._on_subtitle = on_subtitle
        self._on_translated = on_translated
        self._translate_factory = translate_factory  # None disables translation
        self._translator: TranslateWorker | None = None
        self._states = self._new_states(config)
        self._board = self._new_board(config)
        self.history = SubtitleHistory(timing.now())
        self._corpus: SubtitleCorpus | None = None  # open while a session runs
        # F8: lines are still recorded, not translated. Starts off when the user asked for it.
        self._paused = False
        self.translation_paused = config.translator.start_paused
        self.write_subtitle_log = True
        self._slot = LatestSlot()
        self._runner: SourceRunner | None = None
        self.audio: BaseAudioPassthrough | None = None
        self.av_recorder = AVSyncRecorder()  # A/V "Otomatik ölç", fed only while active
        self._tts_factory = tts_factory
        self._tts: TTSWorker | None = None

    @staticmethod
    def _new_board(config: AppConfig) -> SubtitleBoard:
        sub = config.subtitle
        return SubtitleBoard(sub.min_display_ms, sub.ms_per_char, sub.max_display_ms)

    def _new_states(self, config: AppConfig) -> SubtitleStateMachine:
        tr = config.translator
        return SubtitleStateMachine(config.subtitle, self._profile_ignore(),
                                    tr.source_language, tr.target_language)  # fmt: skip

    @property
    def translation_paused(self) -> bool:
        return self._paused

    @translation_paused.setter
    def translation_paused(self, paused: bool) -> None:
        """Turning translation off also takes the shown translation off the screen."""
        self._paused = paused
        if paused:
            vision.PAUSED.set()
        else:
            vision.PAUSED.clear()
        if paused and self._board.reset():
            self._on_subtitle(self._board.shown)

    @property
    def running(self) -> bool:
        return self._runner is not None

    def latest_frame(self) -> Frame | None:
        return self._slot.get()

    def start(self) -> None:
        """Raises SourceError if the source cannot even be constructed."""
        if self._runner is not None:
            return
        self._metrics.clear()
        source = self._source_factory(self._config)
        src = self._config.source
        self._runner = SourceRunner(
            source,
            self._slot,
            self._metrics,
            on_frame=self._frame_arrived,
            on_state=self._on_source_state,
            reconnect_initial_s=src.reconnect_initial_s,
            reconnect_max_s=src.reconnect_max_s,
            max_read_failures=src.max_read_failures,
        )
        self._runner.start()
        self.start_audio()
        self._states = self._new_states(self._config)
        self._board = self._new_board(self._config)
        self.history = SubtitleHistory(timing.now(), self._subtitle_log_path())
        self._open_corpus()
        self._start_translator()
        self._start_ocr()
        self.restart_tts()

    def _frame_arrived(self) -> None:
        """Capture thread, right after the frame was put into the slot."""
        if self.av_recorder.active:
            frame = self._slot.get()
            if frame is not None:
                self.av_recorder.add_frame(frame.image, frame.timestamp)
        self._on_frame()

    # --- read aloud (SPEC 3.11) ------------------------------------------------------
    def restart_tts(self) -> None:
        """(Re)build the TTS worker from `tts.*`; off when stopped or mode is off."""
        if self._tts is not None:
            self._tts.stop()
            self._tts = None
        tts = self._config.tts
        if tts.mode == "off" or self._runner is None:
            return
        engine = self._tts_factory(tts.engine, tts.voice, self._config.audio.output_device)
        if engine is None:
            return
        self._tts = TTSWorker(engine, tts.rate, tts.catch_up)
        self._tts.start()
        log.info("Sesli okuma açık: %s, %s", tts.mode, engine.name)

    def _speak(self, line_id: int, text: str, language: str, replaces: int | None) -> None:
        if self._tts is not None and text.strip():
            self._tts.say(Utterance(line_id, text, language), replaces)

    def _start_ocr(self) -> None:
        if self._ocr_factory is not None:
            self._ocr = OCRWorker(
                self._config, self._metrics, self._slot.get,
                engine_factory=self._ocr_factory,
                on_tick=self._ocr_tick, on_state=self._on_ocr_state,
            )  # fmt: skip
            self._ocr.start()

    def _stop_ocr(self) -> None:
        ocr, self._ocr = self._ocr, None
        if ocr is not None:
            ocr.stop()
            ocr.join(STOP_JOIN_TIMEOUT_S)  # an OCR call in flight finishes first (~0.1 s)

    def restart_ocr(self) -> None:
        """The reading engine changed (local OCR <-> vision): video keeps running."""
        if self._runner is None:
            return
        self._stop_ocr()
        self._start_ocr()

    def _profile_ignore(self) -> tuple[str, ...]:
        return tuple(load_profile(self._config.profile).ignore_phrases)

    def apply_profile(self) -> None:
        """The game profile (or its contents) changed: new ignore list, new prompt."""
        self._states.profile_ignore = self._profile_ignore()
        if self._corpus is not None:
            self._open_corpus()  # lines now belong to the new game
        self.restart_translation()

    def _subtitle_log_path(self):  # noqa: ANN202
        if not self.write_subtitle_log:
            return None
        log_dir = self._config.logging.dir or default_log_dir()
        return log_dir / time.strftime("subtitles-%Y%m%d-%H%M.srt")

    def retranslate_current(self) -> bool:
        """F7: translate the line on screen again, skipping the cache."""
        line = self._states.current
        if line is None or self._translator is None:
            return False
        self._board.line_stable(line)
        self._translator.submit(line, force=True)
        return True

    def _start_translator(self) -> None:
        if self._translate_factory is not None:
            self._translator = TranslateWorker(
                self._config, self._metrics, self._translate_factory, self._translation_done
            )
            self._translator.start()

    def _stop_translator(self) -> None:
        translator, self._translator = self._translator, None
        if translator is not None:
            translator.stop()
            translator.join(STOP_JOIN_TIMEOUT_S)  # a request in flight ends by its timeout

    def restart_translation(self) -> None:
        """Engine, model or keys changed: new translator, video and OCR keep running."""
        if self._runner is None:
            return
        self._stop_translator()
        self._start_translator()

    @property
    def translation(self) -> TranslationService | None:
        return self._translator.service if self._translator is not None else None

    # --- subtitle pipeline (OCR thread -> state machine -> translator thread) -------
    def _ocr_tick(self, tick: OCRTick) -> None:
        self._on_ocr_tick(tick)
        changed = False
        for event in self._states.feed(tick.text, tick.frame_timestamp):
            if isinstance(event, LineStable):
                line = event.line
                self._metrics.tick(M.SUB_LINES)
                log.debug("Kararlı satır #%d%s: %r", line.id,
                          f" (#{line.replaces} uzadı)" if line.replaces else "", line.full_text)  # fmt: skip
                self.history.add(line)
                if self.translation_paused:
                    continue
                if self._config.tts.mode == "source" and self._tts is not None:
                    # Read the original instead of translating it (SPEC 3.11).
                    self._speak(line.id, line.full_text, "en", line.replaces)
                    continue
                self._board.line_stable(line)
                if self._translator is not None:
                    self._translator.submit(line)
            elif isinstance(event, LineCleared):
                self.history.close(event.line_id)
                changed |= self._board.cleared(event.line_id, timing.now())
        changed |= self._board.tick(timing.now())
        if changed:
            self._on_subtitle(self._board.shown)

    def _open_corpus(self) -> None:
        self._close_corpus()
        if self._config.subtitle.save_lines:
            self._corpus = SubtitleCorpus(user_data_dir() / "subtitles.sqlite",
                                          self._config.profile)  # fmt: skip

    def _close_corpus(self) -> None:
        corpus, self._corpus = self._corpus, None
        if corpus is not None:
            corpus.close()

    def _translation_done(self, item: Translated) -> None:
        if item.result is not None:
            self.history.translated(item.line.id, item.result)
            # Test mode echoes the English: that is not a translation worth keeping.
            if self._corpus is not None and item.result.engine != "test":
                source = item.source or item.line.text
                self._corpus.record(source, item.result.text, item.line.speaker,
                                    item.result.engine, not sentence_open(source))  # fmt: skip
        self._on_translated(item)
        if item.result is None:
            return
        if self._config.tts.mode == "translation":
            self._speak(item.line.id, item.result.text, "tr", item.line.replaces)
        if self._board.translated(item.line, item.result, timing.now()):
            view = self._board.shown
            if view is not None:
                self._metrics.observe(M.SUB_LATENCY_MS, view.latency_ms)
            self._on_subtitle(view)

    def force_ocr(self) -> None:
        if self._ocr is not None:
            self._ocr.force()

    def stop(self) -> None:
        if self._tts is not None:
            self._tts.stop()
            self._tts = None
        self.stop_audio()
        self._stop_ocr()
        self._stop_translator()
        self._states.reset()
        self.history.close_all()
        self._close_corpus()
        if self._board.reset():
            self._on_subtitle(None)
        runner, self._runner = self._runner, None
        if runner is not None:
            runner.stop()
            runner.join(STOP_JOIN_TIMEOUT_S)
            if runner.is_alive():
                log.warning("Kaynak thread'i zamanında durmadı.")
        self._slot.put(None)

    # --- audio (independent of video: an audio problem never stops the picture) --
    def start_audio(self) -> None:
        self.stop_audio(notify=False)
        audio = self._config.audio
        if audio.mode == "off" or self._config.source.kind != "capture":
            self._on_audio_state(AudioState.OFF, "")
            return
        if not audio.input_device:
            self._on_audio_state(AudioState.NOT_CONFIGURED, "")
            return
        try:
            passthrough = self._audio_factory(self._config, self._metrics)
            passthrough.start()
        except AudioError as exc:
            log.error("%s", exc.message)
            self._on_audio_state(AudioState.ERROR, exc.message)
            return
        passthrough.tap = self.av_recorder.add_audio
        self.audio = passthrough
        self._on_audio_state(AudioState.RUNNING, "")

    def stop_audio(self, notify: bool = True) -> None:
        if self.audio is not None:
            self.audio.stop()
            self.audio = None
        self._metrics.clear("audio.")
        if notify:
            self._on_audio_state(AudioState.OFF, "")
