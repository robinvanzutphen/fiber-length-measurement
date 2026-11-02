"""System check and 'Find instruments' dialogs (work runs in background threads)."""

from __future__ import annotations

from PyQt5.QtCore import QThread, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QDialog, QHBoxLayout, QHeaderView,
                             QLabel, QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..core import system_check as sc
from ..settings import APP_DIR, Settings
from . import theme
from .widgets import button, muted

_ICON = {sc.OK: ("✔", theme.OK), sc.WARN: ("▲", theme.WARN), sc.FAIL: ("✖", theme.BAD),
         sc.INFO: ("i", theme.ACCENT)}


class Task(QThread):
    """Run a function in a background thread and emit its result (or exception)."""
    done = pyqtSignal(object)

    def __init__(self, fn, parent=None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self._fn())
        except Exception as exc:            # reported by the receiver
            self.done.emit(exc)


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setWordWrap(True)
    hh = t.horizontalHeader()
    hh.setSectionResizeMode(QHeaderView.ResizeToContents)
    hh.setStretchLastSection(True)
    return t


class SystemCheckDialog(QDialog):
    """Lists the installation/instrument checks with a fix for every problem."""
    find_requested = pyqtSignal()

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.checks: list[sc.Check] = []
        self.info: dict = {}
        self._task: Task | None = None
        self.setWindowTitle("System check")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        title = QLabel("System check")
        title.setObjectName("CardTitle")
        lay.addWidget(title)
        lay.addWidget(muted("Checks the software installation and the instruments (read-only: "
                            "nothing is changed on the instruments). Every problem lists how to "
                            "fix it. See Help → Installation for details."))
        self.table = _table(["", "Check", "Result", "How to fix"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(2, QHeaderView.Stretch)     # result and fix share the width
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.table, 1)
        self.summary = QLabel("")
        self.summary.setObjectName("Stage")
        lay.addWidget(self.summary)
        row = QHBoxLayout()
        self.btn_download = button("Download instrument files",
                                   tooltip="Runs mokucli in a console window.")
        self.btn_find = button("Find instruments…")
        self.btn_copy = button("Copy report")
        self.btn_rerun = button("Run again", "primary")
        close = button("Close")
        for b in (self.btn_download, self.btn_find, self.btn_copy):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(self.btn_rerun)
        row.addWidget(close)
        lay.addLayout(row)
        self.btn_download.clicked.connect(self._download)
        self.btn_find.clicked.connect(self.find_requested)
        self.btn_copy.clicked.connect(self._copy)
        self.btn_rerun.clicked.connect(self.run)
        close.clicked.connect(self.accept)
        self.btn_download.setVisible(False)
        self.resize(980, 520)
        self.run()

    def run(self) -> None:
        if self._task and self._task.isRunning():
            return
        self.summary.setText("Checking …")
        self.btn_rerun.setEnabled(False)
        self._task = Task(lambda: sc.run_checks(self.settings), self)
        self._task.done.connect(self._show)
        self._task.start()

    def _show(self, result) -> None:
        self.btn_rerun.setEnabled(True)
        if isinstance(result, Exception):
            self.summary.setText(f"Check failed: {result}")
            return
        self.checks, self.info = result
        self.table.setRowCount(len(self.checks))
        for i, c in enumerate(self.checks):
            sym, color = _ICON[c.status]
            icon = QTableWidgetItem(sym)
            icon.setForeground(QBrush(QColor(color)))
            icon.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 0, icon)
            self.table.setItem(i, 1, QTableWidgetItem(c.name))
            self.table.setItem(i, 2, QTableWidgetItem(c.detail))
            self.table.setItem(i, 3, QTableWidgetItem(c.fix))
        self.table.resizeRowsToContents()
        n_fail = sum(c.status == sc.FAIL for c in self.checks)
        n_warn = sum(c.status == sc.WARN for c in self.checks)
        self.summary.setText("All checks passed." if not (n_fail or n_warn) else
                             f"{n_fail} problem(s), {n_warn} warning(s) - see 'How to fix'.")
        self.summary.setStyleSheet(f"color:{theme.OK if not n_fail else theme.BAD}")
        missing_files = any(c.name == "Moku instrument files" and c.status == sc.FAIL
                            for c in self.checks)
        self.btn_download.setVisible(missing_files and sc.find_mokucli() is not None)
        try:
            (APP_DIR / "system_check.txt").write_text(sc.report(self.checks), encoding="utf-8")
        except OSError:
            pass

    def _download(self) -> None:
        try:
            sc.launch_instrument_download(self.info)
            QMessageBox.information(self, "Download started",
                                    "The download runs in a separate console window "
                                    "(~130 MB). When it has finished, press 'Run again'.")
        except Exception as exc:
            QMessageBox.warning(self, "Download", f"Could not start mokucli:\n{exc}")

    def _copy(self) -> None:
        QApplication.clipboard().setText(sc.report(self.checks))
        self.summary.setText("Report copied to the clipboard.")


