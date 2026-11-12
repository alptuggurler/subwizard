"""Kısayollar: every action with its key; click, press a new key, done (SPEC 3.8, 3.12).
Esc cancels, Backspace clears. A key already used by another action is saved anyway and
both rows turn red until one of them is changed."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFocusEvent, QIcon, QKeyEvent, QKeySequence
from PySide6.QtWidgets import QWidget
from qfluentwidgets import FluentIcon, PushButton, SwitchButton

from subwizard.controller import AppController
from subwizard.i18n import t
from subwizard.ui.pages.base import BasePage, SettingRow
from subwizard.ui.widgets.notice import Notice
from subwizard.utils.hotkeys import ACTIONS, GlobalHotkeys, conflicts

RECORDER_WIDTH = 160
MODIFIER_KEYS = {Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta}


class KeyRecorder(PushButton):
    """Shows a key; after a click, the next key press (with modifiers) becomes the key."""

    recorded = Signal(str)  # "" = cleared

    def __init__(self) -> None:
        super().__init__()
        self.setFixedWidth(RECORDER_WIDTH)
        self.recording = False
        self._key = ""
        self.clicked.connect(self.start)

    def set_key(self, key: str) -> None:
        self._key = key
        if not self.recording:
            self.setText(key or t("hotkeys.none"))

    def start(self) -> None:
        self.recording = True
        self.setText(t("hotkeys.press"))
        self.setFocus()
        self.grabKeyboard()

    def _finish(self, key: str | None) -> None:
        self.recording = False
        self.releaseKeyboard()
        if key is None:
            self.set_key(self._key)
        else:
            self.set_key(key)
            self.recorded.emit(key)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self.recording:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in MODIFIER_KEYS:
            return  # wait for the real key
        if key == Qt.Key.Key_Escape:
            self._finish(None)
        elif key == Qt.Key.Key_Backspace:
            self._finish("")
        else:
            self._finish(QKeySequence(event.keyCombination()).toString(
                QKeySequence.SequenceFormat.PortableText))

    def focusOutEvent(self, event: QFocusEvent) -> None:
        if self.recording:
            self._finish(None)
        super().focusOutEvent(event)


class HotkeysPage(BasePage):
    key = "hotkeys"

    def __init__(self, controller: AppController, parent: QWidget | None = None):
        super().__init__(controller, parent)
        self._notice = Notice()
        self.body.addWidget(self._notice)

        self._global = SwitchButton()
        self._global.setOnText("")
        self._global.setOffText("")
        self._global.checkedChanged.connect(controller.set_global_hotkeys)
        self._global_row = SettingRow(FluentIcon.GLOBE, self._global)
        self.body.addWidget(self._global_row)

        self._start_paused = SwitchButton()
        self._start_paused.setOnText("")
        self._start_paused.setOffText("")
        self._start_paused.checkedChanged.connect(controller.set_start_paused)
        self._start_paused_row = SettingRow(FluentIcon.PAUSE, self._start_paused)
        self.body.addWidget(self._start_paused_row)

        self.recorders: dict[str, KeyRecorder] = {}
        self._rows: dict[str, SettingRow] = {}
        for action in ACTIONS:
            recorder = KeyRecorder()
            recorder.recorded.connect(lambda key, a=action: controller.set_hotkey(a, key))
            self.recorders[action] = recorder
            self._rows[action] = SettingRow(FluentIcon.COMMAND_PROMPT, recorder)
            self.body.addWidget(self._rows[action])

        self._reset = PushButton(FluentIcon.SYNC, "")
        self._reset.clicked.connect(controller.reset_hotkeys)
        self.body.addWidget(self._reset, 0, Qt.AlignmentFlag.AlignLeft)

        controller.hotkeysChanged.connect(self._load)
        controller.settingsChanged.connect(self._load)
        self.retranslate()

    def _load(self) -> None:
        cfg = self.controller.config.hotkeys
        self._global.blockSignals(True)
        self._global.setChecked(cfg.global_hotkeys)
        self._global.blockSignals(False)
        self._start_paused.blockSignals(True)
        self._start_paused.setChecked(self.controller.config.translator.start_paused)
        self._start_paused.blockSignals(False)
        clashing = conflicts(cfg.keys)
        clash_actions = {a for actions in clashing.values() for a in actions}
        for action, recorder in self.recorders.items():
            recorder.set_key(cfg.keys.get(action, ""))
            # An icon, not a style sheet: clearing a sheet would drop the Fluent button style.
            recorder.setIcon(FluentIcon.INFO if action in clash_actions else QIcon())
        if clashing:
            key, actions = next(iter(clashing.items()))
            self._notice.show_message(t(
                "hotkeys.conflict", combo=key,
                actions=", ".join(t(f"hotkeys.action.{a}") for a in actions)), "warn")  # fmt: skip
        elif cfg.global_hotkeys and not GlobalHotkeys.supported():
            self._notice.show_message(t("hotkeys.global_unsupported"), None)
        else:
            self._notice.show_message("")

    def retranslate(self) -> None:
        super().retranslate()
        self._global_row.set_texts(t("hotkeys.global"), t("hotkeys.global.desc"))
        key = self.controller.config.hotkeys.keys.get("pause_translation") or t("hotkeys.none")
        self._start_paused_row.set_texts(t("hotkeys.start_paused"),
                                         t("hotkeys.start_paused.desc", hotkey=key))  # fmt: skip
        for action, row in self._rows.items():
            row.set_texts(t(f"hotkeys.action.{action}"), t(f"hotkeys.action.{action}.desc"))
        self._reset.setText(t("hotkeys.reset"))
        self._load()
