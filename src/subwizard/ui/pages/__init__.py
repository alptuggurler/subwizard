"""Navigation registry: which pages exist, in what order, with which icon.

Pages that are still placeholders are declared here with the milestone that fills
them in; each moves to its own module when it gets real content.
"""

from __future__ import annotations

from dataclasses import dataclass

from qfluentwidgets import FluentIcon

from subwizard.ui.pages.about import AboutPage
from subwizard.ui.pages.base import BasePage, PlaceholderPage
from subwizard.ui.pages.home import HomePage
from subwizard.ui.pages.settings import SettingsPage


@dataclass(frozen=True)
class PageSpec:
    cls: type[BasePage]
    icon: FluentIcon
    bottom: bool = False  # pinned to the bottom of the navigation bar


def _placeholder(key: str, milestone: str) -> type[BasePage]:
    return type(f"{key.title()}Page", (PlaceholderPage,), {"key": key, "milestone": milestone})


PAGES: tuple[PageSpec, ...] = (
    PageSpec(HomePage, FluentIcon.HOME),
    PageSpec(_placeholder("history", "M4"), FluentIcon.HISTORY),
    PageSpec(_placeholder("roi", "M2"), FluentIcon.ZOOM_IN),
    PageSpec(_placeholder("translate", "M3"), FluentIcon.LANGUAGE),
    PageSpec(_placeholder("appearance", "M4"), FluentIcon.PALETTE),
    PageSpec(_placeholder("profiles", "M6"), FluentIcon.GAME),
    PageSpec(_placeholder("web", "M4.5"), FluentIcon.GLOBE),
    PageSpec(_placeholder("device", "M1"), FluentIcon.VIDEO),
    PageSpec(_placeholder("diagnostics", "M1"), FluentIcon.SPEED_HIGH),
    PageSpec(_placeholder("hotkeys", "M4"), FluentIcon.COMMAND_PROMPT),
    PageSpec(SettingsPage, FluentIcon.SETTING, bottom=True),
    PageSpec(AboutPage, FluentIcon.INFO, bottom=True),
)
