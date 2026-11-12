"""Çeviri: engine, model, API keys, context, fallback, test, cache, session cost (SPEC 3.12).

The prompt editor and multi-key management UI grow in M4.8 / M6; keys can already be
several (comma separated) and rotate on quota errors.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    HyperlinkButton,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SwitchButton,
    TransparentToolButton,
)

from subwizard.controller import AppController
from subwizard.core.metrics import M, Snapshot
from subwizard.i18n import t
from subwizard.translator.test_run import SAMPLE_TEXT, TestOutcome
from subwizard.ui.pages.base import BasePage, SettingRow
from subwizard.ui.widgets.notice import Notice
from subwizard.ui.widgets.value_slider import ValueSlider

ENGINES = ("gemini", "deep")
MODES = ("ocr", "vision")
TTS_MODES = ("off", "translation", "source")
COMBO_WIDTH = 260
KEY_FIELD_WIDTH = 240
CONTEXT_RANGE = (0, 10)
API_KEY_URL = "https://aistudio.google.com/apikey"


class TranslatePage(BasePage):
    key = "translate"

    def __init__(self, controller: AppController, parent: QWidget | None = None):
        super().__init__(controller, parent)
        self._notice = Notice()
        self.body.addWidget(self._notice)

        self._engine = ComboBox()
        self._engine.setMinimumWidth(COMBO_WIDTH)
        self._engine.addItems(list(ENGINES))
        self._engine.currentIndexChanged.connect(
            lambda i: controller.set_translator_engine(ENGINES[i]))
        self._engine_row = SettingRow(FluentIcon.LANGUAGE, self._engine)

        self._mode = ComboBox()
        self._mode.setMinimumWidth(COMBO_WIDTH)
        self._mode.addItems(list(MODES))
        self._mode.currentIndexChanged.connect(
            lambda i: controller.set_translator_mode(MODES[i]) if i >= 0 else None)
        self._mode_row = SettingRow(FluentIcon.PHOTO, self._mode)
        self._tts = ComboBox()
        self._tts.setMinimumWidth(COMBO_WIDTH)
        self._tts.addItems(list(TTS_MODES))
        self._tts.currentIndexChanged.connect(
            lambda i: controller.set_tts_mode(TTS_MODES[i]) if i >= 0 else None)
        self._tts_row = SettingRow(FluentIcon.MICROPHONE, self._tts)

        self._model = ComboBox()
        self._model.setMinimumWidth(COMBO_WIDTH)
        self._model.currentTextChanged.connect(self._on_model)
        self._model_row = SettingRow(FluentIcon.ROBOT, self._model)

        self._keys = PasswordLineEdit()
        self._keys.setFixedWidth(KEY_FIELD_WIDTH)
        self._keys.returnPressed.connect(self._save_keys)
        self._save = PrimaryPushButton()
        self._save.clicked.connect(self._save_keys)
        self._key_link = HyperlinkButton()
        self._key_link.setUrl(QUrl(API_KEY_URL))
        key_control = QWidget()
        key_layout = QHBoxLayout(key_control)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(self._key_link)
        key_layout.addWidget(self._keys)
        key_layout.addWidget(self._save)
        self._key_row = SettingRow(FluentIcon.VPN, key_control)

        # Every saved key with its live state (rotation on quota, SPEC 3.6) and a remove
        # button; keys from the environment are listed but not editable here.
        self.key_list = CardWidget()
        self._key_list_layout = QVBoxLayout(self.key_list)
        self._key_list_layout.setContentsMargins(20, 10, 20, 10)
        self._key_list_layout.setSpacing(4)
        self._shown_keys: list[tuple[str, str, int]] | None = None

        self._context = ValueSlider(CONTEXT_RANGE, str)
        self._context.slider.valueChanged.connect(controller.set_context_lines)
        self._context_row = SettingRow(FluentIcon.HISTORY, self._context)

        self._fallback = SwitchButton()
        self._fallback.setOnText("")
        self._fallback.setOffText("")
        self._fallback.checkedChanged.connect(controller.set_fallback_enabled)
        self._fallback_row = SettingRow(FluentIcon.SYNC, self._fallback)

        for row in (self._engine_row, self._model_row, self._key_row, self.key_list,
                    self._context_row, self._fallback_row, self._mode_row,
                    self._tts_row):  # fmt: skip
            self.body.addWidget(row)

        # --- test
        self._test_title = StrongBodyLabel()
        self._test_input = LineEdit()
        self._test_input.setText(SAMPLE_TEXT)
        self._test_button = PushButton()
        self._test_button.setIcon(FluentIcon.SEND)
        self._test_button.clicked.connect(self._run_test)
        self._test_result = BodyLabel()
        self._test_result.setWordWrap(True)
        self._test_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._test_meta = CaptionLabel()
        test_row = QHBoxLayout()
        test_row.addWidget(self._test_input, 1)
        test_row.addWidget(self._test_button)
        test_card = CardWidget()
        test_layout = QVBoxLayout(test_card)
        test_layout.setContentsMargins(20, 16, 20, 16)
        test_layout.setSpacing(8)
        test_layout.addWidget(self._test_title)
        test_layout.addLayout(test_row)
        test_layout.addWidget(self._test_result)
        test_layout.addWidget(self._test_meta)
        self.body.addSpacing(8)
        self.body.addWidget(test_card)

        # --- cache + session usage
        self._cache_label = BodyLabel()
        self._clear_cache = PushButton()
        self._clear_cache.setIcon(FluentIcon.DELETE)
        self._clear_cache.clicked.connect(self._on_clear_cache)
        self._cache_row = SettingRow(FluentIcon.SAVE, self._clear_cache)
        self._usage_row = SettingRow(FluentIcon.SPEED_HIGH, self._cache_label)
        self.body.addWidget(self._cache_row)
        self.body.addWidget(self._usage_row)
        self.advanced_section = [self.key_list, self._mode_row, self._tts_row, self._context_row, self._cache_row, self._usage_row]

        controller.settingsChanged.connect(self._load)
        controller.translationTested.connect(self._on_tested)
        controller.metricsUpdated.connect(self._on_metrics)
        self._load()
        self.retranslate()

    # --- reactions -----------------------------------------------------------------------
    def _on_model(self, model: str) -> None:
        if model and model != self.controller.config.translator.model:
            self.controller.set_translator_model(model)

    def _save_keys(self) -> None:
        """Adds to the saved keys (the list below removes them one by one)."""
        self.controller.add_api_keys(self._keys.text())
        self._keys.clear()

    def refresh_key_list(self) -> None:
        c = self.controller
        _count, source = c.api_key_status()
        keys = [(masked, state, round(left)) for masked, state, left in c.api_keys()]
        if keys == self._shown_keys:
            return
        self._shown_keys = keys
        while self._key_list_layout.count():
            item = self._key_list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)  # gone from the card now (Python frees it)
        self.key_list.setVisible(bool(keys))
        for index, (masked, state, left) in enumerate(keys):
            label = CaptionLabel(f"{index + 1}.  {masked}  ·  "
                                 + t(f"translate.key.{state}", s=left))  # fmt: skip
            row = QHBoxLayout()
            row.addWidget(label, 1)
            if source != "env":
                remove = TransparentToolButton(FluentIcon.DELETE)
                remove.setToolTip(t("translate.key.remove"))
                remove.clicked.connect(lambda _=False, i=index: c.remove_api_key(i))
                row.addWidget(remove)
            holder = QWidget()
            holder.setLayout(row)
            row.setContentsMargins(0, 0, 0, 0)
            self._key_list_layout.addWidget(holder)
        if source == "env":
            self._key_list_layout.addWidget(CaptionLabel(t("translate.key.env_note")))

    def _run_test(self) -> None:
        self._test_button.setEnabled(False)
        self._test_result.setText(t("translate.test.running"))
        self._test_meta.setText("")
        self.controller.test_translation(self._test_input.text().strip() or SAMPLE_TEXT)

    def _on_tested(self, outcome: TestOutcome) -> None:
        self._test_button.setEnabled(True)
        if not outcome.text:
            self._test_result.setText(t("translate.test.failed"))
            self._test_meta.setText(outcome.note)
            return
        self._test_result.setText(outcome.text)
        engine = t(f"translate.engine.{outcome.engine}")
        if outcome.fallback:
            engine += " " + t("subtitle.fallback")
        self._test_meta.setText(t("translate.test.meta", engine=engine,
                                  ms=f"{outcome.latency_ms:.0f}",
                                  cost=f"{outcome.cost_usd:.5f}"))  # fmt: skip

    def _on_clear_cache(self) -> None:
        self.controller.clear_translation_cache()

    def _on_metrics(self, snap: Snapshot) -> None:
        if not self.isVisible():
            return
        cost = snap.gauge(M.TR_COST) or 0.0
        self._cache_label.setText(t(
            "translate.usage.value", requests=snap.counter(M.TR_REQUESTS),
            hits=snap.counter(M.TR_CACHE_HITS), cost=f"{cost:.4f}"))  # fmt: skip
        self.refresh_key_list()  # a key may have started or finished resting

    def _load(self) -> None:
        tr = self.controller.config.translator
        self._engine.blockSignals(True)
        self._engine.setCurrentIndex(ENGINES.index(tr.engine))
        self._engine.blockSignals(False)
        models = list(tr.prices)
        if tr.model not in models:
            models.append(tr.model)  # a model set in config.yaml without a known price
        self._model.blockSignals(True)
        self._model.clear()
        self._model.addItems(models)
        self._model.setCurrentText(tr.model)
        self._model.blockSignals(False)
        gemini = tr.engine == "gemini"
        for row in (self._model_row, self._key_row, self._context_row, self._fallback_row):
            row.setEnabled(gemini)
        self._context.set_value(tr.context_lines)
        self._mode.blockSignals(True)
        self._mode.setCurrentIndex(MODES.index(tr.mode))
        self._mode.blockSignals(False)
        self._mode_row.setEnabled(gemini)
        self._tts.blockSignals(True)
        self._tts.setCurrentIndex(TTS_MODES.index(self.controller.config.tts.mode))
        self._tts.blockSignals(False)
        self._fallback.blockSignals(True)
        self._fallback.setChecked(tr.fallback)
        self._fallback.blockSignals(False)
        self._refresh_texts()

    def _refresh_texts(self) -> None:
        c = self.controller
        tr = c.config.translator
        count, source = c.api_key_status()
        if source == "env":
            status = t("translate.keys.from_env", n=count)
        elif count:
            status = t("translate.keys.saved", n=count)
        else:
            status = t("translate.keys.none")
        self._key_row.set_texts(t("translate.keys"), f"{t('translate.keys.desc')} {status}")
        if tr.engine == "gemini" and not count:
            grade = "warn" if tr.fallback else "bad"
            self._notice.show_message(
                t("translate.notice.no_key_fallback" if tr.fallback else "translate.notice.no_key"),
                grade)  # fmt: skip
        else:
            self._notice.show_message("")
        self._shown_keys = None
        self.refresh_key_list()
        self._cache_row.set_texts(t("translate.cache"),
                                  t("translate.cache.desc", n=c.translation_cache_size()))  # fmt: skip

    def retranslate(self, *_: object) -> None:
        super().retranslate()
        self._engine.blockSignals(True)
        for i, name in enumerate(ENGINES):
            self._engine.setItemText(i, t(f"translate.engine.{name}"))
        self._engine.blockSignals(False)
        self._engine_row.set_texts(t("translate.engine"), t("translate.engine.desc"))
        self._model_row.set_texts(t("translate.model"), t("translate.model.desc"))
        self._mode_row.set_texts(t("translate.mode"), t("translate.mode.desc"))
        self._mode.blockSignals(True)
        for i, mode in enumerate(MODES):
            self._mode.setItemText(i, t(f"translate.mode.{mode}"))
        self._mode.blockSignals(False)
        self._tts_row.set_texts(t("translate.tts"), t("translate.tts.desc"))
        self._tts.blockSignals(True)
        for i, mode in enumerate(TTS_MODES):
            self._tts.setItemText(i, t(f"translate.tts.{mode}"))
        self._tts.blockSignals(False)
        self._keys.setPlaceholderText(t("translate.keys.placeholder"))
        self._save.setText(t("translate.keys.save"))
        self._key_link.setText(t("translate.keys.how"))
        self._context_row.set_texts(t("translate.context"), t("translate.context.desc"))
        self._fallback_row.set_texts(t("translate.fallback"), t("translate.fallback.desc"))
        self._test_title.setText(t("translate.test"))
        self._test_button.setText(t("translate.test.button"))
        self._clear_cache.setText(t("translate.cache.clear"))
        self._usage_row.set_texts(t("translate.usage"), t("translate.usage.desc"))
        if not self._cache_label.text():
            self._cache_label.setText(t("translate.usage.value", requests=0, hits=0, cost="0.0000"))
        self._refresh_texts()
