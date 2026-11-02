"""Instrument drivers. The factories return real or simulated hardware per the settings."""

from __future__ import annotations

from ..settings import Settings


def create_moku(settings: Settings):
    if settings.device.simulate:
        from .simulated import SimMokuDevice
        return SimMokuDevice()
    from .moku import MokuDevice
    return MokuDevice(settings.device.moku_address, settings.device.connect_timeout_s)


def create_wavegen(settings: Settings):
    if settings.device.simulate:
        from .simulated import SimWavegen
        return SimWavegen()
    from .wavegen import VisaWavegen
    return VisaWavegen(settings.device.wavegen_visa)
