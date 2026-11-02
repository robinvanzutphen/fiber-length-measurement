"""Phasemeter tab: live diagnostics of the PLL with all front-end / loop settings.

Settings changes are applied to the Moku immediately (debounced). They do not change the
measurement settings unless "Use for measurements" is pressed; any change means the
cable-reconnection prompt appears again before the next measurement.
"""

from __future__ import annotations

import time
from collections import deque

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout,
                             QHBoxLayout, QLabel, QVBoxLayout, QWidget)

from . import theme
from .widgets import Card, StatusPill, text_width, button, muted, plot, side_column

BANDWIDTHS = ["1Hz", "10Hz", "100Hz", "1kHz", "10kHz", "100kHz"]
WRAPS = ["Off", "1pi", "2pi", "4pi"]
LOCK_MIN_AMPLITUDE_V = 1e-3        # below this a channel is considered to have no signal
HISTORY = [("30 s", 30), ("2 min", 120), ("5 min", 300)]


class PLLBox(QWidget):
    changed = pyqtSignal()

    def __init__(self, ch: int, color: str) -> None:
        super().__init__()
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setVerticalSpacing(6)
        self.enabled = QCheckBox(f"Input {ch}")
        self.enabled.setChecked(True)
        self.enabled.setStyleSheet(f"QCheckBox {{ color: {color}; font-weight: 600; }}")
        self.coupling = QComboBox()
        self.coupling.addItems(["DC", "AC"])
        self.impedance = QComboBox()
        self.impedance.addItems(["50Ohm", "1MOhm"])
        self.range = QComboBox()
        self.range.addItems(["1Vpp", "10Vpp"])
        self.auto = QCheckBox("Auto-acquire")
        self.freq = QDoubleSpinBox()
        self.freq.setRange(0.001, 200.0)
        self.freq.setDecimals(6)
        self.freq.setSuffix(" MHz")
        self.freq.setToolTip("Initial (lock) frequency of the PLL.")
        self.bandwidth = QComboBox()
        self.bandwidth.addItems(BANDWIDTHS)
        self.bandwidth.setToolTip("PLL tracking bandwidth. Should not exceed 1/10 of the frequency.")
        lay.addWidget(self.enabled, 0, 0, 1, 3)
        lay.addWidget(self.coupling, 1, 0)
        lay.addWidget(self.impedance, 1, 1)
        lay.addWidget(self.range, 1, 2)
        lay.addWidget(self.freq, 2, 0, 1, 2)
        lay.addWidget(self.bandwidth, 2, 2)
        lay.addWidget(self.auto, 3, 0, 1, 3)
        for w in (self.coupling, self.impedance, self.range, self.bandwidth):
            w.currentIndexChanged.connect(self.changed)
        for w in (self.enabled, self.auto):
            w.toggled.connect(self.changed)
        self.freq.editingFinished.connect(self.changed)
        self.auto.toggled.connect(lambda on: self.freq.setEnabled(not on and self.isEnabled()))

    def get(self) -> dict:
        return dict(enabled=self.enabled.isChecked(), coupling=self.coupling.currentText(),
                    impedance=self.impedance.currentText(), range=self.range.currentText(),
                    auto_acquire=self.auto.isChecked(), frequency=self.freq.value() * 1e6,
                    bandwidth=self.bandwidth.currentText())

    def set(self, c: dict) -> None:
        widgets = (self.enabled, self.coupling, self.impedance, self.range, self.auto,
                   self.freq, self.bandwidth)
        for w in widgets:
            w.blockSignals(True)
        self.enabled.setChecked(c["enabled"])
        self.coupling.setCurrentText(c["coupling"])
        self.impedance.setCurrentText(c["impedance"])
        self.range.setCurrentText(c["range"])
        self.auto.setChecked(c["auto_acquire"])
        self.freq.setValue(c["frequency"] / 1e6)
        self.bandwidth.setCurrentText(c["bandwidth"])
        for w in widgets:
            w.blockSignals(False)


