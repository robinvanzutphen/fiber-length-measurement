"""Installation / instrument checks and instrument discovery.

Everything here is read-only: the Moku is queried with its public `describe` call (no
ownership is claimed), the generator only with `*IDN?`. Results come with a concrete fix
so users can solve problems without a developer.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from ..settings import Settings

URL_MOKU_SOFTWARE = "https://liquidinstruments.com/software/utilities/"
URL_MOKU_API = "https://apis.liquidinstruments.com/starting-python.html"
URL_NI_VISA = "https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html"
URL_RS_VISA = "https://www.rohde-schwarz.com/applications/r-s-visa-application-note_56280-148812.html"

DEFAULT_MOKUCLI = Path(r"C:\Program Files\Liquid Instruments\Moku CLI\mokucli.exe")
TELEDYNE_VENDOR = "0XF4ED"          # USB vendor id of the T3AFG generators
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0     # CREATE_NO_WINDOW

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"


@dataclass
class Check:
    name: str
    status: str             # ok / warn / fail / info
    detail: str
    fix: str = ""


# ----------------------------------------------------------------------------- helpers
def find_mokucli() -> str | None:
    for cand in (os.environ.get("MOKU_CLI_PATH"), shutil.which("mokucli"), str(DEFAULT_MOKUCLI)):
        if cand and Path(cand).is_file():
            return cand
    return None


def ensure_mokucli_env() -> None:
    """The moku package looks for mokucli via MOKU_CLI_PATH/PATH when it is imported;
    point it to the default install location if it is not on the PATH."""
    if not os.environ.get("MOKU_CLI_PATH") and (path := find_mokucli()):
        os.environ["MOKU_CLI_PATH"] = path


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env, creationflags=_NO_WINDOW)


def mokucli_version(path: str) -> str:
    out = _run([path, "--version"], 10).stdout.strip()
    m = re.search(r"\d+(\.\d+)+", out)
    return m.group(0) if m else out


def _version_tuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return ""


def describe_moku(address: str, timeout: float = 4.0) -> dict:
    """Public device description (firmware, MokuOS, instrument build). No ownership claimed."""
    import requests
    host = re.sub(r"%[^\]]*", "", address)        # a %zone suffix is rejected by the Moku
    r = requests.get(f"http://{host}/api/moku/describe", timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return data.get("data", data)


def instrument_files_dir(build: int, hardware: str) -> Path | None:
    from moku.utilities import get_bitstream_path
    hw = hardware.replace(":", "").lower()
    try:
        return get_bitstream_path(int(build), hw)
    except Exception:
        return None


def download_command(info: dict) -> list[str]:
    hw = info.get("hardware", "Moku:Lab").replace(":", "").lower()
    return ["instrument", "download", str(info.get("instruments_build", "")), "--hw-version", hw]


def launch_instrument_download(info: dict) -> None:
    """Run the download in its own console window (mokucli's progress display needs a
    real console) so the user sees progress and errors."""
    cli = find_mokucli()
    if not cli:
        raise FileNotFoundError("mokucli not found")
    args = [cli] + download_command(info)
    subprocess.Popen(["cmd", "/k"] + args, creationflags=subprocess.CREATE_NEW_CONSOLE)


# ----------------------------------------------------------------------------- discovery
@dataclass
class FoundMoku:
    name: str
    serial: str
    hardware: str
    firmware: str
    address: str            # ready to use in the settings


def discover_mokus(timeout: float = 4.0) -> list[FoundMoku]:
    found: list[FoundMoku] = []
    cli = find_mokucli()
    if cli:
        try:
            out = _run([cli, "list", "--timeout", str(timeout)], timeout + 15).stdout
            for line in out.splitlines():
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cells) == 6 and cells[1].isdigit():
                    name, serial, hw, fw, ip4, ip6 = cells
                    addr = ip4 or (f"[{ip6.split('%')[0]}]" if ip6 else "")
                    found.append(FoundMoku(name, serial, f"Moku:{hw}", fw, addr))
        except Exception:
            pass
    if not found:
        try:
            from moku.finder import Finder
            for m in Finder().find_all(timeout=timeout):
                addr = m.ipv4_addr or (f"[{m.ipv6_addr}]" if m.ipv6_addr else "")
                found.append(FoundMoku(m.name, str(m.serial), m.hw, str(m.fw), addr))
        except Exception:
            pass
    return found


@dataclass
class FoundVisa:
    resource: str
    identity: str
    is_teledyne: bool


def discover_visa() -> list[FoundVisa]:
    import pyvisa
    rm = pyvisa.ResourceManager()
    out = []
    for res in rm.list_resources():
        if not res.upper().startswith("USB"):
            continue
        idn = ""
        try:
            inst = rm.open_resource(res)
            inst.timeout = 2000
            idn = inst.query("*IDN?").strip()
            inst.close()
        except Exception:
            pass
        out.append(FoundVisa(res, idn, TELEDYNE_VENDOR in res.upper()))
    return sorted(out, key=lambda v: not v.is_teledyne)


# ----------------------------------------------------------------------------- checks
def run_checks(settings: Settings) -> tuple[list[Check], dict]:
    """Returns the checks and the Moku description (empty if unreachable)."""
    checks: list[Check] = []
    info: dict = {}
    frozen = getattr(sys, "frozen", False)
    from .. import __version__
    checks.append(Check("FLS software", INFO,
                        f"V{__version__} ({'executable' if frozen else 'Python ' + platform.python_version()})"
                        f", Windows {platform.release()}"))

    mokupkg = package_version("moku")
    if not frozen:
        missing = [p for p in ("moku", "pyvisa", "PyQt5", "pyqtgraph", "numpy", "pandas")
                   if not package_version(p)]
        checks.append(Check("Python packages", FAIL if missing else OK,
                            "missing: " + ", ".join(missing) if missing else
                            f"moku {mokupkg}, pyvisa {package_version('pyvisa')}, "
                            f"PyQt5 {package_version('PyQt5')}",
                            "pip install -r requirements.txt" if missing else ""))

    if settings.device.simulate:
        checks.append(Check("Simulation mode", INFO, "Instrument checks are skipped (simulated "
                                                     "instruments)."))
        return checks, info

    # mokucli
    cli = find_mokucli()
    if not cli:
        checks.append(Check("Moku CLI (mokucli)", FAIL, "Not found.",
                            f"Install 'Moku CLI' from {URL_MOKU_SOFTWARE} and restart FLS."))
    else:
        ver = mokucli_version(cli)
        old = _version_tuple(ver) < (4, 4)
        checks.append(Check("Moku CLI (mokucli)", FAIL if old else OK, f"{ver} - {cli}",
                            f"Version 4.4 or newer is required: update from {URL_MOKU_SOFTWARE}."
                            if old else ""))

    # VISA
    try:
        import pyvisa
        rm = pyvisa.ResourceManager()
        res = rm.list_resources()
        checks.append(Check("VISA library", OK, str(rm.visalib)))
        configured = settings.device.wavegen_visa
        if configured in res:
            checks.append(Check("Waveform generator", OK, configured))
        else:
            tele = [r for r in res if TELEDYNE_VENDOR in r.upper()]
            checks.append(Check(
                "Waveform generator", FAIL if not tele else WARN,
                f"Configured '{configured}' not found." + (f" Found: {', '.join(tele)}" if tele else ""),
                "Use 'Find instruments' to select it." if tele else
                "Switch the generator on and connect its USB cable (rear 'USB Device' port), "
                "then re-run the check."))
    except Exception as exc:
        checks.append(Check("VISA library", FAIL, f"Not available ({exc.__class__.__name__}).",
                            f"Install a VISA library: NI-VISA ({URL_NI_VISA}) or the smaller "
                            f"R&S VISA ({URL_RS_VISA}). Restart FLS afterwards."))

    # Moku
    addr = settings.device.moku_address
    try:
        info = describe_moku(addr)
        checks.append(Check("Moku reachable", OK,
                            f"{info.get('hardware')} at {addr}: MokuOS {info.get('mokuOS')}, "
                            f"firmware {info.get('firmware')}, instruments build "
                            f"{info.get('instruments_build')}"))
    except Exception as exc:
        checks.append(Check("Moku reachable", FAIL, f"No answer from {addr} ({exc.__class__.__name__}).",
                            "Switch the Moku on (wait ~1 min), connect USB, install the Moku app "
                            f"(USB driver) from {URL_MOKU_SOFTWARE}, then use 'Find instruments'."))

    if info:
        try:
            from moku.version import SUPPORTED_PROXY_VERSION
            proxy = int(float(info.get("proxy_version", 0)))
            supported = int(float(SUPPORTED_PROXY_VERSION))
            if proxy == supported:
                checks.append(Check("Moku firmware / Python package", OK,
                                    f"moku package {mokupkg} matches the firmware (API v{proxy})."))
            elif proxy > supported:
                checks.append(Check("Moku firmware / Python package", FAIL,
                                    f"Firmware is newer (API v{proxy}) than the moku package "
                                    f"{mokupkg} (API v{supported}).",
                                    "Update FLS (new executable) or run "
                                    "'pip install --upgrade moku'."))
            else:
                checks.append(Check("Moku firmware / Python package", FAIL,
                                    f"Firmware too old (API v{proxy}); this software needs MokuOS 4.4+.",
                                    "Update the Moku with the Moku desktop app."))
        except Exception as exc:
            checks.append(Check("Moku firmware / Python package", WARN, str(exc)))

        files = instrument_files_dir(info.get("instruments_build", 0), info.get("hardware", ""))
        cmd = "mokucli " + " ".join(download_command(info))
        checks.append(Check("Moku instrument files", OK if files else FAIL,
                            str(files) if files else
                            f"Missing for build {info.get('instruments_build')} "
                            f"(MokuOS {info.get('mokuOS')}).",
                            "" if files else f"Press 'Download instrument files', or run: {cmd}"))

    # data folder
    folder = Path(settings.workflow.measurement_folder)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".fls_write_test"
        probe.write_text("ok")
        probe.unlink()
        checks.append(Check("Measurement folder", OK, str(folder)))
    except Exception as exc:
        checks.append(Check("Measurement folder", FAIL, f"{folder}: {exc}",
                            "Choose another folder (header, 'Change…')."))
    return checks, info


def report(checks: list[Check]) -> str:
    sym = {OK: "OK  ", WARN: "WARN", FAIL: "FAIL", INFO: "INFO"}
    lines = []
    for c in checks:
        lines.append(f"[{sym[c.status]}] {c.name}: {c.detail}")
        if c.fix:
            lines.append(f"       -> {c.fix}")
    return "\n".join(lines)
