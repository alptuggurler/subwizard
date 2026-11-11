"""Settings: language, theme, tray behaviour. Every change applies and saves at once."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget
from qfluentwidgets import ComboBox, FluentIcon, SwitchButton

from subwizard.controller import AppController
from subwizard.i18n import available_languages, t
from subwizard.ui.pages.base import BasePage, SettingRow

THEME_MODES = ("auto", "dark", "light")
COMBO_WIDTH = 180


class SettingsPage(BasePage):
    key = "settings"

    def __init__(self, controller: AppController, parent: QWidget | None = None):
        super().__init__(controller, parent)
        self._language_codes = ["auto", *available_languages()]

        self._language = ComboBox()
        self._language.setFixedWidth(COMBO_WIDTH)
        self._theme = ComboBox()
        self._theme.setFixedWidth(COMBO_WIDTH)
        self._tray = SwitchButton()
        self._tray.setOnText("")
        self._tray.setOffText("")
        self._tray.setChecked(controller.config.ui.close_to_tray)

        self._language_row = SettingRow(FluentIcon.LANGUAGE, self._language)
        self._theme_row = SettingRow(FluentIcon.PALETTE, self._theme)
        self._tray_row = SettingRow(FluentIcon.MINIMIZE, self._tray)
        for row in (self._language_row, self._theme_row, self._tray_row):
            self.body.addWidget(row)

        self.retranslate()
        self._language.currentIndexChanged.connect(self._on_language)
        self._theme.currentIndexChanged.connect(self._on_theme)
        self._tray.checkedChanged.connect(controller.set_close_to_tray)

    def _fill(self, combo: ComboBox, labels: list[str], index: int) -> None:
        """Replace a combo's items without firing change handlers."""
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(labels)
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def retranslate(self) -> None:
        super().retranslate()
        ui = self.controller.config.ui
        names = available_languages()
        self._fill(
            self._language,
            [t("settings.language.auto"), *names.values()],
            self._language_codes.index(ui.language),
        )
        self._fill(
            self._theme,
            [t(f"settings.theme.{mode}") for mode in THEME_MODES],
            THEME_MODES.index(ui.theme),
        )
        self._language_row.set_texts(t("settings.language"), t("settings.language.desc"))
        self._theme_row.set_texts(t("settings.theme"), t("settings.theme.desc"))
        self._tray_row.set_texts(t("settings.close_to_tray"), t("settings.close_to_tray.desc"))

    def _on_language(self, index: int) -> None:
        self.controller.set_language(self._language_codes[index])

    def _on_theme(self, index: int) -> None:
        self.controller.set_theme(THEME_MODES[index])
