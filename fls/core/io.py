"""Reading Moku Phasemeter stream files (CSV with a '%'-commented header)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

# Column positions used by V1 - only a fallback if the header cannot be parsed.
_FALLBACK = dict(t=0, f1=2, p1=3, a1=4, f2=7, p2=8, a2=9)
_COLUMN_NAMES = {
    "t": "Time (s)",
    "f1": "Input 1 Frequency (Hz)", "p1": "Input 1 Phase (cyc)", "a1": "Input 1 I (V)",
    "f2": "Input 2 Frequency (Hz)", "p2": "Input 2 Phase (cyc)", "a2": "Input 2 I (V)",
}


@dataclass
class RawData:
    """One phasemeter stream. Phase in cycles, frequency in Hz."""
    path: Path
    t: np.ndarray
    f1: np.ndarray
    p1: np.ndarray
    a1: np.ndarray
    f2: np.ndarray
    p2: np.ndarray
    a2: np.ndarray
    sample_rate: float
    acquired: str = ""
    header: list[str] = field(default_factory=list)

    @property
    def freq(self) -> np.ndarray:
        return 0.5 * (self.f1 + self.f2)

    @property
    def phase(self) -> np.ndarray:
        """Phase difference In1 - In2 in cycles (same convention as V1)."""
        return self.p1 - self.p2

    def __len__(self) -> int:
        return len(self.t)


def read_header(path: str | Path, max_lines: int = 50) -> list[str]:
    lines = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for _ in range(max_lines):
            line = fh.readline()
            if not line.startswith("%"):
                break
            lines.append(line[1:].strip())
    return lines


def load_phasemeter_csv(path: str | Path) -> RawData:
    path = Path(path)
    header = read_header(path)
    if not header:
        raise ValueError(f"{path.name} is not a Moku Phasemeter CSV file (no '%' header).")

    cols = dict(_FALLBACK)
    names = [c.strip() for c in header[-1].split(",")]
    if all(n in names for n in _COLUMN_NAMES.values()):
        cols = {k: names.index(v) for k, v in _COLUMN_NAMES.items()}

    data = pd.read_csv(path, comment="%", header=None, skipinitialspace=True,
                       on_bad_lines="skip", engine="c").to_numpy(dtype=float)
    data = data[np.all(np.isfinite(data[:, : max(cols.values()) + 1]), axis=1)]
    if len(data) < 10:
        raise ValueError(f"{path.name} contains no usable data rows.")

    rate = np.nan
    acquired = ""
    for line in header:
        if m := re.search(r"Acquisition rate:\s*([0-9.eE+-]+)", line):
            rate = float(m.group(1))
        if line.startswith("Acquired"):
            acquired = line.replace("Acquired", "").strip()
    if not np.isfinite(rate):
        rate = 1.0 / float(np.median(np.diff(data[:, cols["t"]])))

    return RawData(path=path, sample_rate=rate, acquired=acquired, header=header,
                   **{k: data[:, i] for k, i in cols.items()})
