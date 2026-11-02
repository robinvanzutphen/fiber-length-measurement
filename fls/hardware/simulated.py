"""Simulated Moku + waveform generator for demos, training and testing without hardware.

The simulated phasemeter writes a synthetic stream file in the real Moku CSV format,
including the artefacts the analysis has to cope with (step transients, single-sample
phase glitches, loss of signal after the sweep). Time runs 10x faster than real time.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np

from ..settings import SweepSettings
from .base import PhasemeterState

TIME_SCALE = 0.1
SIM_INTERNAL_DELAY_S = 2.0e-9      # system imbalance seen by the calibration
SIM_FUT_DELAY_S = 10.115e-9        # "fiber under test" (~2.07 m)
SIM_SCOPE_DELAY_S = 8.3e-9         # phase offset shown on the simulated scope


class _Lab:
    wavegen_output = False
    sweep = SweepSettings()
    sweep_t0: float | None = None      # time of the last trigger

    def sweep_elapsed(self) -> float | None:
        """Elapsed sweep time in real-instrument seconds (None if not triggered)."""
        if self.sweep_t0 is None:
            return None
        return (time.time() - self.sweep_t0) / TIME_SCALE

    def sweeping(self) -> bool:
        e = self.sweep_elapsed()
        return e is not None and e < self.sweep.duration_s

    def current_freq(self) -> float:
        e = self.sweep_elapsed()
        s = self.sweep
        if e is None:
            return s.start_hz
        n_steps = int(s.duration_s / 33.6e-3)
        k = min(int(e / 33.6e-3), n_steps)
        return s.start_hz + k * (s.stop_hz - s.start_hz) / n_steps


LAB = _Lab()
_BW_HZ = {"1Hz": 1, "10Hz": 10, "100Hz": 100, "1kHz": 1e3, "10kHz": 1e4, "100kHz": 1e5,
          "1MHz": 1e6}


def _nonlinear(f: np.ndarray) -> np.ndarray:
    """Smooth electronic phase response (cycles), similar in scale to the real system."""
    x = f / 1e8
    return 0.03 * np.sin(2.1 * x) + 0.01 * x ** 2


def synthesize_stream(path: Path, delay_s: float, sweep: SweepSettings, duration_s: float,
                      rate: float = 1907.3486328, seed: int | None = None) -> None:
    rng = np.random.default_rng(seed)
    n = int(duration_s * rate)
    t = np.arange(n) / rate
    t0 = 0.5                                        # trigger delay
    step_dt = 33.6e-3
    n_steps = int(sweep.duration_s / step_dt)
    k = np.clip(np.floor((t - t0) / step_dt), 0, n_steps).astype(int)
    f = sweep.start_hz + k * (sweep.stop_hz - sweep.start_hz) / n_steps
    f[t < t0] = sweep.start_hz
    # step transients: first sample of each step lies between two steps
    first = np.r_[False, np.diff(k) != 0]
    f_meas = f.copy()
    f_meas[first] -= 0.5 * (sweep.stop_hz - sweep.start_hz) / n_steps
    f1 = f_meas + rng.normal(0, 0.5, n)
    f2 = f_meas + rng.normal(0, 0.5, n)

    drift = rng.normal(0, 0.3e-12)                  # run-to-run delay jitter
    dphi = f * (delay_s + drift) + _nonlinear(f) + rng.normal(0, 1e-5, n)
    dphi[first] += rng.normal(0, 5e-4, first.sum())
    glitch = rng.random(n) < 2e-4
    common = np.mod(np.cumsum(f) / rate, 2 ** 22)   # accumulated phase, wraps like the Moku
    p2 = -common + rng.normal(0, 1e-6, n)
    p1 = p2 + dphi
    p1[glitch] += 64.0

    amp1 = np.full(n, 0.21) + rng.normal(0, 1e-4, n)
    amp2 = np.full(n, 0.22) + rng.normal(0, 1e-4, n)
    lost = t > t0 + sweep.duration_s
    amp1[lost] = amp2[lost] = 3e-6
    p1[lost] += rng.normal(0, 1e5, lost.sum())
    q = rng.normal(0, 1e-5, n)
    setf = np.full(n, sweep.start_hz)

    header = [
        "% Moku:Lab Phasemeter (SIMULATED)",
        "% Input 1, DC, 50 Ohm, 1 Vpp, simulated",
        "% Input 2, DC, 50 Ohm, 1 Vpp, simulated",
        f"% Acquisition rate: {rate:.10e} Hz",
        "% Advanced settings: phase wrapping at Off, phase auto-reset at Off",
        "% Output 1 (off)", "% Output 2 (off)", "% Internal 10 MHz clock",
        f"% Acquired {time.strftime('%Y-%m-%dT%H:%M:%S')} (simulated)",
        "% Time (s), Input 1 Set Frequency (Hz), Input 1 Frequency (Hz), Input 1 Phase (cyc), "
        "Input 1 I (V), Input 1 Q (V), Input 2 Set Frequency (Hz), Input 2 Frequency (Hz), "
        "Input 2 Phase (cyc), Input 2 I (V), Input 2 Q (V)",
    ]
    data = np.column_stack([t, setf, f1, p1, amp1, q, setf, f2, p2, amp2, q])
    tmp = Path(path).with_suffix(".partial")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(header) + "\n")
        np.savetxt(fh, data, fmt="%.16e", delimiter=", ")
    tmp.replace(path)


class SimPhasemeter:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._status = "IDLE"
        self._error = ""
        self._cancel = threading.Event()
        self.cfg: dict = {}
        self._rng = np.random.default_rng()
        self._acc = 0.0
        self._t_last = time.time()

    def configure(self, cfg: dict) -> None:
        self.cfg = cfg
        time.sleep(0.3)

    def reacquire(self) -> None:
        self._acc = 0.0

    def summary(self) -> str:
        return "Simulated Moku:Lab Phasemeter"

    def live(self) -> dict:
        time.sleep(0.03)
        now = time.time()
        dt, self._t_last = now - self._t_last, now
        f = LAB.current_freq()
        self._acc += f * dt
        out: dict = {"averaging_time": "8.39ms"}
        for ch, amp in ((1, 0.21), (2, 0.22)):
            c = self.cfg.get(f"ch{ch}", {})
            if not c.get("enabled", True):
                continue
            locked = LAB.wavegen_output and (_BW_HZ.get(c.get("bandwidth"), 1e5) >= 1e4
                                             or not LAB.sweeping())
            if locked:
                extra = f * SIM_SCOPE_DELAY_S + float(_nonlinear(np.array(f))) if ch == 1 else 0.0
                phase = self._acc + extra + self._rng.normal(0, 1e-5)
                out[f"ch{ch}"] = dict(frequency=f + self._rng.normal(0, 0.5),
                                      amplitude=amp + self._rng.normal(0, 1e-4),
                                      phase=float(phase))
            else:
                out[f"ch{ch}"] = dict(
                    frequency=c.get("frequency", 1e6) + self._rng.normal(0, 3e3),
                    amplitude=(0.02 if LAB.wavegen_output else 2e-6) * self._rng.random(),
                    phase=float(self._rng.uniform(-1e3, 1e3)))
        return out

    def start_stream(self, path: Path, duration_s: float, rate: str) -> None:
        is_cal = Path(path).name.upper().startswith("CAL")
        delay = SIM_INTERNAL_DELAY_S + (0.0 if is_cal else SIM_FUT_DELAY_S)
        sweep = LAB.sweep
        self._status, self._error = "RUNNING", ""
        self._cancel.clear()

        def worker():
            end = time.time() + duration_s * TIME_SCALE
            while time.time() < end:
                if self._cancel.is_set():
                    self._status = "CANCELLED"
                    return
                time.sleep(0.1)
            try:
                self._status = "FINALIZING"
                synthesize_stream(Path(path), delay, sweep, duration_s)
                self._status = "COMPLETED"
            except Exception as exc:
                self._error, self._status = str(exc), "ERROR"

        self._thread = threading.Thread(target=worker, daemon=True, name="SimStream")
        self._thread.start()

    def stream_status(self) -> str:
        return self._status

    def finish_stream(self, timeout_s: float) -> None:
        if self._thread:
            self._thread.join(timeout_s)
        if self._status != "COMPLETED":
            raise RuntimeError(f"Simulated stream ended with status {self._status} {self._error}")

    def abort_stream(self) -> None:
        self._cancel.set()


class SimScope:
    def __init__(self) -> None:
        self.cfg: dict = {}
        self._rng = np.random.default_rng()

    def configure(self, cfg: dict) -> None:
        self.cfg = cfg

    def frame(self, timeout_s: float = 2.0) -> dict | None:
        time.sleep(0.05)
        span = self.cfg.get("span_s", 3e-6)
        n = int(self.cfg.get("points", 1024))
        t = np.linspace(-span / 2, span / 2, n)
        f = LAB.sweep.start_hz
        jitter = self._rng.uniform(0, 1 / f)
        out = {"time": t.tolist()}
        for ch, (amp, dc, delay) in {1: (0.10, 0.21, 0.0), 2: (0.11, 0.22, SIM_SCOPE_DELAY_S)}.items():
            if not self.cfg.get(f"ch{ch}", {}).get("enabled", True):
                out[f"ch{ch}"] = None
                continue
            y = np.full(n, dc) if not LAB.wavegen_output else \
                dc + amp * np.cos(2 * np.pi * f * (t + jitter - delay))
            if self.cfg.get(f"ch{ch}", {}).get("coupling") == "AC":
                y = y - dc
            out[f"ch{ch}"] = (y + self._rng.normal(0, 2e-3, n)).tolist()
        return out


class SimMokuDevice(PhasemeterState):
    time_scale = TIME_SCALE

    def __init__(self) -> None:
        self.address = "simulated"
        self.kind: str | None = None
        self._pm = SimPhasemeter()
        self._scope = SimScope()
        self._reset_pm_state()

    @property
    def connected(self) -> bool:
        return self.kind is not None

    def phasemeter(self) -> SimPhasemeter:
        if self.kind != "Phasemeter":
            time.sleep(0.5)
            self.kind = "Phasemeter"
            self._reset_pm_state()
        return self._pm

    def oscilloscope(self) -> SimScope:
        if self.kind != "Oscilloscope":
            time.sleep(0.5)
            self.kind = "Oscilloscope"
            self._reset_pm_state()
        return self._scope

    def close(self) -> None:
        self.kind = None
        self._reset_pm_state()


class SimWavegen:
    def __init__(self) -> None:
        self.output_on = False

    def identify(self) -> str:
        return "Simulated T3AFG120"

    def configure_sweep(self, s: SweepSettings) -> None:
        LAB.sweep = s
        LAB.sweep_t0 = None

    def set_output(self, on: bool) -> None:
        self.output_on = on
        LAB.wavegen_output = on
        if not on:
            LAB.sweep_t0 = None

    def trigger(self) -> None:
        LAB.sweep_t0 = time.time()

    def close(self) -> None:
        self.set_output(False)
