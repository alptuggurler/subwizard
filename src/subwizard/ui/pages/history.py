"""Altyazı Geçmişi: every line of the session with its translation (SPEC 3.9, 3.12):
search, copy, retranslate, mark as bad, export as .txt / .srt."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QTableWidgetItem, QWidget
from qfluentwidgets import CaptionLabel, FluentIcon, PushButton, SearchLineEdit, TableWidget

from subwizard.controller import AppController
from subwizard.i18n import t
from subwizard.pipeline.history import HistoryEntry, to_srt, to_text
from subwizard.ui.metrics_view import DASH
from subwizard.ui.pages.base import BasePage

COLUMNS = ("time", "source", "translation", "engine")
TABLE_MIN_HEIGHT = 420
REFRESH_DEBOUNCE_MS = 150  # many lines can finish at once; refresh the table once
BAD_MARK = "⚠ "


class HistoryPage(BasePage):
    key = "history"

    def __init__(self, controller: AppController, parent: QWidget | None = None):
        super().__init__(controller, parent)
        self._shown: list[HistoryEntry] = []

        self._search = SearchLineEdit()
        self._search.textChanged.connect(self.refresh)
        self._copy = PushButton(FluentIcon.COPY, "")
        self._copy.clicked.connect(self.copy_selected)
        self._retranslate = PushButton(FluentIcon.SYNC, "")
        self._retranslate.clicked.connect(self.retranslate_selected)
        self._bad = PushButton(FluentIcon.CANCEL, "")
        self._bad.clicked.connect(self.mark_selected_bad)
        self._export_txt = PushButton(FluentIcon.SAVE, "")
        self._export_txt.clicked.connect(lambda: self._export("txt"))
        self._export_srt = PushButton(FluentIcon.SAVE, "")
        self._export_srt.clicked.connect(lambda: self._export("srt"))
        bar = QHBoxLayout()
        bar.addWidget(self._search, 1)
        for button in (self._copy, self._retranslate, self._bad, self._export_txt,
                       self._export_srt):  # fmt: skip
            bar.addWidget(button)
        self.body.addLayout(bar)

        self.table = TableWidget()
        self.table.setColumnCount(len(COLUMNS))
        self.table.setMinimumHeight(TABLE_MIN_HEIGHT)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setWordWrap(True)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.body.addWidget(self.table)
        self._status = CaptionLabel()
        self.body.addWidget(self._status)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(REFRESH_DEBOUNCE_MS)
        self._timer.timeout.connect(self.refresh)
        controller.translated.connect(self._timer.start)
        controller.subtitleChanged.connect(self._timer.start)
        controller.historyChanged.connect(self._timer.start)
        self.retranslate()

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        entries = self.controller.history.search(self._search.text())
        selected = {e.line_id for e in self._selected()}
        self._shown = entries
        self.table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            engine = e.engine + (f" {t('subtitle.fallback')}" if e.fallback else "")
            cells = (e.clock, e.source, (BAD_MARK if e.bad else "") + (e.translation or DASH),
                     engine or DASH)  # fmt: skip
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, e.line_id)
                self.table.setItem(row, col, item)
            if e.line_id in selected:
                self.table.selectRow(row)
        self.table.resizeRowsToContents()
        self._status.setText(t("history.count", n=len(entries),
                               total=len(self.controller.history.entries())))  # fmt: skip

    def _selected(self) -> list[HistoryEntry]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self._shown[r] for r in rows if r < len(self._shown)]

    def copy_selected(self) -> None:
        entries = self._selected() or self._shown
        QGuiApplication.clipboard().setText(to_text(entries))

    def retranslate_selected(self) -> None:
        for entry in self._selected():
            self.controller.retranslate_entry(entry.line_id)

    def mark_selected_bad(self) -> None:
        for entry in self._selected():
            self.controller.mark_bad(entry.line_id, not entry.bad)

    def export(self, path: Path, kind: str) -> None:
        entries = self._shown
        text = to_srt(entries) if kind == "srt" else to_text(entries)
        path.write_text(text, encoding="utf-8")

    def _export(self, kind: str) -> None:
        name, _ = QFileDialog.getSaveFileName(self, t(f"history.export.{kind}"),
                                              f"subwizard.{kind}", f"*.{kind}")  # fmt: skip
        if name:
            self.export(Path(name), kind)

    def retranslate(self) -> None:
        super().retranslate()
        self._search.setPlaceholderText(t("history.search"))
        self._copy.setText(t("history.copy"))
        self._retranslate.setText(t("history.retranslate"))
        self._bad.setText(t("history.bad"))
        self._export_txt.setText(t("history.export.txt"))
        self._export_srt.setText(t("history.export.srt"))
        self.table.setHorizontalHeaderLabels([t(f"history.col.{c}") for c in COLUMNS])
        self.refresh()
