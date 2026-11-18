"""Tray icon / macOS menu bar item: status, start/stop and the everyday settings in one
menu, so SubWizard can be run without opening its window (SPEC 3.12).

The menu is rebuilt each time it opens: states, today's spending and the profile list
are always current, and no signal wiring per item is needed.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from subwizard import APP_NAME, platform, profiles
from subwizard.controller import AppController
from subwizard.core.metrics import M
from subwizard.i18n import t
from subwizard.ui.icon import make_app_icon, make_menu_bar_icon

IS_MAC = sys.platform == "darwin"
STATUS_MAX_CHARS = 60  # long notices (quota, offline) are cut in the menu

SOURCE_KINDS = ("capture", "screen", "file")
DISPLAY_MODES = ("window", "web", "both")
ENGINES = ("gemini", "deep", "ollama", "test")
READ_MODES = ("ocr", "vision")
POSITIONS = ("above", "replace", "bottom", "top")
TTS_MODES = ("off", "translation", "source")


def short(text: str, limit: int = STATUS_MAX_CHARS) -> str:
    """First sentence, cut to `limit` characters."""
    first = text.split(". ")[0].rstrip(".")
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def money(usd: float) -> str:
    return f"{usd:.4f}" if usd < 1 else f"{usd:.2f}"


class TrayIcon(QSystemTrayIcon):
    def __init__(self, controller: AppController, window: QWidget):
        super().__init__(make_menu_bar_icon() if IS_MAC else make_app_icon(), window)
        self._controller = controller
        self._window = window
        self.setToolTip(APP_NAME)

        self._menu = QMenu()
        self._submenus: list[QMenu] = []
        self._groups: list[QActionGroup] = []
        self._menu.aboutToShow.connect(self.rebuild)
        self.setContextMenu(self._menu)

        self.activated.connect(self._on_activated)
        controller.dockChanged.connect(self._apply_dock)
        controller.languageChanged.connect(self.rebuild)
        self.rebuild()
        if IS_MAC and not controller.config.ui.show_in_dock:
            self._apply_dock(False)

    # --- building ------------------------------------------------------------------
    def _action(self, menu: QMenu, text: str, handler: Callable[[], object] | None = None,
                *, checked: bool | None = None, enabled: bool = True) -> QAction:  # fmt: skip
        action = QAction(text, menu)
        if checked is not None:
            action.setCheckable(True)
            action.setChecked(checked)
        action.setEnabled(enabled)
        if handler is not None:
            action.triggered.connect(lambda *_: handler())
        menu.addAction(action)
        return action

    def _choice(self, title: str, values: Sequence[str], labels: Sequence[str], current: str,
                setter: Callable[[str], object], enabled: bool = True) -> QMenu:  # fmt: skip
        """A submenu of radio items; picking one calls setter(value)."""
        sub = self._menu.addMenu(title)
        sub.setEnabled(enabled)
        self._submenus.append(sub)
        group = QActionGroup(sub)
        group.setExclusive(True)
        self._groups.append(group)
        for value, label in zip(values, labels, strict=True):
            action = self._action(sub, label, lambda v=value: setter(v), checked=value == current)
            group.addAction(action)
        return sub

    def rebuild(self, *_: object) -> None:
        for sub in self._submenus:
            sub.deleteLater()
        self._submenus.clear()
        self._groups.clear()
        self._menu.clear()
        c = self._controller
        cfg = c.config
        m = self._menu

        # Status (read-only lines).
        status = t("home.status.running") if c.running else t("home.status.idle")
        self._action(m, f"{status} · {t(f'home.card.source.{cfg.source.kind}')}", enabled=False)
        from subwizard.ui.pages.home import translator_state  # avoids a page import cycle

        self._action(m, short(translator_state(c)[0]), enabled=False)
        today = self._today_spend()
        if today or cfg.translator.engine == "gemini":
            limit = cfg.translator.daily_limit_usd
            limit_part = f" / ${limit:.2f}" if limit > 0 else ""
            self._action(m, t("tray.spend", today=money(today), limit=limit_part), enabled=False)
        m.addSeparator()

        # Everyday controls.
        self.start_action = self._action(
            m, t("tray.stop") if c.running else t("tray.start"), c.toggle_running
        )
        self._action(m, t("tray.translation_on"), c.toggle_translation_pause,
                     checked=not c.translation_paused)  # fmt: skip
        self._action(m, t("tray.subtitles_on"), c.toggle_subtitles, checked=cfg.overlay.visible)
        if cfg.source.kind != "screen":
            self._action(m, t("tray.mute"), c.toggle_mute, checked=cfg.audio.muted)
        last = c.last_translation()
        self._action(m, t("hotkeys.action.copy_last"), self._copy_last, enabled=bool(last))
        m.addSeparator()

        # Settings, as submenus.
        kinds = [k for k in SOURCE_KINDS if k != "file" or cfg.source.path]
        self._choice(t("tray.source"), kinds, [t(f"home.card.source.{k}") for k in kinds],
                     cfg.source.kind, c.set_source_kind)  # fmt: skip
        self._choice(t("web.mode"), DISPLAY_MODES, [t(f"web.mode.{v}") for v in DISPLAY_MODES],
                     cfg.display.mode, c.set_display_mode)  # fmt: skip
        self._choice(t("translate.engine"), ENGINES, [t(f"translate.engine.{v}") for v in ENGINES],
                     cfg.translator.engine, c.set_translator_engine)  # fmt: skip
        self._choice(t("translate.mode"), READ_MODES, [t(f"translate.mode.{v}") for v in READ_MODES],
                     cfg.translator.mode, c.set_translator_mode)  # fmt: skip
        self._choice(t("tray.position"), POSITIONS,
                     [t(f"appearance.position.{v}") for v in POSITIONS],
                     cfg.overlay.position, lambda v: c.set_overlay_option("position", v))  # fmt: skip
        found = profiles.list_profiles()
        names = sorted(found, key=lambda n: (n != profiles.DEFAULT, n.lower()))
        self._choice(t("nav.profiles"), names, [found[n].title or n for n in names],
                     cfg.profile, c.set_game_profile)  # fmt: skip
        self._choice(t("translate.tts"), TTS_MODES, [t(f"translate.tts.{v}") for v in TTS_MODES],
                     cfg.tts.mode, c.set_tts_mode)  # fmt: skip
        m.addSeparator()

        # Web page (phone / OBS).
        web_on = c.web.running
        self._action(m, t("tray.web_open"), self._open_web, enabled=web_on)
        self._action(m, t("tray.web_copy"), self._copy_web, enabled=web_on)
        m.addSeparator()

        # Window and pages.
        self._action(m, t("tray.show"), self.show_window)
        pages = m.addMenu(t("tray.pages"))
        self._submenus.append(pages)
        for page in getattr(self._window, "pages", []):
            self._action(pages, t(f"nav.{page.key}"), lambda p=page: self.show_window(p))
        self._action(m, t("settings.wizard.open"), c.request_wizard)
        if IS_MAC:
            self._action(m, t("tray.dock"), lambda: c.set_show_in_dock(not cfg.ui.show_in_dock),
                         checked=cfg.ui.show_in_dock)  # fmt: skip
        m.addSeparator()
        self._quit = self._action(m, t("tray.quit"), QApplication.quit)

    # Kept for callers that refresh texts after a language change.
    retranslate = rebuild

    def _today_spend(self) -> float:
        """Live gauge while a session runs; otherwise straight from the ledger file, so the
        number is there before the first Start too."""
        today = self._controller.snapshot().gauge(M.TR_COST_TODAY)
        if today is not None:
            return today
        from subwizard.translator.service import spend_path
        from subwizard.translator.spend import SpendLedger

        ledger = SpendLedger(spend_path())
        try:
            return ledger.today().cost_usd
        finally:
            ledger.close()

    # --- handlers -------------------------------------------------------------------
    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # macOS opens the menu on click; elsewhere a click opens the window.
        if not IS_MAC and reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_window()

    def show_window(self, page: QWidget | None = None) -> None:
        if page is not None and hasattr(self._window, "switchTo"):
            self._window.switchTo(page)
        self._window.showNormal()
        self._window.raise_()
        self._window.activateWindow()
        platform.activate_app()  # without a Dock icon the window would open behind others

    def _apply_dock(self, visible: bool) -> None:
        platform.set_dock_visible(visible)
        if visible and self._window.isVisible():
            self.show_window()  # the policy change hides our windows for a moment

    def _copy_last(self) -> None:
        text = self._controller.last_translation()
        if text:
            QGuiApplication.clipboard().setText(text)

    def _open_web(self) -> None:
        QDesktopServices.openUrl(QUrl(self._controller.web.url()))

    def _copy_web(self) -> None:
        QGuiApplication.clipboard().setText(self._controller.web.url())
