# PyInstaller spec for FLS V2 (folder build). Used by build_exe.bat.
import os

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
METADATA = sum((copy_metadata(p) for p in ("moku", "pyvisa", "PyQt5", "pyqtgraph", "numpy",
                                           "pandas")), [])

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    datas=[
        (os.path.join(ROOT, "fls", "ui", "assets"), os.path.join("fls", "ui", "assets")),
        (os.path.join(ROOT, "docs"), "docs"),
    ] + METADATA,
    hiddenimports=(collect_submodules("moku.instruments")
                   + collect_submodules("zeroconf")
                   + ["pyvisa.ctwrapper"]),
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "scipy", "PyQt6", "PySide2",
              "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FLS",
    console=False,          # windowed; use "FLS.exe --check" output file for diagnostics
    icon=os.path.join(ROOT, "packaging", "fls.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="FLS")
