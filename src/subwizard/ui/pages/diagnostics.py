"""Diagnostics: every live metric as numbers + "copy report" (SPEC 3.2.1).

Graphs of the last 60 s and CSV export arrive in M5.
"""

from __future__ import annotations

import platform as py_platform
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFileDialog, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, CardWidget, FluentIcon, PushButton, StrongBodyLabel

from subwizard import APP_NAME, __version__
from subwizard.controller import AppController
from subwizard.core.metrics import Snapshot
from subwizard.core.metrics_log import MetricsRecorder
from subwizard.core.support import make_support_bundle
from subwizard.i18n import t
from subwizard.ui.metrics_view import GRADE_COLORS, Row, metric_rows, report_text
from subwizard.ui.pages.base import BasePage
from subwizard.utils.paths import default_log_dir

LABEL_COLUMN_WIDTH = 260


class _Section(CardWidget):
    """One group of metrics: a title and a label/value grid."""

    def __init__(self, rows: list[Row]):
        super().__init__()
        self._title = StrongBodyLabel()
        self._section = rows[0].section
        self._labels: list[tuple[str, BodyLabel]] = []
        self._values: list[BodyLabel] = []

        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)
        grid.setColumnMinimumWidth(0, LABEL_COLUMN_WIDTH)
        grid.setColumnStretch(1, 1)
        for i, row in enumerate(rows):
            label, value = BodyLabel(), BodyLabel()
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(label, i, 0)
            grid.addWidget(value, i, 1)
            self._labels.append((row.label, label))
            self._values.append(value)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(self._title)
        layout.addLayout(grid)

    def retranslate(self) -> None:
        self._title.setText(t(self._section))
        for key, label in self._labels:
            label.setText(t(key))

    def update_values(self, rows: list[Row]) -> None:
        for value, row in zip(self._values, rows, strict=True):
            value.setText(row.value)
            color = GRADE_COLORS[row.grade]
            value.setStyleSheet(f"color:{color}" if color else "")


class DiagnosticsPage(BasePage):
    key = "diagnostics"

    def __init__(self, controller: AppController, parent: QWidget | None = None):
        super().__init__(controller, parent)
        self._copy = PushButton()
        self._copy.setIcon(FluentIcon.COPY)
        self._copy.clicked.connect(self.copy_report)
        self._legend = CaptionLabel()
        top = QHBoxLayout()
        top.addWidget(self._legend, 1)
        top.addWidget(self._copy)
        self.recorder = MetricsRecorder()
        self._csv = PushButton()
        self._csv.setIcon(FluentIcon.DOCUMENT)
        self._csv.clicked.connect(self._save_csv)
        top.addWidget(self._csv)
        self._bundle = PushButton()
        self._bundle.setIcon(FluentIcon.ZIP_FOLDER)
        self._bundle.clicked.connect(self._save_bundle)
        top.addWidget(self._bundle)
        self.body.addLayout(top)

        self._sections: list[_Section] = []
        for rows in self._grouped(Snapshot()):
            section = _Section(rows)
            self._sections.append(section)
            self.body.addWidget(section)

        controller.metricsUpdated.connect(self._on_metrics)
        self.retranslate()
        self._on_metrics(Snapshot(), force=True)

    def _grouped(self, snap: Snapshot) -> list[list[Row]]:
        groups: dict[str, list[Row]] = {}
        for row in metric_rows(snap, self.controller.config):
            groups.setdefault(row.section, []).append(row)
        return list(groups.values())

    def _on_metrics(self, snap: Snapshot, force: bool = False) -> None:
        if not force:
            self.recorder.record(snap)  # kept while the page is hidden too
        if not (force or self.isVisible()):
            return
        for section, rows in zip(self._sections, self._grouped(snap), strict=True):
            section.update_values(rows)

    def report(self) -> str:
        header = [
            f"{APP_NAME} {__version__}",
            f"{py_platform.platform()} · Python {py_platform.python_version()}",
            f"source: {self.controller.config.source.kind} · state: {self.controller.source_state}"
            f" · audio: {self.controller.audio_state}",
        ]
        return report_text(self.controller.snapshot(), self.controller.config, header)

    def copy_report(self) -> None:
        QGuiApplication.clipboard().setText(self.report())
        self._copy.setText(t("diag.copied"))

    def _save_csv(self) -> None:
        name = f"subwizard-metrics-{time.strftime('%Y%m%d-%H%M')}.csv"
        path, _ = QFileDialog.getSaveFileName(self, t("diag.csv"), str(Path.home() / name),
                                              "*.csv")  # fmt: skip
        if path:
            Path(path).write_text(self.recorder.to_csv(), encoding="utf-8")

    def save_bundle(self, dest: Path) -> Path:
        cfg = self.controller.config
        return make_support_bundle(dest, cfg, cfg.logging.dir or default_log_dir(), self.report(),
                                   self.recorder.to_csv())  # fmt: skip

    def _save_bundle(self) -> None:
        name = f"subwizard-support-{time.strftime('%Y%m%d-%H%M')}.zip"
        path, _ = QFileDialog.getSaveFileName(self, t("diag.bundle"), str(Path.home() / name),
                                              "*.zip")  # fmt: skip
        if path:
            self.save_bundle(Path(path))
            self._bundle.setText(t("diag.bundle.saved"))

    def retranslate(self) -> None:
        super().retranslate()
        self._copy.setText(t("diag.copy_report"))
        self._bundle.setText(t("diag.bundle"))
        self._csv.setText(t("diag.csv"))
        self._csv.setToolTip(t("diag.csv.tip"))
        self._bundle.setToolTip(t("diag.bundle.tip"))
        self._legend.setText(t("diag.legend"))
        for section in self._sections:
            section.retranslate()
