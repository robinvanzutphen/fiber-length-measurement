"""Data processing: step extraction, calibration and delay/length analysis.

Protocol (replaces the V1 sample-by-sample filter)
--------------------------------------------------
The generator sweeps in discrete frequency steps (~29 kHz every ~34 ms). The
phasemeter samples each step many times (~64 samples at 1.91 kSa/s), plus a few
samples while the generator is stepping and the PLL re-locks.

1. Sample validity: inside the analysis band, both channels locked to the same
   frequency, and both signal amplitudes present.
2. Plateau detection: a sample is "stable" when the frequency differs by less than
   the tolerance from BOTH neighbours. Consecutive stable samples form one plateau
   (one sweep step); a few samples at each plateau edge are dropped as settling guard.
3. Per-plateau outlier rejection: samples whose phase deviates more than N robust
   sigma (MAD) from the plateau median are removed. This removes single-sample
   glitches (e.g. +64 cycle jumps) that slipped through the V1 diff-filter, and
   samples taken while the PLL was still settling.
4. Each plateau is reduced to one point (mean frequency, mean phase, std, count).
   Fitting on ~3500 independent step points is fast, insensitive to sampling
   artefacts, and gives realistic confidence intervals.
5. Calibration: order-N polynomial (scaled domain, numerically stable) with
   iterative robust sigma clipping. Steps rejected in the calibration mark
   unstable frequencies that are also excluded from measurements.
6. Measurement: calibration subtracted, then a linear fit with iterative robust
   clipping. Slope = delay; length = |delay| x group velocity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.polynomial import Polynomial

from ..settings import AnalysisSettings
from .io import RawData, load_phasemeter_csv

PHASE_FLOOR_CYC = 1e-5     # lower bound on the per-plateau robust sigma
MIN_GOOD_RATE = 1000.0     # Sa/s; below this the steps are poorly resolved
_MAD = 1.4826


def robust_sigma(x: np.ndarray) -> float:
    if len(x) == 0:
        return float("nan")
    return float(_MAD * np.median(np.abs(x - np.median(x))))


# ----------------------------------------------------------------------------- steps
@dataclass
class StepData:
    """One point per sweep step."""
    freq: np.ndarray
    phase: np.ndarray
    phase_std: np.ndarray
    count: np.ndarray
    stats: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.freq)


def extract_steps(raw: RawData, cfg: AnalysisSettings) -> StepData:
    f, p = raw.freq, raw.phase
    n = len(f)
    warnings: list[str] = []

    band = (f > cfg.f_min_hz) & (f < cfg.f_max_hz)
    if not band.any():
        raise ValueError("No data inside the analysis band - was the sweep running?")
    a1_ref = np.median(raw.a1[band])
    a2_ref = np.median(raw.a2[band])
    valid = (band & np.isfinite(p)
             & (np.abs(raw.f1 - raw.f2) < cfg.channel_tol_hz)
             & (raw.a1 > cfg.min_amplitude_frac * a1_ref)
             & (raw.a2 > cfg.min_amplitude_frac * a2_ref))

    d = np.abs(np.diff(f))
    stable = np.zeros(n, dtype=bool)
    stable[1:-1] = (d[:-1] < cfg.step_tol_hz) & (d[1:] < cfg.step_tol_hz)
    plateau_id = np.cumsum(~stable)

    min_samples = cfg.min_plateau_samples
    guard = cfg.edge_guard_samples
    if raw.sample_rate < MIN_GOOD_RATE:
        warnings.append(f"Low acquisition rate ({raw.sample_rate:.0f} Sa/s): sweep steps are "
                        f"poorly resolved. Use 1.91 kSa/s.")
        min_samples, guard = 1, 0

    keep = stable & valid
    df = pd.DataFrame({"pid": plateau_id[keep], "f": f[keep], "p": p[keep]})
    g = df.groupby("pid", sort=False)
    if guard > 0:
        pos = g.cumcount()
        pos_rev = g.cumcount(ascending=False)
        df = df[(pos >= guard) & (pos_rev >= guard)]
        g = df.groupby("pid", sort=False)

    med = g["p"].transform("median")
    dev = (df["p"] - med).abs()
    sigma = np.maximum(_MAD * dev.groupby(df["pid"]).transform("median"), PHASE_FLOOR_CYC)
    inlier = dev <= cfg.outlier_sigma * sigma
    n_glitch = int((~inlier).sum())
    df = df[inlier]

    agg = df.groupby("pid", sort=True).agg(f=("f", "mean"), p=("p", "mean"),
                                           s=("p", "std"), n=("p", "size"))
    agg = agg[agg["n"] >= min_samples].sort_values("f")
    if len(agg) < 50:
        raise ValueError(f"Only {len(agg)} usable sweep steps found - check signal and sweep.")

    stats = dict(samples=n, valid_samples=int(valid.sum()), stable_samples=int(keep.sum()),
                 rejected_outlier_samples=n_glitch, steps=len(agg),
                 median_samples_per_step=float(agg["n"].median()),
                 sample_rate=raw.sample_rate, warnings=warnings)
    return StepData(freq=agg["f"].to_numpy(), phase=agg["p"].to_numpy(),
                    phase_std=agg["s"].fillna(0).to_numpy(), count=agg["n"].to_numpy(),
                    stats=stats)


# ----------------------------------------------------------------------------- calibration
@dataclass
class Calibration:
    path: Path
    acquired: str
    poly: Polynomial
    order: int
    f_min: float
    f_max: float
    steps: StepData
    used: np.ndarray            # mask over steps
    residual: np.ndarray        # phase - poly(freq), all steps
    rms_cycles: float

    @property
    def rejected_freqs(self) -> np.ndarray:
        return self.steps.freq[~self.used]

    def summary(self) -> dict:
        return dict(file=str(self.path), acquired=self.acquired, poly_order=self.order,
                    f_min_hz=self.f_min, f_max_hz=self.f_max, steps=len(self.steps),
                    steps_used=int(self.used.sum()), rms_cycles=self.rms_cycles,
                    sample_rate=self.steps.stats.get("sample_rate"),
                    warnings=list(self.steps.stats.get("warnings", [])))


def fit_calibration(steps: StepData, cfg: AnalysisSettings, path: Path = Path(""),
                    acquired: str = "", max_iter: int = 10) -> Calibration:
    f, p = steps.freq, steps.phase
    used = np.ones(len(f), dtype=bool)
    for _ in range(max_iter):
        poly = Polynomial.fit(f[used], p[used], cfg.poly_order)
        r = p - poly(f)
        s = max(robust_sigma(r[used]), 1e-9)
        new = np.abs(r - np.median(r[used])) <= cfg.outlier_sigma * s
        if np.array_equal(new, used):
            break
        used = new
    poly = Polynomial.fit(f[used], p[used], cfg.poly_order)
    r = p - poly(f)
    return Calibration(path=Path(path), acquired=acquired, poly=poly, order=cfg.poly_order,
                       f_min=float(f[used].min()), f_max=float(f[used].max()), steps=steps,
                       used=used, residual=r, rms_cycles=float(np.std(r[used])))


def load_calibration(path: str | Path, cfg: AnalysisSettings) -> Calibration:
    raw = load_phasemeter_csv(path)
    return fit_calibration(extract_steps(raw, cfg), cfg, path=raw.path, acquired=raw.acquired)


# ----------------------------------------------------------------------------- measurement
@dataclass
class MeasurementResult:
    path: Path
    acquired: str
    calibration_path: Path
    steps: StepData
    in_range: np.ndarray        # steps that could be compared to the calibration
    used: np.ndarray            # steps used in the final linear fit
    calibrated: np.ndarray      # phase - calibration, all steps (nan outside range)
    residual: np.ndarray        # calibrated - linear fit
    slope_s: float              # d(phase)/d(f) in cycles/Hz = seconds
    intercept: float
    stderr_s: float
    r2: float
    rms_cycles: float
    velocity_m_s: float
    warnings: list[str]

    @property
    def delay_s(self) -> float:
        return abs(self.slope_s)

    @property
    def ci95_s(self) -> float:
        return 1.96 * self.stderr_s

    @property
    def length_m(self) -> float:
        return self.delay_s * self.velocity_m_s

    @property
    def length_ci95_m(self) -> float:
        return self.ci95_s * self.velocity_m_s

    def fit_line(self, f: np.ndarray) -> np.ndarray:
        return self.slope_s * f + self.intercept

    def summary(self) -> dict:
        return dict(file=str(self.path), acquired=self.acquired,
                    calibration=str(self.calibration_path),
                    delay_ps=self.delay_s * 1e12, ci95_ps=self.ci95_s * 1e12,
                    length_m=self.length_m, length_ci95_mm=self.length_ci95_m * 1e3,
                    slope_ns=self.slope_s * 1e9, r2=self.r2, rms_mcyc=self.rms_cycles * 1e3,
                    steps_used=int(self.used.sum()), steps_total=len(self.steps),
                    velocity_m_s=self.velocity_m_s, warnings=list(self.warnings),
                    step_stats={k: v for k, v in self.steps.stats.items() if k != "warnings"})


def analyse_measurement(raw: RawData, cal: Calibration, cfg: AnalysisSettings,
                        max_iter: int = 10) -> MeasurementResult:
    steps = extract_steps(raw, cfg)
    f, p = steps.freq, steps.phase
    warnings = list(steps.stats.get("warnings", []))

    in_range = (f >= cal.f_min) & (f <= cal.f_max)
    rejected = np.sort(cal.rejected_freqs)
    if len(rejected) and len(cal.steps) > 1:
        tol = 0.5 * float(np.median(np.diff(cal.steps.freq)))
        idx = np.clip(np.searchsorted(rejected, f), 1, len(rejected)) - 1
        near = np.minimum(np.abs(f - rejected[idx]),
                          np.abs(f - rejected[np.minimum(idx + 1, len(rejected) - 1)]))
        in_range &= near > tol
    if in_range.sum() < 50:
        raise ValueError("Measurement and calibration frequency ranges hardly overlap.")

    y = np.full(len(f), np.nan)
    y[in_range] = p[in_range] - cal.poly(f[in_range])

    used = in_range.copy()
    for _ in range(max_iter):
        a, b = np.polyfit(f[used], y[used], 1)
        r = y - (a * f + b)
        s = max(robust_sigma(r[used]), 1e-9)
        new = in_range & (np.abs(r - np.median(r[used])) <= cfg.outlier_sigma * s)
        if np.array_equal(new, used):
            break
        used = new

    fx, fy = f[used], y[used]
    n = len(fx)
    (a, b), cov = np.polyfit(fx, fy, 1, cov="unscaled")
    resid = fy - (a * fx + b)
    dof = max(n - 2, 1)
    s2 = float(resid @ resid) / dof
    stderr = float(np.sqrt(s2 * cov[0, 0]))
    ss_tot = float(((fy - fy.mean()) ** 2).sum())
    r2 = 1.0 - float(resid @ resid) / ss_tot if ss_tot > 0 else float("nan")

    frac = n / max(len(f), 1)
    if frac < 0.7:
        warnings.append(f"Only {frac:.0%} of the sweep steps were usable.")
    rms = float(np.std(resid))
    if rms > 5e-4:
        warnings.append(f"High residual ({rms * 1e3:.2f} mcycles): check fibers, signal "
                        f"levels and calibration.")

    return MeasurementResult(path=raw.path, acquired=raw.acquired, calibration_path=cal.path,
                             steps=steps, in_range=in_range, used=used, calibrated=y,
                             residual=y - (a * f + b), slope_s=float(a), intercept=float(b),
                             stderr_s=stderr, r2=r2, rms_cycles=rms,
                             velocity_m_s=cfg.velocity_m_s, warnings=warnings)
