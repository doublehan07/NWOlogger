"""Shared widgets and helpers."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QDateTime, QObject, QRunnable, Qt, QThreadPool, QTimeZone, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from ..core import bands, modes, regions, timeutil
from ..core.fields import BY_COL
from . import theme

tr = i18n.tr


# ------------------------------------------------------------------ formatting

def fmt(col: str, v: Any) -> str:
    if v is None or v == "":
        return ""
    f = BY_COL.get(col)
    if col in ("freq", "freq_rx"):
        try:
            return f"{float(v):.5f}"
        except (TypeError, ValueError):
            return str(v)
    if col in ("band", "band_rx"):
        return bands.display_band(v)
    if f and f.kind == "time":
        return str(v).replace("T", " ").replace("Z", "")
    if col == "tx_pwr":
        try:
            return f"{float(v):g}"
        except (TypeError, ValueError):
            return str(v)
    if col == "dupe_flag":
        return {"DUPE": tr("Dupe"), "SUSPECT": tr("Suspect"), "OK": ""}.get(v, str(v))
    return str(v)


def mode_text(q: dict) -> str:
    return modes.display(q.get("mode"), q.get("submode"))


def province_text(q: dict) -> str:
    st = q.get("state")
    if st and q.get("dxcc") == regions.CHINA_DXCC:
        return regions.display_cn(st, i18n.lang())
    return st or ""


# ------------------------------------------------------------------ widgets

class Section(QFrame):
    """A rounded card with a small title and a grid layout."""

    def __init__(self, title: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Section")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 10)
        outer.setSpacing(6)
        if title:
            lab = QLabel(title.upper())
            lab.setObjectName("SectionTitle")
            outer.addWidget(lab)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(6)
        outer.addLayout(self.grid)
        outer.addStretch(1)
        self.outer = outer

    def add(self, item, stretch: int = 0) -> None:
        """Add a widget or layout under the grid. A stretching item takes the spare room."""
        last = self.outer.count() - 1
        tail = self.outer.itemAt(last)
        if stretch and tail is not None and tail.spacerItem() is not None:
            self.outer.takeAt(last)
            last = self.outer.count()
        if isinstance(item, QWidget):
            self.outer.insertWidget(last, item, stretch)
        else:
            self.outer.insertLayout(last, item, stretch)

    def row(self, r: int, label: str, widget: QWidget, col: int = 0, span: int = 1) -> QWidget:
        lab = QLabel(label)
        lab.setProperty("muted", True)
        lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.grid.addWidget(lab, r, col * 2)
        self.grid.addWidget(widget, r, col * 2 + 1, 1, span * 2 - 1)
        return widget


class Chip(QLabel):
    def __init__(self, text: str = "", kind: str = "muted", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("Chip")
        self.set_kind(kind)

    def set_kind(self, kind: str) -> None:
        self.setProperty("kind", kind)
        theme.repolish(self)


class AutoLineEdit(QLineEdit):
    """A line edit that remembers whether its text came from a lookup (tinted) or a guess (italic)."""

    def __init__(self, parent: QWidget | None = None, mono: bool = False):
        super().__init__(parent)
        self._auto = False
        if mono:
            self.setProperty("mono", True)
        self.textEdited.connect(self._edited)

    def _edited(self, _t: str) -> None:
        if self._auto:
            self._auto = False
            self.setProperty("autofill", False)
            self.setProperty("guess", False)
            self.setToolTip("")
            theme.repolish(self)

    def set_auto(self, value: Any, *, guess: bool = False, source: str = "") -> bool:
        """Fill only when empty or still auto-filled. Returns True if the text was set."""
        if self.text() and not self._auto:
            return False
        txt = "" if value is None else str(value)
        self.setText(txt)
        self.setCursorPosition(0)
        self._auto = bool(txt)
        self.setProperty("autofill", bool(txt) and not guess)
        self.setProperty("guess", bool(txt) and guess)
        self.setToolTip((tr("Guessed from the callsign") if guess else tr("From lookup") +
                         (f": {source}" if source else "")) if txt else "")
        theme.repolish(self)
        return True

    def is_auto(self) -> bool:
        return self._auto

    def clear_all(self) -> None:
        self.clear()
        self._edited("")


class UtcDateTimeEdit(QDateTimeEdit):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setTimeZone(QTimeZone.utc())
        self.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.setCalendarPopup(True)
        self.setProperty("mono", True)
        self.setFont(theme.mono_font())
        self.setMinimumWidth(170)

    def set_iso(self, iso: str | None) -> None:
        dt = timeutil.parse_iso(iso) if iso else timeutil.now_utc()
        q = QDateTime.fromSecsSinceEpoch(int(dt.timestamp()), QTimeZone.utc())
        self.blockSignals(True)
        self.setDateTime(q)
        self.blockSignals(False)

    def iso(self) -> str:
        return timeutil.epoch_to_iso(self.dateTime().toSecsSinceEpoch()) or timeutil.now_iso()


def combo(items: list[str], editable: bool = False, current: str | None = None) -> QComboBox:
    c = QComboBox()
    c.setEditable(editable)
    c.addItems(items)
    if current is not None:
        c.setCurrentText(current)
    return c


def primary(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("primary", True)
    return b


def hbox(*widgets: QWidget | None, spacing: int = 6, margins: tuple[int, int, int, int] = (0, 0, 0, 0),
         stretch_at: int | None = None) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for i, w in enumerate(widgets):
        if stretch_at is not None and i == stretch_at:
            lay.addStretch(1)
        if w is None:
            lay.addStretch(1)
        else:
            lay.addWidget(w)
    return lay


# ------------------------------------------------------------------ background work

class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)


class Task(QRunnable):
    def __init__(self, fn: Callable[..., Any], *args: Any, **kw: Any):
        super().__init__()
        self.fn, self.args, self.kw = fn, args, kw
        self.signals = _Signals()
        self.setAutoDelete(False)

    def run(self) -> None:
        try:
            res = self.fn(*self.args, **self.kw)
        except Exception as e:  # noqa: BLE001 - reported to the UI
            msg = f"{e}"
            if not msg:
                msg = traceback.format_exc(limit=3)
            self.signals.failed.emit(msg)
            return
        self.signals.done.emit(res)


_alive: set[Task] = set()


def run_async(fn: Callable[..., Any], *args: Any, on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[str], None] | None = None, **kw: Any) -> Task:
    """Run ``fn`` on a worker thread; callbacks run on the GUI thread. ``fn`` must not touch the DB."""
    t = Task(fn, *args, **kw)
    _alive.add(t)

    def _done(res: Any) -> None:
        _alive.discard(t)
        if on_done:
            on_done(res)

    def _fail(msg: str) -> None:
        _alive.discard(t)
        if on_error:
            on_error(msg)

    t.signals.done.connect(_done, Qt.ConnectionType.QueuedConnection)
    t.signals.failed.connect(_fail, Qt.ConnectionType.QueuedConnection)
    QThreadPool.globalInstance().start(t)
    return t
