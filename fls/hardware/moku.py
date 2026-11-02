"""Moku:Lab access (moku >= 4.4 API).

A Moku runs one instrument at a time. MokuDevice owns the connection and deploys
the Phasemeter or the Oscilloscope on demand; switching takes a few seconds.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .base import PhasemeterState

log = logging.getLogger(__name__)

PHASEMETER = "Phasemeter"
OSCILLOSCOPE = "Oscilloscope"


class MokuPhasemeter:
    def __init__(self, inst) -> None:
        self._i = inst

    def configure(self, cfg: dict) -> None:
        """cfg: see Settings.phasemeter_config()."""
        for ch in (1, 2):
            c = cfg[f"ch{ch}"]
            self._i.enable_input(ch, bool(c["enabled"]))
            if not c["enabled"]:
                continue
            self._i.set_frontend(ch, impedance=c["impedance"], coupling=c["coupling"],
                                 range=c["range"])
            self._i.set_pm_loop(ch, auto_acquire=bool(c["auto_acquire"]),
                                frequency=float(c["frequency"]), bandwidth=c["bandwidth"])
        self._i.set_phase_wrap(cfg["phase_wrap"])
        self._i.set_auto_reset(cfg["auto_reset"])
        self._i.reacquire()

    def reacquire(self) -> None:
        self._i.reacquire()

    def summary(self) -> str:
        try:
            return str(self._i.summary())
        except Exception:  # informative only
            return ""

    def live(self) -> dict:
        return dict(self._i.get_data())

    def start_stream(self, path: Path, duration_s: float, rate: str) -> None:
        self._i.start_streaming(duration=duration_s, acquisition_rate=rate)
        self._i.stream_to_file(str(Path(path).resolve()))

    def stream_status(self) -> str:
        return str(self._i.get_stream_status().get("status", "UNKNOWN"))

    def finish_stream(self, timeout_s: float) -> None:
        """Block until the file is completely written (raises on stream errors)."""
        self._i.wait_for_stream_to_file(timeout=timeout_s)

    def abort_stream(self) -> None:
        try:
            self._i.stop_streaming()
        except Exception as exc:
            log.debug("stop_streaming: %s", exc)


class MokuScope:
    def __init__(self, inst) -> None:
        self._i = inst

    def configure(self, cfg: dict) -> None:
        """cfg keys: ch{1,2}: {enabled, range, coupling, impedance}, span_s, points,
        trigger: {source, level, edge, mode}, acquisition_mode."""
        for ch in (1, 2):
            c = cfg[f"ch{ch}"]
            self._i.set_frontend(ch, impedance=c["impedance"], coupling=c["coupling"],
                                 range=c["range"])
            self._i.set_source(ch, f"Input{ch}" if c["enabled"] else "None")
        half = cfg["span_s"] / 2
        self._i.set_timebase(-half, half, max_length=int(cfg["points"]))
        trg = cfg["trigger"]
        self._i.set_trigger(type="Edge", source=trg["source"], level=float(trg["level"]),
                            edge=trg["edge"], mode=trg["mode"])
        self._i.set_acquisition_mode(cfg["acquisition_mode"])

    def frame(self, timeout_s: float = 2.0) -> dict | None:
        d = self._i.get_data(timeout=timeout_s)
        return dict(time=d["time"], ch1=d.get("ch1"), ch2=d.get("ch2"))


class MokuDevice(PhasemeterState):
    time_scale = 1.0   # real time

    def __init__(self, address: str, connect_timeout: float = 15.0) -> None:
        self.address = address
        self.connect_timeout = connect_timeout
        self._inst = None
        self.kind: str | None = None
        self._reset_pm_state()

    @property
    def connected(self) -> bool:
        return self._inst is not None

    def phasemeter(self) -> MokuPhasemeter:
        if self.kind != PHASEMETER:
            from moku.instruments import Phasemeter
            self._deploy(Phasemeter, PHASEMETER)
        return MokuPhasemeter(self._inst)

    def oscilloscope(self) -> MokuScope:
        if self.kind != OSCILLOSCOPE:
            from moku.instruments import Oscilloscope
            self._deploy(Oscilloscope, OSCILLOSCOPE)
        return MokuScope(self._inst)

    def _deploy(self, cls, kind: str) -> None:
        self.close()
        log.info("Deploying %s on Moku %s ...", kind, self.address)
        self._inst = cls(self.address, force_connect=True,
                         connect_timeout=self.connect_timeout)
        self.kind = kind

    def close(self) -> None:
        if self._inst is not None:
            try:
                self._inst.relinquish_ownership()
            except Exception as exc:
                log.debug("relinquish_ownership: %s", exc)
        self._inst = None
        self.kind = None
        self._reset_pm_state()
