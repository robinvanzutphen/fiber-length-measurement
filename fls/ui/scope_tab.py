"""Oscilloscope tab: live view of both inputs with the phase difference between them."""

from __future__ import annotations

import time
from collections import deque

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout,
                             QHBoxLayout, QSpinBox, QVBoxLayout, QWidget)

from ..core.scope_analysis import analyse_frame
from . import theme
from .widgets import Card, StatusPill, ValueDisplay, button, muted, plot, side_column

# (span in seconds, label)
SPANS = [(0.5e-6, "0.5 µs  (50 ns/div)"), (1e-6, "1 µs  (100 ns/div)"),
         (2e-6, "2 µs  (200 ns/div)"), (3e-6, "3 µs  (300 ns/div)"),
         (5e-6, "5 µs  (500 ns/div)"), (10e-6, "10 µs  (1 µs/div)"),
         (20e-6, "20 µs  (2 µs/div)"), (50e-6, "50 µs  (5 µs/div)"),
         (100e-6, "100 µs  (10 µs/div)"), (1e-3, "1 ms  (100 µs/div)")]
DEFAULT_SPAN = 4          # 5 µs: five periods of the 1 MHz sweep start frequency
POINTS = ["1024", "2048", "4096", "8192", "16384"]
DEFAULT_POINTS = "2048"   # measured: ~7 frames/s; 16384 points drops to ~1 frame/s


class ChannelBox(QWidget):
    changed = pyqtSignal()

    def __init__(self, ch: int, color: str) -> None:
        super().__init__()
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.enabled = QCheckBox(f"Input {ch}")
        self.enabled.setChecked(True)
        self.enabled.setStyleSheet(f"QCheckBox {{ color: {color}; font-weight: 600; }}")
        self.range = QComboBox()
        self.range.addItems(["1Vpp", "10Vpp"])
        self.coupling = QComboBox()
        self.coupling.addItems(["DC", "AC"])
        self.impedance = QComboBox()
        self.impedance.addItems(["50Ohm", "1MOhm"])
        lay.addWidget(self.enabled, 0, 0, 1, 3)
        lay.addWidget(self.range, 1, 0)
        lay.addWidget(self.coupling, 1, 1)
        lay.addWidget(self.impedance, 1, 2)
        for w in (self.range, self.coupling, self.impedance):
            w.currentIndexChanged.connect(self.changed)
        self.enabled.toggled.connect(self.changed)

    def cfg(self) -> dict:
        return dict(enabled=self.enabled.isChecked(), range=self.range.currentText(),
                    coupling=self.coupling.currentText(), impedance=self.impedance.currentText())


