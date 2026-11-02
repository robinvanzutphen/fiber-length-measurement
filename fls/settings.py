"""Application settings: typed dataclasses persisted as JSON in the user's app-data folder.

Every field carries a label/help text in its metadata so the settings dialog can be
generated automatically. Unknown or invalid keys in the JSON file are ignored, so old
settings files never prevent the program from starting.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

log = logging.getLogger(__name__)

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "FLS"
SETTINGS_FILE = Path(os.environ.get("FLS_SETTINGS", APP_DIR / "settings.json"))
LOG_DIR = APP_DIR / "logs"


def _f(default, label: str, help: str = "", choices: tuple | None = None, unit: str = "",
       advanced: bool = False):
    """Dataclass field with UI metadata."""
    meta = dict(label=label, help=help, choices=choices, unit=unit, advanced=advanced)
    if isinstance(default, (list, dict)):
        return field(default_factory=lambda: type(default)(default), metadata=meta)
    return field(default=default, metadata=meta)


@dataclass
class DeviceSettings:
    moku_address: str = _f("[fe80::7269:79ff:feb7:2ff]", "Moku address",
                           "IPv4 address, or IPv6 link-local address in brackets for USB.")
    wavegen_visa: str = _f("USB0::0xF4ED::0xEE3A::T0102C23370246::INSTR", "Waveform generator VISA",
                           "VISA resource string of the Teledyne T3AFG waveform generator.")
    simulate: bool = _f(False, "Simulated hardware",
                        "Run without instruments (demo / training mode). Data is synthetic.")
    connect_timeout_s: float = _f(15.0, "Moku connect timeout", unit="s", advanced=True)


@dataclass
class SweepSettings:
    """Waveform generator sweep - identical to V1."""
    start_hz: float = _f(1e6, "Sweep start", unit="Hz",
                         help="The generator idles at this frequency until the sweep is triggered.")
    stop_hz: float = _f(120e6, "Sweep stop", unit="Hz")
    duration_s: float = _f(120.0, "Sweep duration", unit="s")
    amplitude_vpp: float = _f(0.6, "Amplitude", unit="Vpp")
    offset_v: float = _f(0.6, "DC offset", unit="V")


@dataclass
class PhasemeterSettings:
    coupling: str = _f("DC", "Input coupling", choices=("DC", "AC"))
    impedance: str = _f("50Ohm", "Input impedance", choices=("50Ohm", "1MOhm"))
    input_range: str = _f("1Vpp", "Input range", choices=("1Vpp", "10Vpp"))
    pll_bandwidth: str = _f("100kHz", "PLL bandwidth",
                            choices=("1kHz", "10kHz", "100kHz"),
                            help="Should not exceed 1/10 of the tracking (start) frequency.")
    acquisition_rate: str = _f("1.91kSa/s", "Acquisition rate",
                               choices=("477Sa/s", "1.91kSa/s", "15.3kSa/s"),
                               help="1.91 kSa/s gives ~60 samples per frequency step (paper setting).")
    stream_extra_s: float = _f(10.0, "Extra streaming time", unit="s", advanced=True,
                               help="Streaming continues this long after the sweep duration.")


@dataclass
class AnalysisSettings:
    f_min_hz: float = _f(10e6, "Analysis band start", unit="Hz")
    f_max_hz: float = _f(115e6, "Analysis band stop", unit="Hz")
    poly_order: int = _f(20, "Calibration polynomial order")
    velocity_m_s: float = _f(2.05e8, "Group velocity in fiber", unit="m/s",
                             help="Length = delay x velocity. 2.05e8 m/s corresponds to n_g = 1.4624.")
    step_tol_hz: float = _f(100.0, "Plateau frequency tolerance", unit="Hz", advanced=True,
                            help="Neighbouring samples closer than this belong to the same sweep step.")
    channel_tol_hz: float = _f(100.0, "Max. In1/In2 frequency mismatch", unit="Hz", advanced=True)
    min_plateau_samples: int = _f(5, "Min. samples per step", advanced=True)
    edge_guard_samples: int = _f(1, "Samples dropped at step edges", advanced=True)
    outlier_sigma: float = _f(5.0, "Outlier threshold", unit="robust sigma", advanced=True)
    min_amplitude_frac: float = _f(0.2, "Min. amplitude (fraction of median)", advanced=True)
    cal_max_age_h: float = _f(8.0, "Calibration age warning", unit="h",
                              help="Warn when the active calibration is older than this.")


@dataclass
class WorkflowSettings:
    measurement_folder: str = _f(str(Path.home() / "fiber_measurements"), "Measurement folder")
    always_prompt_cable: bool = _f(False, "Cable prompt before every run",
                                   help="Normally the cable prompt only appears after the "
                                        "phasemeter was (re)deployed or its settings changed.")
    last_name: str = _f("", "Last measurement name", advanced=True)
    last_calibration: str = _f("", "Last calibration file", advanced=True)


@dataclass
class Settings:
    device: DeviceSettings = field(default_factory=DeviceSettings)
    sweep: SweepSettings = field(default_factory=SweepSettings)
    phasemeter: PhasemeterSettings = field(default_factory=PhasemeterSettings)
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)
    workflow: WorkflowSettings = field(default_factory=WorkflowSettings)

    SECTIONS = ("device", "sweep", "phasemeter", "analysis", "workflow")

    @property
    def stream_seconds(self) -> float:
        return self.sweep.duration_s + self.phasemeter.stream_extra_s

    def phasemeter_config(self) -> dict:
        """Phasemeter configuration used for measurements (both channels identical,
        PLL locked at the sweep start frequency)."""
        p = self.phasemeter
        ch = dict(enabled=True, coupling=p.coupling, impedance=p.impedance, range=p.input_range,
                  auto_acquire=False, frequency=float(self.sweep.start_hz), bandwidth=p.pll_bandwidth)
        return dict(ch1=dict(ch), ch2=dict(ch), phase_wrap="Off", auto_reset="Off")

    # ---- persistence
    @classmethod
    def load(cls, path: Path = SETTINGS_FILE) -> "Settings":
        s = cls()
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return s
        except Exception as exc:  # corrupt file: start with defaults
            log.warning("Could not read settings (%s); using defaults.", exc)
            return s
        for section in cls.SECTIONS:
            obj = getattr(s, section)
            for f in fields(obj):
                if f.name in data.get(section, {}):
                    value = data[section][f.name]
                    try:
                        setattr(obj, f.name, type(getattr(obj, f.name))(value))
                    except (TypeError, ValueError):
                        log.warning("Ignoring invalid setting %s.%s=%r", section, f.name, value)
        return s

    def save(self, path: Path = SETTINGS_FILE) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {section: asdict(getattr(self, section)) for section in self.SECTIONS}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)

    def snapshot(self) -> dict:
        """Plain dict copy, stored next to every data file for traceability."""
        return {section: asdict(getattr(self, section)) for section in self.SECTIONS}
