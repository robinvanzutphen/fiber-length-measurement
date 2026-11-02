"""State shared by the real and simulated Moku device classes.

The BNC cables have to be reconnected after the phasemeter front-end/PLL has been set up.
The device therefore remembers which phasemeter configuration is applied and for which
configuration the cable reconnection was confirmed. Both are cleared whenever the
Phasemeter is (re)deployed, e.g. after connecting or after using the oscilloscope.
"""

from __future__ import annotations

import copy


class PhasemeterState:
    def _reset_pm_state(self) -> None:
        self.pm_applied: dict | None = None
        self.pm_cable_ok: dict | None = None

    def ensure_phasemeter(self, cfg: dict):
        """Deploy the Phasemeter if needed and apply `cfg` only if it differs from the
        configuration already on the device. Returns the phasemeter."""
        pm = self.phasemeter()
        if cfg != self.pm_applied:
            pm.configure(cfg)
            self.pm_applied = copy.deepcopy(cfg)
        return pm

    def needs_cable_prompt(self) -> bool:
        return self.pm_applied is None or self.pm_cable_ok != self.pm_applied

    def mark_cables_ok(self) -> None:
        self.pm_cable_ok = copy.deepcopy(self.pm_applied)