class FindInstrumentsDialog(QDialog):
    """Searches for Mokus (mokucli / mDNS) and USB VISA instruments and stores the selection."""

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._tasks: list[Task] = []
        self.setWindowTitle("Find instruments")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.addWidget(muted("Make sure the Moku and the waveform generator are switched on and "
                            "connected by USB. Select one row in each list and press 'Use selected'."))
        lay.addWidget(QLabel("<b>Moku</b>"))
        self.mokus = _table(["Name", "Serial", "Hardware", "Firmware", "Address"])
        lay.addWidget(self.mokus)
        lay.addWidget(QLabel("<b>Waveform generator (USB / VISA)</b>"))
        self.visa = _table(["VISA resource", "Identity"])
        lay.addWidget(self.visa)
        self.state = muted("")
        lay.addWidget(self.state)
        row = QHBoxLayout()
        self.btn_search = button("Search again")
        self.btn_use = button("Use selected", "primary")
        close = button("Cancel")
        row.addWidget(self.btn_search)
        row.addStretch()
        row.addWidget(self.btn_use)
        row.addWidget(close)
        lay.addLayout(row)
        self.btn_search.clicked.connect(self.search)
        self.btn_use.clicked.connect(self._use)
        close.clicked.connect(self.reject)
        self.resize(820, 480)
        self.search()

    def search(self) -> None:
        self.state.setText("Searching … (about 5 s)")
        self.btn_search.setEnabled(False)
        self._pending = 2
        for fn, slot in ((lambda: sc.discover_mokus(4.0), self._show_mokus),
                         (sc.discover_visa, self._show_visa)):
            t = Task(fn, self)
            t.done.connect(slot)
            t.start()
            self._tasks.append(t)

    def _finished_one(self) -> None:
        self._pending -= 1
        if self._pending == 0:
            self.btn_search.setEnabled(True)
            n_m, n_v = self.mokus.rowCount(), self.visa.rowCount()
            self.state.setText(f"Found {n_m} Moku(s) and {n_v} USB VISA instrument(s)."
                               + ("" if n_m else "  No Moku: is it on, is the Moku app (USB "
                                                 "driver) installed, is mokucli installed?")
                               + ("" if n_v else "  No generator: is it on and is a VISA "
                                                 "library installed?"))

    def _fill(self, table: QTableWidget, rows: list[list[str]], current: str, col: int) -> None:
        table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, v in enumerate(r):
                table.setItem(i, j, QTableWidgetItem(v))
            if r[col] == current or i == 0:
                table.selectRow(i)
        table.resizeColumnsToContents()

    def _show_mokus(self, res) -> None:
        rows = [] if isinstance(res, Exception) else \
            [[m.name, m.serial, m.hardware, m.firmware, m.address] for m in res]
        self._fill(self.mokus, rows, self.settings.device.moku_address, 4)
        self._finished_one()

    def _show_visa(self, res) -> None:
        rows = [] if isinstance(res, Exception) else [[v.resource, v.identity] for v in res]
        self._fill(self.visa, rows, self.settings.device.wavegen_visa, 0)
        self._finished_one()

    def _use(self) -> None:
        changed = []
        m = self.mokus.currentRow()
        if m >= 0 and self.mokus.item(m, 4) and self.mokus.item(m, 4).text():
            self.settings.device.moku_address = self.mokus.item(m, 4).text()
            changed.append(f"Moku: {self.settings.device.moku_address}")
        v = self.visa.currentRow()
        if v >= 0 and self.visa.item(v, 0):
            self.settings.device.wavegen_visa = self.visa.item(v, 0).text()
            changed.append(f"Generator: {self.settings.device.wavegen_visa}")
        if changed:
            self.settings.save()
            QMessageBox.information(self, "Instruments", "Saved:\n" + "\n".join(changed) +
                                    "\n\nReconnect to use them.")
        self.accept()
