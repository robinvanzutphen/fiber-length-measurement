"""Results tab: all analysed measurements in the current folder, with selection statistics."""

from __future__ import annotations

import csv

import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout,
                             QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.storage import RESULT_COLUMNS
from .widgets import Card, ValueDisplay, button, muted

COLUMNS = [("timestamp", "Time"), ("name", "Name"), ("run", "Run"), ("delay_ps", "Delay (ps)"),
           ("ci95_ps", "±CI (ps)"), ("length_m", "Length (m)"), ("r2", "R²"),
           ("rms_mcyc", "RMS (mcyc)"), ("steps_used", "Steps"), ("source", "Source"),
           ("calibration", "Calibration"), ("file", "File"), ("warnings", "Warnings")]
NUMERIC = {"delay_ps", "ci95_ps", "length_m", "r2", "rms_mcyc", "steps_used", "run"}
FORMATS = {"delay_ps": "{:.2f}", "ci95_ps": "{:.3f}", "length_m": "{:.4f}", "r2": "{:.8f}",
           "rms_mcyc": "{:.3f}", "steps_used": "{:.0f}", "run": "{:.0f}"}


class _NumItem(QTableWidgetItem):
    def __lt__(self, other):
        a, b = self.data(Qt.UserRole), other.data(Qt.UserRole)
        if isinstance(a, float) and isinstance(b, float):
            return a < b
        return super().__lt__(other)


class ResultsTab(QWidget):
    reanalyse = pyqtSignal(list)            # raw file paths
    open_folder = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[dict] = []
        self._folder = None
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        stats = Card("Selection statistics")
        row = QHBoxLayout()
        self.s_n = ValueDisplay("Measurements", "", big=False, template="00000")
        self.s_delay = ValueDisplay("Mean delay", "ps", big=False, template="0 000 000.00")
        self.s_dsd = ValueDisplay("Std. deviation", "ps", big=False, template="0 000.00")
        self.s_len = ValueDisplay("Mean length", "m", big=False, template="0 000.0000")
        self.s_lsd = ValueDisplay("Std. deviation", "mm", big=False, template="0 000.000")
        for w in (self.s_n, self.s_delay, self.s_dsd, self.s_len, self.s_lsd):
            row.addWidget(w)
            row.addSpacing(24)
        row.addStretch()
        stats.body.addLayout(row)
        stats.body.addWidget(muted("Select rows (Ctrl/Shift-click) to compute statistics; "
                                   "no selection = all visible rows."))
        root.addWidget(stats)

        table_card = Card("Results in this folder")
        bar = QHBoxLayout()
        bar.addWidget(muted("Name:", wrap=False))
        self.filter = QComboBox()
        self.filter.setMinimumWidth(200)
        bar.addWidget(self.filter)
        bar.addStretch()
        self.btn_reanalyse = button("Re-analyse selected",
                                    tooltip="Analyse the raw files again with the active calibration.")
        self.btn_export = button("Export…")
        self.btn_folder = button("Open folder")
        self.btn_refresh = button("Refresh")
        for b in (self.btn_reanalyse, self.btn_export, self.btn_folder, self.btn_refresh):
            bar.addWidget(b)
        table_card.body.addLayout(bar)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([c[1] for c in COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setStretchLastSection(True)
        table_card.body.addWidget(self.table)
        root.addWidget(table_card, 1)

        self.filter.currentIndexChanged.connect(self._populate)
        self.table.itemSelectionChanged.connect(self._update_stats)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_folder.clicked.connect(self.open_folder)
        self.btn_export.clicked.connect(self._export)
        self.btn_reanalyse.clicked.connect(self._reanalyse)

    def set_folder(self, folder) -> None:
        self._folder = folder
        self.refresh()

    def refresh(self) -> None:
        self._rows = self._folder.read_results() if self._folder else []
        current = self.filter.currentText()
        names = sorted({r.get("name", "") for r in self._rows})
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.addItem("All")
        self.filter.addItems(names)
        if current in names:
            self.filter.setCurrentText(current)
        self.filter.blockSignals(False)
        self._populate()

    def _visible_rows(self) -> list[dict]:
        name = self.filter.currentText()
        return [r for r in self._rows if name in ("", "All") or r.get("name") == name]

    def _populate(self) -> None:
        rows = self._visible_rows()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, (key, _) in enumerate(COLUMNS):
                raw = r.get(key, "")
                text = raw
                item = _NumItem()
                if key in NUMERIC:
                    try:
                        v = float(raw)
                        text = FORMATS[key].format(v)
                        item.setData(Qt.UserRole, v)
                        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    except (TypeError, ValueError):
                        pass
                item.setText(str(text))
                if j == 0:
                    item.setData(Qt.UserRole + 1, r)
                self.table.setItem(i, j, item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(0, Qt.DescendingOrder)
        self._update_stats()

    def _selected_rows(self) -> list[dict]:
        rows = {idx.row() for idx in self.table.selectionModel().selectedRows()}
        if not rows:
            rows = range(self.table.rowCount())
        return [self.table.item(i, 0).data(Qt.UserRole + 1) for i in sorted(rows)]

    def _update_stats(self) -> None:
        rows = self._selected_rows()
        d = np.array([_f(r.get("delay_ps")) for r in rows])
        l = np.array([_f(r.get("length_m")) for r in rows])
        d, l = d[np.isfinite(d)], l[np.isfinite(l)]
        self.s_n.set(str(len(d)))
        self.s_delay.set(f"{d.mean():.2f}" if len(d) else "—")
        self.s_dsd.set(f"{d.std(ddof=1):.2f}" if len(d) > 1 else "—")
        self.s_len.set(f"{l.mean():.4f}" if len(l) else "—")
        self.s_lsd.set(f"{l.std(ddof=1) * 1e3:.3f}" if len(l) > 1 else "—")

    def _export(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export results", "results_export.csv",
                                              "CSV files (*.csv)")
        if path:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=RESULT_COLUMNS, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)

    def _reanalyse(self) -> None:
        if not self._folder:
            return
        paths = []
        for r in self._selected_rows() if self.table.selectionModel().selectedRows() else []:
            p = self._folder.measurements / r.get("file", "")
            if p.is_file():
                paths.append(str(p))
        self.reanalyse.emit(paths)


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")
