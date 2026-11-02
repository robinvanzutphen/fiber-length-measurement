"""Small reusable widgets."""

from __future__ import annotations

import pyqtgraph as pg
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                             QSizePolicy, QVBoxLayout, QWidget)

from . import theme


class Card(QFrame):
    """White rounded panel with a title; add content to `card.body`."""

    def __init__(self, title: str = "", subtitle: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        if title:
            head = QHBoxLayout()
            self.title = QLabel(title)
            self.title.setObjectName("CardTitle")
            head.addWidget(self.title)
            head.addStretch()
            self.header = head
            lay.addLayout(head)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("Muted")
            sub.setWordWrap(True)
            lay.addWidget(sub)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        lay.addLayout(self.body)


class StatusPill(QWidget):
    COLORS = {"ok": theme.OK, "off": "#9ca3af", "busy": theme.WARN, "bad": theme.BAD,
              "info": theme.ACCENT}

    def __init__(self, text: str, longest: str = "", parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.dot = QLabel()
        self.dot.setFixedSize(10, 10)
        self.label = QLabel(text)
        if longest:     # reserve room for the longest text so neighbours never shift
            self.label.setFixedWidth(text_width(self.label, longest))
        lay.addWidget(self.dot)
        lay.addWidget(self.label)
        self.set_state("off")

    def set_state(self, state: str, text: str | None = None) -> None:
        self.dot.setStyleSheet(f"background:{self.COLORS[state]}; border-radius:5px;")
        if text is not None:
            self.label.setText(text)


class ValueDisplay(QWidget):
    """Caption above a large value with unit.

    The widget has a fixed width and the value label reserves the width of `template`, so
    changing numbers never resize or shift anything around it (no jitter with noisy data).
    """

    def __init__(self, caption: str, unit: str = "", big: bool = True, template: str = "",
                 width: int | None = None, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        cap = QLabel(caption)
        cap.setObjectName("Muted")
        row = QHBoxLayout()
        row.setSpacing(6)
        self.value = QLabel("—")
        self.value.setObjectName("BigValue" if big else "MidValue")
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if unit:    # numbers right-aligned in their reserved width, unit directly after
            self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.unit = QLabel(unit)
        self.unit.setObjectName("Muted")
        row.addWidget(self.value)
        row.addWidget(self.unit, 0, Qt.AlignBottom)
        row.addStretch()
        lay.addWidget(cap)
        lay.addLayout(row)
        self.sub = QLabel("")
        self.sub.setObjectName("Muted")
        self.sub.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(self.sub)
        template = template or ("-00 000.00" if big else "-0.00000000")
        self.value.setMinimumWidth(text_width(self.value, template))
        self.setFixedWidth(width or max(text_width(cap, caption),
                                        text_width(self.value, template)
                                        + text_width(self.unit, unit) + 16))

    def set(self, value: str, sub: str = "") -> None:
        self.value.setText(value)
        self.sub.setText(sub)
        self.sub.setToolTip(sub)


def text_width(label: QLabel, text: str) -> int:
    """Width of `text` in the label's final (stylesheet) font."""
    label.ensurePolished()
    return label.fontMetrics().horizontalAdvance(text) + 2


def button(text: str, kind: str | None = None, tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def muted(text: str = "", wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("Muted")
    lab.setWordWrap(wrap)
    return lab


def plot(xlabel: str, ylabel: str, xunit: str = "", yunit: str = "") -> pg.PlotWidget:
    w = pg.PlotWidget()
    w.showGrid(x=True, y=True, alpha=0.15)
    w.setLabel("bottom", xlabel, units=xunit)
    w.setLabel("left", ylabel, units=yunit)
    w.getPlotItem().getViewBox().setMouseMode(pg.ViewBox.RectMode)
    w.addLegend(offset=(-10, 10))
    return w


def side_column(layout, width: int) -> QScrollArea:
    """Fixed-width, vertically scrollable column (keeps controls readable on small screens)."""
    inner = QWidget()
    inner.setLayout(layout)
    area = QScrollArea()
    area.setWidget(inner)
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setFixedWidth(width)
    return area
