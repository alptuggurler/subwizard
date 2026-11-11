"""AppController: the thin bridge between the Qt-free core and the UI (SPEC 3.12).

The UI never touches config or the pipeline directly; it calls the controller and
listens to its signals. Every setting change is applied and saved immediately.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QLocale, QObject, Signal

from subwizard.config import AppConfig, Language, ThemeMode, save_config
from subwizard.i18n import i18n, match_language

log = logging.getLogger(__name__)


class AppController(QObject):
    languageChanged = Signal(str)  # resolved language code ("tr", "en")
    themeChanged = Signal(str)  # "auto" | "dark" | "light"
    runningChanged = Signal(bool)

    def __init__(self, config: AppConfig, config_path: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.config = config
        self._config_path = config_path
        self._running = False
        i18n.set_language(self.resolved_language())

    # --- settings ---------------------------------------------------------
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

    def _save(self) -> None:
        try:
            save_config(self.config, self._config_path)
        except OSError as exc:
            # The change still applies for this session; losing persistence is not fatal.
            log.error("Ayarlar kaydedilemedi (%s): %s", self._config_path, exc)

    # --- pipeline (wired up from M1 on) -------------------------------------
    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._set_running(True)

    def stop(self) -> None:
        self._set_running(False)

    def toggle_running(self) -> None:
        self._set_running(not self._running)

    def _set_running(self, running: bool) -> None:
        if running == self._running:
            return
        self._running = running
        log.info("Durum: %s", "çalışıyor" if running else "durdu")
        self.runningChanged.emit(running)
