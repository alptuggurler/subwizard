"""Shared page scaffolding: title + description header, live retranslation."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    IconWidget,
    ScrollArea,
    StrongBodyLabel,
    TitleLabel,
)

from subwizard.controller import AppController
from subwizard.i18n import t

PAGE_MARGIN = 36
PAGE_SPACING = 12
ROW_ICON_SIZE = 20


class BasePage(ScrollArea):
    """A scrollable page. Subclasses add widgets to `self.body` and override
    `retranslate()` (calling super) so language changes apply instantly."""

    key: str = ""  # i18n suffix: nav.<key>, page.<key>.desc

    def __init__(self, controller: AppController, parent: QWidget | None = None):
        super().__init__(parent)
        self.controller = controller
        self.setObjectName(f"page-{self.key}")

        self._view = QWidget()
        self._view.setObjectName("pageView")
        self.setWidget(self._view)
        self.setWidgetResizable(True)
        self.setStyleSheet(
            "QScrollArea{background:transparent;border:none}#pageView{background:transparent}"
        )

        self.body = QVBoxLayout(self._view)
        self.body.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN - 8, PAGE_MARGIN, PAGE_MARGIN)
        self.body.setSpacing(PAGE_SPACING)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._title = TitleLabel()
        self._desc = BodyLabel()
        self._desc.setWordWrap(True)
        self.body.addWidget(self._title)
        self.body.addWidget(self._desc)
        self.body.addSpacing(PAGE_SPACING)

    def retranslate(self) -> None:
        self._title.setText(t(f"nav.{self.key}"))
        self._desc.setText(t(f"page.{self.key}.desc"))


class SettingRow(CardWidget):
    """One setting: icon, title, description, and a control on the right."""

    def __init__(self, icon: FluentIcon, control: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self._title = StrongBodyLabel()
        self._desc = CaptionLabel()
        self._desc.setWordWrap(True)

        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(self._title)
        text.addWidget(self._desc)

        icon_widget = IconWidget(icon)
        icon_widget.setFixedSize(ROW_ICON_SIZE, ROW_ICON_SIZE)

        row = QHBoxLayout(self)
        row.setContentsMargins(20, 14, 20, 14)
        row.setSpacing(16)
        row.addWidget(icon_widget)
        row.addLayout(text, 1)
        row.addWidget(control)

    def set_texts(self, title: str, description: str) -> None:
        self._title.setText(title)
        self._desc.setText(description)


class PlaceholderPage(BasePage):
    """A page whose content is scheduled for a later milestone."""

    milestone: str = ""

    def __init__(self, controller: AppController, parent: QWidget | None = None):
        super().__init__(controller, parent)
        self._note_title = StrongBodyLabel()
        self._note = BodyLabel()
        self._note.setWordWrap(True)

        card = CardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.addWidget(self._note_title)
        layout.addWidget(self._note)
        self.body.addWidget(card)
        self.retranslate()

    def retranslate(self) -> None:
        super().retranslate()
        self._note_title.setText(t("common.coming_soon.title"))
        self._note.setText(t("common.coming_soon", milestone=self.milestone))
