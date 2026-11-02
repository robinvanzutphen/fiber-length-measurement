"""Application entry point: logging, theme, main window."""

from __future__ import annotations

import argparse
import logging
import sys
import traceback
from datetime import datetime

from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtWidgets import QApplication, QMessageBox

from . import APP_NAME
from .settings import APP_DIR, LOG_DIR, SETTINGS_FILE, Settings


class LogBridge(QObject, logging.Handler):
    """Forwards log records to the GUI (thread-safe via a queued signal)."""
    message = pyqtSignal(str)

    def __init__(self) -> None:
        QObject.__init__(self)
        logging.Handler.__init__(self, logging.INFO)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s - %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.message.emit(self.format(record))
        except RuntimeError:   # GUI already gone
            pass


def setup_logging() -> LogBridge:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(LOG_DIR / f"fls_{datetime.now():%Y%m%d}.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s - %(message)s"))
    root.addHandler(fh)
    if sys.stderr:      # a windowed executable has no console
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s - %(message)s",
                                               "%H:%M:%S"))
        root.addHandler(console)
    bridge = LogBridge()
    root.addHandler(bridge)
    logging.getLogger("moku").setLevel(logging.WARNING)
    logging.getLogger("pyvisa").setLevel(logging.WARNING)
    return bridge


def _excepthook(exc_type, exc, tb) -> None:
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    logging.getLogger(APP_NAME).error("Unhandled error:\n%s", text)
    if QApplication.instance():
        QMessageBox.critical(None, "FLS - unexpected error",
                             f"{exc_type.__name__}: {exc}\n\nDetails were written to the log "
                             f"({LOG_DIR}).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fiber length measurement (FLS V2)")
    parser.add_argument("--simulate", action="store_true",
                        help="use simulated instruments for this session")
    parser.add_argument("--check", action="store_true",
                        help="run the system check, write it to %%APPDATA%%\\FLS\\system_check.txt "
                             "(and the console) and exit")
    args = parser.parse_args(argv)

    from .core.system_check import ensure_mokucli_env
    ensure_mokucli_env()            # before the moku package is imported anywhere
    first_run = not SETTINGS_FILE.exists()

    if args.check:
        from .core.system_check import FAIL, report, run_checks
        checks, _ = run_checks(Settings.load())
        text = report(checks)
        APP_DIR.mkdir(parents=True, exist_ok=True)
        (APP_DIR / "system_check.txt").write_text(text, encoding="utf-8")
        if sys.stdout:
            print(text)
        return 1 if any(c.status == FAIL for c in checks) else 0

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)

    bridge = setup_logging()
    sys.excepthook = _excepthook

    from .ui import theme
    from .ui.main_window import MainWindow

    theme.apply(app)
    from PyQt5.QtGui import QIcon
    from pathlib import Path
    app.setWindowIcon(QIcon(str(Path(theme.__file__).parent / "assets" / "fls.png")))
    settings = Settings.load()
    if args.simulate:
        settings.device.simulate = True
    win = MainWindow(settings, bridge, first_run=first_run and not args.simulate)
    win.show()
    return app.exec_()
