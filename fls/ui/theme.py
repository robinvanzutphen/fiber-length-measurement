"""Colours, Qt stylesheet and pyqtgraph defaults."""

from pathlib import Path

from PyQt5.QtGui import QColor, QFont, QPalette
from PyQt5.QtWidgets import QApplication

ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
OK = "#16a34a"
WARN = "#d97706"
BAD = "#dc2626"
MUTED = "#6b7280"
TEXT = "#1f2937"
BG = "#f3f5f9"
CARD = "#ffffff"
BORDER = "#dfe3ea"

CH1 = "#2563eb"
CH2 = "#ea580c"
FIT = "#dc2626"
REJECTED = "#b8bec8"

_ASSETS = (Path(__file__).parent / "assets").as_posix()

STYLESHEET = f"""
QWidget {{ color: {TEXT}; font-family: "Segoe UI"; font-size: 10pt; }}
QMainWindow, QDialog {{ background: {BG}; }}
QFrame#Card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#Header {{ background: {CARD}; border-bottom: 1px solid {BORDER}; }}
QLabel#AppTitle {{ font-size: 15pt; font-weight: 600; }}
QLabel#CardTitle {{ font-size: 11pt; font-weight: 600; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#BigValue {{ font-size: 22pt; font-weight: 600; }}
QLabel#MidValue {{ font-size: 13pt; font-weight: 600; }}
QLabel#Warning {{ color: {WARN}; }}
QLabel#Stage {{ font-weight: 600; }}
QPushButton {{ background: {CARD}; border: 1px solid #cdd3dd; border-radius: 6px;
               padding: 6px 14px; }}
QPushButton:hover {{ background: #eef2f8; }}
QPushButton:pressed {{ background: #e2e8f0; }}
QPushButton:disabled {{ color: #a0a7b3; background: #f5f6f8; border-color: #e3e6eb; }}
QPushButton[kind="primary"] {{ background: {ACCENT}; color: white; border: none; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {ACCENT_DARK}; }}
QPushButton[kind="primary"]:disabled {{ background: #9db8f0; color: #eef2fb; }}
QPushButton[kind="danger"] {{ background: {BAD}; color: white; border: none; font-weight: 600; }}
QPushButton[kind="danger"]:disabled {{ background: #f0b1b1; color: #fff; }}
QPushButton:checked {{ background: #dbe7fe; border-color: {ACCENT}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: white; border: 1px solid #cdd3dd;
               border-radius: 5px; padding: 4px 6px; min-height: 20px; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url({_ASSETS}/chevron_down.svg); width: 10px; height: 6px; }}
QComboBox QAbstractItemView {{ background: white; border: 1px solid {BORDER};
    selection-background-color: #dbe7fe; selection-color: {TEXT}; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 18px; border: none;
    background: transparent; subcontrol-origin: border; }}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; }}
QAbstractSpinBox::up-arrow {{ image: url({_ASSETS}/chevron_up.svg); width: 9px; height: 5px; }}
QAbstractSpinBox::down-arrow {{ image: url({_ASSETS}/chevron_down.svg); width: 9px; height: 5px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QProgressBar {{ background: #e8ecf2; border: none; border-radius: 6px; height: 12px;
                text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}
QTabWidget::pane {{ border: none; }}
QTabBar {{ font-weight: 600; }}
QTabBar::tab {{ background: transparent; padding: 8px 18px; margin-right: 2px;
                border-bottom: 2px solid transparent; color: {MUTED}; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTableView {{ background: white; border: 1px solid {BORDER}; border-radius: 8px;
              gridline-color: #eef0f4; selection-background-color: #dbe7fe;
              selection-color: {TEXT}; }}
QHeaderView::section {{ background: #f7f8fb; border: none; border-bottom: 1px solid {BORDER};
                        padding: 6px; font-weight: 600; }}
QPlainTextEdit {{ background: white; border: 1px solid {BORDER}; border-radius: 8px;
                  font-family: Consolas; font-size: 9pt; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 8px; margin-top: 14px; padding: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; font-weight: 600; }}
QToolTip {{ background: #111827; color: white; border: none; padding: 6px; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.Base, QColor("white"))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    app.setPalette(pal)
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLESHEET)

    import pyqtgraph as pg
    pg.setConfigOptions(background="w", foreground=TEXT, antialias=True)
