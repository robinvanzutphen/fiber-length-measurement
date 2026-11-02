"""Write THIRD_PARTY_LICENSES.txt for the executable: license of every installed package.

Run with the build environment's Python:  python packaging/collect_licenses.py <output file>
"""

import sys
from importlib import metadata
from pathlib import Path

HEADER = """FLS V2 - third-party software included in this executable
===========================================================

FLS itself is licensed under the MIT license (see LICENSE in the source folder).

This executable bundles the Python runtime (PSF License) and the Python packages
listed below. PyQt5 is licensed under the GNU GPL v3; as a consequence this
executable as a whole is distributed under the terms of the GNU GPL v3. The
complete source code of FLS is included in the folder 'source'. The source code
of the third-party packages is available from https://pypi.org.

Not included (installed separately by the user): Moku desktop app, Moku CLI,
Moku instrument files (Liquid Instruments), VISA library, Omicron software.
"""

SKIP = {"pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib", "altgraph",
        "pefile", "pywin32-ctypes", "pytest", "pluggy", "iniconfig", "colorama", "fls"}


def main(out: str) -> None:
    parts = [HEADER]
    dists = sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower())
    for d in dists:
        name = d.metadata["Name"]
        if name.lower() in SKIP:
            continue
        lic = d.metadata.get("License-Expression") or d.metadata.get("License") or ""
        if len(lic) > 100:
            lic = ""
        cls = [c.split("::")[-1].strip() for c in d.metadata.get_all("Classifier") or []
               if c.startswith("License")]
        parts.append("=" * 78)
        parts.append(f"{name} {d.version}  -  {lic or ', '.join(cls) or 'see license text'}")
        home = d.metadata.get("Home-page") or ""
        if home:
            parts.append(home)
        for f in d.files or []:
            fname = Path(str(f)).name.upper()
            if fname.startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")):
                try:
                    parts.append("-" * 78)
                    parts.append(Path(d.locate_file(f)).read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    pass
    Path(out).write_text("\n".join(parts), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main(sys.argv[1])
