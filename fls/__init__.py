"""FLS - Fiber Length System (V2).

Measures the optical transfer delay / length of fibers with a swept intensity
modulation and a PLL phasemeter, following van Zutphen et al., Opt. Lett. 50(2), 2025.
"""

import sys
from pathlib import Path

__version__ = "2.0.0"
APP_NAME = "FLS"
APP_TITLE = "Fiber Length Measurement"
PAPER_DOI = "https://doi.org/10.1364/OL.546043"


def resource_path(relative: str) -> Path:
    """Files shipped next to the package (docs, ...): the repository root when running from
    source, the unpacked bundle when running as executable."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / relative
