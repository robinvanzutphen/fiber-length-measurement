"""Main window: header (instruments, folder), tabs, and the wiring between GUI and workers."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from PyQt5.QtCore import QThread, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                             QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget)

from .. import APP_TITLE, __version__
from ..core.processing import Calibration, MeasurementResult
from ..core.sequence import CALIBRATION, RunRequest
from ..core.storage import MeasurementFolder, read_sidecar, write_sidecar
from ..settings import LOG_DIR, Settings
from .dialogs import CableDialog, HelpDialog, SettingsDialog
from .measure_tab import MeasureTab
from .phasemeter_tab import PhasemeterTab
from .results_tab import ResultsTab
from .scope_tab import ScopeTab
from .widgets import StatusPill, button, muted, text_width
from .workers import HardwareWorker, ProcessingWorker

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    # GUI -> workers (queued across threads)
    sig_connect = pyqtSignal()
    sig_disconnect = pyqtSignal()
    sig_wavegen = pyqtSignal(bool)
    sig_start = pyqtSignal(object, str)
    sig_scope_cfg = pyqtSignal(dict)
    sig_scope_acquire = pyqtSignal()
    sig_load_cal = pyqtSignal(str)
    sig_analyse = pyqtSignal(str, object, dict)
    sig_pm_apply = pyqtSignal(dict)
    sig_pm_read = pyqtSignal()
    sig_pm_reacquire = pyqtSignal()
    sig_trigger = pyqtSignal()

    def __init__(self, settings: Settings, log_bridge=None, first_run: bool = False) -> None:
        super().__init__()
        self.settings = settings
        self.folder = MeasurementFolder(settings.workflow.measurement_folder)
        self.calibration: Calibration | None = None
        self._status: dict = {}
        self._busy = False
        self._acquired_cals: set[str] = set()
        self._run_kind = ""

        self.setWindowTitle(f"{APP_TITLE} — V{__version__}")
        self.resize(1440, 900)
        self.setMinimumSize(1200, 760)
        self._build()
        self._start_workers()
        if log_bridge is not None:
            log_bridge.message.connect(self._append_log)
        self._set_folder(self.folder.root, initial=True)
        self._update_controls()
        self._check_task = None
        if first_run:
            QTimer.singleShot(300, self._first_run)
        else:
            QTimer.singleShot(500, self._background_check)
        log.info("FLS V%s started%s.", __version__,
                 " in SIMULATION mode" if settings.device.simulate else "")

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = QFrame()
        header.setObjectName("Header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 10, 18, 10)
        hl.setSpacing(14)
        title = QLabel(APP_TITLE)
        title.setObjectName("AppTitle")
        title.setMinimumWidth(text_width(title, APP_TITLE) + 8)
        hl.addWidget(title)
        self.sim_badge = QLabel(" SIMULATION ")
        self.sim_badge.setStyleSheet("background:#fef3c7; color:#92400e; border-radius:4px;"
                                     "font-weight:600; padding:2px 6px;")
        hl.addWidget(self.sim_badge)
        hl.addSpacing(18)
        self.pill_moku = StatusPill("Moku", longest="Moku: Oscilloscope")
        self.pill_wg = StatusPill("Generator", longest="Generator: output on ")
        hl.addWidget(self.pill_moku)
        hl.addWidget(self.pill_wg)
        self.btn_connect = button("Connect", "primary")
        hl.addWidget(self.btn_connect)
        hl.addStretch()
        hl.addWidget(muted("Folder:", wrap=False))
        self.folder_label = QLabel()
        self.folder_label.setMaximumWidth(270)
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        hl.addWidget(self.folder_label)
        self.btn_folder = button("Change…", tooltip="Select the folder where all data is saved.")
        hl.addWidget(self.btn_folder)
        self.btn_check = button("System check", tooltip="Check installation and instruments.")
        self.btn_settings = button("Settings")
        self.btn_help = button("Help")
        hl.addWidget(self.btn_check)
        hl.addWidget(self.btn_settings)
        hl.addWidget(self.btn_help)
        lay.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.measure = MeasureTab()
        self.pm = PhasemeterTab(self.settings.phasemeter_config())
        self.scope = ScopeTab()
        self.results = ResultsTab()
        log_page = QWidget()
        ll = QVBoxLayout(log_page)
        ll.setContentsMargins(14, 14, 14, 14)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        ll.addWidget(self.log_view)
        row = QHBoxLayout()
        row.addWidget(muted(f"Log files: {LOG_DIR}", wrap=False))
        row.addStretch()
        b = button("Open log folder")
        b.clicked.connect(lambda: os.startfile(LOG_DIR) if LOG_DIR.exists() else None)
        row.addWidget(b)
        ll.addLayout(row)
        self.tabs.addTab(self.measure, "Measure")
        self.tabs.addTab(self.pm, "Phasemeter")
        self.tabs.addTab(self.scope, "Oscilloscope")
        self.tabs.addTab(self.results, "Results")
        self.tabs.addTab(log_page, "Log")
        lay.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        # wiring (GUI side)
        self.btn_connect.clicked.connect(self._connect_clicked)
        self.btn_folder.clicked.connect(self._choose_folder)
        self.btn_settings.clicked.connect(self._open_settings)
        self.btn_help.clicked.connect(lambda: HelpDialog(self).exec_())
        self.btn_check.clicked.connect(self._open_system_check)
        self.tabs.currentChanged.connect(self._tab_changed)
        mc, cc = self.measure.meas_card, self.measure.cal_card
        cc.measure_clicked.connect(self._start_calibration)
        cc.load_clicked.connect(self._load_calibration_dialog)
        mc.start_clicked.connect(self._start_measurement)
        mc.abort_clicked.connect(self._abort)
        mc.open_clicked.connect(self._open_measurement_file)
        mc.name.setText(self.settings.workflow.last_name)
        self.results.open_folder.connect(lambda: os.startfile(self.folder.root))
        self.results.reanalyse.connect(self._reanalyse)

    # ------------------------------------------------------------------ workers
    def _start_workers(self) -> None:
        self.hw = HardwareWorker(self.settings)
        self.proc = ProcessingWorker(self.settings)
        self._hw_thread = QThread(self)
        self._proc_thread = QThread(self)
        self.hw.moveToThread(self._hw_thread)
        self.proc.moveToThread(self._proc_thread)

        self.sig_connect.connect(self.hw.connect_instruments)
        self.sig_disconnect.connect(self.hw.disconnect_instruments)
        self.sig_wavegen.connect(self.hw.set_wavegen_output)
        self.sig_start.connect(self.hw.start_sequence)
        self.sig_scope_cfg.connect(self.hw.scope_configure)
        self.sig_scope_acquire.connect(self.hw.scope_acquire)
        self.sig_load_cal.connect(self.proc.load_calibration)
        self.sig_analyse.connect(self.proc.analyse)

        self.hw.status.connect(self._on_status)
        self.hw.busy.connect(self._on_busy)
        self.hw.stage.connect(self.measure.progress.set_stage)
        self.hw.progress.connect(self._on_progress)
        self.hw.cable_request.connect(self._on_cable_request)
        self.hw.run_finished.connect(self._on_run_finished)
        self.hw.sequence_finished.connect(self._on_sequence_finished)
        self.hw.error.connect(self._error)
        self.hw.scope_frame.connect(self.scope.on_frame)
        self.scope.configure.connect(self.sig_scope_cfg)
        self.scope.acquire.connect(self.sig_scope_acquire)
        self.scope.wavegen_output.connect(self.sig_wavegen)

        self.sig_pm_apply.connect(self.hw.pm_apply)
        self.sig_pm_read.connect(self.hw.pm_read)
        self.sig_pm_reacquire.connect(self.hw.pm_reacquire)
        self.sig_trigger.connect(self.hw.trigger_sweep)
        self.pm.apply.connect(self.sig_pm_apply)
        self.pm.read.connect(self.sig_pm_read)
        self.pm.reacquire.connect(self.sig_pm_reacquire)
        self.pm.wavegen_output.connect(self.sig_wavegen)
        self.pm.trigger.connect(self.sig_trigger)
        self.pm.use_for_measurements.connect(self._use_pm_settings)
        self.hw.pm_reading.connect(self.pm.on_reading)
        self.hw.sweep_started.connect(self.pm.on_sweep_started)

        self.proc.calibration_ready.connect(self._on_calibration)
        self.proc.measurement_ready.connect(self._on_measurement)
        self.proc.failed.connect(lambda what, msg: self._error(msg))

        self._hw_thread.start()
        self._proc_thread.start()

    # ------------------------------------------------------------------ helpers
    def _append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        self.statusBar().showMessage(text.split(" - ", 1)[-1], 8000)

    def _error(self, msg: str) -> None:
        log.error(msg.replace("\n", " "))
        QMessageBox.warning(self, "FLS", msg)

    def _connected(self) -> bool:
        return bool(self._status.get("moku") and self._status.get("wavegen"))

    def _update_controls(self) -> None:
        self.measure.set_busy(self._busy, can_measure=self.calibration is not None)
        self.btn_connect.setEnabled(not self._busy)
        self.btn_folder.setEnabled(not self._busy)
        self.sim_badge.setVisible(self.settings.device.simulate)
        if self._busy and self._run_kind:
            avail, reason = False, "Not available while a measurement is running."
        elif not self._status.get("moku"):
            avail, reason = False, "Connect the instruments first."
        else:
            avail, reason = True, ""
        wg_ok = bool(self._status.get("wavegen")) and not self._run_kind
        wg_on = bool(self._status.get("wavegen_output"))
        for tab in (self.scope, self.pm):
            tab.set_available(avail, reason)
            tab.set_wavegen_state(wg_ok, wg_on)

    # ------------------------------------------------------------------ folder
    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select measurement folder",
                                                str(self.folder.root))
        if path:
            self._set_folder(Path(path))

    def _set_folder(self, path: Path, initial: bool = False) -> None:
        self.folder = MeasurementFolder(path)
        try:
            self.folder.root.mkdir(parents=True, exist_ok=True)   # subfolders on first save
        except OSError as exc:
            self._error(f"Cannot use folder {path}:\n{exc}")
            return
        self.settings.workflow.measurement_folder = str(self.folder.root)
        self.settings.save()
        self.folder_label.setText(self.folder_label.fontMetrics().elidedText(
            str(self.folder.root), Qt.ElideMiddle, 260))
        self.folder_label.setToolTip(str(self.folder.root))
        self.results.set_folder(self.folder)
        log.info("Measurement folder: %s", self.folder.root)
        cal = None
        if initial and self.settings.workflow.last_calibration:
            p = Path(self.settings.workflow.last_calibration)
            cal = p if p.is_file() else None
        cal = cal or self.folder.latest_calibration()
        if cal:
            log.info("Loading calibration %s", cal.name)
            self.sig_load_cal.emit(str(cal))
        self._refresh_trend()

    # ------------------------------------------------------------------ instruments
    def _connect_clicked(self) -> None:
        if self._status.get("moku") or self._status.get("wavegen"):
            self.scope.stop()
            self.pm.stop()
            self.sig_disconnect.emit()
        else:
            self.sig_connect.emit()

    def _on_status(self, st: dict) -> None:
        self._status = st
        inst = st.get("instrument") or "connected"
        self.pill_moku.set_state("ok" if st["moku"] else "off",
                                 f"Moku: {inst}" if st["moku"] else "Moku")
        self.pill_wg.set_state("ok" if st["wavegen"] else "off",
                               "Generator: output on" if st.get("wavegen_output") else "Generator")
        if not st["moku"]:
            self.pm.stop()
            self.scope.stop()
        connected_any = st["moku"] or st["wavegen"]
        self.btn_connect.setText("Disconnect" if connected_any else "Connect")
        self.btn_connect.setProperty("kind", None if connected_any else "primary")
        self.btn_connect.style().unpolish(self.btn_connect)
        self.btn_connect.style().polish(self.btn_connect)
        self._update_controls()

    def _on_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy and not self._run_kind:
            self.pill_moku.set_state("busy", "Moku: working …")
        self._update_controls()

    def _open_settings(self) -> None:
        sim_before = self.settings.device.simulate
        if SettingsDialog(self.settings, self, locked=self._busy).exec_():
            log.info("Settings saved.")
            if self.settings.device.simulate != sim_before and (self._status.get("moku")
                                                                or self._status.get("wavegen")):
                QMessageBox.information(self, "FLS", "Reconnect to switch between real and "
                                                     "simulated instruments.")
            if self.calibration is not None:
                self.sig_load_cal.emit(str(self.calibration.path))   # re-fit with new settings
            self.pm.set_measurement_config(self.settings.phasemeter_config())
            self._update_controls()

    def _use_pm_settings(self, cfg: dict) -> None:
        c, p = cfg["ch1"], self.settings.phasemeter
        p.coupling, p.impedance = c["coupling"], c["impedance"]
        p.input_range, p.pll_bandwidth = c["range"], c["bandwidth"]
        self.settings.save()
        self.pm.set_measurement_config(self.settings.phasemeter_config())
        log.info("Measurement phasemeter settings updated: %s, %s, %s, PLL %s.",
                 p.coupling, p.impedance, p.input_range, p.pll_bandwidth)
        notes = []
        if abs(c["frequency"] - self.settings.sweep.start_hz) > 1e-3 or c["auto_acquire"]:
            notes.append("Measurements always lock at the sweep start frequency "
                         f"({self.settings.sweep.start_hz / 1e6:g} MHz) without auto-acquire.")
        if cfg["phase_wrap"] != "Off" or cfg["auto_reset"] != "Off":
            notes.append("Measurements always use phase wrap and auto-reset 'Off'.")
        if cfg["ch1"] != cfg["ch2"]:
            notes.append("Measurements use the same settings (Input 1) for both inputs.")
        if notes:
            QMessageBox.information(self, "Measurement settings", "\n\n".join(notes))

    # ------------------------------------------------------------------ system check
    def _first_run(self) -> None:
        QMessageBox.information(
            self, "Welcome",
            "Welcome to FLS.\n\nThe system check opens next: it tests the installation and "
            "finds the instruments. Every problem comes with a fix; the full installation guide "
            "is under Help.")
        self._open_system_check()

    def _background_check(self) -> None:
        from ..core.system_check import FAIL, report, run_checks
        from .system_dialogs import Task
        self._check_task = Task(lambda: run_checks(self.settings), self)

        def done(res) -> None:
            if isinstance(res, Exception):
                return
            checks, _ = res
            n_fail = sum(c.status == FAIL for c in checks)
            self.btn_check.setText(f"⚠ System check ({n_fail})" if n_fail else "System check")
            self.btn_check.setProperty("kind", "danger" if n_fail else None)
            self.btn_check.style().unpolish(self.btn_check)
            self.btn_check.style().polish(self.btn_check)
            if n_fail:
                log.warning("System check: %d problem(s). Click 'System check' for the fixes.\n%s",
                            n_fail, report(checks))
        self._check_task.done.connect(done)
        self._check_task.start()

    def _open_system_check(self) -> None:
        from .system_dialogs import FindInstrumentsDialog, SystemCheckDialog
        dlg = SystemCheckDialog(self.settings, self)

        def find() -> None:
            if FindInstrumentsDialog(self.settings, dlg).exec_():
                dlg.run()
        dlg.find_requested.connect(find)
        dlg.exec_()
        self._background_check()

    def _tab_changed(self, idx: int) -> None:
        if self.tabs.widget(idx) is not self.scope:
            self.scope.stop()
        if self.tabs.widget(idx) is not self.pm:
            self.pm.stop()
        if self.tabs.widget(idx) is self.results:
            self.results.refresh()

    # ------------------------------------------------------------------ acquisition
    def _ready_for_run(self) -> bool:
        if not self._connected():
            self._error("Connect the Moku and the waveform generator first.")
            return False
        return True

    def _start_calibration(self) -> None:
        if not self._ready_for_run():
            return
        ok = QMessageBox.question(self, "Measure calibration",
                                  "Calibration is measured WITHOUT the fiber under test.\n\n"
                                  "Is the setup in its calibration configuration?")
        if ok != QMessageBox.Yes:
            return
        self._launch(RunRequest(kind=CALIBRATION))

    def _start_measurement(self) -> None:
        if not self._ready_for_run():
            return
        if self.calibration is None:
            self._error("Load or measure a calibration first.")
            return
        req = self.measure.meas_card.request()
        if not req.name:
            req.name = "measurement"
            self.measure.meas_card.name.setText(req.name)
        self.settings.workflow.last_name = req.name
        self.settings.save()
        self._refresh_trend()
        self._launch(req)

    def _launch(self, req: RunRequest) -> None:
        self.scope.stop()
        self.pm.stop()
        self._run_kind = req.kind
        self._series_total = req.repeats
        self.measure.progress.reset("Starting …")
        self.tabs.setCurrentWidget(self.measure)
        what = "calibration" if req.kind == CALIBRATION else (
            f"series '{req.name}' ({'until stopped' if req.repeats == 0 else f'{req.repeats} runs'})"
            if req.repeats != 1 else f"measurement '{req.name}'")
        log.info("Starting %s.", what)
        self.sig_start.emit(req, str(self.folder.root))

    def _abort(self) -> None:
        if QMessageBox.question(self, "Abort", "Abort the running acquisition?\n"
                                "The current run will be discarded.") == QMessageBox.Yes:
            self.measure.progress.set_stage("Aborting …")
            self.hw.abort()

    def _on_progress(self, frac: float, elapsed: float, remaining: float) -> None:
        self.measure.progress.set_progress(frac, elapsed, remaining)

    def _on_cable_request(self, run: int, total: int) -> None:
        self.raise_()
        self.activateWindow()
        dlg = CableDialog(run, total, self)
        self.hw.confirm_cable(dlg.exec_() == CableDialog.Accepted)

    def _on_run_finished(self, kind: str, path: str, meta: dict) -> None:
        if kind == CALIBRATION:
            self._acquired_cals.add(str(Path(path)))
            self.sig_load_cal.emit(path)
        else:
            ctx = dict(source="acquired", name=meta.get("name", ""), run=meta.get("run"),
                       acquired=meta.get("finished", ""))
            self.sig_analyse.emit(path, self.calibration, ctx)

    def _on_sequence_finished(self, outcome: str, runs: int) -> None:
        text = {"done": "Finished", "aborted": "Aborted", "error": "Stopped after an error"}[outcome]
        self.measure.progress.reset(f"{text} ({runs} run{'s' if runs != 1 else ''} completed)")
        self._run_kind = ""
        self._update_controls()

    # ------------------------------------------------------------------ calibration
    def _load_calibration_dialog(self) -> None:
        start = self.folder.calibrations if self.folder.calibrations.exists() else self.folder.root
        path, _ = QFileDialog.getOpenFileName(self, "Load calibration", str(start),
                                              "Phasemeter CSV (*.csv)")
        if path:
            self.sig_load_cal.emit(path)

    def _on_calibration(self, cal: Calibration) -> None:
        self.calibration = cal
        self.settings.workflow.last_calibration = str(cal.path)
        self.settings.save()
        if str(cal.path) in self._acquired_cals:
            payload = read_sidecar(cal.path)
            payload["calibration"] = cal.summary()
            write_sidecar(cal.path, payload)
        age_h = (time.time() - cal.path.stat().st_mtime) / 3600 if cal.path.exists() else None
        self.measure.cal_card.set_calibration(cal, age_h, self.settings.analysis.cal_max_age_h,
                                              cal.steps.stats.get("warnings", []))
        self.measure.show_calibration(cal)
        if self._run_kind == CALIBRATION or str(cal.path) in self._acquired_cals:
            self.measure.plot_tabs.setCurrentWidget(self.measure.cal_plot)
        log.info("Calibration active: %s (%d steps, RMS %.1f µcycles)", cal.path.name,
                 int(cal.used.sum()), cal.rms_cycles * 1e6)
        self._update_controls()

    # ------------------------------------------------------------------ measurement results
    def _open_measurement_file(self) -> None:
        if self.calibration is None:
            self._error("Load or measure a calibration first.")
            return
        start = self.folder.measurements if self.folder.measurements.exists() else self.folder.root
        path, _ = QFileDialog.getOpenFileName(self, "Analyse measurement file", str(start),
                                              "Phasemeter CSV (*.csv)")
        if path:
            self.sig_analyse.emit(path, self.calibration, dict(source="loaded",
                                                               name=Path(path).stem, run=None))

    def _reanalyse(self, paths: list) -> None:
        if self.calibration is None:
            self._error("Load or measure a calibration first.")
            return
        if not paths:
            self._error("Select one or more rows whose raw file is in this folder.")
            return
        for p in paths:
            acq = read_sidecar(Path(p)).get("acquisition", {})
            self.sig_analyse.emit(p, self.calibration,
                                  dict(source="reanalysed", name=acq.get("name") or Path(p).stem,
                                       run=acq.get("run")))

    def _on_measurement(self, r: MeasurementResult, ctx: dict) -> None:
        name, run, source = ctx.get("name", ""), ctx.get("run"), ctx.get("source")
        label = f"{name}" + (f" · run {run}" if run else "") + f" · {r.path.name}"
        if source == "loaded":
            label += "  (not added to results)"
        self.measure.result.show_result(r, label)
        self.measure.show_measurement(r)
        log.info("Result %s: delay %.2f ps ± %.3f ps, length %.4f m, R² %.8f",
                 r.path.name, r.delay_s * 1e12, r.ci95_s * 1e12, r.length_m, r.r2)
        for w in r.warnings:
            log.warning("%s: %s", r.path.name, w)
        if source in ("acquired", "reanalysed"):
            s = r.summary()
            if source == "acquired":
                payload = read_sidecar(r.path)
                payload["analysis"] = s
                write_sidecar(r.path, payload)
            self.folder.append_result(dict(
                timestamp=time.strftime("%Y-%m-%d %H:%M:%S"), name=name, run=run or "",
                delay_ps=f"{s['delay_ps']:.4f}", ci95_ps=f"{s['ci95_ps']:.4f}",
                length_m=f"{s['length_m']:.6f}", length_ci95_mm=f"{s['length_ci95_mm']:.4f}",
                r2=f"{s['r2']:.10f}", rms_mcyc=f"{s['rms_mcyc']:.4f}",
                steps_used=s["steps_used"], steps_total=s["steps_total"],
                calibration=Path(s["calibration"]).name, file=r.path.name, source=source,
                warnings=" | ".join(r.warnings)))
            self.results.refresh()
        self._refresh_trend(name)

    def _refresh_trend(self, name: str | None = None) -> None:
        name = name or self.measure.meas_card.name.text().strip()
        if not name:
            return
        rows = [r for r in self.folder.read_results() if r.get("name") == name]
        self.measure.show_trend(name, rows, self.settings.analysis.velocity_m_s)

    # ------------------------------------------------------------------ shutdown
    def closeEvent(self, event) -> None:
        if self._busy and self._run_kind:
            if QMessageBox.question(self, "Quit", "An acquisition is running. Abort it and quit?") \
                    != QMessageBox.Yes:
                event.ignore()
                return
            self.hw.abort()
        self.scope.stop()
        self.pm.stop()
        for t in (self._hw_thread, self._proc_thread):
            t.quit()
        self._hw_thread.wait(15000)
        self._proc_thread.wait(5000)
        self.hw.disconnect_instruments()     # thread stopped: safe to call directly
        self.settings.save()
        super().closeEvent(event)
