"""Çeviri: engine, model, API keys, context, fallback, test, cache, session cost (SPEC 3.12).

The prompt editor and multi-key management UI grow in M4.8 / M6; keys can already be
several (comma separated) and rotate on quota errors.
"""

from __future__ import annotations

from PySide6.QtCore import QLocale, Qt, QUrl
from PySide6.QtGui import QDoubleValidator
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
from subwizard.translator import openrouter as orouter
from subwizard.translator.test_run import SAMPLE_TEXT, TestOutcome
from subwizard.ui.pages.base import BasePage, SettingRow
from subwizard.ui.widgets.notice import Notice
from subwizard.ui.widgets.value_slider import ValueSlider

ENGINES = ("gemini", "openrouter", "deep", "ollama", "test")
PAID_ENGINES = ("gemini", "openrouter")  # model, keys, context, spending
MODES = ("ocr", "vision")
TTS_MODES = ("off", "translation", "source")
TTS_ENGINES = ("auto", "edge")
LOCAL_MODEL_WIDTH = 200
TTS_COMBO_WIDTH = 220  # system voice (SAPI / say) or Edge online neural voices
COMBO_WIDTH = 260
KEY_FIELD_WIDTH = 240
CONTEXT_RANGE = (0, 10)
API_KEY_URLS = {
    "gemini": "https://aistudio.google.com/apikey",
    "openrouter": "https://openrouter.ai/settings/keys",
}
OPENROUTER_CREDITS_URL = "https://openrouter.ai/settings/credits"
# Offered in the model list; any other openrouter.ai/models name can go in config.yaml.
OPENROUTER_MODELS = ("google/gemini-2.5-flash-lite", "google/gemini-2.5-flash")


LIMIT_FIELD_WIDTH = 120


def hours_text(hours: float) -> str:
    """"~230", or one decimal under 10 hours ("~2.5")."""
    return f"~{hours:.0f}" if hours >= 10 else f"~{hours:.1f}"


def money(usd: float) -> str:
    """Small amounts keep four decimals ($0.0012), larger ones two ($1.25)."""
    return f"{usd:.4f}" if usd < 1 else f"{usd:.2f}"


