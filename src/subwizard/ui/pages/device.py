"""Device & Audio: capture card + mode, live preview, audio routing, VU meter,
level, latency profile and the A/V sync slider (SPEC 3.12)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    ToolButton,
)

from subwizard import platform
from subwizard.audio.devices import list_audio_devices
from subwizard.controller import AppController
from subwizard.core.metrics import Grade, M, Snapshot
from subwizard.core.session import AudioState
from subwizard.i18n import t
from subwizard.sources.base import VideoMode
from subwizard.sources.capture_card import mode_hints
from subwizard.ui.metrics_view import DASH, target_fps
from subwizard.ui.pages.base import BasePage, SettingRow
from subwizard.ui.widgets.notice import Notice
from subwizard.ui.widgets.value_slider import SLIDER_WIDTH, VALUE_WIDTH, ValueSlider
from subwizard.ui.widgets.video_view import VideoView
from subwizard.ui.widgets.vu_meter import VUMeter

COMBO_WIDTH = 300
PREVIEW_HEIGHT = 200
PREVIEW_WIDTH = int(PREVIEW_HEIGHT * 16 / 9)
SOURCE_KINDS = ("capture", "file")
AUDIO_PROFILES = ("low", "balanced", "safe")
FALLBACK_DEVICE_COUNT = 4  # indexes offered when devices cannot be listed by name
# Offered when the card's own mode list is unavailable (non-Windows, or the query failed).
FALLBACK_MODES = (
    VideoMode(1920, 1080, 60.0, "MJPG"),
    VideoMode(1920, 1080, 30.0, "MJPG"),
    VideoMode(1280, 720, 60.0, "MJPG"),
    VideoMode(1920, 1080, 60.0, "YUY2"),
    VideoMode(1920, 1080, 30.0, "YUY2"),
    VideoMode(1280, 720, 60.0, "YUY2"),
)
GAIN_RANGE_DB = (-60, 6)
DELAY_RANGE_MS = (-200, 500)
VIDEO_FILTER = "Video (*.mp4 *.mkv *.mov *.avi)"


def _set_items(combo: ComboBox, labels: list[str], index: int) -> None:
    """Replace a combo's items without firing change handlers."""
    combo.blockSignals(True)
    combo.clear()
    combo.addItems(labels)
    combo.setCurrentIndex(max(0, index))
    combo.blockSignals(False)


def _combo() -> ComboBox:
    combo = ComboBox()
    combo.setFixedWidth(COMBO_WIDTH)
    return combo


