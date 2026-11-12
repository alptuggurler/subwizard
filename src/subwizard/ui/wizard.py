"""Kurulum Sihirbazı: first-run setup in 8 steps (SPEC 3.12).

Every choice goes through AppController and is saved at once, like the rest of the app;
the wizard only decides the order. Steps reuse the real pages (Cihaz & Ses, Çeviri,
Alan & OCR) embedded without their headers, so there is one implementation of each.
Opens on start until finished or closed (`ui.wizard_done`); Ayarlar can open it again.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    IconWidget,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
    isDarkTheme,
)

from subwizard import APP_NAME, profiles
from subwizard.controller import AppController
from subwizard.i18n import available_languages, t
from subwizard.ui.icon import make_app_icon
from subwizard.ui.pages.device import DevicePage
from subwizard.ui.pages.roi import RoiPage
from subwizard.ui.pages.translate import TranslatePage
from subwizard.ui.pages.web import QrView

STEPS = ("welcome", "mode", "capture", "audio", "translate", "profile", "region", "summary")
MODES = ("window", "web", "both")
PC_GAME = "pc"  # 4th card: no capture card, the PC's own screen is the source
PROFILES = ("gta6", "default")
SIZE = (1080, 760)
SIDEBAR_WIDTH = 220
CARD_ICON = 32
BACKGROUND = {True: QColor(32, 32, 32), False: QColor(243, 243, 243)}
SIDEBAR = {True: QColor(26, 26, 26), False: QColor(233, 233, 233)}
ACCENT = QColor(124, 92, 255)


class ChoiceCard(CardWidget):
    """A big selectable option (display mode, game profile)."""

    chosen = Signal(str)

    def __init__(self, value: str, icon: FluentIcon) -> None:
        super().__init__()
        self.value = value
        self.selected = False
        self.title = StrongBodyLabel()
        self.text = BodyLabel()
        self.text.setWordWrap(True)
        self.diagram = CaptionLabel()
        self.diagram.setWordWrap(True)
        icon_widget = IconWidget(icon)
        icon_widget.setFixedSize(CARD_ICON, CARD_ICON)
        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(self.title)
        text.addWidget(self.text)
        text.addWidget(self.diagram)
        row = QHBoxLayout(self)
        row.setContentsMargins(20, 16, 20, 16)
        row.setSpacing(16)
        row.addWidget(icon_widget, 0, Qt.AlignmentFlag.AlignTop)
        row.addLayout(text, 1)
        self.clicked.connect(lambda: self.chosen.emit(self.value))

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if self.selected:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(ACCENT)
            p.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 8, 8)
            p.end()


class _Step(QWidget):
    """Title + text + content."""

    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key
        self.title = TitleLabel()
        self.text = BodyLabel()
        self.text.setWordWrap(True)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(36, 28, 36, 12)
        self.layout_.setSpacing(12)
        self.layout_.addWidget(self.title)
        self.layout_.addWidget(self.text)

    def retranslate(self, variant: str = "") -> None:
        key = variant or self.key  # a variant uses its own texts (PC game: "screen")
        self.title.setText(t(f"wizard.{key}.title"))
        self.text.setText(t(f"wizard.{key}.text"))


class SetupWizard(QWidget):
    finished = Signal()

    def __init__(self, controller: AppController) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.controller = controller
        c = controller
        self.setWindowTitle(f"{APP_NAME} — {t('wizard.window')}")
        self.setWindowIcon(make_app_icon())
        self.resize(*SIZE)

        # --- sidebar with the step list
        self._step_labels: list[BodyLabel] = []
        sidebar = QVBoxLayout()
        sidebar.setContentsMargins(24, 28, 12, 24)
        sidebar.setSpacing(14)
        self._brand = SubtitleLabel(APP_NAME)
        sidebar.addWidget(self._brand)
        sidebar.addSpacing(8)
        for _ in STEPS:
            label = BodyLabel()
            self._step_labels.append(label)
            sidebar.addWidget(label)
        sidebar.addStretch(1)
        self._sidebar = QWidget()
        self._sidebar.setFixedWidth(SIDEBAR_WIDTH)
        self._sidebar.setLayout(sidebar)  # background painted by SetupWizard.paintEvent

        # --- steps
        self.stack = QStackedWidget()
        self.steps: dict[str, _Step] = {}
        for key in STEPS:
            step = _Step(key)
            self.steps[key] = step
            self.stack.addWidget(step)

        # 1 welcome: language
        self._language = ComboBox()
        self._language.setMinimumWidth(260)
        self._languages = ["auto", *available_languages()]
        self._language.currentIndexChanged.connect(
            lambda i: c.set_language(self._languages[i]) if i >= 0 else None)
        self.steps["welcome"].layout_.addWidget(self._language, 0, Qt.AlignmentFlag.AlignLeft)
        self.steps["welcome"].layout_.addStretch(1)

        # 2 display mode
        self.mode_cards = {m: ChoiceCard(m, icon) for m, icon in
                           zip((*MODES, PC_GAME), (FluentIcon.VIDEO, FluentIcon.PHONE,
                                                   FluentIcon.TILES, FluentIcon.GAME),
                               strict=True)}  # fmt: skip
        for card in self.mode_cards.values():
            card.chosen.connect(self._choose_mode)
            self.steps["mode"].layout_.addWidget(card)
        self.steps["mode"].layout_.addStretch(1)

        # 3 capture card, 4 audio: the real Cihaz & Ses page, one half each
        self.device_video = DevicePage(c)
        self.device_video.embed(hide=self.device_video.audio_section)
        self.steps["capture"].layout_.addWidget(self.device_video, 1)
        self.device_audio = DevicePage(c)
        self.device_audio.embed(hide=self.device_audio.video_section)
        self.steps["audio"].layout_.addWidget(self.device_audio, 1)

        # 5 translation: the real Çeviri page without the advanced rows
        self.translate = TranslatePage(c)
        self.translate.embed(hide=self.translate.advanced_section)
        self._no_key = PushButton(FluentIcon.SKIP_FORWARD, "")
        self._no_key.clicked.connect(self._continue_without_key)
        self.steps["translate"].layout_.addWidget(self.translate, 1)
        self.steps["translate"].layout_.addWidget(self._no_key, 0, Qt.AlignmentFlag.AlignLeft)

        # 6 game profile
        # The built-in ones first (with translated texts), then the user's own profiles.
        found = profiles.list_profiles()
        self._profiles = {name: found[name] for name in (*PROFILES, *found) if name in found}
        self.profile_cards = {
            p: ChoiceCard(p, FluentIcon.APPLICATION if p == profiles.DEFAULT else FluentIcon.GAME)
            for p in self._profiles
        }
        for card in self.profile_cards.values():
            card.chosen.connect(self._choose_profile)
            self.steps["profile"].layout_.addWidget(card)
        self.steps["profile"].layout_.addStretch(1)

        # 7 subtitle region: the real Alan & OCR page without the OCR settings
        self.roi = RoiPage(c)
        self.roi.embed(hide=self.roi.settings_section)
        self.steps["region"].layout_.addWidget(self.roi, 1)

        # 8 summary (+ QR in Mode B)
        self._summary = BodyLabel()
        self._summary.setWordWrap(True)
        self._summary.setTextFormat(Qt.TextFormat.RichText)
        self.qr = QrView()
        self._qr_caption = CaptionLabel()
        summary_row = QHBoxLayout()
        summary_row.addWidget(self._summary, 1)
        qr_box = QVBoxLayout()
        qr_box.addWidget(self.qr)
        qr_box.addWidget(self._qr_caption)
        qr_box.addStretch(1)
        summary_row.addLayout(qr_box)
        self.steps["summary"].layout_.addLayout(summary_row)
        self.steps["summary"].layout_.addStretch(1)

        # --- bottom bar
        self._back = PushButton(FluentIcon.LEFT_ARROW, "")
        self._back.clicked.connect(self.back)
        self._next = PrimaryPushButton()
        self._next.clicked.connect(self.next)
        bar = QHBoxLayout()
        bar.setContentsMargins(36, 8, 36, 24)
        bar.addWidget(self._back)
        bar.addStretch(1)
        bar.addWidget(self._next)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.addWidget(self.stack, 1)
        content.addLayout(bar)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._sidebar)
        root.addLayout(content, 1)

        c.languageChanged.connect(self.retranslate)
        c.settingsChanged.connect(self._load)
        c.webChanged.connect(self._load)
        c.themeChanged.connect(lambda *_: self.update())
        self.retranslate()
        self.go(0)

    # --- navigation ---------------------------------------------------------------------
    @property
    def index(self) -> int:
        return self.stack.currentIndex()

    @property
    def current(self) -> str:
        return STEPS[self.index]

    def _skipped(self, key: str) -> bool:
        # Mode B: the sound comes from the TV, the PC plays nothing (SPEC 3.12 step 4).
        # PC game: the game plays its own sound.
        cfg = self.controller.config
        return key == "audio" and (cfg.display.mode == "web" or cfg.source.kind == "screen")

    @property
    def pc_game(self) -> bool:
        return self.controller.config.source.kind == "screen"

    def go(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        key = STEPS[index]
        if key in ("capture", "audio", "region") and not self.controller.running:
            self.controller.start()  # live preview, measured FPS, VU, OCR
        if key == "summary":
            self._refresh_summary()
        self._back.setEnabled(index > 0)
        self._next.setText(t("wizard.start") if key == "summary" else t("wizard.next"))
        self._next.setIcon(FluentIcon.PLAY if key == "summary" else FluentIcon.RIGHT_ARROW)
        self._refresh_sidebar()

    def next(self) -> None:
        if self.current == "summary":
            self.finish()
            return
        index = self.index + 1
        while self._skipped(STEPS[index]):
            index += 1
        self.go(index)

    def back(self) -> None:
        index = self.index - 1
        while index > 0 and self._skipped(STEPS[index]):
            index -= 1
        self.go(max(0, index))

    def finish(self) -> None:
        c = self.controller
        c.finish_wizard()
        if not c.running:
            c.start()
        self.finished.emit()
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.controller.finish_wizard()  # closed early: do not nag on every start
        super().closeEvent(event)

    # --- step actions -------------------------------------------------------------------
    def _continue_without_key(self) -> None:
        self.controller.set_translator_engine("deep")
        self.next()

    def _choose_mode(self, mode: str) -> None:
        c = self.controller
        if mode == PC_GAME:
            c.set_source_kind("screen")
            c.set_display_mode("window")  # over the game; the web page stays optional
        else:
            if c.config.source.kind == "screen":
                c.set_source_kind("capture")
            c.set_display_mode(mode)
        self._load()

    def _profile_title(self, name: str) -> str:
        if name in PROFILES:
            return t(f"wizard.profile.{name}")
        profile = self._profiles.get(name)
        return (profile.title if profile else "") or name

    def _choose_profile(self, name: str) -> None:
        self.controller.set_game_profile(name)
        self._load()

    # --- state -> widgets ---------------------------------------------------------------
    def _load(self) -> None:
        cfg = self.controller.config
        self._language.blockSignals(True)
        self._language.setCurrentIndex(self._languages.index(cfg.ui.language))
        self._language.blockSignals(False)
        for mode, card in self.mode_cards.items():
            card.set_selected(mode == PC_GAME if self.pc_game
                              else mode == cfg.display.mode)  # fmt: skip
        self.steps["capture"].retranslate("screen" if self.pc_game else "")
        for name, card in self.profile_cards.items():
            card.set_selected(name == cfg.profile)
        self._refresh_sidebar()
        if self.current == "summary":
            self._refresh_summary()

    def _refresh_sidebar(self) -> None:
        for i, (key, label) in enumerate(zip(STEPS, self._step_labels, strict=True)):
            mark = "●" if i == self.index else ("✓" if i < self.index else "○")
            short = "screen" if key == "capture" and self.pc_game else key
            text = f"{mark}  {t(f'wizard.{short}.short')}"
            if self._skipped(key):
                text += f"  ({t('wizard.skipped')})"
            label.setText(text)
            label.setEnabled(not self._skipped(key))
            font = label.font()
            font.setBold(i == self.index)
            label.setFont(font)

    def _refresh_summary(self) -> None:
        c = self.controller
        cfg = c.config
        count, _ = c.api_key_status()
        engine = (t("wizard.summary.gemini", n=count) if cfg.translator.engine == "gemini" and count
                  else t("wizard.summary.free"))  # fmt: skip
        source = (str(cfg.source.path) if cfg.source.kind == "file"
                  else t("device.monitor.item_unknown", number=cfg.source.monitor) if self.pc_game
                  else str(cfg.source.device))  # fmt: skip
        mode = t("web.mode.pc") if self.pc_game else t(f"web.mode.{cfg.display.mode}")
        rows = [
            (t("wizard.mode.short"), mode),
            (t("wizard.screen.short" if self.pc_game else "wizard.capture.short"), source),
            (t("wizard.translate.short"), engine),
            (t("wizard.profile.short"), self._profile_title(cfg.profile)),
            (t("wizard.region.short"), t(f"roi.template.{cfg.roi.template}")),
        ]  # fmt: skip
        self._summary.setText("<br>".join(f"<b>{k}:</b> {v}" for k, v in rows)
                              + f"<br><br>{t('wizard.summary.hint')}")  # fmt: skip
        web = c.web.running
        self.qr.setVisible(web)
        self._qr_caption.setVisible(web)
        if web:
            self.qr.set_text(c.web.url())
            self._qr_caption.setText(c.web.url())

    def paintEvent(self, event: QPaintEvent) -> None:
        dark = isDarkTheme()
        p = QPainter(self)
        p.fillRect(self.rect(), BACKGROUND[dark])
        p.fillRect(self._sidebar.geometry(), SIDEBAR[dark])
        p.end()

    def retranslate(self, *_: object) -> None:
        self.setWindowTitle(f"{APP_NAME} — {t('wizard.window')}")
        for step in self.steps.values():
            step.retranslate("screen" if step.key == "capture" and self.pc_game else "")
        self._language.blockSignals(True)
        self._language.clear()
        self._language.addItems([t("settings.language.auto"), *available_languages().values()])
        self._language.blockSignals(False)
        for mode, card in self.mode_cards.items():
            card.title.setText(t(f"web.mode.{mode}"))
            card.text.setText(t(f"wizard.mode.{mode}.text"))
            card.diagram.setText(t(f"wizard.mode.{mode}.diagram"))
        for name, card in self.profile_cards.items():
            card.title.setText(self._profile_title(name))
            card.text.setText(t(f"wizard.profile.{name}.text") if name in PROFILES
                              else self._profiles[name].game_info)  # fmt: skip
        self._no_key.setText(t("wizard.no_key"))
        self._back.setText(t("wizard.back"))
        for page in (self.device_video, self.device_audio, self.translate, self.roi):
            page.retranslate()
        self._load()
        if hasattr(self, "_next"):
            self.go(self.index)
