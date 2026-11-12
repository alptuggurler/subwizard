"""AppController: the thin bridge between the Qt-free core and the UI (SPEC 3.12).

The UI never touches config or the pipeline directly; it calls the controller and
listens to its signals. Every setting change is applied and saved immediately.
Worker threads only ever reach the UI through the signals emitted here.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLocale, QObject, QTimer, Signal, Slot

from subwizard import platform, profiles
from subwizard.audio.devices import guess_capture_input, list_audio_devices
from subwizard.config import (
    AppConfig,
    AudioProfileName,
    DisplayMode,
    HotkeysConfig,
    Language,
    PerfOverlay,
    ThemeMode,
    save_config,
)
from subwizard.core import secrets
from subwizard.core.metrics import MetricsHub, Snapshot
from subwizard.core.session import AudioState, Session
from subwizard.core.sysmon import SystemMonitor
from subwizard.i18n import i18n, match_language
from subwizard.ocr.roi import TEMPLATES, Rect
from subwizard.ocr.worker import OCRState, OCRTick
from subwizard.pipeline.board import SubtitleView
from subwizard.translator.test_run import TestOutcome, run_test
from subwizard.utils.hotkeys import normalize_key
from subwizard.web.qr import qr_text
from subwizard.web.server import WebOverlayServer
from subwizard.sources.base import Frame, SourceError, VideoMode
from subwizard.sources.runner import SourceState

log = logging.getLogger(__name__)

SAVE_DEBOUNCE_MS = 400  # sliders fire many changes; write the file once they settle
PERF_OVERLAY_CYCLE: tuple[PerfOverlay, ...] = ("off", "compact", "detailed")
# Appearance presets (SPEC 3.12): only the keys they change.
OVERLAY_PRESETS: dict[str, dict[str, object]] = {
    "netflix": {"text_color": "#FFFFFF", "background_opacity": 0.0, "outline": True,
                "bold": False, "font_size_pct": 4.5, "speaker_color": "#FFFFFF"},
    "ingame": {"text_color": "#FFFFFF", "background_color": "#000000",
               "background_opacity": 0.65, "outline": True, "bold": True,
               "font_size_pct": 4.5, "speaker_color": "#FFD24A"},
    "high_contrast": {"text_color": "#FFFF00", "background_color": "#000000",
                      "background_opacity": 1.0, "outline": False, "bold": True,
                      "font_size_pct": 6.0, "speaker_color": "#00FFFF"},
}  # fmt: skip


class AppController(QObject):
    languageChanged = Signal(str)  # resolved language code ("tr", "en")
    themeChanged = Signal(str)  # "auto" | "dark" | "light"
    runningChanged = Signal(bool)
    frameReady = Signal()  # a new frame is in the latest-only slot (from the source thread)
    metricsUpdated = Signal(object)  # Snapshot, `metrics.ui_hz` times per second
    sourceStateChanged = Signal(str, str)  # SourceState value, user-facing problem ("" if none)
    audioStateChanged = Signal(str, str)  # AudioState value, user-facing message ("" if none)
    perfOverlayChanged = Signal(str)
    settingsChanged = Signal()  # some source/audio setting changed (pages refresh their controls)
    ocrTick = Signal(object)  # OCRTick, from the OCR thread at up to `ocr.hz`
    ocrStateChanged = Signal(str, str)  # OCRState value, error message ("" if none)
    calibrateRequested = Signal()  # F2 / --calibrate: open the region calibration
    subtitleChanged = Signal(object)  # SubtitleView | None: the translation on screen
    translated = Signal(object)  # Translated: every finished line (history, M4)
    translationTested = Signal(object)  # TestOutcome from test_translation()
    pauseChanged = Signal(bool)  # F8: translation paused
    overlayChanged = Signal()  # an overlay.* setting changed (video + preview repaint)
    hotkeysChanged = Signal()
    historyChanged = Signal()
    webChanged = Signal()
    profileChanged = Signal()  # game profile selected, saved or deleted
    soundTested = Signal(bool, str)  # test_sound(): ok, device name or error
    avSyncMeasured = Signal(int, int, float)  # new delay_ms (-1: failed), events, offset ms
    wizardRequested = Signal()  # Ayarlar -> "Kurulum sihirbazını aç"  # web overlay started / stopped / reconfigured  # a history entry changed outside the session (retranslate, mark)

    # Worker threads emit these; the slots then run on the Qt thread.
    _sourceStateFromThread = Signal(str, str)
    _audioStateFromThread = Signal(str, str)
    _ocrStateFromThread = Signal(str, str)
    _subtitleFromThread = Signal(object)

    def __init__(self, config: AppConfig, config_path: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.config = config
        self._config_path = config_path
        self._running = False
        self.source_state = SourceState.STOPPED.value
        self.source_problem = ""
        self.audio_state = AudioState.OFF.value
        self.audio_message = ""
        self.ocr_state = OCRState.OFF.value
        self.ocr_message = ""
        self.last_ocr: OCRTick | None = None
        i18n.set_language(self.resolved_language())

        self.metrics = MetricsHub(config.metrics.short_window_s, config.metrics.long_window_s)
        self.web = WebOverlayServer(config.web, self.metrics)
        self._sysmon = SystemMonitor(self.metrics)
        self._session = Session(
            config,
            self.metrics,
            on_frame=self.frameReady.emit,
            on_source_state=lambda state, key: self._sourceStateFromThread.emit(state.value, key),
            on_audio_state=lambda state, msg: self._audioStateFromThread.emit(state.value, msg),
            on_ocr_tick=self._on_ocr_tick,
            on_ocr_state=lambda state, msg: self._ocrStateFromThread.emit(state.value, msg),
            on_subtitle=self._publish_subtitle,
            on_translated=self.translated.emit,
        )
        self._sourceStateFromThread.connect(self._on_source_state)
        self._audioStateFromThread.connect(self._on_audio_state)
        self._ocrStateFromThread.connect(self._on_ocr_state)
        self._subtitleFromThread.connect(self._on_subtitle)
        self.subtitle: SubtitleView | None = None
        self._key_status: tuple[int, str] | None = None
        self._masked_keys: list[str] = []

        self._metrics_timer = QTimer(self)
        self._metrics_timer.setInterval(int(1000 / config.metrics.ui_hz))
        self._metrics_timer.timeout.connect(self._publish_metrics)
        self._metrics_timer.start()

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self.flush)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)
        self._apply_display_mode()

    # --- persistence ----------------------------------------------------------
    def _save(self, debounce: bool = False) -> None:
        if debounce:
            self._save_timer.start()
        else:
            self.flush()

    def flush(self) -> None:
        self._save_timer.stop()
        try:
            save_config(self.config, self._config_path)
        except OSError as exc:
            # The change still applies for this session; losing persistence is not fatal.
            log.error("Ayarlar kaydedilemedi (%s): %s", self._config_path, exc)

    def shutdown(self) -> None:
        self._metrics_timer.stop()
        if self._save_timer.isActive():
            self.flush()
        self._session.stop()
        self.web.stop()

    # --- display mode + web overlay (SPEC 3.10) -----------------------------------------
    def _publish_subtitle(self, view: SubtitleView | None) -> None:
        """Translator thread: the web page gets the line now, the Qt side via the event
        loop, so both appear together (M4.5 acceptance: < 100 ms apart)."""
        self.web.publish_view(view)
        self._subtitleFromThread.emit(view)

    @property
    def web_enabled(self) -> bool:
        return self.config.display.mode in ("web", "both")

    def _apply_display_mode(self) -> None:
        if self.web_enabled:
            if not self.web.running and self.web.start():
                url = self.web.url()
                # SPEC 3.10: URL + QR on the console too, for scanning with a phone.
                print(f"\nWeb overlay: {url}\nOBS: {self.web.url('/obs')}\n{qr_text(url)}",
                      flush=True)  # fmt: skip
            if self.subtitle is not None:
                self.web.publish_view(self.subtitle)
        else:
            self.web.stop()
        self.webChanged.emit()

    def set_display_mode(self, mode: DisplayMode) -> None:
        if mode == self.config.display.mode:
            return
        self.config.display.mode = mode
        self._save()
        self._apply_display_mode()
        self.overlayChanged.emit()  # web-only mode hides the subtitle on the PC video

    def set_web_option(self, key: str, value: object) -> None:
        """Port / host / token need a restart; page defaults apply on the next page load."""
        setattr(self.config.web, key, value)
        self._save(debounce=key not in ("port", "host", "token"))
        if key in ("port", "host", "token") and self.web_enabled:
            self.web.stop()
            self._apply_display_mode()
        else:
            self.webChanged.emit()

    # --- general settings -------------------------------------------------------
    def resolved_language(self) -> str:
        """Configured language, with "auto" mapped to the system UI language."""
        choice = self.config.ui.language
        if choice == "auto":
            return match_language(QLocale.system().name())
        return choice

    def set_language(self, language: Language) -> None:
        if language == self.config.ui.language:
            return
        self.config.ui.language = language
        self._save()
        resolved = self.resolved_language()
        i18n.set_language(resolved)
        self.languageChanged.emit(resolved)

    def set_theme(self, theme: ThemeMode) -> None:
        if theme == self.config.ui.theme:
            return
        self.config.ui.theme = theme
        self._save()
        self.themeChanged.emit(theme)

    def set_close_to_tray(self, enabled: bool) -> None:
        if enabled == self.config.ui.close_to_tray:
            return
        self.config.ui.close_to_tray = enabled
        self._save()

    def cycle_perf_overlay(self) -> None:
        """F9: off -> compact -> detailed -> off."""
        current = PERF_OVERLAY_CYCLE.index(self.config.ui.perf_overlay)
        self.config.ui.perf_overlay = PERF_OVERLAY_CYCLE[(current + 1) % len(PERF_OVERLAY_CYCLE)]
        self._save()
        self.perfOverlayChanged.emit(self.config.ui.perf_overlay)

    # --- pipeline ---------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._running

    def latest_frame(self) -> Frame | None:
        return self._session.latest_frame()

    def _autoselect_capture_audio(self) -> None:
        """If no audio input was chosen yet, pick the capture card's own one."""
        src, audio = self.config.source, self.config.audio
        if audio.input_device or src.kind != "capture" or audio.mode == "off":
            return
        name = src.device
        if isinstance(name, int):
            devices = platform.list_video_devices()
            name = devices[name].name if name < len(devices) else ""
        guess = guess_capture_input(name, list_audio_devices("input"))
        if guess:
            log.info("Capture card ses girişi otomatik seçildi: %s", guess)
            audio.input_device = guess
            self._save()
            self.settingsChanged.emit()

    def start(self) -> None:
        if self._running:
            return
        self._autoselect_capture_audio()
        try:
            self._session.start()
        except SourceError as exc:
            log.error("%s", exc.message)
            self._on_source_state(SourceState.FAILED.value, exc.message)
            return
        self._running = True
        self.runningChanged.emit(True)

    def stop(self) -> None:
        if not self._running:
            return
        self._session.stop()
        self.last_ocr = None
        self._running = False
        self.runningChanged.emit(False)

    def toggle_running(self) -> None:
        self.stop() if self._running else self.start()

    def _restart_if_running(self) -> None:
        if self._running:
            self.stop()
            self.start()

    @Slot(str, str)
    def _on_source_state(self, state: str, problem: str) -> None:
        # A stopped runner reports STOPPED after a failure; keep showing why it failed.
        if state == SourceState.STOPPED and self.source_state == SourceState.FAILED:
            return
        self.source_state, self.source_problem = state, problem
        self.sourceStateChanged.emit(state, problem)
        if state in (SourceState.ENDED, SourceState.FAILED):
            self.stop()  # the source is gone for good: reflect that in Start/Stop

    @Slot(str, str)
    def _on_audio_state(self, state: str, message: str) -> None:
        self.audio_state, self.audio_message = state, message
        self.audioStateChanged.emit(state, message)

    def _on_ocr_tick(self, tick: OCRTick) -> None:
        # Runs on the OCR thread: store the reference, hand the rest to Qt.
        self.last_ocr = tick
        self.ocrTick.emit(tick)

    @Slot(str, str)
    def _on_ocr_state(self, state: str, message: str) -> None:
        self.ocr_state, self.ocr_message = state, message
        self.ocrStateChanged.emit(state, message)

    @Slot(object)
    def _on_subtitle(self, view: SubtitleView | None) -> None:
        self.subtitle = view
        self.subtitleChanged.emit(view)

    def force_ocr(self) -> None:
        self._session.force_ocr()

    def _publish_metrics(self) -> None:
        self._sysmon.sample()
        audio = self._session.audio
        if audio is not None and hasattr(audio, "publish_metrics"):
            audio.publish_metrics()
        self.metricsUpdated.emit(self.metrics.snapshot())

    def snapshot(self) -> Snapshot:
        return self.metrics.snapshot()

    # --- source settings ----------------------------------------------------------
    def set_source_kind(self, kind: str) -> None:
        if kind == self.config.source.kind:
            return
        self.config.source.kind = kind
        self._source_changed()

    def set_source_device(self, device: int | str) -> None:
        if device == self.config.source.device:
            return
        self.config.source.device = device
        self._autoselect_capture_audio()
        self._source_changed()

    def set_source_path(self, path: Path) -> None:
        self.config.source.path = path
        self.config.source.kind = "file"
        self._source_changed()

    def set_source_mode(self, mode: VideoMode) -> None:
        src = self.config.source
        if (src.width, src.height, src.fps, src.fourcc) == (
            mode.width, mode.height, int(mode.fps), mode.fourcc
        ):  # fmt: skip
            return
        src.width, src.height, src.fps, src.fourcc = (
            mode.width, mode.height, int(mode.fps), mode.fourcc
        )  # fmt: skip
        self._source_changed()

    def _source_changed(self) -> None:
        self._save()
        self.settingsChanged.emit()
        self._restart_if_running()

    # --- audio settings -------------------------------------------------------------
    def _audio_changed(self) -> None:
        """A setting that needs the streams reopened (device, profile, on/off)."""
        self._save()
        self.settingsChanged.emit()
        if self._running:
            self._session.start_audio()

    def set_audio_enabled(self, enabled: bool) -> None:
        mode = "passthrough" if enabled else "off"
        if mode != self.config.audio.mode:
            self.config.audio.mode = mode
            self._audio_changed()

    def set_audio_input(self, name: str | None) -> None:
        if name != self.config.audio.input_device:
            self.config.audio.input_device = name
            self._audio_changed()

    def set_audio_output(self, name: str | None) -> None:
        if name != self.config.audio.output_device:
            self.config.audio.output_device = name
            self._audio_changed()

    def set_audio_profile(self, profile: AudioProfileName) -> None:
        if profile != self.config.audio.profile:
            self.config.audio.profile = profile
            self._audio_changed()

    # These three apply to the running stream at once — no reopen, no audible gap.
    def set_audio_delay_ms(self, delay_ms: int) -> None:
        self.config.audio.delay_ms = delay_ms
        if self._session.audio is not None:
            self._session.audio.set_delay_ms(delay_ms)
        self._save(debounce=True)

    def set_audio_gain_db(self, gain_db: float) -> None:
        self.config.audio.gain_db = gain_db
        if self._session.audio is not None:
            self._session.audio.set_gain_db(gain_db)
        self._save(debounce=True)

    def set_audio_muted(self, muted: bool) -> None:
        if muted == self.config.audio.muted:
            return
        self.config.audio.muted = muted
        if self._session.audio is not None:
            self._session.audio.set_muted(muted)
        self._save()
        self.settingsChanged.emit()

    # --- region & OCR settings (read by the OCR thread every tick: applied live) -----
    def set_roi_template(self, template: str) -> None:
        roi = self.config.roi
        if template != "custom":
            roi.rect = TEMPLATES[template].as_list()
        roi.template = template
        self._save()
        self.settingsChanged.emit()

    def set_roi_rect(self, rect: Rect) -> None:
        if not rect.usable:
            return
        self.config.roi.rect = rect.as_list()
        self.config.roi.template = "custom"
        self._save()
        self.settingsChanged.emit()

    def add_roi_region(self, rect: Rect, *, exclude: bool) -> None:
        """One more region read together with the main one, or an area blacked out."""
        if not rect.usable:
            return
        roi = self.config.roi
        if exclude:
            roi.exclude_rects = [*roi.exclude_rects, rect.as_list()]
        else:
            roi.extra_rects = [*roi.extra_rects, rect.as_list()]
        self._save()
        self.settingsChanged.emit()

    def clear_roi_regions(self) -> None:
        self.config.roi.extra_rects = []
        self.config.roi.exclude_rects = []
        self._save()
        self.settingsChanged.emit()

    def set_roi_padding(self, padding_pct: float) -> None:
        self.config.roi.padding_pct = padding_pct
        self._save(debounce=True)

    def set_ocr_hz(self, hz: float) -> None:
        self.config.ocr.hz = hz
        self._save(debounce=True)

    def set_ocr_min_confidence(self, value: float) -> None:
        self.config.ocr.min_confidence = value
        self.force_ocr()  # re-read now so the effect is visible at once
        self._save(debounce=True)

    def set_preprocess_option(self, key: str, value: object) -> None:
        prep = self.config.ocr.preprocess[self.config.ocr.engine]
        setattr(prep, key, value)
        self.force_ocr()
        self._save()

    # --- translation (SPEC 3.6) -------------------------------------------------------
    def _translation_changed(self) -> None:
        self._save()
        self._session.restart_translation()
        self.settingsChanged.emit()

    def set_translator_engine(self, engine: str) -> None:
        self.config.translator.engine = engine
        self._translation_changed()

    def set_translator_mode(self, mode: str) -> None:
        """"ocr" or "vision" (experimental): the reading engine is rebuilt."""
        if mode != self.config.translator.mode:
            self.config.translator.mode = mode
            self._save()
            self.settingsChanged.emit()
            self._session.restart_ocr()

    def set_tts_mode(self, mode: str) -> None:
        """Sesli okuma (SPEC 3.11): "off" | "translation" | "source"."""
        if mode != self.config.tts.mode:
            self.config.tts.mode = mode
            self._save()
            self.settingsChanged.emit()
            self._session.restart_tts()

    def set_translator_model(self, model: str) -> None:
        self.config.translator.model = model
        self._translation_changed()

    def set_context_lines(self, lines: int) -> None:
        self.config.translator.context_lines = lines
        self._save(debounce=True)  # read per request: no restart needed

    def set_fallback_enabled(self, enabled: bool) -> None:
        self.config.translator.fallback = enabled
        self._translation_changed()

    def api_key_status(self) -> tuple[int, str]:
        """(number of keys, where from: "env" | "keyring" | ""). Cached: the credential
        store is slow to ask and pages ask on every refresh."""
        if self._key_status is None:
            keys, source = secrets.load_api_keys()
            self._key_status = (len(keys), source)
            self._masked_keys = [secrets.mask(k) for k in keys]
        return self._key_status

    def set_api_keys(self, text: str) -> None:
        """Comma or newline separated; empty removes the saved keys."""
        keys = secrets.split_keys(text)
        secrets.save_api_keys(keys)
        self._key_status = None
        log.info("API anahtarları kaydedildi: %d anahtar (%s)", len(keys),
                 ", ".join(secrets.mask(k) for k in keys) or "yok")  # fmt: skip
        self._session.restart_translation()
        self.settingsChanged.emit()

    def api_keys(self) -> list[tuple[str, str, float]]:
        """The keys for the UI: (masked key, state, seconds left to rest). The state is
        "active" / "ready" / "resting" while translating, else "saved"."""
        self.api_key_status()  # fills the cache: the credential store is slow to ask
        keys = self._masked_keys
        service = self._session.translation
        engine = service.primary if service is not None else None
        states = engine.key_states() if hasattr(engine, "key_states") else []
        if len(states) != len(keys):
            states = [("saved", 0.0)] * len(keys)
        return [(k, state, left) for k, (state, left) in zip(keys, states)]

    def add_api_keys(self, text: str) -> int:
        """Append keys (comma / newline separated) to the saved ones; returns how many
        were new. Keys from the environment cannot be edited here."""
        saved = secrets.keys_from_keyring()
        new = [k for k in secrets.split_keys(text) if k not in saved]
        if new:
            self.set_api_keys(",".join([*saved, *new]))
        return len(new)

    def remove_api_key(self, index: int) -> None:
        saved = secrets.keys_from_keyring()
        if 0 <= index < len(saved):
            self.set_api_keys(",".join(saved[:index] + saved[index + 1:]))

    def test_translation(self, text: str) -> None:
        """Translate `text` once with the current settings on a helper thread; the
        outcome arrives through `translationTested`. Bypasses the cache."""
        import threading

        config = self.config.model_copy(deep=True)
        threading.Thread(
            target=lambda: self.translationTested.emit(run_test(config, text)),
            name="translate-test", daemon=True,
        ).start()  # fmt: skip

    def test_sound(self) -> None:
        """Cihaz & Ses "Sesi test et": a short tone on the output device, on a helper
        thread. `soundTested(ok, text)`: the device name, or the error to show."""
        import threading

        from subwizard.audio.test_tone import play_test_tone

        audio = self.config.audio

        def run() -> None:
            try:
                name = play_test_tone(audio.output_device, audio.sample_rate)
            except Exception as exc:  # PortAudio: device busy, unsupported rate, unplugged
                log.warning("Ses testi başarısız: %s", exc)
                self.soundTested.emit(False, str(exc))
            else:
                self.soundTested.emit(True, name)

        threading.Thread(target=run, name="sound-test", daemon=True).start()

    def measure_av_sync(self) -> bool:
        """Cihaz & Ses "Otomatik ölç": listen for MEASURE_S, then set `audio.delay_ms` to
        the estimate. False when it cannot start (no capture running with audio)."""
        from PySide6.QtCore import QTimer

        from subwizard.audio.avsync import MEASURE_S

        session = self._session
        if not self._running or session.audio is None or session.av_recorder.active:
            return False
        session.av_recorder.start()
        log.info("A/V otomatik ölçüm başladı (%.0f sn)", MEASURE_S)
        QTimer.singleShot(int(MEASURE_S * 1000), self._finish_av_sync)
        return True

    def _finish_av_sync(self) -> None:
        from subwizard.core.session import MAX_AUDIO_DELAY_MS

        est = self._session.av_recorder.stop()
        if est is None or not est.reliable:
            log.info("A/V otomatik ölçüm sonuçsuz: %s", est)
            self.avSyncMeasured.emit(-1, est.pairs if est else 0, 0.0)
            return
        # Measured on the input side, before the delay buffer: the current delay is not in it.
        delay = est.suggested_delay_ms(0, MAX_AUDIO_DELAY_MS)
        log.info("A/V otomatik ölçüm: fark %.0f ms (%d olay, yayılım %.0f ms) -> gecikme %d ms",
                 est.offset_ms, est.pairs, est.spread_ms, delay)  # fmt: skip
        self.set_audio_delay_ms(delay)
        self.avSyncMeasured.emit(delay, est.pairs, est.offset_ms)

    def translation_cache_size(self) -> int:
        service = self._session.translation
        if service is not None:
            return len(service.cache)
        from subwizard.translator.service import open_cache

        cache = open_cache(self.config)
        try:
            return len(cache)
        finally:
            cache.close()

    def clear_translation_cache(self) -> None:
        service = self._session.translation
        if service is not None:
            service.cache.clear()
        else:
            from subwizard.translator.service import open_cache

            cache = open_cache(self.config)
            cache.clear()
            cache.close()
        log.info("Çeviri önbelleği temizlendi.")
        self.settingsChanged.emit()

    # --- history, hotkey actions (SPEC 3.8, 3.9) ---------------------------------------
    @property
    def history(self):  # noqa: ANN201 - SubtitleHistory
        return self._session.history

    @property
    def translation_paused(self) -> bool:
        return self._session.translation_paused

    def toggle_translation_pause(self) -> None:
        paused = not self._session.translation_paused
        self._session.translation_paused = paused
        log.info("Çeviri %s.", "duraklatıldı" if paused else "devam ediyor")
        self.pauseChanged.emit(paused)

    def retranslate_current(self) -> bool:
        return self._session.retranslate_current()

    def retranslate_entry(self, line_id: int) -> bool:
        """History page: translate a past line again with the current settings, bypassing
        the cache, on a helper thread; `historyChanged` follows."""
        import threading

        entry = self.history.get(line_id)
        if entry is None:
            return False
        text = entry.source
        if entry.speaker and text.startswith(f"{entry.speaker}: "):
            text = text[len(entry.speaker) + 2:]
        config = self.config.model_copy(deep=True)

        def work() -> None:
            outcome = run_test(config, text)
            if outcome.text:
                self.history.set_translation(line_id, outcome.text, outcome.engine,
                                             outcome.fallback)  # fmt: skip
            self.historyChanged.emit()

        threading.Thread(target=work, name="retranslate", daemon=True).start()
        return True

    def mark_bad(self, line_id: int, bad: bool = True) -> None:
        self.history.mark_bad(line_id, bad)
        self.historyChanged.emit()

    def toggle_mute(self) -> None:
        self.set_audio_muted(not self.config.audio.muted)

    def last_translation(self) -> str:
        if self.subtitle is not None:
            return self.subtitle.text
        done = [e for e in self.history.entries() if e.translation]
        return done[-1].translation if done else ""

    def toggle_subtitles(self) -> None:
        self.set_overlay_option("visible", not self.config.overlay.visible)

    def set_overlay_option(self, key: str, value: object) -> None:
        setattr(self.config.overlay, key, value)
        self._save(debounce=True)
        self.overlayChanged.emit()

    def apply_overlay_preset(self, name: str) -> None:
        for key, value in OVERLAY_PRESETS[name].items():
            setattr(self.config.overlay, key, value)
        self._save()
        self.overlayChanged.emit()
        self.settingsChanged.emit()

    def set_hotkey(self, action: str, key: str) -> list[str]:
        """Bind `key` to `action`; returns the other actions already using that key (the
        UI warns; the binding is saved anyway so the user can then fix the other one)."""
        key = normalize_key(key)
        self.config.hotkeys.keys = {**self.config.hotkeys.keys, action: key}
        self._save()
        self.hotkeysChanged.emit()
        return [a for a, k in self.config.hotkeys.keys.items()
                if a != action and key and normalize_key(k) == key]  # fmt: skip

    def reset_hotkeys(self) -> None:
        self.config.hotkeys.keys = HotkeysConfig().keys
        self._save()
        self.hotkeysChanged.emit()

    def set_global_hotkeys(self, enabled: bool) -> None:
        self.config.hotkeys.global_hotkeys = enabled
        self._save()
        self.hotkeysChanged.emit()

    # --- setup wizard (SPEC 3.12) ----------------------------------------------------------
    def set_game_profile(self, name: str) -> None:
        """Select a game profile: its region template, ignore list and prompt apply now."""
        profile = profiles.load_profile(name)
        self.config.profile = profile.name
        self._save()
        if profile.roi_template in TEMPLATES:
            self.set_roi_template(profile.roi_template)
        self._session.apply_profile()
        self.profileChanged.emit()

    def save_game_profile(self, profile: profiles.GameProfile) -> None:
        """Store a profile (as the user's copy); if it is the active one, apply it."""
        profiles.save_profile(profile)
        if profile.name == self.config.profile:
            self._session.apply_profile()
        self.profileChanged.emit()

    def delete_game_profile(self, name: str) -> None:
        """A user profile is removed; a built-in one goes back to how it shipped."""
        profiles.delete_profile(name)
        if name == self.config.profile and not profiles.is_builtin(name):
            self.set_game_profile(profiles.DEFAULT)
            return
        if name == self.config.profile:
            self._session.apply_profile()
        self.profileChanged.emit()

    def request_wizard(self) -> None:
        self.wizardRequested.emit()

    def finish_wizard(self) -> None:
        if not self.config.ui.wizard_done:
            self.config.ui.wizard_done = True
            self._save()

    def request_calibration(self) -> None:
        """F2: calibrate on a frozen frame; starts the source first if needed."""
        if not self._running:
            self.start()
        if self._running:
            self.calibrateRequested.emit()