class ScopeTab(QWidget):
    configure = pyqtSignal(dict)
    acquire = pyqtSignal()
    wavegen_output = pyqtSignal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.running = False
        self._single = False
        self._pending = False
        self._enabled = True
        self._dphi = deque(maxlen=20)
        self._frame_times = deque(maxlen=10)
        self._last_frame = None

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)

        # ---- display + measurements
        center = QVBoxLayout()
        center.setSpacing(12)
        pc = Card()
        self.plot = plot("Time", "Voltage", "s", "V")
        self.curve1 = self.plot.plot(pen=pg.mkPen(theme.CH1, width=1.5), name="Input 1")
        self.curve2 = self.plot.plot(pen=pg.mkPen(theme.CH2, width=1.5), name="Input 2")
        for c in (self.curve1, self.curve2):      # fast drawing of long frames
            c.opts["antialias"] = False
        self.trig_line = pg.InfiniteLine(angle=0, movable=True,
                                         pen=pg.mkPen(theme.MUTED, width=1, style=Qt.DashLine))
        self.plot.addItem(self.trig_line)
        self.trig_line.sigPositionChangeFinished.connect(
            lambda: self.trig_level.setValue(self.trig_line.value()))
        self.plot.addItem(pg.InfiniteLine(pos=0, angle=90,
                                          pen=pg.mkPen("#c7cdd6", width=1, style=Qt.DotLine)))
        self.state = muted("Stopped. Press Run to start the oscilloscope.")
        pc.body.addWidget(self.plot)
        pc.body.addWidget(self.state)
        center.addWidget(pc, 1)

        mc = Card("Measurements")
        grid = QGridLayout()
        grid.setHorizontalSpacing(26)
        self.dphi = ValueDisplay("Phase difference (In1 − In2)", "deg", template="+000.000",
                                 width=290)
        self.delay = ValueDisplay("Equivalent delay", "ns", template="+0000.000", width=210)
        self.freq = ValueDisplay("Frequency", "MHz", big=False, template="000.0000", width=230)
        self.m1 = ValueDisplay("Input 1", "", big=False, template="0000.0 mVpp", width=150)
        self.m2 = ValueDisplay("Input 2", "", big=False, template="0000.0 mVpp", width=150)
        self.m1.value.setStyleSheet(f"color:{theme.CH1}")
        self.m2.value.setStyleSheet(f"color:{theme.CH2}")
        grid.addWidget(self.dphi, 0, 0, 2, 1)
        grid.addWidget(self.delay, 0, 1, 2, 1)
        grid.addWidget(self.freq, 0, 2)
        grid.addWidget(self.m1, 0, 3)
        grid.addWidget(self.m2, 0, 4)
        avg_row = QHBoxLayout()
        avg_row.addWidget(muted("Average over", wrap=False))
        self.n_avg = QSpinBox()
        self.n_avg.setRange(1, 500)
        self.n_avg.setValue(20)
        self.n_avg.valueChanged.connect(lambda n: self._reset_avg(n))
        avg_row.addWidget(self.n_avg)
        avg_row.addWidget(muted("frames", wrap=False))
        reset = button("Reset")
        reset.clicked.connect(lambda: self._reset_avg(self.n_avg.value()))
        avg_row.addWidget(reset)
        avg_row.addStretch()
        grid.addLayout(avg_row, 1, 2, 1, 3)
        grid.setColumnStretch(5, 1)
        mc.body.addLayout(grid)
        center.addWidget(mc)
        root.addLayout(center, 1)

        # ---- controls
        side = QVBoxLayout()
        side.setSpacing(12)

        acq = Card("Acquisition")
        row = QHBoxLayout()
        self.btn_run = button("Run", "primary")
        self.btn_single = button("Single")
        row.addWidget(self.btn_run, 1)
        row.addWidget(self.btn_single)
        acq.body.addLayout(row)
        form = QFormLayout()
        self.span = QComboBox()
        self.span.addItems([s[1] for s in SPANS])
        self.span.setCurrentIndex(DEFAULT_SPAN)
        self.points = QComboBox()
        self.points.addItems(POINTS)
        self.points.setCurrentText(DEFAULT_POINTS)
        self.points.setToolTip("More points = slower update (16384 points: about 1 frame/s).")
        self.acq_mode = QComboBox()
        self.acq_mode.addItems(["Normal", "Precision", "DeepMemory"])
        self.acq_mode.setCurrentText("Normal")
        self.acq_mode.setToolTip("Precision averages samples (lower noise); Normal decimates.")
        self.autoscale = QCheckBox("Autoscale vertical")
        self.autoscale.setChecked(True)
        self.xy = QCheckBox("XY display (Input 1 vs Input 2)")
        form.addRow("Timebase:", self.span)
        form.addRow("Points:", self.points)
        form.addRow("Mode:", self.acq_mode)
        acq.body.addLayout(form)
        acq.body.addWidget(self.autoscale)
        acq.body.addWidget(self.xy)
        side.addWidget(acq)

        gen = Card("Waveform generator")
        self.gen_pill = StatusPill("Output off")
        gen.header.addWidget(self.gen_pill)
        self.btn_gen = button("Output on (sweep start)")
        self.btn_gen.setCheckable(True)
        self.btn_gen.setToolTip("The generator idles at the sweep start frequency (1 MHz) "
                                "until a sweep is triggered.")
        gen.body.addWidget(self.btn_gen)
        side.addWidget(gen)

        chc = Card("Inputs")
        self.ch1 = ChannelBox(1, theme.CH1)
        self.ch2 = ChannelBox(2, theme.CH2)
        chc.body.addWidget(self.ch1)
        chc.body.addWidget(self.ch2)
        side.addWidget(chc)

        trc = Card("Trigger")
        tf = QFormLayout()
        self.trig_src = QComboBox()
        self.trig_src.addItems(["Input1", "Input2", "External"])
        self.trig_edge = QComboBox()
        self.trig_edge.addItems(["Rising", "Falling", "Both"])
        self.trig_mode = QComboBox()
        self.trig_mode.addItems(["Auto", "Normal"])
        self.trig_level = QDoubleSpinBox()
        self.trig_level.setRange(-5, 5)
        self.trig_level.setDecimals(3)
        self.trig_level.setSingleStep(0.01)
        self.trig_level.setSuffix(" V")
        self.trig_level.setValue(0.2)
        lvl = QHBoxLayout()
        lvl.addWidget(self.trig_level, 1)
        self.btn_50 = button("50 %", tooltip="Set the level to the middle of the trigger signal.")
        lvl.addWidget(self.btn_50)
        tf.addRow("Source:", self.trig_src)
        tf.addRow("Edge:", self.trig_edge)
        tf.addRow("Level:", lvl)
        tf.addRow("Mode:", self.trig_mode)
        trc.body.addLayout(tf)
        side.addWidget(trc)
        side.addStretch()
        side.setContentsMargins(0, 0, 6, 0)
        root.addWidget(side_column(side, 340))

        # ---- wiring
        self.btn_run.clicked.connect(self.toggle_run)
        self.btn_single.clicked.connect(self.single)
        self.btn_gen.toggled.connect(self.wavegen_output)
        self.btn_50.clicked.connect(self._level_50)
        for w in (self.span, self.points, self.acq_mode, self.trig_src, self.trig_edge,
                  self.trig_mode):
            w.currentIndexChanged.connect(self._config_changed)
        self.trig_level.editingFinished.connect(self._config_changed)
        self.trig_level.valueChanged.connect(self.trig_line.setValue)
        self.ch1.changed.connect(self._config_changed)
        self.ch2.changed.connect(self._config_changed)
        self.xy.toggled.connect(self._xy_changed)
        self.trig_line.setValue(self.trig_level.value())
        self._apply_xrange()

    # ---- config
    def cfg(self) -> dict:
        return dict(ch1=self.ch1.cfg(), ch2=self.ch2.cfg(), span_s=SPANS[self.span.currentIndex()][0],
                    points=int(self.points.currentText()), acquisition_mode=self.acq_mode.currentText(),
                    trigger=dict(source=self.trig_src.currentText(), level=self.trig_level.value(),
                                 edge=self.trig_edge.currentText(), mode=self.trig_mode.currentText()))

    def _config_changed(self) -> None:
        self.configure.emit(self.cfg())
        self._reset_avg(self.n_avg.value())
        self._apply_xrange()

    def _apply_xrange(self) -> None:
        """Time axis follows the timebase, like a real oscilloscope."""
        if not self.xy.isChecked():
            half = SPANS[self.span.currentIndex()][0] / 2
            self.plot.setXRange(-half, half, padding=0)

    def _xy_changed(self, on: bool) -> None:
        if on:
            self.plot.setLabel("bottom", "Input 2", units="V")
            self.plot.setLabel("left", "Input 1", units="V")
        else:
            self.plot.setLabel("bottom", "Time", units="s")
            self.plot.setLabel("left", "Voltage", units="V")
        self.trig_line.setVisible(not on)
        self.plot.enableAutoRange()
        self._apply_xrange()

    def _level_50(self) -> None:
        f = self._last_frame
        key = {"Input1": "ch1", "Input2": "ch2"}.get(self.trig_src.currentText())
        if f and key and f.get(key) is not None:
            y = np.asarray(f[key], float)
            self.trig_level.setValue(0.5 * (np.nanmax(y) + np.nanmin(y)))
            self._config_changed()

    def _reset_avg(self, n: int) -> None:
        self._dphi = deque(maxlen=n)

    # ---- run control
    def set_available(self, ok: bool, reason: str = "") -> None:
        self._enabled = ok
        self.btn_run.setEnabled(ok)
        self.btn_single.setEnabled(ok)
        if not ok:
            self.stop()
            if reason:
                self.state.setText(reason)
        elif not self.running:
            self.state.setText("Stopped. Press Run to start the oscilloscope.")

    def set_wavegen_state(self, connected: bool, on: bool) -> None:
        self.btn_gen.blockSignals(True)
        self.btn_gen.setChecked(on)
        self.btn_gen.blockSignals(False)
        self.btn_gen.setEnabled(connected and self._enabled)
        self.btn_gen.setText("Output off" if on else "Output on (sweep start)")
        self.gen_pill.set_state("ok" if on else "off", "Output on" if on else "Output off")

    def toggle_run(self) -> None:
        if self.running:
            self.stop()
        else:
            self.running, self._single = True, False
            self.btn_run.setText("Stop")
            self.btn_run.setProperty("kind", "danger")
            self._repolish(self.btn_run)
            self.state.setText("Starting oscilloscope (deploying instrument on the Moku) …")
            self.configure.emit(self.cfg())
            self._request()

    def single(self) -> None:
        if not self.running:
            self._single = True
            self.configure.emit(self.cfg())
            self._request()

    def stop(self) -> None:
        self.running = False
        self.btn_run.setText("Run")
        self.btn_run.setProperty("kind", "primary")
        self._repolish(self.btn_run)
        if self._enabled:
            self.state.setText("Stopped.")

    def _request(self) -> None:
        if not self._pending:
            self._pending = True
            self.acquire.emit()

    @staticmethod
    def _repolish(w) -> None:
        w.style().unpolish(w)
        w.style().polish(w)

    # ---- frames
    def on_frame(self, frame) -> None:
        self._pending = False
        if frame is None:
            if self.running:
                self.state.setText("No data from the Moku - retrying …")
                QTimer.singleShot(500, self._next)
            return
        if self.running:
            self._request()        # fetch the next frame while this one is drawn
        self._frame_times.append(time.monotonic())
        self._last_frame = frame
        t = np.asarray(frame["time"], float)
        y1 = None if frame.get("ch1") is None else np.asarray(frame["ch1"], float)
        y2 = None if frame.get("ch2") is None else np.asarray(frame["ch2"], float)
        if self.xy.isChecked() and y1 is not None and y2 is not None:
            self.curve1.setData(y2, y1)
            self.curve2.setData([], [])
        else:
            self.curve1.setData(t, y1) if y1 is not None else self.curve1.setData([], [])
            self.curve2.setData(t, y2) if y2 is not None else self.curve2.setData([], [])
        if self.xy.isChecked():
            self.plot.enableAutoRange()
        elif self.autoscale.isChecked():
            self.plot.enableAutoRange(axis=pg.ViewBox.YAxis)
        self._update_measurements(t, y1, y2)
        if self.running:
            ft = self._frame_times
            fps = (len(ft) - 1) / (ft[-1] - ft[0]) if len(ft) > 1 and ft[-1] > ft[0] else 0.0
            self.state.setText(f"Running · {fps:.1f} frames/s · {len(t)} points")
        else:
            self.state.setText("Single acquisition done.")
        self._single = False

    def _next(self) -> None:
        if self.running:
            self._request()

    def _update_measurements(self, t, y1, y2) -> None:
        def ch_text(y):
            return f"{np.ptp(y) * 1e3:.1f} mVpp" if y is not None else "off"

        def ch_sub(y):
            return f"mean {np.mean(y) * 1e3:.1f} mV" if y is not None else ""

        self.m1.set(ch_text(y1), ch_sub(y1))
        self.m2.set(ch_text(y2), ch_sub(y2))
        a = analyse_frame(t, y1, y2) if (y1 is not None and y2 is not None) else None
        if a is None:
            self.dphi.set("—", "needs both inputs and at least half a period")
            self.delay.set("—")
            self.freq.set("—")
            return
        self._dphi.append(np.radians(a.dphi_deg))
        z = np.exp(1j * np.array(self._dphi))
        mean = np.degrees(np.angle(z.mean()))
        sd = np.degrees(np.sqrt(max(-2 * np.log(min(abs(z.mean()), 1.0)), 0.0)))
        n = len(self._dphi)
        self.dphi.set(f"{mean:+.3f}", f"std {sd:.3f}° over {n} frames")
        self.delay.set(f"{mean / 360 / a.frequency_hz * 1e9:+.3f}",
                       f"at {a.frequency_hz / 1e6:.4f} MHz")
        self.freq.set(f"{a.frequency_hz / 1e6:.4f}",
                      f"amplitudes {a.ch1.amplitude * 1e3:.1f} / {a.ch2.amplitude * 1e3:.1f} mV")