def spend_text(snap: Snapshot, limit: float) -> str:
    """Today / all time / this session, from the ledger's live gauges."""
    limit_part = t("translate.spend.limit_part", limit=f"{limit:.2f}") if limit > 0 else ""
    return t("translate.spend.value", today=money(snap.gauge(M.TR_COST_TODAY) or 0.0),
             limit=limit_part, total=money(snap.gauge(M.TR_COST_TOTAL) or 0.0),
             session=money(snap.gauge(M.TR_COST) or 0.0))  # fmt: skip


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
        self._tts_engine = ComboBox()
        self._tts_engine.setMinimumWidth(COMBO_WIDTH)
        self._tts_engine.addItems(list(TTS_ENGINES))
        self._tts_engine.currentIndexChanged.connect(
            lambda i: controller.set_tts_engine(TTS_ENGINES[i]) if i >= 0 else None)
        tts_control = QWidget()
        tts_layout = QHBoxLayout(tts_control)
        tts_layout.setContentsMargins(0, 0, 0, 0)
        for combo in (self._tts_engine, self._tts):  # fixed: leave room for the description
            combo.setFixedWidth(TTS_COMBO_WIDTH)
            tts_layout.addWidget(combo)
        self._tts_row = SettingRow(FluentIcon.MICROPHONE, tts_control)

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

        # Local model (Ollama): its name, and whether it is Gemini's first fallback.
        self._local_model = LineEdit()
        self._local_model.setFixedWidth(LOCAL_MODEL_WIDTH)
        self._local_model.editingFinished.connect(
            lambda: controller.set_ollama_model(self._local_model.text()))
        self._local_fallback = SwitchButton()
        self._local_fallback.setOnText("")
        self._local_fallback.setOffText("")
        self._local_fallback.checkedChanged.connect(controller.set_local_fallback)
        local_control = QWidget()
        local_layout = QHBoxLayout(local_control)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_layout.addWidget(self._local_model)
        local_layout.addWidget(self._local_fallback)
        self._local_row = SettingRow(FluentIcon.IOT, local_control)

        # OpenRouter account: what the key may still spend, free requests today, and what
        # a top-up buys with the chosen model (shown only for that engine).
        self.account_card = CardWidget()
        account_layout = QVBoxLayout(self.account_card)
        account_layout.setContentsMargins(20, 14, 20, 14)
        account_layout.setSpacing(6)
        account_head = QHBoxLayout()
        self._account_title = StrongBodyLabel()
        self._account_refresh = PushButton()
        self._account_refresh.setIcon(FluentIcon.SYNC)
        self._account_refresh.clicked.connect(self.refresh_account)
        self._account_link = HyperlinkButton()
        self._account_link.setUrl(QUrl(OPENROUTER_CREDITS_URL))
        account_head.addWidget(self._account_title, 1)
        account_head.addWidget(self._account_link)
        account_head.addWidget(self._account_refresh)
        account_layout.addLayout(account_head)
        self._account_status = BodyLabel()
        self._account_status.setWordWrap(True)
        self._account_plan = CaptionLabel()
        self._account_plan.setWordWrap(True)
        account_layout.addWidget(self._account_status)
        account_layout.addWidget(self._account_plan)
        self._account: tuple[object, str] | None = None  # last answer, kept for retranslate
        controller.openrouterAccount.connect(self._on_account)

        for row in (self._engine_row, self._model_row, self._key_row, self.key_list,
                    self.account_card,
                    self._context_row, self._fallback_row, self._local_row, self._mode_row,
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
        self._test_meta.setWordWrap(True)  # an error reason can be long; copyable for reports
        self._test_meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
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

        # --- spending: live numbers and the daily limit (applies on Enter / leaving)
        self._spend_label = BodyLabel()
        self._spend_row = SettingRow(FluentIcon.SHOPPING_CART, self._spend_label)
        self._limit = LineEdit()
        self._limit.setFixedWidth(LIMIT_FIELD_WIDTH)
        validator = QDoubleValidator(0.0, 1000.0, 2, self._limit)
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        validator.setLocale(QLocale.c())  # "0.50" on every system (a Turkish one would want "0,50")
        self._limit.setValidator(validator)
        self._limit.editingFinished.connect(self._on_limit)
        self._limit_row = SettingRow(FluentIcon.STOP_WATCH, self._limit)

        for row in (self._spend_row, self._limit_row, self._cache_row, self._usage_row):
            self.body.addWidget(row)
        self.advanced_section = [self.key_list, self._mode_row, self._tts_row, self._context_row,
                                 self._local_row,
                                 self._spend_row, self._limit_row, self._cache_row,
                                 self._usage_row]  # fmt: skip

        controller.settingsChanged.connect(self._load)
        controller.translationTested.connect(self._on_tested)
        controller.metricsUpdated.connect(self._on_metrics)
        self._load()
        self.retranslate()

    # --- reactions -----------------------------------------------------------------------
    def _on_model(self, model: str) -> None:
        tr = self.controller.config.translator
        current = tr.openrouter_model if tr.engine == "openrouter" else tr.model
        if model and model != current:
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

    # --- OpenRouter account ---------------------------------------------------------------
    def refresh_account(self) -> None:
        self._account_refresh.setEnabled(False)
        self._account_status.setText(t("translate.or.loading"))
        self.controller.refresh_openrouter_account()

    def _on_account(self, info: object, error: str) -> None:
        self._account = (info, error)
        self._account_refresh.setEnabled(True)
        self._show_account()

    def _show_account(self) -> None:
        tr = self.controller.config.translator
        info, error = self._account or (None, "")
        if error == "no_key":
            self._account_status.setText(t("translate.or.no_key"))
        elif error:
            self._account_status.setText(t("translate.or.error", reason=error))
        elif isinstance(info, orouter.KeyInfo):
            lines = []
            if info.remaining is not None and info.limit is not None:
                lines.append(t("translate.or.limit", limit=f"{info.limit:.2f}",
                               remaining=f"{info.remaining:.2f}", today=money(info.usage_daily)))  # fmt: skip
            else:
                lines.append(t("translate.or.no_limit", usage=money(info.usage),
                               today=money(info.usage_daily)))  # fmt: skip
            per_hour = orouter.cost_per_hour(tr.openrouter_model, self._line_tokens())
            hours = orouter.hours_for(info.remaining or 0.0, per_hour)
            if hours is not None and info.remaining:
                lines.append(t("translate.or.hours", model=tr.openrouter_model,
                               hours=hours_text(hours)))  # fmt: skip
            if info.free_limit:
                lines.append(t("translate.or.free", used=info.free_used, limit=info.free_limit))
            self._account_status.setText("\n".join(lines))
        else:
            self._account_status.setText("")
        self._account_plan.setText(self._plan_text())

    def _line_tokens(self) -> tuple[float, float]:
        return self.controller.average_line_tokens() or orouter.TYPICAL_LINE_TOKENS

    def _plan_text(self) -> str:
        """What a top-up buys with the chosen model, and the free-model daily caps."""
        model = self.controller.config.translator.openrouter_model
        per_hour = orouter.cost_per_hour(model, self._line_tokens())
        free = t("translate.or.free_rule", low=orouter.FREE_DAILY_LOW,
                 high=orouter.FREE_DAILY_HIGH, threshold=f"{orouter.FREE_THRESHOLD_USD:.0f}")  # fmt: skip
        if per_hour is None:
            return t("translate.or.unknown_price", model=model) + " " + free
        top_ups = " · ".join(
            t("translate.or.top_up", usd=usd, hours=hours_text(usd / per_hour))
            for usd in orouter.TOP_UPS_USD)  # fmt: skip
        return t("translate.or.plan", model=model, lines=orouter.LINES_PER_HOUR,
                 per_hour=money(per_hour), top_ups=top_ups) + " " + free  # fmt: skip

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

    def _on_limit(self) -> None:
        text = self._limit.text().strip().replace(",", ".")
        try:
            usd = float(text) if text else 0.0
        except ValueError:
            return
        if usd != self.controller.config.translator.daily_limit_usd:
            self.controller.set_daily_limit(usd)

    def _on_metrics(self, snap: Snapshot) -> None:
        if not self.isVisible():
            return
        cost = snap.gauge(M.TR_COST) or 0.0
        self._cache_label.setText(t(
            "translate.usage.value", requests=snap.counter(M.TR_REQUESTS),
            hits=snap.counter(M.TR_CACHE_HITS), cost=f"{cost:.4f}"))  # fmt: skip
        self._spend_label.setText(spend_text(snap, self.controller.config.translator.daily_limit_usd))
        self.refresh_key_list()  # a key may have started or finished resting

    def _load(self) -> None:
        tr = self.controller.config.translator
        self._engine.blockSignals(True)
        self._engine.setCurrentIndex(ENGINES.index(tr.engine))
        self._engine.blockSignals(False)
        openrouter = tr.engine == "openrouter"
        current = tr.openrouter_model if openrouter else tr.model
        models = list(OPENROUTER_MODELS if openrouter else tr.prices)
        if current not in models:
            models.append(current)  # a model set in config.yaml without a known price
        self._model.blockSignals(True)
        self._model.clear()
        self._model.addItems(models)
        self._model.setCurrentText(current)
        self._model.blockSignals(False)
        gemini = tr.engine == "gemini"
        paid = tr.engine in PAID_ENGINES
        self._key_link.setUrl(QUrl(API_KEY_URLS["openrouter" if openrouter else "gemini"]))
        for row in (self._model_row, self._key_row, self._context_row, self._fallback_row):
            row.setEnabled(paid)
        self._context.set_value(tr.context_lines)
        self._mode.blockSignals(True)
        self._mode.setCurrentIndex(MODES.index(tr.mode))
        self._mode.blockSignals(False)
        self._mode_row.setEnabled(gemini)  # vision mode is Gemini only
        self._local_model.blockSignals(True)
        self._local_model.setText(tr.ollama_model)
        self._local_model.blockSignals(False)
        self._local_fallback.blockSignals(True)
        self._local_fallback.setChecked(tr.local_fallback)
        self._local_fallback.blockSignals(False)
        self._local_fallback.setEnabled(paid)  # as the engine it is not a fallback
        self._local_row.setEnabled(paid or tr.engine == "ollama")
        self._tts.blockSignals(True)
        self._tts.setCurrentIndex(TTS_MODES.index(self.controller.config.tts.mode))
        self._tts.blockSignals(False)
        self._tts_engine.blockSignals(True)
        self._tts_engine.setCurrentIndex(int(self.controller.config.tts.engine == "edge"))
        self._tts_engine.setEnabled(self.controller.config.tts.mode != "off")
        self._tts_engine.blockSignals(False)
        self._fallback.blockSignals(True)
        self._fallback.setChecked(tr.fallback)
        self._fallback.blockSignals(False)
        if not self._limit.hasFocus():  # never overwrite what is being typed
            self._limit.setText(f"{tr.daily_limit_usd:.2f}" if tr.daily_limit_usd else "")
        self._limit_row.setEnabled(paid)
        was_visible = self.account_card.isVisibleTo(self)
        self.account_card.setVisible(openrouter)
        if openrouter and (not was_visible or self._account is None):
            self.refresh_account()  # engine just chosen, or first look
        elif openrouter:
            self._show_account()  # the model may have changed: new estimates
        self._spend_label.setText(spend_text(self.controller.snapshot(), tr.daily_limit_usd))
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
        keys = "translate.keys.openrouter" if tr.engine == "openrouter" else "translate.keys"
        self._key_row.set_texts(t(keys), f"{t(keys + '.desc')} {status}")
        self._keys.setPlaceholderText(t(keys + ".placeholder"))
        if tr.engine == "test":
            self._notice.show_message(t("translate.notice.test"), "warn")
        elif tr.engine in PAID_ENGINES and not count:
            grade = "warn" if tr.fallback else "bad"
            prefix = "translate.notice.no_openrouter_key" if tr.engine == "openrouter" else (
                "translate.notice.no_key")
            self._notice.show_message(t(f"{prefix}_fallback" if tr.fallback else prefix), grade)
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
        self._tts_engine.blockSignals(True)
        for i, engine in enumerate(TTS_ENGINES):
            self._tts_engine.setItemText(i, t(f"translate.tts.engine.{engine}"))
        self._tts_engine.blockSignals(False)
        self._save.setText(t("translate.keys.save"))
        self._key_link.setText(t("translate.keys.how"))
        self._context_row.set_texts(t("translate.context"), t("translate.context.desc"))
        self._fallback_row.set_texts(t("translate.fallback"), t("translate.fallback.desc"))
        self._local_row.set_texts(t("translate.local"), t("translate.local.desc"))
        self._local_model.setPlaceholderText("translategemma:4b")
        self._local_fallback.setToolTip(t("translate.local.fallback"))
        self._test_title.setText(t("translate.test"))
        self._test_button.setText(t("translate.test.button"))
        self._clear_cache.setText(t("translate.cache.clear"))
        self._usage_row.set_texts(t("translate.usage"), t("translate.usage.desc"))
        self._spend_row.set_texts(t("translate.spend"), t("translate.spend.desc"))
        self._limit_row.set_texts(t("translate.limit"), t("translate.limit.desc"))
        self._limit.setPlaceholderText(t("translate.limit.placeholder"))
        self._account_title.setText(t("translate.or.title"))
        self._account_refresh.setText(t("translate.or.refresh"))
        self._account_link.setText(t("translate.or.credits"))
        if self.controller.config.translator.engine == "openrouter":
            self._show_account()
        if not self._cache_label.text():
            self._cache_label.setText(t("translate.usage.value", requests=0, hits=0, cost="0.0000"))
        self._refresh_texts()
