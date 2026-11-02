"""Background workers. Each lives in its own QThread so the GUI never blocks.

HardwareWorker   - owns the Moku and the waveform generator; runs sequences and scope frames.
ProcessingWorker - loads files and runs the analysis.
"""

from __future__ import annotations

import logging
import traceback

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from ..core.processing import analyse_measurement, load_calibration
from ..core.io import load_phasemeter_csv
from ..core.sequence import AcquisitionSequence, Aborted, RunRequest, SequenceCallbacks
from ..core.storage import MeasurementFolder
from ..hardware import create_moku, create_wavegen
from ..settings import Settings

log = logging.getLogger(__name__)


class HardwareWorker(QObject):
    status = pyqtSignal(dict)
    busy = pyqtSignal(bool)
    stage = pyqtSignal(str)
    progress = pyqtSignal(float, float, float)          # fraction, elapsed, remaining (s)
    cable_request = pyqtSignal(int, int)                # run, total
    run_finished = pyqtSignal(str, str, dict)           # kind, path, meta
    sequence_finished = pyqtSignal(str, int)            # outcome: done/aborted/error, runs done
    scope_frame = pyqtSignal(object)
    pm_reading = pyqtSignal(object)                     # dict from Phasemeter.get_data or None
    sweep_started = pyqtSignal(float)                   # expected sweep duration (s)
    error = pyqtSignal(str)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.moku = None
        self.wavegen = None
        self._seq: AcquisitionSequence | None = None
        self._scope_cfg: dict | None = None
        self._scope_dirty = True
        self._pm_cfg: dict | None = None

    # ---- status
    def _emit_status(self) -> None:
        self.status.emit(dict(
            moku=bool(self.moku and self.moku.connected),
            instrument=(self.moku.kind if self.moku else None) or "",
            wavegen=self.wavegen is not None,
            wavegen_output=bool(self.wavegen and self.wavegen.output_on),
            simulated=self.settings.device.simulate,
        ))

    # ---- connections
    @pyqtSlot()
    def connect_instruments(self) -> None:
        self.busy.emit(True)
        try:
            self._close_all()
            mode = "simulated " if self.settings.device.simulate else ""
            log.info("Connecting to %sMoku (%s) ...", mode, self.settings.device.moku_address)
            try:
                self.moku = create_moku(self.settings)
                self.moku.phasemeter()
                log.info("Moku connected - Phasemeter deployed.")
            except Exception as exc:
                self.moku = None
                self.error.emit(f"Moku connection failed:\n{exc}")
            log.info("Connecting to waveform generator ...")
            try:
                self.wavegen = create_wavegen(self.settings)
                log.info("Waveform generator connected: %s", self.wavegen.identify() or "OK")
            except Exception as exc:
                self.wavegen = None
                self.error.emit(f"Waveform generator connection failed:\n{exc}")
        finally:
            self._emit_status()
            self.busy.emit(False)

    @pyqtSlot()
    def disconnect_instruments(self) -> None:
        self._close_all()
        log.info("Instruments disconnected.")
        self._emit_status()

    def _close_all(self) -> None:
        if self.wavegen:
            self.wavegen.close()
        if self.moku:
            self.moku.close()
        self.moku = self.wavegen = None
        self._scope_dirty = True

    @pyqtSlot(bool)
    def set_wavegen_output(self, on: bool) -> None:
        if not self.wavegen:
            self.error.emit("Waveform generator is not connected.")
            return
        try:
            if on:
                self.wavegen.configure_sweep(self.settings.sweep)
            self.wavegen.set_output(on)
            if on:
                log.info("Generator output ON (%.3g MHz, sweep armed)",
                         self.settings.sweep.start_hz / 1e6)
            else:
                log.info("Generator output OFF")
        except Exception as exc:
            self.error.emit(f"Generator error: {exc}")
        self._emit_status()

    # ---- sequences
    @pyqtSlot(object, str)
    def start_sequence(self, req: RunRequest, folder: str) -> None:
        if not (self.moku and self.wavegen):
            self.error.emit("Connect the Moku and the waveform generator first.")
            self.sequence_finished.emit("error", 0)
            return
        self.busy.emit(True)
        self._scope_dirty = True
        cb = SequenceCallbacks(stage=self.stage.emit, progress=self.progress.emit,
                               request_cable=self.cable_request.emit,
                               run_finished=self.run_finished.emit)
        self._seq = AcquisitionSequence(self.moku, self.wavegen, self.settings,
                                        MeasurementFolder(folder), cb)
        outcome, done = "done", 0
        try:
            self._emit_status()
            done = self._seq.run(req)
        except Aborted:
            outcome = "aborted"
            log.warning("Acquisition aborted by user.")
        except Exception as exc:
            outcome = "error"
            log.error("Acquisition failed: %s\n%s", exc, traceback.format_exc())
            self.error.emit(f"Acquisition failed:\n{exc}")
        finally:
            done = done or 0
            self._seq = None
            self._emit_status()
            self.busy.emit(False)
            self.sequence_finished.emit(outcome, done)

    # thread-safe, called directly from the GUI thread
    def abort(self) -> None:
        if self._seq:
            self._seq.abort()

    def confirm_cable(self, proceed: bool) -> None:
        if self._seq:
            self._seq.confirm_cable(proceed)

    @pyqtSlot()
    def trigger_sweep(self) -> None:
        if not self.wavegen:
            self.error.emit("Waveform generator is not connected.")
            return
        try:
            if not self.wavegen.output_on:
                self.wavegen.configure_sweep(self.settings.sweep)
                self.wavegen.set_output(True)
            self.wavegen.trigger()
            dur = self.settings.sweep.duration_s * (self.moku.time_scale if self.moku else 1.0)
            log.info("Sweep triggered (%.3g -> %.3g MHz in %.0f s).",
                     self.settings.sweep.start_hz / 1e6, self.settings.sweep.stop_hz / 1e6,
                     self.settings.sweep.duration_s)
            self.sweep_started.emit(dur)
        except Exception as exc:
            self.error.emit(f"Generator error: {exc}")
        self._emit_status()

    # ---- phasemeter diagnostics
    @pyqtSlot(dict)
    def pm_apply(self, cfg: dict) -> None:
        """Store the diagnostic configuration and apply it (deploys the Phasemeter if needed)."""
        self._pm_cfg = cfg
        if not self.moku:
            return
        try:
            self.moku.ensure_phasemeter(cfg)
            log.info("Phasemeter settings applied.")
        except Exception as exc:
            self.error.emit(f"Could not apply phasemeter settings:\n{exc}")
        self._emit_status()

    @pyqtSlot()
    def pm_reacquire(self) -> None:
        if not self.moku:
            return
        try:
            self.moku.ensure_phasemeter(self._pm_cfg or self.settings.phasemeter_config()) \
                .reacquire()
            log.info("Phasemeter channels re-acquired.")
        except Exception as exc:
            self.error.emit(f"Re-acquire failed: {exc}")

    @pyqtSlot()
    def pm_read(self) -> None:
        if not self.moku:
            self.pm_reading.emit(None)
            return
        try:
            switching = self.moku.kind != "Phasemeter"
            if switching:
                log.info("Switching Moku to Phasemeter ...")
            pm = self.moku.ensure_phasemeter(self._pm_cfg or self.settings.phasemeter_config())
            if switching:
                self._scope_dirty = True
                self._emit_status()
            self.pm_reading.emit(pm.live())
        except Exception as exc:
            log.error("Phasemeter read failed: %s", exc)
            self.pm_reading.emit(None)

    # ---- oscilloscope
    @pyqtSlot(dict)
    def scope_configure(self, cfg: dict) -> None:
        self._scope_cfg = cfg
        self._scope_dirty = True

    @pyqtSlot()
    def scope_acquire(self) -> None:
        if not self.moku:
            self.error.emit("Connect the Moku first.")
            self.scope_frame.emit(None)
            return
        try:
            if self.moku.kind != "Oscilloscope":
                log.info("Switching Moku to Oscilloscope ...")
                self._scope_dirty = True
            scope = self.moku.oscilloscope()
            if self._scope_dirty and self._scope_cfg:
                scope.configure(self._scope_cfg)
                self._scope_dirty = False
                self._emit_status()
            self.scope_frame.emit(scope.frame())
        except Exception as exc:
            log.error("Scope error: %s", exc)
            self._scope_dirty = True
            self.scope_frame.emit(None)


class ProcessingWorker(QObject):
    calibration_ready = pyqtSignal(object)              # Calibration
    measurement_ready = pyqtSignal(object, dict)        # MeasurementResult, context
    failed = pyqtSignal(str, str)                       # what, message

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings

    @pyqtSlot(str)
    def load_calibration(self, path: str) -> None:
        try:
            cal = load_calibration(path, self.settings.analysis)
            self.calibration_ready.emit(cal)
        except Exception as exc:
            log.error("Calibration failed: %s", exc)
            self.failed.emit("calibration", f"Could not use calibration {path}:\n{exc}")

    @pyqtSlot(str, object, dict)
    def analyse(self, path: str, cal, context: dict) -> None:
        try:
            raw = load_phasemeter_csv(path)
            res = analyse_measurement(raw, cal, self.settings.analysis)
            self.measurement_ready.emit(res, context)
        except Exception as exc:
            log.error("Analysis failed: %s", exc)
            self.failed.emit("measurement", f"Could not analyse {path}:\n{exc}")
