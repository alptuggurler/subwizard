"""Oyun Profilleri (SPEC 3.6, 3.12): pick the active profile; edit game info, prompt
extra, region template, ignore list and glossary; new / copy / import / export / delete."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TableWidget,
)

from subwizard import profiles
from subwizard.controller import AppController
from subwizard.i18n import t
from subwizard.ocr.roi import TEMPLATES
from subwizard.profiles import KEEP, GameProfile
from subwizard.ui.pages.base import BasePage
from subwizard.ui.widgets.notice import Notice

COMBO_WIDTH = 260
TEXT_HEIGHT = 72
GLOSSARY_MIN_HEIGHT = 220
ROI_CHOICES = ("", *TEMPLATES)  # "" = leave the user's region alone


def _labelled(title: QWidget, field: QWidget, hint: QWidget | None = None) -> QVBoxLayout:
    box = QVBoxLayout()
    box.setSpacing(4)
    box.addWidget(title)
    box.addWidget(field)
    if hint is not None:
        box.addWidget(hint)
    return box


class ProfilesPage(BasePage):
    key = "profiles"

    def __init__(self, controller: AppController, parent: QWidget | None = None):
        super().__init__(controller, parent)
        self._names: list[str] = []
        self._loading = False

        self._notice = Notice()
        self.body.addWidget(self._notice)

        # --- which profile
        self.picker = ComboBox()
        self.picker.setMinimumWidth(COMBO_WIDTH)
        self.picker.currentIndexChanged.connect(self._on_pick)
        self._active = CaptionLabel()
        self.use_button = PrimaryPushButton(FluentIcon.ACCEPT, "")
        self.use_button.clicked.connect(self.use_selected)
        self._new = PushButton(FluentIcon.ADD, "")
        self._new.clicked.connect(lambda: self.new_profile(copy=False))
        self._copy = PushButton(FluentIcon.COPY, "")
        self._copy.clicked.connect(lambda: self.new_profile(copy=True))
        self._import = PushButton(FluentIcon.DOWNLOAD, "")
        self._import.clicked.connect(self._ask_import)
        self._export = PushButton(FluentIcon.SHARE, "")
        self._export.clicked.connect(self._ask_export)
        self.delete_button = PushButton(FluentIcon.DELETE, "")
        self.delete_button.clicked.connect(self.delete_selected)
        top = QHBoxLayout()
        top.addWidget(self.picker)
        top.addWidget(self.use_button)
        top.addWidget(self._active, 1)
        self.body.addLayout(top)
        manage = QHBoxLayout()
        for button in (self._new, self._copy, self._import, self._export, self.delete_button):
            manage.addWidget(button)
        manage.addStretch(1)
        self.body.addLayout(manage)

        # --- the profile's fields
        self._title_label, self.title_edit = StrongBodyLabel(), LineEdit()
        self._info_label, self.info_edit = StrongBodyLabel(), PlainTextEdit()
        self._info_hint = CaptionLabel()
        self._extra_label, self.extra_edit = StrongBodyLabel(), PlainTextEdit()
        self._roi_label, self.roi_combo = StrongBodyLabel(), ComboBox()
        self.roi_combo.setMinimumWidth(COMBO_WIDTH)
        self._ignore_label, self.ignore_edit = StrongBodyLabel(), PlainTextEdit()
        self._ignore_hint = CaptionLabel()
        for edit in (self.info_edit, self.extra_edit, self.ignore_edit):
            edit.setFixedHeight(TEXT_HEIGHT)
        for hint in (self._info_hint, self._ignore_hint):
            hint.setWordWrap(True)

        fields = CardWidget()
        form = QVBoxLayout(fields)
        form.setContentsMargins(20, 16, 20, 16)
        form.setSpacing(12)
        row = QHBoxLayout()
        row.addLayout(_labelled(self._title_label, self.title_edit), 1)
        row.addLayout(_labelled(self._roi_label, self.roi_combo))
        form.addLayout(row)
        form.addLayout(_labelled(self._info_label, self.info_edit, self._info_hint))
        form.addLayout(_labelled(self._extra_label, self.extra_edit))
        form.addLayout(_labelled(self._ignore_label, self.ignore_edit, self._ignore_hint))
        self.body.addWidget(fields)

        # --- glossary
        self._glossary_title = StrongBodyLabel()
        self._glossary_hint = CaptionLabel()
        self._glossary_hint.setWordWrap(True)
        self.glossary = TableWidget()
        self.glossary.setColumnCount(2)
        self.glossary.setMinimumHeight(GLOSSARY_MIN_HEIGHT)
        self.glossary.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.glossary.verticalHeader().hide()
        header = self.glossary.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._add_term = PushButton(FluentIcon.ADD, "")
        self._add_term.clicked.connect(lambda: self.add_term("", KEEP, edit=True))
        self._remove_term = PushButton(FluentIcon.REMOVE, "")
        self._remove_term.clicked.connect(self.remove_selected_terms)
        glossary_bar = QHBoxLayout()
        glossary_bar.addWidget(self._glossary_title)
        glossary_bar.addStretch(1)
        glossary_bar.addWidget(self._add_term)
        glossary_bar.addWidget(self._remove_term)
        glossary_card = CardWidget()
        glossary_layout = QVBoxLayout(glossary_card)
        glossary_layout.setContentsMargins(20, 16, 20, 16)
        glossary_layout.addLayout(glossary_bar)
        glossary_layout.addWidget(self._glossary_hint)
        glossary_layout.addWidget(self.glossary)
        self.body.addWidget(glossary_card)

        self.save_button = PrimaryPushButton(FluentIcon.SAVE, "")
        self.save_button.clicked.connect(self.save)
        self._saved = BodyLabel()
        bottom = QHBoxLayout()
        bottom.addWidget(self.save_button)
        bottom.addWidget(self._saved, 1)
        self.body.addLayout(bottom)

        controller.profileChanged.connect(self.reload)
        self.retranslate()

    # --- state -----------------------------------------------------------------------
    @property
    def selected(self) -> str:
        i = self.picker.currentIndex()
        return self._names[i] if 0 <= i < len(self._names) else profiles.DEFAULT

    def reload(self, select: str | None = None) -> None:
        current = select or (self.selected if self._names else self.controller.config.profile)
        found = profiles.list_profiles()
        self._names = list(found)
        self.picker.blockSignals(True)
        self.picker.clear()
        active = self.controller.config.profile
        for name, profile in found.items():
            label = profile.title or name
            self.picker.addItem(f"{label}  ✓" if name == active else label)
        self.picker.setCurrentIndex(self._names.index(current) if current in self._names else 0)
        self.picker.blockSignals(False)
        self._show(found.get(self.selected) or profiles.load_profile(self.selected))

    def _on_pick(self, index: int) -> None:
        if index >= 0:
            self._show(profiles.load_profile(self.selected))

    def _show(self, profile: GameProfile) -> None:
        self._loading = True
        self.title_edit.setText(profile.title)
        self.info_edit.setPlainText(profile.game_info)
        self.extra_edit.setPlainText(profile.prompt_extra)
        self.ignore_edit.setPlainText("\n".join(profile.ignore_phrases))
        self.roi_combo.setCurrentIndex(ROI_CHOICES.index(profile.roi_template)
                                       if profile.roi_template in ROI_CHOICES else 0)  # fmt: skip
        self.glossary.setRowCount(0)
        for term, target in profile.glossary.items():
            self.add_term(term, target)
        self._loading = False
        name = profile.name
        is_active = name == self.controller.config.profile
        self.use_button.setEnabled(not is_active)
        self._active.setText(t("profiles.active", name=self._title_of(self.controller.config.profile)))
        # A built-in profile can only be "reset" (its user copy removed), not deleted.
        builtin, user = profiles.is_builtin(name), profiles.is_user(name)
        self.delete_button.setEnabled(user)
        self.delete_button.setText(t("profiles.reset") if builtin else t("profiles.delete"))
        self._saved.setText("")

    def _title_of(self, name: str) -> str:
        return profiles.load_profile(name).title or name

    def current_profile(self) -> GameProfile:
        """What the form shows, as a profile (not yet saved)."""
        glossary: dict[str, str] = {}
        for row in range(self.glossary.rowCount()):
            term, target = (self.glossary.item(row, c) for c in (0, 1))
            term_text = term.text().strip() if term else ""
            if term_text:
                glossary[term_text] = (target.text().strip() if target else "") or KEEP
        ignore = [line.strip() for line in self.ignore_edit.toPlainText().splitlines() if line.strip()]
        return GameProfile(
            name=self.selected, title=self.title_edit.text().strip(),
            game_info=self.info_edit.toPlainText().strip(),
            prompt_extra=self.extra_edit.toPlainText().strip(),
            roi_template=ROI_CHOICES[max(0, self.roi_combo.currentIndex())],
            ignore_phrases=ignore, glossary=glossary,
        )  # fmt: skip

    # --- actions ---------------------------------------------------------------------
    def add_term(self, term: str, target: str, edit: bool = False) -> None:
        row = self.glossary.rowCount()
        self.glossary.insertRow(row)
        self.glossary.setItem(row, 0, QTableWidgetItem(term))
        self.glossary.setItem(row, 1, QTableWidgetItem(target))
        if edit:
            self.glossary.setCurrentCell(row, 0)
            self.glossary.editItem(self.glossary.item(row, 0))

    def remove_selected_terms(self) -> None:
        for row in sorted({i.row() for i in self.glossary.selectedIndexes()}, reverse=True):
            self.glossary.removeRow(row)

    def save(self) -> None:
        profile = self.current_profile()
        self.controller.save_game_profile(profile)
        self.reload(select=profile.name)
        self._saved.setText(t("profiles.saved"))

    def use_selected(self) -> None:
        self.save()
        self.controller.set_game_profile(self.selected)

    def new_profile(self, copy: bool) -> None:
        base = self.current_profile() if copy else GameProfile()
        name = profiles.unique_name(f"{self.selected}-copy" if copy else "new-game")
        title = f"{base.title or self.selected} ({t('profiles.copy_suffix')})" if copy else (
            t("profiles.new_title"))
        self.controller.save_game_profile(base.model_copy(update={"name": name, "title": title}))
        self.reload(select=name)

    def delete_selected(self) -> None:
        self.controller.delete_game_profile(self.selected)
        self.reload(select=self.selected if self.selected in profiles.list_profiles() else None)

    def import_file(self, path: Path) -> None:
        try:
            profile = profiles.import_profile(path)
        except ValueError:
            self._notice.show_message(t("profiles.import_failed", file=path.name), "bad")
            return
        self._notice.show_message("")
        self.controller.profileChanged.emit()
        self.reload(select=profile.name)

    def export_file(self, path: Path) -> None:
        path.write_text(self.current_profile().to_yaml(), encoding="utf-8")
        self._saved.setText(t("profiles.exported", file=path.name))

    def _ask_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, t("profiles.import"), "", "YAML (*.yaml *.yml)")
        if path:
            self.import_file(Path(path))

    def _ask_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, t("profiles.export"), f"{self.selected}.yaml",
                                              "YAML (*.yaml)")  # fmt: skip
        if path:
            self.export_file(Path(path))

    def retranslate(self) -> None:
        super().retranslate()
        self.use_button.setText(t("profiles.use"))
        self._new.setText(t("profiles.new"))
        self._copy.setText(t("profiles.copy"))
        self._import.setText(t("profiles.import"))
        self._export.setText(t("profiles.export"))
        self._title_label.setText(t("profiles.title"))
        self._info_label.setText(t("profiles.game_info"))
        self._info_hint.setText(t("profiles.game_info.hint"))
        self._extra_label.setText(t("profiles.prompt_extra"))
        self._roi_label.setText(t("profiles.roi"))
        self._ignore_label.setText(t("profiles.ignore"))
        self._ignore_hint.setText(t("profiles.ignore.hint"))
        self._glossary_title.setText(t("profiles.glossary"))
        self._glossary_hint.setText(t("profiles.glossary.hint"))
        self._add_term.setText(t("profiles.term.add"))
        self._remove_term.setText(t("profiles.term.remove"))
        self.glossary.setHorizontalHeaderLabels([t("profiles.term"), t("profiles.target")])
        self.save_button.setText(t("profiles.save"))
        self.roi_combo.blockSignals(True)
        index = max(0, self.roi_combo.currentIndex())
        self.roi_combo.clear()
        self.roi_combo.addItems([t("profiles.roi.keep")] + [t(f"roi.template.{n}") for n in TEMPLATES])
        self.roi_combo.setCurrentIndex(index)
        self.roi_combo.blockSignals(False)
        self.reload()