class DevicePage(BasePage):
    key = "device"

    def __init__(self, controller: AppController, parent: QWidget | None = None):
        super().__init__(controller, parent)
        self._video_devices: list[int | str] = []
        self._modes: list[VideoMode] = []
        self._inputs: list[str | None] = []
        self._outputs: list[str | None] = []

        # --- video ---------------------------------------------------------------
        self._video_title = SubtitleLabel()
        self.body.addWidget(self._video_title)

        self._kind = _combo()
        self._kind_row = SettingRow(FluentIcon.VIDEO, self._kind)

        self._device = _combo()
        self._refresh = ToolButton(FluentIcon.SYNC)
        self._refresh.clicked.connect(self.reload_devices)
        device_control = QWidget()
        device_layout = QHBoxLayout(device_control)
        device_layout.setContentsMargins(0, 0, 0, 0)
        device_layout.addWidget(self._refresh)
        device_layout.addWidget(self._device)
        self._device_row = SettingRow(FluentIcon.CAMERA, device_control)

        self._mode = _combo()
        self._mode_row = SettingRow(FluentIcon.ZOOM_IN, self._mode)

        self._browse = PushButton()
        self._browse.setIcon(FluentIcon.FOLDER)
        self._browse.clicked.connect(self._pick_file)
        self._file_row = SettingRow(FluentIcon.MOVIE, self._browse)

        for row in (self._kind_row, self._device_row, self._mode_row, self._file_row):
            self.body.addWidget(row)

        # Live preview + "requested -> actual" card side by side.
        self._preview = VideoView(controller, measure=False)
        self._preview.setFixedSize(PREVIEW_WIDTH, PREVIEW_HEIGHT)
        self._mode_title = StrongBodyLabel()
        self._requested = BodyLabel()
        self._actual = BodyLabel()
        self._measured = BodyLabel()
        self._mode_notice = Notice()
        mode_text = QVBoxLayout()
        mode_text.setSpacing(6)
        for widget in (self._mode_title, self._requested, self._actual, self._measured,
                       self._mode_notice):  # fmt: skip
            mode_text.addWidget(widget)
        mode_text.addStretch(1)
        mode_card = CardWidget()
        mode_layout = QHBoxLayout(mode_card)
        mode_layout.setContentsMargins(20, 16, 20, 16)
        mode_layout.setSpacing(20)
        mode_layout.addWidget(self._preview)
        mode_layout.addLayout(mode_text, 1)
        self.body.addWidget(mode_card)
        self.video_section = [self._video_title, self._kind_row, self._device_row,
                              self._mode_row, self._file_row, mode_card]  # fmt: skip

        # --- audio ---------------------------------------------------------------
        self.body.addSpacing(12)
        self._audio_title = SubtitleLabel()
        self.body.addWidget(self._audio_title)
        self._audio_notice = Notice()
        self.body.addWidget(self._audio_notice)

        self._enabled = SwitchButton()
        self._enabled.setOnText("")
        self._enabled.setOffText("")
        self._enabled_row = SettingRow(FluentIcon.VOLUME, self._enabled)
        self._input = _combo()
        self._input_row = SettingRow(FluentIcon.MICROPHONE, self._input)
        self._output = _combo()
        self._sound_test = PushButton(FluentIcon.PLAY, "")
        self._sound_test.clicked.connect(self._on_sound_test)
        self._sound_result = CaptionLabel()
        output_control = QWidget()
        output_layout = QHBoxLayout(output_control)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(self._sound_result)
        output_layout.addWidget(self._output)
        output_layout.addWidget(self._sound_test)
        self._output_row = SettingRow(FluentIcon.SPEAKERS, output_control)
        controller.soundTested.connect(self._on_sound_tested)

        self._vu = VUMeter()
        self._vu.setFixedWidth(SLIDER_WIDTH)
        self._vu_value = CaptionLabel()
        self._vu_value.setFixedWidth(VALUE_WIDTH + 40)
        self._vu_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        vu_control = QWidget()
        vu_layout = QHBoxLayout(vu_control)
        vu_layout.setContentsMargins(0, 0, 0, 0)
        vu_layout.addWidget(self._vu)
        vu_layout.addWidget(self._vu_value)
        self._vu_row = SettingRow(FluentIcon.HEADPHONE, vu_control)

        self._gain = ValueSlider(GAIN_RANGE_DB, lambda v: f"{v:+d} dB")
        self._gain_row = SettingRow(FluentIcon.VOLUME, self._gain)
        self._muted = SwitchButton()
        self._muted.setOnText("")
        self._muted.setOffText("")
        self._muted_row = SettingRow(FluentIcon.MUTE, self._muted)
        self._profile = _combo()
        self._profile_row = SettingRow(FluentIcon.SPEED_HIGH, self._profile)
        self._delay = ValueSlider(DELAY_RANGE_MS, lambda v: f"{v:+d} ms")
        self._delay_row = SettingRow(FluentIcon.HISTORY, self._delay)

        for row in (self._enabled_row, self._input_row, self._output_row, self._vu_row,
                    self._gain_row, self._muted_row, self._profile_row, self._delay_row):  # fmt: skip
            self.body.addWidget(row)
        self.audio_section = [self._audio_title, self._audio_notice, self._enabled_row,
                              self._input_row, self._output_row, self._vu_row, self._gain_row,
                              self._muted_row, self._profile_row, self._delay_row]  # fmt: skip

        self.reload_devices()

        self._kind.currentIndexChanged.connect(
            lambda i: controller.set_source_kind(SOURCE_KINDS[i])
        )
        self._device.currentIndexChanged.connect(self._on_device)
        self._mode.currentIndexChanged.connect(lambda i: controller.set_source_mode(self._modes[i]))
        self._enabled.checkedChanged.connect(controller.set_audio_enabled)
        self._input.currentIndexChanged.connect(
            lambda i: controller.set_audio_input(self._inputs[i])
        )
        self._output.currentIndexChanged.connect(
            lambda i: controller.set_audio_output(self._outputs[i])
        )
        self._gain.slider.valueChanged.connect(lambda v: controller.set_audio_gain_db(float(v)))
        self._muted.checkedChanged.connect(controller.set_audio_muted)
        self._profile.currentIndexChanged.connect(
            lambda i: controller.set_audio_profile(AUDIO_PROFILES[i])
        )
        self._delay.slider.valueChanged.connect(self._on_delay)

        controller.settingsChanged.connect(self.retranslate)
        controller.audioStateChanged.connect(self._on_audio_state)
        controller.metricsUpdated.connect(self._on_metrics)

    # --- device lists -----------------------------------------------------------------
    def reload_devices(self) -> None:
        """Re-enumerate video and audio devices (also the refresh button)."""
        found = platform.list_video_devices()
        if found:
            self._video_devices = [d.name for d in found]
            self._video_labels = [d.name for d in found]
        else:
            self._video_devices = list(range(FALLBACK_DEVICE_COUNT))
            self._video_labels = []  # filled in retranslate (needs i18n)
        self._found_video = found
        self._inputs = [None, *(d.name for d in list_audio_devices("input"))]
        self._outputs = [None, *(d.name for d in list_audio_devices("output"))]
        self._reload_modes()
        self.retranslate()

    def _device_index(self) -> int | None:
        """DirectShow index of the configured capture device, if it can be known."""
        device = self.controller.config.source.device
        if isinstance(device, int):
            return device
        return platform.find_video_device(device)

    def _reload_modes(self) -> None:
        index = self._device_index()
        modes = platform.list_video_modes(index) if index is not None else []
        self._modes = list(modes or FALLBACK_MODES)
        src = self.controller.config.source
        current = VideoMode(src.width, src.height, float(src.fps), src.fourcc)
        if current not in self._modes:
            self._modes.insert(0, current)

    def _on_device(self, i: int) -> None:
        self.controller.set_source_device(self._video_devices[i])
        self._reload_modes()
        self.retranslate()

    def _pick_file(self) -> None:
        start = self.controller.config.source.path
        path, _ = QFileDialog.getOpenFileName(
            self, t("device.file.pick"), str(start.parent) if start else "", VIDEO_FILTER
        )
        if path:
            self.controller.set_source_path(Path(path))

    def _on_delay(self, value: int) -> None:
        self.controller.set_audio_delay_ms(value)
        self._refresh_audio_notice()

    # --- live ---------------------------------------------------------------------------
    def _on_audio_state(self, *_: object) -> None:
        self._refresh_audio_notice()

    def _refresh_audio_notice(self, snap: Snapshot | None = None) -> None:
        c = self.controller
        message, grade = "", "warn"
        if c.audio_state == AudioState.ERROR:
            message, grade = c.audio_message, "bad"
        elif c.audio_state == AudioState.RUNNING and snap is not None:
            if snap.gauge(M.AUDIO_CLIPPING):
                message, grade = t("audio.clipping"), "bad"
            elif snap.gauge(M.AUDIO_SILENT):
                message = t("audio.no_signal")
        if not message and c.config.audio.delay_ms < 0:
            message = t("device.delay.negative")
        self._audio_notice.show_message(message, grade)  # type: ignore[arg-type]

    def _on_metrics(self, snap: Snapshot) -> None:
        if not self.isVisible():
            return
        c = self.controller
        requested, actual = snap.gauge(M.SOURCE_REQUESTED), snap.gauge(M.SOURCE_ACTUAL)
        running = c.running and actual is not None
        self._requested.setText(
            t("device.mode.requested", mode=str(requested) if requested and c.running else DASH)
        )
        self._actual.setText(t("device.mode.actual", mode=str(actual) if running else DASH))
        fps = snap.rate(M.SOURCE_FPS).short
        self._measured.setText(
            t("device.mode.measured", fps=f"{fps:.1f}" if running else DASH)
        )
        hints = mode_hints(requested, actual) if running else []
        target = target_fps(snap)
        grade: Grade = "warn"
        if hints:
            message = " ".join(t(hint) for hint in hints)
        elif running and target and fps < target * c.config.metrics.fps_warn_ratio:
            message = t("device.fps_low", fps=f"{fps:.0f}", target=f"{target:.0f}")
        else:
            message = ""
        self._mode_notice.show_message(message, grade)

        left, right = snap.gauge(M.AUDIO_LEVEL_L), snap.gauge(M.AUDIO_LEVEL_R)
        self._vu.set_levels(left, right)
        self._vu_value.setText(
            f"{left:.0f} / {right:.0f} dBFS" if left is not None and right is not None else DASH
        )
        self._refresh_audio_notice(snap)

    def _on_sound_test(self) -> None:
        self._sound_test.setEnabled(False)
        self._sound_result.setText(t("device.sound_test.playing"))
        self.controller.test_sound()

    def _on_sound_tested(self, ok: bool, text: str) -> None:
        self._sound_test.setEnabled(True)
        self._sound_result.setText(t("device.sound_test.done") if ok
                                   else t("device.sound_test.failed", error=text))  # fmt: skip

    def retranslate(self, *_: object) -> None:
        super().retranslate()
        c = self.controller
        src, audio = c.config.source, c.config.audio

        self._video_title.setText(t("device.section.video"))
        self._audio_title.setText(t("device.section.audio"))
        self._mode_title.setText(t("device.mode.title"))

        _set_items(self._kind, [t(f"device.kind.{k}") for k in SOURCE_KINDS],
                   SOURCE_KINDS.index(src.kind) if src.kind in SOURCE_KINDS else 0)  # fmt: skip
        self._kind_row.set_texts(t("device.kind"), t("device.kind.desc"))

        labels = [d.name for d in self._found_video] or [
            t("device.capture.index", index=i) for i in self._video_devices
        ]
        devices = list(self._video_devices)
        if src.device not in devices:  # saved device is unplugged: keep showing it
            devices.insert(0, src.device)
            labels.insert(0, t("device.capture.missing", name=src.device))
            self._video_devices = devices
        _set_items(self._device, labels, devices.index(src.device))
        self._device_row.set_texts(
            t("device.capture"),
            t("device.capture.desc" if self._found_video else "device.capture.desc_unlisted"),
        )
        self._refresh.setToolTip(t("device.refresh"))

        src_mode = VideoMode(src.width, src.height, float(src.fps), src.fourcc)
        _set_items(self._mode, [str(m) for m in self._modes], self._modes.index(src_mode))
        self._mode_row.set_texts(t("device.mode"), t("device.mode.desc"))

        self._browse.setText(t("device.file.pick"))
        self._file_row.set_texts(
            t("device.file"), str(src.path) if src.path else t("device.file.none")
        )
        is_capture = src.kind == "capture"
        self._device_row.setVisible(is_capture)
        self._mode_row.setVisible(is_capture)
        self._file_row.setVisible(not is_capture)

        default = t("device.audio.default")
        none = t("device.audio.none")
        _set_items(self._input, [none, *(n for n in self._inputs[1:])],
                   self._inputs.index(audio.input_device)
                   if audio.input_device in self._inputs else 0)  # fmt: skip
        _set_items(self._output, [default, *(n for n in self._outputs[1:])],
                   self._outputs.index(audio.output_device)
                   if audio.output_device in self._outputs else 0)  # fmt: skip
        _set_items(self._profile, [t(f"device.profile.{p}") for p in AUDIO_PROFILES],
                   AUDIO_PROFILES.index(audio.profile))  # fmt: skip

        for switch, value in ((self._enabled, audio.mode == "passthrough"),
                              (self._muted, audio.muted)):  # fmt: skip
            switch.blockSignals(True)
            switch.setChecked(value)
            switch.blockSignals(False)
        self._gain.set_value(round(audio.gain_db))
        self._delay.set_value(audio.delay_ms)

        self._enabled_row.set_texts(t("device.audio.enabled"), t("device.audio.enabled.desc"))
        self._input_row.set_texts(t("device.audio.input"), t("device.audio.input.desc"))
        self._output_row.set_texts(t("device.audio.output"), t("device.audio.output.desc"))
        self._vu_row.set_texts(t("device.vu"), t("device.vu.desc"))
        self._gain_row.set_texts(t("device.gain"), t("device.gain.desc"))
        self._sound_test.setText(t("device.sound_test"))
        self._sound_test.setToolTip(t("device.sound_test.tip"))
        self._muted_row.set_texts(t("device.muted"), t("device.muted.desc"))
        self._profile_row.set_texts(t("device.profile"), t("device.profile.desc"))
        self._delay_row.set_texts(t("device.delay"), t("device.delay.desc"))
        self._refresh_audio_notice()
