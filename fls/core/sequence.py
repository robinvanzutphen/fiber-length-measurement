"""Acquisition sequence (calibration or measurement, single or repeated).

Per run - same order as V1:
  1. Configure phasemeter front-end + PLL (lock at the sweep start frequency). Skipped
     when exactly this configuration is already on the device.
  2. Configure the generator sweep (manual trigger) and switch its output on;
     the output idles at the start frequency.
  3. Re-acquire the PLL lock.
  4. Pause: the user reconnects the BNC cables and confirms. Only needed after the
     phasemeter was (re)deployed or its configuration changed (or if always requested).
  5. Re-acquire, start streaming to file, trigger the sweep.
  6. Wait for the stream to complete and the file to be fully written.
  7. Generator output off (always, also on error/abort).

This module is free of Qt so it can be tested and scripted; the GUI talks to it through
the callbacks and the thread-safe abort()/confirm_cable() methods.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..settings import Settings
from .storage import MeasurementFolder, timestamp, write_sidecar

log = logging.getLogger(__name__)

CALIBRATION = "calibration"
MEASUREMENT = "measurement"


class Aborted(Exception):
    pass


@dataclass
class RunRequest:
    kind: str = MEASUREMENT
    name: str = ""
    repeats: int = 1            # 0 = until stopped
    interval_s: float = 0.0     # pause between repeats

    @property
    def is_series(self) -> bool:
        return self.kind == MEASUREMENT and self.repeats != 1


@dataclass
class SequenceCallbacks:
    stage: Callable[[str], None] = lambda text: None
    progress: Callable[[float, float, float], None] = lambda frac, elapsed, remaining: None
    request_cable: Callable[[int, int], None] = lambda run, total: None
    run_finished: Callable[[str, str, dict], None] = lambda kind, path, meta: None


class AcquisitionSequence:
    POLL_S = 0.25

    def __init__(self, moku, wavegen, settings: Settings, folder: MeasurementFolder,
                 callbacks: SequenceCallbacks) -> None:
        self.moku = moku
        self.wavegen = wavegen
        self.s = settings
        self.folder = folder
        self.cb = callbacks
        self._abort = threading.Event()
        self._cable = threading.Event()
        self._cable_ok = False

    # ---- thread-safe controls (called from the GUI thread)
    def abort(self) -> None:
        self._abort.set()
        self._cable.set()

    def confirm_cable(self, proceed: bool) -> None:
        self._cable_ok = proceed
        self._cable.set()

    # ---- helpers
    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._abort.is_set():
                raise Aborted()
            time.sleep(min(self.POLL_S, max(end - time.monotonic(), 0)))

    def _check_abort(self) -> None:
        if self._abort.is_set():
            raise Aborted()

    # ---- public
    def run(self, req: RunRequest) -> int:
        """Run the request; returns the number of completed runs. Raises Aborted."""
        self._abort.clear()
        total = req.repeats if req.kind == MEASUREMENT else 1
        series_ts = timestamp() if req.is_series else None
        done = 0
        i = 0
        while total == 0 or i < total:
            i += 1
            self._check_abort()
            if req.kind == CALIBRATION:
                path = self.folder.new_calibration_path()
            else:
                path = self.folder.new_measurement_path(req.name, series_ts,
                                                        i if req.is_series else None)
            meta = self._run_once(req, path, i, total)
            done += 1
            self.cb.run_finished(req.kind, str(path), meta)
            if (total == 0 or i < total) and req.interval_s > 0:
                self.cb.stage(f"Waiting {req.interval_s:.0f} s before run {i + 1}")
                self._sleep(req.interval_s)
        return done

    def _run_once(self, req: RunRequest, path: Path, run: int, total: int) -> dict:
        s = self.s
        tag = f"run {run}/{total}" if total != 1 else ("calibration" if req.kind == CALIBRATION
                                                         else "measurement")
        started = time.strftime("%Y-%m-%d %H:%M:%S")
        log.info("Starting %s -> %s", tag, path.name)
        try:
            self.cb.stage(f"Configuring phasemeter ({tag})")
            pm = self.moku.ensure_phasemeter(s.phasemeter_config())
            ask_cable = s.workflow.always_prompt_cable or self.moku.needs_cable_prompt()
            self._check_abort()

            self.cb.stage("Starting waveform generator")
            self.wavegen.configure_sweep(s.sweep)
            self.wavegen.set_output(True)
            self._sleep(1.0)
            pm.reacquire()

            if ask_cable:
                self.cb.stage("Waiting for cable reconnection")
                self._cable.clear()
                self._cable_ok = False
                self.cb.request_cable(run, total)
                while not self._cable.wait(self.POLL_S):
                    pass
                self._check_abort()
                if not self._cable_ok:
                    raise Aborted()
                self.moku.mark_cables_ok()
                log.info("Cable reconnection confirmed.")
            else:
                log.info("Phasemeter unchanged since cable reconnection - no prompt needed.")

            pm.reacquire()
            self._sleep(0.5)
            summary = pm.summary()

            self.cb.stage(f"Sweeping ({tag})")
            stream_s = s.stream_seconds
            expected = stream_s * self.moku.time_scale
            pm.start_stream(path, stream_s, s.phasemeter.acquisition_rate)
            self.wavegen.trigger()
            t0 = time.monotonic()
            last_poll = 0.0
            status = "RUNNING"
            while True:
                elapsed = time.monotonic() - t0
                frac = min(elapsed / expected, 1.0)
                self.cb.progress(frac, elapsed, max(expected - elapsed, 0.0))
                if self._abort.is_set():
                    pm.abort_stream()
                    raise Aborted()
                if elapsed - last_poll >= 1.0:
                    last_poll = elapsed
                    status = pm.stream_status()
                    if status in ("COMPLETED", "CANCELLED", "ERROR"):
                        break
                if elapsed > expected + 60:
                    raise TimeoutError("Streaming did not finish in time.")
                time.sleep(self.POLL_S)
            if status != "COMPLETED":
                raise RuntimeError(f"Phasemeter stream ended with status {status}.")

            self.cb.stage("Saving data")
            pm.finish_stream(timeout_s=60)
            if not path.exists() or path.stat().st_size == 0:
                raise RuntimeError(f"Data file was not written: {path}")
        finally:
            try:
                self.wavegen.set_output(False)
            except Exception as exc:
                log.warning("Could not switch generator output off: %s", exc)

        meta = dict(kind=req.kind, name=req.name, run=run if req.is_series else None,
                    started=started, finished=time.strftime("%Y-%m-%d %H:%M:%S"),
                    raw_file=path.name, instrument_summary=summary, settings=s.snapshot())
        write_sidecar(path, {"acquisition": meta})
        log.info("Saved %s", path)
        return meta
