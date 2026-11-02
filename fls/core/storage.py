"""Measurement folder layout, file naming and the results log.

<measurement folder>/
    calibrations/CAL_<YYYYmmdd_HHMMSS>.csv      raw phasemeter stream
    calibrations/CAL_<YYYYmmdd_HHMMSS>.json     settings + fit summary
    measurements/<name>_<YYYYmmdd_HHMMSS>.csv          single run
    measurements/<name>_<YYYYmmdd_HHMMSS>_r003.csv     run 3 of a repeated series
    measurements/<...>.json                     settings + analysis result
    results.csv                                 one row per analysed measurement
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np

RESULT_COLUMNS = ["timestamp", "name", "run", "delay_ps", "ci95_ps", "length_m",
                  "length_ci95_mm", "r2", "rms_mcyc", "steps_used", "steps_total",
                  "calibration", "file", "source", "warnings"]


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_name(name: str, default: str = "measurement") -> str:
    name = re.sub(r"[^A-Za-z0-9_.\- ]+", "", name).strip().replace(" ", "_")
    return name or default


class MeasurementFolder:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()

    @property
    def calibrations(self) -> Path:
        return self.root / "calibrations"

    @property
    def measurements(self) -> Path:
        return self.root / "measurements"

    @property
    def results_file(self) -> Path:
        return self.root / "results.csv"

    def ensure(self) -> None:
        for d in (self.root, self.calibrations, self.measurements):
            d.mkdir(parents=True, exist_ok=True)

    # ---- naming
    def new_calibration_path(self) -> Path:
        self.ensure()
        return self._unique(self.calibrations / f"CAL_{timestamp()}.csv")

    def new_measurement_path(self, name: str, series_ts: str | None = None,
                             run: int | None = None) -> Path:
        self.ensure()
        stem = f"{safe_name(name)}_{series_ts or timestamp()}"
        if run is not None:
            stem += f"_r{run:03d}"
        return self._unique(self.measurements / f"{stem}.csv")

    @staticmethod
    def _unique(path: Path) -> Path:
        i = 2
        candidate = path
        while candidate.exists():
            candidate = path.with_name(f"{path.stem}_{i}{path.suffix}")
            i += 1
        return candidate

    # ---- discovery
    def latest_calibration(self) -> Path | None:
        """Newest calibration in this folder (also finds V1-style CALIB_*.csv in the root)."""
        candidates = list(self.calibrations.glob("CAL_*.csv")) + list(self.root.glob("CALIB_*.csv"))
        candidates = [c for c in candidates if c.stat().st_size > 0]
        return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None

    # ---- results log
    def append_result(self, row: dict) -> None:
        self.ensure()
        new = not self.results_file.exists()
        with open(self.results_file, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=RESULT_COLUMNS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in RESULT_COLUMNS})

    def read_results(self) -> list[dict]:
        if not self.results_file.exists():
            return []
        with open(self.results_file, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))


def write_sidecar(data_path: Path, payload: dict) -> Path:
    """Write <data file>.json next to the raw data file."""
    out = Path(data_path).with_suffix(".json")
    out.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
    return out


def read_sidecar(data_path: Path) -> dict:
    p = Path(data_path).with_suffix(".json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, Path):
        return str(o)
    return str(o)
