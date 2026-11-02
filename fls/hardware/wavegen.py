"""Teledyne LeCroy T3AFG waveform generator over VISA (SCPI identical to V1)."""

from __future__ import annotations

import time

from ..settings import SweepSettings


class VisaWavegen:
    def __init__(self, resource: str) -> None:
        import pyvisa
        self._rm = pyvisa.ResourceManager()
        self._res = self._rm.open_resource(resource)
        self._res.timeout = 5000
        self._res.write("*RST")
        self.output_on = False

    def identify(self) -> str:
        try:
            return self._res.query("*IDN?").strip()
        except Exception:
            return ""

    def configure_sweep(self, s: SweepSettings) -> None:
        """Sine sweep with manual trigger: the output idles at the start frequency
        until trigger() is called."""
        w = self._res.write
        w("C1:OUTPut LOAD,50")
        w(f"C1:SWeepWaVe CARR,AMP,{s.amplitude_vpp}")
        w(f"C1:SWeepWaVe CARR,OFST,{s.offset_v}")
        w("C1:SWeepWaVe CARR,WVTP,SINE")
        w(f"C1:SWeepWaVe TIME,{s.duration_s:g}")
        w(f"C1:SWeepWaVe STOP,{s.stop_hz:g}")
        w(f"C1:SWeepWaVe START,{s.start_hz:g}")
        w("C1:SWeepWaVe STATE,ON")
        w("C1:SWeepWaVe TRSR,MAN")
        time.sleep(0.1)

    def set_output(self, on: bool) -> None:
        self._res.write(f"C1:OUTPut {'ON' if on else 'OFF'}")
        self.output_on = on

    def trigger(self) -> None:
        self._res.write("C1:SWeepWaVe MTRIG")

    def close(self) -> None:
        try:
            if self.output_on:
                self.set_output(False)
            self._res.close()
        except Exception:
            pass
