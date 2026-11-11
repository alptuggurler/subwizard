"""AppController: the thin bridge between the Qt-free core and the UI (SPEC 3.12).

The UI never touches config or the pipeline directly; it calls the controller and
listens to its signals. Every setting change is applied and saved immediately.
Worker threads only ever reach the UI through the signals emitted here.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLocale, QObject, QTimer, Signal, Slot

from subwizard import platform
from subwizard.audio.devices import guess_capture_input, list_audio_devices
from subwizard.config import (
    AppConfig,
    AudioProfileName,
    Language,
    PerfOverlay,
    ThemeMode,
    save_config,
)
from subwizard.core.metrics import MetricsHub, Snapshot
from subwizard.core.session import AudioState, Session
from subwizard.core.sysmon import SystemMonitor
from subwizard.i18n import i18n, match_language
from subwizard.sources.base import Frame, SourceError, VideoMode
from subwizard.sources.runner import SourceState

log = logging.getLogger(__name__)

SAVE_DEBOUNCE_MS = 400  # sliders fire many changes; write the file once they settle
PERF_OVERLAY_CYCLE: tuple[PerfOverlay, ...] = ("off", "compact", "detailed")


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

    # Worker threads emit these; the slots then run on the Qt thread.
    _sourceStateFromThread = Signal(str, str)
    _audioStateFromThread = Signal(str, str)

    def __init__(self, config: AppConfig, config_path: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.config = config
        self._config_path = config_path
        self._running = False
        self.source_state = SourceState.STOPPED.value
        self.source_problem = ""
        self.audio_state = AudioState.OFF.value
        self.audio_message = ""
        i18n.set_language(self.resolved_language())

        self.metrics = MetricsHub(config.metrics.short_window_s, config.metrics.long_window_s)
        self._sysmon = SystemMonitor(self.metrics)
        self._session = Session(
            config,
            self.metrics,
            on_frame=self.frameReady.emit,
            on_source_state=lambda state, key: self._sourceStateFromThread.emit(state.value, key),
            on_audio_state=lambda state, msg: self._audioStateFromThread.emit(state.value, msg),
        )
        self._sourceStateFromThread.connect(self._on_source_state)
        self._audioStateFromThread.connect(self._on_audio_state)

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
