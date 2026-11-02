"""Oscilloscope frame analysis: amplitudes and the phase difference between the channels.

Both channels are fitted with a sine of a common frequency, x = A cos(2 pi f t - phi) + c,
using linear least squares at each trial frequency and a 1-D search for the frequency
that minimises the combined residual. This works with only one or two periods on
screen, where FFT-based estimates are too coarse.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MAX_POINTS = 1024


@dataclass
class ChannelStats:
    vpp: float
    mean: float
    rms_ac: float
    amplitude: float      # fitted sine amplitude (peak)
    phase_rad: float


@dataclass
class FrameAnalysis:
    frequency_hz: float
    ch1: ChannelStats
    ch2: ChannelStats
    dphi_deg: float       # phase(In1) - phase(In2), wrapped to (-180, 180]

    @property
    def delay_s(self) -> float:
        return self.dphi_deg / 360.0 / self.frequency_hz if self.frequency_hz > 0 else float("nan")


def _fit_at(t: np.ndarray, ys: list[np.ndarray], f: float):
    w = 2 * np.pi * f * t
    A = np.column_stack([np.cos(w), np.sin(w), np.ones_like(t)])
    coef, res, *_ = np.linalg.lstsq(A, np.column_stack(ys), rcond=None)
    resid = float(res.sum()) if res.size else float(((A @ coef - np.column_stack(ys)) ** 2).sum())
    return coef, resid


def _estimate_frequency(t: np.ndarray, y: np.ndarray) -> float:
    """Coarse estimate: peak of the zero-padded, windowed spectrum (noise-robust)."""
    dt = float(np.median(np.diff(t)))
    y = (y - y.mean()) * np.hanning(len(y))
    n = 16 * len(y)
    spec = np.abs(np.fft.rfft(y, n))
    freqs = np.fft.rfftfreq(n, dt)
    spec[freqs < 0.5 / (t[-1] - t[0])] = 0          # ignore DC / sub-period content
    return float(freqs[int(np.argmax(spec))])


def analyse_frame(t: np.ndarray, y1: np.ndarray, y2: np.ndarray,
                  f_hint: float | None = None) -> FrameAnalysis | None:
    t = np.asarray(t, float)
    y1 = np.asarray(y1, float)
    y2 = np.asarray(y2, float)
    ok = np.isfinite(t) & np.isfinite(y1) & np.isfinite(y2)
    t, y1, y2 = t[ok], y1[ok], y2[ok]
    if len(t) > MAX_POINTS:        # plenty for a sine fit; keeps the GUI responsive
        step = int(np.ceil(len(t) / MAX_POINTS))
        t, y1, y2 = t[::step], y1[::step], y2[::step]
    if len(t) < 16 or np.ptp(y1) <= 0 or np.ptp(y2) <= 0:
        return None

    f0 = f_hint or _estimate_frequency(t, y1)
    span = t[-1] - t[0]
    # search +-30% around the estimate, then refine around the best grid point
    grid = np.linspace(0.7 * f0, 1.3 * f0, 61)
    for _ in range(3):
        res = [_fit_at(t, [y1, y2], f)[1] for f in grid]
        k = int(np.argmin(res))
        lo, hi = grid[max(k - 1, 0)], grid[min(k + 1, len(grid) - 1)]
        grid = np.linspace(lo, hi, 21)
    f = float(grid[np.argmin([_fit_at(t, [y1, y2], g)[1] for g in grid])])
    if f <= 0 or f * span < 0.5:
        return None
    coef, _ = _fit_at(t, [y1, y2], f)

    def stats(y, c) -> ChannelStats:
        amp = float(np.hypot(c[0], c[1]))
        return ChannelStats(vpp=float(np.ptp(y)), mean=float(np.mean(y)),
                            rms_ac=float(np.std(y)), amplitude=amp,
                            phase_rad=float(np.arctan2(c[1], c[0])))

    s1, s2 = stats(y1, coef[:, 0]), stats(y2, coef[:, 1])
    d = np.degrees(s1.phase_rad - s2.phase_rad)
    d = (d + 180.0) % 360.0 - 180.0
    return FrameAnalysis(frequency_hz=f, ch1=s1, ch2=s2, dphi_deg=float(d))
