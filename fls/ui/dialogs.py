"""Dialogs: cable reconnection prompt, settings editor, user guide."""

from __future__ import annotations

from dataclasses import fields

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                             QFormLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox, QTabWidget,
                             QTextBrowser, QVBoxLayout, QWidget)

from .. import PAPER_DOI, resource_path
from ..settings import Settings
from .widgets import button, muted


class CableDialog(QDialog):
    """Modal pause before streaming. Accept = continue, reject = abort the run."""

    def __init__(self, run: int, total: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Reconnect BNC cables")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        title = QLabel("Reconnect the BNC cables")
        title.setObjectName("CardTitle")
        lay.addWidget(title)
        series = f" (run {run} of {total})" if total > 1 else (f" (run {run})" if total == 0 else "")
        lay.addWidget(muted(
            f"The phasemeter is locked at the sweep start frequency{series}.\n\n"
            "Reconnect the BNC cables as required, then press Continue to start the sweep."))
        row = QHBoxLayout()
        row.addStretch()
        abort = button("Abort run", "danger")
        go = button("Continue", "primary")
        go.setDefault(True)
        abort.clicked.connect(self.reject)
        go.clicked.connect(self.accept)
        row.addWidget(abort)
        row.addWidget(go)
        lay.addLayout(row)
        self.setMinimumWidth(420)


SECTION_TITLES = {"device": "Instruments", "sweep": "Sweep", "phasemeter": "Phasemeter",
                  "analysis": "Analysis", "workflow": "Workflow"}


class SettingsDialog(QDialog):
    """Generated from the Settings dataclasses (labels/help/choices in field metadata)."""

    def __init__(self, settings: Settings, parent=None, locked: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.settings = settings
        self._editors: list[tuple[object, str, QWidget]] = []
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        for section in Settings.SECTIONS:
            obj = getattr(settings, section)
            page = QWidget()
            form = QFormLayout(page)
            form.setLabelAlignment(Qt.AlignRight)
            form.setContentsMargins(16, 16, 16, 16)
            for f in fields(obj):
                meta = f.metadata
                if meta.get("advanced") and section == "workflow":
                    continue
                editor = self._editor(getattr(obj, f.name), meta)
                if meta.get("help"):
                    editor.setToolTip(meta["help"])
                label = meta.get("label", f.name) + (" *" if meta.get("advanced") else "")
                form.addRow(label + ":", editor)
                self._editors.append((obj, f.name, editor))
            if section == "sweep":
                form.addRow(muted("The sweep is identical to V1. Change only if you know why."))
            if section == "device":
                find = button("Find instruments…",
                              tooltip="Search USB/network and fill in the addresses.")
                find.clicked.connect(self._find)
                form.addRow("", find)
            tabs.addTab(page, SECTION_TITLES[section])
        lay.addWidget(tabs)
        lay.addWidget(muted("* advanced. Hover a field for an explanation. Changes apply to the "
                            "next run; instrument changes require reconnecting."))
        if locked:
            lay.addWidget(muted("An acquisition is running - settings are read-only."))
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        box.button(QDialogButtonBox.Save).setEnabled(not locked)
        lay.addWidget(box)
        self.resize(560, 480)

    def _find(self) -> None:
        from .system_dialogs import FindInstrumentsDialog
        if FindInstrumentsDialog(self.settings, self).exec_():
            for obj, name, w in self._editors:
                if obj is self.settings.device and isinstance(w, QLineEdit):
                    w.setText(str(getattr(obj, name)))

    @staticmethod
    def _editor(value, meta) -> QWidget:
        if meta.get("choices"):
            w = QComboBox()
            w.addItems(list(meta["choices"]))
            w.setCurrentText(str(value))
            return w
        if isinstance(value, bool):
            w = QCheckBox()
            w.setChecked(value)
            return w
        if isinstance(value, int):
            w = QSpinBox()
            w.setRange(0, 1_000_000)
            w.setValue(value)
            return w
        if isinstance(value, float):
            w = QDoubleSpinBox()
            w.setDecimals(6 if abs(value) < 10 else 1)
            w.setRange(-1e12, 1e12)
            w.setValue(value)
            w.setSuffix(f" {meta['unit']}" if meta.get("unit") else "")
            return w
        w = QLineEdit(str(value))
        w.setMinimumWidth(320)
        return w

    def _save(self) -> None:
        for obj, name, w in self._editors:
            if isinstance(w, QComboBox):
                v = w.currentText()
            elif isinstance(w, QCheckBox):
                v = w.isChecked()
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                v = w.value()
            else:
                v = w.text().strip()
            setattr(obj, name, type(getattr(obj, name))(v))
        self.settings.save()
        self.accept()


class HelpDialog(QDialog):
    """Shows docs/USER_GUIDE.md (the same guide as on GitHub)."""

    def __init__(self, parent=None, anchor: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("User guide")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        lay = QVBoxLayout(self)
        text = QTextBrowser()
        text.setOpenExternalLinks(True)
        guide = resource_path("docs/USER_GUIDE.md")
        try:
            text.setSearchPaths([str(guide.parent)])
            text.setMarkdown(guide.read_text(encoding="utf-8"))
        except OSError:
            text.setPlainText(f"User guide not found ({guide}). See {PAPER_DOI} and the "
                              f"README of the software package.")
        if anchor:
            text.scrollToAnchor(anchor)
        lay.addWidget(text)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.resize(900, 760)
