"""Home / Player: Start/Stop, the live video, status cards and status line (SPEC 3.12)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    FluentIcon,
    IconWidget,
    PrimaryPushButton,
    StrongBodyLabel,
    ToolButton,
)

from subwizard.controller import AppController
from subwizard.core.metrics import Grade, M, Snapshot
from subwizard.core.session import AudioState
from subwizard.i18n import t
from subwizard.sources.capture_card import mode_hints
from subwizard.sources.runner import SourceState
from subwizard.ui.pages.base import BasePage
from subwizard.ui.widgets.notice import Notice, StatusDot
from subwizard.ui.widgets.video_view import VideoView

START_BUTTON_SIZE = (160, 40)
CARD_ICON_SIZE = 22
VIDEO_MIN_HEIGHT = 240

SOURCE_GRADES: dict[str, Grade | None] = {
    SourceState.RUNNING: "ok",
    SourceState.CONNECTING: "warn",
    SourceState.RECONNECTING: "warn",
    SourceState.FAILED: "bad",
    SourceState.STOPPED: None,
    SourceState.ENDED: None,
}


def translator_state(c: AppController) -> tuple[str, Grade | None]:
    """One line for the Home card: which engine will translate, and is it set up."""
    tr = c.config.translator
    if tr.engine == "deep":
        return t("home.translator.deep"), "ok"
    keys, _source = c.api_key_status()
    if keys:
        return t("home.translator.gemini", model=tr.model, keys=keys), "ok"
    if tr.fallback:
        return t("home.translator.no_key"), "warn"
    return t("home.translator.off"), "bad"


def web_state(c: AppController) -> tuple[str, Grade | None]:
    web = c.web
    if not web.running:
        if web.error:
            return t("web.state.error"), "bad"
        return t("home.state.off"), None
    return t("web.state.clients", n=web.client_count), "ok" if web.client_count else "warn"


class StatusCard(CardWidget):
    """One subsystem: icon, title, colored dot and a one-line state."""

    def __init__(self, icon: FluentIcon):
        super().__init__()
        self._title = StrongBodyLabel()
        self._state = CaptionLabel()
        self._dot = StatusDot()

        icon_widget = IconWidget(icon)
        icon_widget.setFixedSize(CARD_ICON_SIZE, CARD_ICON_SIZE)
        state_row = QHBoxLayout()
        state_row.setSpacing(8)
        state_row.addWidget(self._dot)
        state_row.addWidget(self._state, 1)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(self._title)
        text.addLayout(state_row)

        row = QHBoxLayout(self)
        row.setContentsMargins(20, 12, 20, 12)
        row.setSpacing(14)
        row.addWidget(icon_widget)
        row.addLayout(text, 1)

    def set_content(self, title: str, state: str, grade: Grade | None) -> None:
        self._title.setText(title)
        self._state.setText(state)
        self._dot.set_grade(grade)


class HomePage(BasePage):
    key = "home"

    def __init__(self, controller: AppController, parent: QWidget | None = None):
        super().__init__(controller, parent)
        self._desc.hide()  # the video needs the room
        self.body.setAlignment(Qt.AlignmentFlag(0))  # let the video stretch

        self._toggle = PrimaryPushButton()
        self._toggle.setFixedSize(*START_BUTTON_SIZE)
        self._toggle.clicked.connect(controller.toggle_running)
        self._status = StrongBodyLabel()
        self._mode = CaptionLabel()
        self._fullscreen = ToolButton(FluentIcon.FULL_SCREEN)
        self._fullscreen.clicked.connect(self.toggle_fullscreen)

        state = QVBoxLayout()
        state.setSpacing(0)
        state.addWidget(self._status)
        state.addWidget(self._mode)
        top = QHBoxLayout()
        top.setSpacing(16)
        top.addWidget(self._toggle)
        top.addLayout(state, 1)
        top.addWidget(self._fullscreen)
        self.body.addLayout(top)

        self._notice = Notice()
        self.body.addWidget(self._notice)

        self.video = VideoView(controller)
        self.video.setMinimumHeight(VIDEO_MIN_HEIGHT)
        self.video.escapePressed.connect(self._leave_fullscreen)
        self.video.doubleClicked.connect(self.toggle_fullscreen)
        self._video_index = self.body.count()
        self.body.addWidget(self.video, 1)

        self._capture_card = StatusCard(FluentIcon.VIDEO)
        self._audio_card = StatusCard(FluentIcon.VOLUME)
        self._translator_card = StatusCard(FluentIcon.LANGUAGE)
        self._web_card = StatusCard(FluentIcon.GLOBE)
        cards = QHBoxLayout()
        cards.setSpacing(12)
        for card in (self._capture_card, self._audio_card, self._translator_card, self._web_card):
            cards.addWidget(card)
        self.body.addLayout(cards)

        self._status_line = CaptionLabel()
        self.body.addWidget(self._status_line)

        self._snapshot = Snapshot()
        controller.runningChanged.connect(self.retranslate)
        controller.sourceStateChanged.connect(self.retranslate)
        controller.audioStateChanged.connect(self.retranslate)
        controller.settingsChanged.connect(self.retranslate)
        controller.metricsUpdated.connect(self._on_metrics)
        self.retranslate()

    # --- fullscreen (F11) -----------------------------------------------------------
    def toggle_fullscreen(self) -> None:
        if self.video.fullscreen:
            self._leave_fullscreen()
            return
        self.video.setWindowTitle(t("nav.home"))
        self.video.enter_fullscreen(self.window().screen().geometry())

    def _leave_fullscreen(self) -> None:
        if not self.video.fullscreen:
            return
        self.video.leave_fullscreen()
        self.body.insertWidget(self._video_index, self.video, 1)
        self.video.show()

    # --- state -> text ---------------------------------------------------------------
    def _on_metrics(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        if self.isVisible() or self.video.fullscreen:
            self._refresh_live()

    def _refresh_live(self) -> None:
        """The parts that change with every metrics tick."""
        c, snap = self.controller, self._snapshot
        self._web_card.set_content(t("home.card.web"), *web_state(c))
        source_state = c.source_state

        capture_text = t(f"source.state.{source_state}")
        actual = snap.gauge(M.SOURCE_ACTUAL)
        if source_state == SourceState.RUNNING and actual is not None:
            capture_text = f"{actual} · {snap.rate(M.SOURCE_FPS).short:.0f} FPS"
        self._capture_card.set_content(
            t(f"home.card.source.{c.config.source.kind}"), capture_text, SOURCE_GRADES[source_state]
        )

        audio_grade: Grade | None = None
        audio_text = t(f"audio.state.{c.audio_state}")
        if c.audio_state == AudioState.RUNNING:
            audio_grade = "ok"
            if c.config.audio.muted:
                audio_text, audio_grade = t("audio.muted"), None
            elif snap.gauge(M.AUDIO_SILENT):
                audio_text, audio_grade = t("audio.no_signal.short"), "warn"
            elif snap.gauge(M.AUDIO_CLIPPING):
                audio_text, audio_grade = t("audio.clipping.short"), "bad"
        elif c.audio_state == AudioState.ERROR:
            audio_grade = "bad"
        self._audio_card.set_content(t("home.card.audio"), audio_text, audio_grade)

        if c.running:
            age = snap.stat(M.RENDER_FRAME_AGE_MS)
            self._status_line.setText(
                t(
                    "home.status_line",
                    source=f"{snap.rate(M.SOURCE_FPS).short:.1f}",
                    render=f"{snap.rate(M.RENDER_FPS).short:.1f}",
                    age=f"{age.mean:.1f}",
                )
            )
        else:
            self._status_line.setText("")

        # One problem at a time, most urgent first.
        hints = mode_hints(snap.gauge(M.SOURCE_REQUESTED), actual) if c.running else []
        if c.source_problem and source_state != SourceState.RUNNING:
            grade: Grade = "bad" if source_state == SourceState.FAILED else "warn"
            self._notice.show_message(c.source_problem, grade)
        elif c.audio_state == AudioState.ERROR:
            self._notice.show_message(c.audio_message, "bad")
        elif hints:
            self._notice.show_message(
                t("home.mode_mismatch", requested=snap.gauge(M.SOURCE_REQUESTED), actual=actual)
                + " "
                + t(hints[0]),
                "warn",
            )
        else:
            self._notice.show_message("")

        if c.running and source_state != SourceState.RUNNING:
            self.video.set_placeholder(c.source_problem or t(f"source.state.{source_state}"))
        elif not c.running:
            self.video.set_placeholder(c.source_problem or t("home.video.stopped"))

    def retranslate(self, *_: object) -> None:
        super().retranslate()
        c = self.controller
        self._toggle.setText(t("home.stop") if c.running else t("home.start"))
        self._toggle.setIcon(FluentIcon.PAUSE if c.running else FluentIcon.PLAY)
        self._status.setText(t("home.status.running") if c.running else t("home.status.idle"))
        self._mode.setText(t("home.mode", mode=t(f"home.mode.{c.config.display.mode}")))
        self._fullscreen.setToolTip(t("home.fullscreen"))
        self._translator_card.set_content(t("home.card.translator"), *translator_state(c))
        self._refresh_live()
