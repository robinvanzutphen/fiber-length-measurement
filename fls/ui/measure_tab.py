"""Measure tab: calibration, measurement control, progress, latest result and plots."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout,
                             QHBoxLayout, QLabel, QLineEdit, QProgressBar, QSpinBox, QTabWidget,
                             QVBoxLayout, QWidget)

from ..core.processing import Calibration, MeasurementResult
from ..core.sequence import MEASUREMENT, RunRequest
from . import theme
from .widgets import Card, StatusPill, ValueDisplay, button, muted, plot, side_column


def _fmt_time(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


class CalibrationCard(Card):
    measure_clicked = pyqtSignal()
    load_clicked = pyqtSignal()

    def __init__(self) -> None:
        super().__init__("1 · Calibration", "Setup without fiber under test.")
        self.pill = StatusPill("No calibration")
        self.header.addWidget(self.pill)
        self.info = muted("Measure a new calibration, or load one from a previous session.")
        self.warn = QLabel("")
        self.warn.setObjectName("Warning")
        self.warn.setWordWrap(True)
        self.warn.hide()
        row = QHBoxLayout()
        self.btn_measure = button("Measure calibration", "primary",
                                  "Run a sweep without fiber under test and use it as calibration.")
        self.btn_load = button("Load…", tooltip="Use an existing calibration file (.csv).")
        row.addWidget(self.btn_measure, 1)
        row.addWidget(self.btn_load)
        self.body.addWidget(self.info)
        self.body.addWidget(self.warn)
        self.body.addLayout(row)
        self.btn_measure.clicked.connect(self.measure_clicked)
        self.btn_load.clicked.connect(self.load_clicked)

    def set_calibration(self, cal: Calibration | None, age_h: float | None, max_age_h: float,
                        warnings: list[str]) -> None:
        if cal is None:
            self.pill.set_state("off", "No calibration")
            self.info.setText("Measure a new calibration, or load one from a previous session.")
            self.warn.hide()
            self.btn_measure.setProperty("kind", "primary")
        else:
            old = age_h is not None and age_h > max_age_h
            self.pill.set_state("busy" if old else "ok", "Old" if old else "Active")
            if age_h is None:
                age = ""
            elif age_h < 1:
                age = f"{age_h * 60:.0f} min ago"
            elif age_h < 48:
                age = f"{age_h:.1f} h ago"
            else:
                age = f"{age_h / 24:.0f} days ago"
            self.info.setText(f"<b>{cal.path.name}</b><br>{age} · {int(cal.used.sum())} steps · "
                              f"fit RMS {cal.rms_cycles * 1e6:.0f} µcycles")
            msgs = list(warnings)
            if old:
                msgs.insert(0, f"Calibration is older than {max_age_h:g} h - consider recalibrating.")
            self.warn.setText("\n".join("⚠ " + m for m in msgs))
            self.warn.setVisible(bool(msgs))
            self.btn_measure.setProperty("kind", None)
        self.btn_measure.style().unpolish(self.btn_measure)
        self.btn_measure.style().polish(self.btn_measure)


class MeasurementCard(Card):
    start_clicked = pyqtSignal()
    abort_clicked = pyqtSignal()
    open_clicked = pyqtSignal()

    def __init__(self) -> None:
        super().__init__("2 · Measurement", "Connect the fiber under test.")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. patchcord_A")
        self.name.setToolTip("Used in the file names and the results table.")
        self.mode = QComboBox()
        self.mode.addItems(["Single run", "Repeated runs"])
        self.repeats = QSpinBox()
        self.repeats.setRange(2, 10000)
        self.repeats.setValue(10)
        self.until_stopped = QCheckBox("until stopped")
        rep_row = QHBoxLayout()
        rep_row.addWidget(self.repeats)
        rep_row.addWidget(self.until_stopped)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0, 24 * 3600)
        self.interval.setDecimals(0)
        self.interval.setSuffix(" s")
        self.interval.setToolTip("Pause between the end of one run and the start of the next.")
        form.addRow("Name:", self.name)
        form.addRow("Mode:", self.mode)
        self._rep_label = QLabel("Runs:")
        self._int_label = QLabel("Pause:")
        form.addRow(self._rep_label, rep_row)
        form.addRow(self._int_label, self.interval)
        self.body.addLayout(form)
        row = QHBoxLayout()
        self.btn_start = button("Start measurement", "primary")
        self.btn_abort = button("Abort", "danger")
        self.btn_abort.setEnabled(False)
        row.addWidget(self.btn_start, 1)
        row.addWidget(self.btn_abort)
        self.body.addLayout(row)
        self.btn_open = button("Analyse an existing measurement file…")
        self.body.addWidget(self.btn_open)

        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.until_stopped.toggled.connect(lambda on: self.repeats.setEnabled(not on))
        self.btn_start.clicked.connect(self.start_clicked)
        self.btn_abort.clicked.connect(self.abort_clicked)
        self.btn_open.clicked.connect(self.open_clicked)
        self._mode_changed()

    def _mode_changed(self) -> None:
        rep = self.mode.currentIndex() == 1
        for w in (self._rep_label, self.repeats, self.until_stopped, self._int_label,
                  self.interval):
            w.setVisible(rep)

    def request(self) -> RunRequest:
        rep = self.mode.currentIndex() == 1
        n = 0 if (rep and self.until_stopped.isChecked()) else (self.repeats.value() if rep else 1)
        return RunRequest(kind=MEASUREMENT, name=self.name.text().strip(), repeats=n,
                          interval_s=self.interval.value() if rep else 0.0)


class ProgressCard(Card):
    def __init__(self) -> None:
        super().__init__("Progress")
        self.stage = QLabel("Idle")
        self.stage.setObjectName("Stage")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.time = muted("")
        self.body.addWidget(self.stage)
        self.body.addWidget(self.bar)
        self.body.addWidget(self.time)

    def set_stage(self, text: str) -> None:
        self.stage.setText(text)

    def set_progress(self, frac: float, elapsed: float, remaining: float) -> None:
        self.bar.setValue(int(frac * 1000))
        self.time.setText(f"{_fmt_time(elapsed)} elapsed · {_fmt_time(remaining)} remaining")

    def reset(self, text: str = "Idle") -> None:
        self.stage.setText(text)
        self.bar.setValue(0)
        self.time.setText("")


class ResultCard(Card):
    def __init__(self) -> None:
        super().__init__("Latest result")
        self.source = muted("No measurement yet.")
        self.header.addWidget(self.source)
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        self.delay = ValueDisplay("Optical delay", "ps", template="000 000.00")
        self.length = ValueDisplay("Length", "m", template="000.0000")
        self.ci = ValueDisplay("95 % CI", "ps", big=False, template="± 00.000")
        self.r2 = ValueDisplay("R²", "", big=False, template="0.00000000")
        self.rms = ValueDisplay("Residual RMS", "mcycles", big=False, template="00.000")
        self.steps = ValueDisplay("Sweep steps used", "", big=False, template="0000 / 0000")
        grid.addWidget(self.delay, 0, 0, 2, 1)
        grid.addWidget(self.length, 0, 1, 2, 1)
        grid.addWidget(self.ci, 0, 2)
        grid.addWidget(self.r2, 0, 3)
        grid.addWidget(self.rms, 1, 2)
        grid.addWidget(self.steps, 1, 3)
        grid.setColumnStretch(4, 1)
        self.body.addLayout(grid)
        self.warn = QLabel("")
        self.warn.setObjectName("Warning")
        self.warn.setWordWrap(True)
        self.warn.hide()
        self.body.addWidget(self.warn)

    def show_result(self, r: MeasurementResult, label: str) -> None:
        self.source.setText(label)
        self.delay.set(f"{r.delay_s * 1e12:,.2f}".replace(",", " "))
        self.length.set(f"{r.length_m:.4f}", f"± {r.length_ci95_m * 1e3:.3f} mm")
        self.ci.set(f"± {r.ci95_s * 1e12:.3f}")
        self.r2.set(f"{r.r2:.8f}")
        self.rms.set(f"{r.rms_cycles * 1e3:.3f}")
        self.steps.set(f"{int(r.used.sum())} / {len(r.steps)}")
        self.warn.setText("\n".join("⚠ " + w for w in r.warnings))
        self.warn.setVisible(bool(r.warnings))


class DualPlot(QWidget):
    """Data + fit on top, residuals below, shared x axis."""

    def __init__(self, ylabel: str, rlabel: str) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.top = plot("Frequency", ylabel, "Hz", "")
        self.bottom = plot("Frequency", rlabel, "Hz", "")
        self.bottom.setXLink(self.top)
        self.bottom.setMaximumHeight(220)
        lay.addWidget(self.top, 3)
        lay.addWidget(self.bottom, 2)

    def clear(self) -> None:
        self.top.clear()
        self.bottom.clear()


def _scatter(w, x, y, color, name=None, size=4):
    w.plot(x, y, pen=None, symbol="o", symbolSize=size, symbolBrush=pg.mkBrush(color),
           symbolPen=None, name=name)


class MeasureTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(12)
        self.cal_card = CalibrationCard()
        self.meas_card = MeasurementCard()
        self.progress = ProgressCard()
        left.addWidget(self.cal_card)
        left.addWidget(self.meas_card)
        left.addWidget(self.progress)
        left.addStretch()
        left.setContentsMargins(0, 0, 6, 0)
        root.addWidget(side_column(left, 390))

        right = QVBoxLayout()
        right.setSpacing(12)
        self.result = ResultCard()
        right.addWidget(self.result)
        plots = Card()
        self.plot_tabs = QTabWidget()
        self.meas_plot = DualPlot("Calibrated phase", "Residual")
        self.cal_plot = DualPlot("Phase difference", "Residual")
        self.trend = plot("Measurement #", "Delay (ps)")
        self.trend.getAxis("left").enableAutoSIPrefix(False)
        self.trend_info = muted("")
        trend_w = QWidget()
        tl = QVBoxLayout(trend_w)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(self.trend)
        tl.addWidget(self.trend_info)
        self.plot_tabs.addTab(self.meas_plot, "Measurement")
        self.plot_tabs.addTab(self.cal_plot, "Calibration")
        self.plot_tabs.addTab(trend_w, "Series")
        plots.body.addWidget(self.plot_tabs)
        right.addWidget(plots, 1)
        root.addLayout(right, 1)

    # ---- state
    def set_busy(self, busy: bool, can_measure: bool) -> None:
        c, m = self.cal_card, self.meas_card
        c.btn_measure.setEnabled(not busy)
        c.btn_load.setEnabled(not busy)
        m.btn_start.setEnabled(not busy and can_measure)
        m.btn_open.setEnabled(not busy and can_measure)
        m.btn_abort.setEnabled(busy)
        for w in (m.name, m.mode, m.repeats, m.until_stopped, m.interval):
            w.setEnabled(not busy)
        if not busy:
            m.repeats.setEnabled(not m.until_stopped.isChecked())

    # ---- plots
    def show_calibration(self, cal: Calibration) -> None:
        p = self.cal_plot
        p.clear()
        st = cal.steps
        _scatter(p.top, st.freq[cal.used], st.phase[cal.used], theme.CH1, "Sweep steps")
        if (~cal.used).any():
            _scatter(p.top, st.freq[~cal.used], st.phase[~cal.used], theme.FIT, "Rejected", 6)
        fx = np.linspace(cal.f_min, cal.f_max, 1000)
        p.top.plot(fx, cal.poly(fx), pen=pg.mkPen(theme.FIT, width=2),
                   name=f"Polynomial (order {cal.order})")
        p.top.setLabel("left", "Phase difference", units="cycles")
        _scatter(p.bottom, st.freq[cal.used], cal.residual[cal.used] * 1e6, theme.CH1, size=3)
        if (~cal.used).any():
            _scatter(p.bottom, st.freq[~cal.used], cal.residual[~cal.used] * 1e6, theme.FIT, size=5)
        p.bottom.setLabel("left", "Residual", units="µcycles")
        p.bottom.setYRange(*_robust_range(cal.residual[cal.used] * 1e6))

    def show_measurement(self, r: MeasurementResult) -> None:
        p = self.meas_plot
        p.clear()
        st = r.steps
        rej = r.in_range & ~r.used
        _scatter(p.top, st.freq[r.used], r.calibrated[r.used], theme.CH1, "Calibrated steps")
        if rej.any():
            _scatter(p.top, st.freq[rej], r.calibrated[rej], theme.REJECTED, "Rejected", 5)
        fx = st.freq[r.in_range]
        fx = np.array([fx.min(), fx.max()])
        p.top.plot(fx, r.fit_line(fx), pen=pg.mkPen(theme.FIT, width=2),
                   name=f"Linear fit: {r.slope_s * 1e9:+.4f} ns")
        p.top.setLabel("left", "Calibrated phase", units="cycles")
        _scatter(p.bottom, st.freq[r.used], r.residual[r.used] * 1e6, theme.CH1, size=3)
        if rej.any():
            _scatter(p.bottom, st.freq[rej], r.residual[rej] * 1e6, theme.REJECTED, size=4)
        p.bottom.setLabel("left", "Residual", units="µcycles")
        p.bottom.setYRange(*_robust_range(r.residual[r.used] * 1e6))
        self.plot_tabs.setCurrentWidget(self.meas_plot)

    def show_trend(self, name: str, rows: list[dict], velocity_m_s: float) -> None:
        self.trend.clear()
        vals = []
        for row in rows:
            try:
                vals.append(float(row["delay_ps"]))
            except (KeyError, ValueError):
                pass
        if not vals:
            self.trend_info.setText(f"No results for '{name}' in this folder yet.")
            return
        y = np.array(vals)
        x = np.arange(1, len(y) + 1)
        self.trend.plot(x, y, pen=pg.mkPen(theme.CH1, width=1.5), symbol="o", symbolSize=7,
                        symbolBrush=theme.CH1, symbolPen=None, name=name)
        self.trend.setLabel("bottom", "Measurement #")
        self.trend.setLabel("left", "Delay (ps)")
        if len(y) > 1:
            sd = np.std(y, ddof=1)
            self.trend_info.setText(
                f"'{name}': {len(y)} results · mean {y.mean():.2f} ps · "
                f"std {sd:.2f} ps (≈ {sd * 1e-12 * velocity_m_s * 1e6:.0f} µm)")
        else:
            self.trend_info.setText(f"'{name}': 1 result")


def _robust_range(y: np.ndarray, k: float = 8.0) -> tuple[float, float]:
    if len(y) == 0:
        return -1.0, 1.0
    med = float(np.median(y))
    s = 1.4826 * float(np.median(np.abs(y - med))) or 1.0
    lo, hi = max(y.min(), med - k * s), min(y.max(), med + k * s)
    pad = 0.1 * (hi - lo or 1.0)
    return lo - pad, hi + pad
