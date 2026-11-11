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
from subwizard.audio.devices import AudioDevice, find_device, list_audio_devices
from subwizard.audio.meter import LevelMeter
from subwizard.audio.passthrough import PassthroughSettings, SoundDevicePassthrough
from subwizard.config import AppConfig
from subwizard.core.metrics import M, MetricsHub
from subwizard.ocr.base import BaseOCR
from subwizard.ocr.worker import OCRState, OCRTick, OCRWorker, build_ocr
from subwizard.pipeline.board import SubtitleBoard, SubtitleView
from subwizard.pipeline.history import SubtitleHistory
from subwizard.pipeline.subtitle_state import LineCleared, LineStable, SubtitleStateMachine
from subwizard.pipeline.translate_worker import TranslateWorker, Translated
from subwizard.translator.service import TranslationService, build_service
from subwizard.sources.base import BaseSource, Frame, LatestSlot, SourceError, VideoMode
from subwizard.sources.capture_card import CaptureCardSource
from subwizard.sources.runner import SourceRunner, SourceState
from subwizard.sources.video_file import VideoFileSource
from subwizard.utils import timing
from subwizard.utils.paths import default_log_dir

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
    raise SourceError("error.source_unsupported", kind=src.kind)


def build_passthrough(config: AppConfig, metrics: MetricsHub) -> BaseAudioPassthrough:
    audio = config.audio
    inputs, outputs = list_audio_devices("input"), list_audio_devices("output")
    input_index = find_device(audio.input_device, "input", inputs)
    if input_index is None:
        raise AudioError("error.audio_input_missing", name=audio.input_device)
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
        self._states = SubtitleStateMachine(config.subtitle)
        self._board = SubtitleBoard(config.subtitle.min_display_ms)
        self.history = SubtitleHistory(timing.now())
        self.translation_paused = False  # F8: lines are still recorded, not translated
        self.write_subtitle_log = True
        self._slot = LatestSlot()
        self._runner: SourceRunner | None = None
        self.audio: BaseAudioPassthrough | None = None

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
            on_frame=self._on_frame,
            on_state=self._on_source_state,
            reconnect_initial_s=src.reconnect_initial_s,
            reconnect_max_s=src.reconnect_max_s,
            max_read_failures=src.max_read_failures,
        )
        self._runner.start()
        self.start_audio()
        self._states = SubtitleStateMachine(self._config.subtitle)
        self._board = SubtitleBoard(self._config.subtitle.min_display_ms)
        self.history = SubtitleHistory(timing.now(), self._subtitle_log_path())
        self._start_translator()
        if self._ocr_factory is not None:
            self._ocr = OCRWorker(
                self._config, self._metrics, self._slot.get,
                engine_factory=self._ocr_factory,
                on_tick=self._ocr_tick, on_state=self._on_ocr_state,
            )  # fmt: skip
            self._ocr.start()

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
                self._board.line_stable(line)
                if self._translator is not None:
                    self._translator.submit(line)
            elif isinstance(event, LineCleared):
                self.history.close(event.line_id)
                changed |= self._board.cleared(event.line_id, timing.now())
        changed |= self._board.tick(timing.now())
        if changed:
            self._on_subtitle(self._board.shown)

    def _translation_done(self, item: Translated) -> None:
        if item.result is not None:
            self.history.translated(item.line.id, item.result)
        self._on_translated(item)
        if item.result is None:
            return
        if self._board.translated(item.line, item.result, timing.now()):
            view = self._board.shown
            if view is not None:
                self._metrics.observe(M.SUB_LATENCY_MS, view.latency_ms)
            self._on_subtitle(view)

    def force_ocr(self) -> None:
        if self._ocr is not None:
            self._ocr.force()

    def stop(self) -> None:
        self.stop_audio()
        ocr, self._ocr = self._ocr, None
        if ocr is not None:
            ocr.stop()
            ocr.join(STOP_JOIN_TIMEOUT_S)  # an OCR call in flight finishes first (~0.1 s)
        self._stop_translator()
        self._states.reset()
        self.history.close_all()
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
        self.audio = passthrough
        self._on_audio_state(AudioState.RUNNING, "")

    def stop_audio(self, notify: bool = True) -> None:
        if self.audio is not None:
            self.audio.stop()
            self.audio = None
        self._metrics.clear("audio.")
        if notify:
            self._on_audio_state(AudioState.OFF, "")
