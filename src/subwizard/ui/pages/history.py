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
HEADER_SAMPLE_ROWS = 50  # time/engine column width from a sample, not every row


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
        header.setResizeContentsPrecision(HEADER_SAMPLE_ROWS)
        self.body.addWidget(self.table)
        self._status = CaptionLabel()
        self.body.addWidget(self._status)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(REFRESH_DEBOUNCE_MS)
        self._timer.timeout.connect(self.refresh)
        self._rows: list[tuple] = []  # what the table shows now: (line_id, *cells)
        self._dirty = False
        controller.translated.connect(self._schedule)
        controller.subtitleChanged.connect(self._schedule)
        controller.historyChanged.connect(self._schedule)
        self.retranslate()

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        if self._dirty:
            self.refresh()

    def _schedule(self) -> None:
        # Found on Windows: rebuilding hundreds of rows on every subtitle froze the UI,
        # also while another page was open. Hidden: only remember to refresh.
        if self.isVisible():
            self._timer.start()
        else:
            self._dirty = True

    @staticmethod
    def _cells(e: HistoryEntry) -> tuple:
        engine = e.engine + (f" {t('subtitle.fallback')}" if e.fallback else "")
        return (e.line_id, e.clock, e.source,
                (BAD_MARK if e.bad else "") + (e.translation or DASH), engine or DASH)  # fmt: skip

    def refresh(self) -> None:
        self._dirty = False
        entries = self.controller.history.search(self._search.text())
        selected = {e.line_id for e in self._selected()}
        self._shown = entries
        rows = [self._cells(e) for e in entries]
        old = self._rows
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(rows))
            for row, cells in enumerate(rows):
                if row < len(old) and old[row] == cells:
                    continue  # only new or changed rows are rebuilt and measured
                for col, text in enumerate(cells[1:]):
                    item = QTableWidgetItem(text)
                    item.setData(Qt.ItemDataRole.UserRole, cells[0])
                    self.table.setItem(row, col, item)
                self.table.resizeRowToContents(row)
                if cells[0] in selected:
                    self.table.selectRow(row)
        finally:
            self.table.setUpdatesEnabled(True)
        self._rows = rows
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