def _cell(template: str, color: str | None = None) -> QLabel:
    lab = QLabel("—")
    lab.setObjectName("MidValue")
    lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    if color:
        lab.setStyleSheet(f"color:{color}")
    lab.setFixedWidth(text_width(lab, template) + 8)
    return lab


class PhasemeterTab(QWidget):
    apply = pyqtSignal(dict)
    read = pyqtSignal()
    reacquire = pyqtSignal()
    wavegen_output = pyqtSignal(bool)
    trigger = pyqtSignal()
    use_for_measurements = pyqtSignal(dict)

    def __init__(self, measurement_cfg: dict) -> None:
        super().__init__()
        self._meas_cfg = measurement_cfg
        self.live = False
        self._pending = False
        self._available = True
        self._t0 = time.monotonic()
        self._last_request = 0.0
        self._sweep_end: float | None = None
        self._sweep_dur = 0.0
        self._hist = {k: deque() for k in ("t", "f1", "f2", "dp")}
        self._apply_timer = QTimer(self, singleShot=True, interval=400)
        self._apply_timer.timeout.connect(self._emit_apply)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)

        # ---------------------------------------------------------------- center
        center = QVBoxLayout()
        center.setSpacing(12)
        rc = Card("Live readout")
        self.lock_pill = StatusPill("Stopped", longest="Not locked: check signal")
        rc.header.addWidget(self.lock_pill)
        self.btn_live = button("Start live", "primary")
        self.btn_clear = button("Clear plots")
        rc.header.addSpacing(12)
        rc.header.addWidget(self.btn_clear)
        rc.header.addWidget(self.btn_live)
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(6)
        for j, head in enumerate(["", "Frequency", "Phase", "Amplitude", "Status"]):
            h = muted(head, wrap=False)
            h.setAlignment(Qt.AlignRight if 0 < j < 4 else Qt.AlignLeft)
            grid.addWidget(h, 0, j)
        self.cells = {}
        rows = [("ch1", "Input 1", theme.CH1), ("ch2", "Input 2", theme.CH2),
                ("diff", "In1 − In2", theme.TEXT)]
        for i, (key, name, color) in enumerate(rows, start=1):
            lab = QLabel(name)
            lab.setStyleSheet(f"color:{color}; font-weight:600;")
            grid.addWidget(lab, i, 0)
            f = _cell("-000 000.0 Hz" if key == "diff" else "000.000000 MHz", color=color)
            p = _cell("-00 000 000.000000 cyc", color=color)
            a = _cell("Δφ/f -0000.000 ns" if key == "diff" else "0000.00 mV", color=color)
            s = StatusPill("—", longest="Frequency mismatch")
            grid.addWidget(f, i, 1)
            grid.addWidget(p, i, 2)
            grid.addWidget(a, i, 3)
            grid.addWidget(s, i, 4)
            self.cells[key] = (f, p, a, s)
        grid.setColumnStretch(5, 1)
        rc.body.addLayout(grid)
        center.addWidget(rc)

        pc = Card("History")
        hist_row = QHBoxLayout()
        hist_row.addWidget(muted("Show last", wrap=False))
        self.history = QComboBox()
        self.history.addItems([h[0] for h in HISTORY])
        self.history.setCurrentIndex(1)
        hist_row.addWidget(self.history)
        hist_row.addStretch()
        pc.header.addLayout(hist_row)
        self.p_freq = plot("Time", "Frequency", "s", "Hz")
        self.p_dphi = plot("Time", "Phase difference In1 − In2 (cycles)", "s", "")
        self.p_dphi.getAxis("left").enableAutoSIPrefix(False)
        self.p_dphi.setXLink(self.p_freq)
        self.c_f1 = self.p_freq.plot(pen=pg.mkPen(theme.CH1, width=1.5), name="Input 1")
        self.c_f2 = self.p_freq.plot(pen=pg.mkPen(theme.CH2, width=1.5, style=Qt.DashLine),
                                     name="Input 2")
        self.c_dp = self.p_dphi.plot(pen=pg.mkPen(theme.TEXT, width=1.5))
        pc.body.addWidget(self.p_freq, 1)
        pc.body.addWidget(self.p_dphi, 1)
        center.addWidget(pc, 1)
        root.addLayout(center, 1)

        # ---------------------------------------------------------------- side
        side = QVBoxLayout()
        side.setSpacing(12)

        gen = Card("Waveform generator")
        self.gen_pill = StatusPill("Output off", longest="Sweeping 000 / 000 s")
        gen.header.addWidget(self.gen_pill)
        row = QHBoxLayout()
        self.btn_arm = button("Arm", tooltip="Configure the sweep and switch the output on. "
                                             "The generator idles at the start frequency.")
        self.btn_trigger = button("Trigger sweep", "primary")
        self.btn_off = button("Output off")
        row.addWidget(self.btn_arm)
        row.addWidget(self.btn_trigger, 1)
        row.addWidget(self.btn_off)
        gen.body.addLayout(row)
        side.addWidget(gen)

        loop = Card("Phase-locked loops")
        self.btn_reacquire = button("Re-acquire both channels", "primary")
        loop.body.addWidget(self.btn_reacquire)
        self.link = QCheckBox("Same settings for both inputs")
        self.link.setChecked(True)
        loop.body.addWidget(self.link)
        self.pll1 = PLLBox(1, theme.CH1)
        self.pll2 = PLLBox(2, theme.CH2)
        loop.body.addWidget(self.pll1)
        loop.body.addWidget(self.pll2)
        adv = QFormLayout()
        self.wrap = QComboBox()
        self.wrap.addItems(WRAPS)
        self.reset = QComboBox()
        self.reset.addItems(WRAPS)
        adv.addRow("Phase wrap:", self.wrap)
        adv.addRow("Auto-reset:", self.reset)
        loop.body.addLayout(adv)
        self.applied = muted("", wrap=True)
        loop.body.addWidget(self.applied)
        side.addWidget(loop)

        ms = Card("Measurement settings")
        self.diff_note = QLabel("")
        self.diff_note.setObjectName("Warning")
        self.diff_note.setWordWrap(True)
        row = QHBoxLayout()
        self.btn_load = button("Load", tooltip="Load the settings used for measurements.")
        self.btn_use = button("Use for measurements",
                              tooltip="Store these settings (Input 1) as the measurement settings.")
        row.addWidget(self.btn_load)
        row.addWidget(self.btn_use, 1)
        ms.body.addWidget(self.diff_note)
        ms.body.addLayout(row)
        side.addWidget(ms)
        side.addStretch()
        side.setContentsMargins(0, 0, 6, 0)
        root.addWidget(side_column(side, 360))

        # ---------------------------------------------------------------- wiring
        self.btn_live.clicked.connect(self.toggle_live)
        self.btn_clear.clicked.connect(self.clear)
        self.btn_reacquire.clicked.connect(self.reacquire)
        self.btn_arm.clicked.connect(lambda: self.wavegen_output.emit(True))
        self.btn_off.clicked.connect(lambda: self.wavegen_output.emit(False))
        self.btn_trigger.clicked.connect(self.trigger)
        self.btn_load.clicked.connect(lambda: self.load_config(self._meas_cfg, apply=True))
        self.btn_use.clicked.connect(lambda: self.use_for_measurements.emit(self.config()))
        self.pll1.changed.connect(self._changed)
        self.pll2.changed.connect(self._changed)
        self.link.toggled.connect(self._changed)
        self.wrap.currentIndexChanged.connect(self._changed)
        self.reset.currentIndexChanged.connect(self._changed)
        self.history.currentIndexChanged.connect(self._redraw)
        self._sweep_timer = QTimer(self, interval=500)
        self._sweep_timer.timeout.connect(self._update_sweep)

        self.load_config(measurement_cfg, apply=False)

    # ---------------------------------------------------------------- config
    def config(self) -> dict:
        c1 = self.pll1.get()
        c2 = dict(c1) if self.link.isChecked() else self.pll2.get()
        if self.link.isChecked():
            c2["enabled"] = self.pll2.enabled.isChecked()
        return dict(ch1=c1, ch2=c2, phase_wrap=self.wrap.currentText(),
                    auto_reset=self.reset.currentText())

    def load_config(self, cfg: dict, apply: bool) -> None:
        self.pll1.set(cfg["ch1"])
        self.pll2.set(cfg["ch2"])
        for w, v in ((self.wrap, cfg["phase_wrap"]), (self.reset, cfg["auto_reset"])):
            w.blockSignals(True)
            w.setCurrentText(v)
            w.blockSignals(False)
        self._sync_link()
        self._update_diff_note()
        if apply:
            self._emit_apply()

    def set_measurement_config(self, cfg: dict) -> None:
        self._meas_cfg = cfg
        self._update_diff_note()

    def _sync_link(self) -> None:
        linked = self.link.isChecked()
        for w in (self.pll2.coupling, self.pll2.impedance, self.pll2.range, self.pll2.auto,
                  self.pll2.freq, self.pll2.bandwidth):
            w.setEnabled(not linked and self._available)
        if linked:
            c = self.pll1.get()
            c["enabled"] = self.pll2.enabled.isChecked()
            self.pll2.set(c)

    def _changed(self) -> None:
        self._sync_link()
        self._update_diff_note()
        self.applied.setText("Applying …")
        self._apply_timer.start()

    def _emit_apply(self) -> None:
        if self._available:
            self.apply.emit(self.config())
            self.applied.setText("Applied to the Moku. The cable prompt will appear before the "
                                 "next measurement.")

    def _update_diff_note(self) -> None:
        same = self.config() == self._meas_cfg
        self.diff_note.setText("" if same else "These settings differ from the measurement "
                                               "settings. Measurements use the measurement settings.")
        self.diff_note.setVisible(not same)
        self.btn_use.setEnabled(not same)

    # ---------------------------------------------------------------- availability
    def set_available(self, ok: bool, reason: str = "") -> None:
        self._available = ok
        for w in (self.btn_live, self.btn_reacquire, self.btn_load, self.pll1, self.pll2,
                  self.link, self.wrap, self.reset):
            w.setEnabled(ok)
        self._sync_link()
        if not ok:
            self.stop()
            self.lock_pill.set_state("off", reason or "Not available")

    def set_wavegen_state(self, connected: bool, on: bool) -> None:
        ok = connected and self._available
        self.btn_arm.setEnabled(ok and not on)
        self.btn_trigger.setEnabled(ok)
        self.btn_off.setEnabled(ok and on)
        if not on:
            self._sweep_end = None
            self._sweep_timer.stop()
            self.gen_pill.set_state("off", "Output off")
        elif self._sweep_end is None:
            self.gen_pill.set_state("ok", "Armed (start freq.)")

    def on_sweep_started(self, duration_s: float) -> None:
        self._sweep_dur = duration_s
        self._sweep_end = time.monotonic() + duration_s
        self._sweep_timer.start()
        self._update_sweep()

    def _update_sweep(self) -> None:
        if self._sweep_end is None:
            return
        left = self._sweep_end - time.monotonic()
        if left <= 0:
            self._sweep_end = None
            self._sweep_timer.stop()
            self.gen_pill.set_state("ok", "Sweep done (at stop)")
            return
        self.gen_pill.set_state("busy", f"Sweeping {self._sweep_dur - left:3.0f} / "
                                        f"{self._sweep_dur:.0f} s")

    # ---------------------------------------------------------------- live readout
    def toggle_live(self) -> None:
        if self.live:
            self.stop()
            return
        self.live = True
        self.btn_live.setText("Stop live")
        self.btn_live.setProperty("kind", "danger")
        self._repolish(self.btn_live)
        self.lock_pill.set_state("busy", "Starting …")
        self.apply.emit(self.config())
        self._request()

    def stop(self) -> None:
        self.live = False
        self.btn_live.setText("Start live")
        self.btn_live.setProperty("kind", "primary")
        self._repolish(self.btn_live)
        if self._available:
            self.lock_pill.set_state("off", "Stopped")

    def clear(self) -> None:
        for d in self._hist.values():
            d.clear()
        self._t0 = time.monotonic()
        self._redraw()

    def _request(self) -> None:
        if self.live and not self._pending:
            self._pending = True
            self._last_request = time.monotonic()
            self.read.emit()

    def on_reading(self, data) -> None:
        self._pending = False
        if not self.live:
            return
        # ~10 readings per second at most
        wait_ms = max(0, int(100 - (time.monotonic() - self._last_request) * 1000))
        QTimer.singleShot(wait_ms if data else 500, self._request)
        if not data:
            self.lock_pill.set_state("bad", "No data from the Moku")
            return
        self._show(data)

    def _show(self, d: dict) -> None:
        ch = {k: d.get(k) for k in ("ch1", "ch2")}
        locked = {}
        for k in ("ch1", "ch2"):
            f, p, a, s = self.cells[k]
            c = ch[k]
            if not c:
                for lab in (f, p, a):
                    lab.setText("—")
                s.set_state("off", "Disabled")
                locked[k] = None
                continue
            f.setText(f"{c['frequency'] / 1e6:.6f} MHz")
            p.setText(f"{c['phase']:,.6f} cyc".replace(",", " "))
            a.setText(f"{c['amplitude'] * 1e3:.2f} mV")
            locked[k] = c["amplitude"] > LOCK_MIN_AMPLITUDE_V
            s.set_state("ok" if locked[k] else "bad", "Signal" if locked[k] else "No signal")

        f, p, a, s = self.cells["diff"]
        if ch["ch1"] and ch["ch2"]:
            df = ch["ch1"]["frequency"] - ch["ch2"]["frequency"]
            dp = ch["ch1"]["phase"] - ch["ch2"]["phase"]
            fm = 0.5 * (ch["ch1"]["frequency"] + ch["ch2"]["frequency"])
            match = abs(df) < 100
            both = bool(locked["ch1"] and locked["ch2"] and match)
            f.setText(f"{df:+,.1f} Hz".replace(",", " ") if abs(df) < 1e6 else "> 1 MHz")
            p.setText(f"{dp:+,.6f} cyc".replace(",", " ") if abs(dp) < 1e6 else "—")
            a.setText(f"Δφ/f {dp / fm * 1e9:+.3f} ns" if both and fm > 0 else "Δφ/f —")
            s.set_state("ok" if match else "busy", "Same frequency" if match else "Frequency mismatch")
            self.lock_pill.set_state("ok" if both else "bad",
                                     "Both channels locked" if both else "Not locked: check signal")
            t = time.monotonic() - self._t0
            # phase difference is only meaningful while locked: gaps in the plot otherwise
            for key, v in (("t", t), ("f1", ch["ch1"]["frequency"]),
                           ("f2", ch["ch2"]["frequency"]), ("dp", dp if both else np.nan)):
                self._hist[key].append(v)
            while self._hist["t"] and t - self._hist["t"][0] > HISTORY[-1][1]:
                for dq in self._hist.values():
                    dq.popleft()
            self._redraw()
        else:
            for lab in (f, p, a):
                lab.setText("—")
            s.set_state("off", "Needs both inputs")
            self.lock_pill.set_state("busy", "Only one input enabled")

    def _redraw(self) -> None:
        if not self._hist["t"]:
            for c in (self.c_f1, self.c_f2, self.c_dp):
                c.setData([], [])
            return
        t = np.fromiter(self._hist["t"], float)
        keep = t >= t[-1] - HISTORY[self.history.currentIndex()][1]
        self.c_f1.setData(t[keep], np.fromiter(self._hist["f1"], float)[keep])
        self.c_f2.setData(t[keep], np.fromiter(self._hist["f2"], float)[keep])
        self.c_dp.setData(t[keep], np.fromiter(self._hist["dp"], float)[keep], connect="finite")

    @staticmethod
    def _repolish(w) -> None:
        w.style().unpolish(w)
        w.style().polish(w)
