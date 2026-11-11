"""Theme switching (dark / light / follow system), applied live."""

from __future__ import annotations

from qfluentwidgets import Theme, setTheme, setThemeColor

ACCENT_COLOR = "#7C5CFF"
_THEMES = {"auto": Theme.AUTO, "dark": Theme.DARK, "light": Theme.LIGHT}


def apply_theme(mode: str) -> None:
    setTheme(_THEMES[mode])
    setThemeColor(ACCENT_COLOR)
