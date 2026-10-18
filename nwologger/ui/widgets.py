"""Shared widgets and helpers."""

from __future__ import annotations

import logging
import threading
import traceback
from collections.abc import Callable, Iterable
from typing import Any

from PySide6.QtCore import QDateTime, QObject, QSize, Qt, QTimer, QTimeZone, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionToolButton,
    QStylePainter,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from .. import i18n
from ..core import bands, modes, net, regions, timeutil
from ..core.fields import BY_COL
from . import theme

tr = i18n.tr
log = logging.getLogger("nwologger.ui")


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

class ScrollArea(QScrollArea):
    """Keep a form's minimum size inside a viewport, rather than forcing its window off-screen."""

    def __init__(self, widget: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setWidget(widget)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.widget().sizeHint()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(100, 100)


def fit_to_screen(window: QWidget) -> None:
    """Fit a normal window, including its native frame, to its screen's logical work area."""
    if window.isMaximized() or window.isFullScreen() or window.isMinimized():
        return
    screen = window.screen()
    if screen is None:
        return
    bounds = screen.availableGeometry()
    handle = window.windowHandle()
    if handle is not None:
        bounds = bounds.marginsRemoved(handle.frameMargins())
    rect = window.geometry()
    rect.setSize(rect.size().boundedTo(bounds.size()))
    rect.moveLeft(max(bounds.left(), min(rect.left(), bounds.right() - rect.width() + 1)))
    rect.moveTop(max(bounds.top(), min(rect.top(), bounds.bottom() - rect.height() + 1)))
    if rect != window.geometry():
        window.setGeometry(rect)


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


# ------------------------------------------------------------------ a drop-down list with tick boxes

class CheckListButton(QToolButton):
    """A button that drops down a list with tick boxes (the grid map's Callsigns and Grids): the button names the
    ticked entries, the list shows each with a count, and quick buttons above it tick a set at once."""

    changed = Signal()

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.title = title
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)  # may shrink, see paintEvent
        self._menu = QMenu(self)
        box = QWidget(self._menu)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 6, 8, 6)
        self.quick_row = QHBoxLayout()
        lay.addLayout(self.quick_row)
        self.list = QListWidget(box)
        self.list.setMinimumWidth(300)
        self.list.setMinimumHeight(180)
        self.list.itemChanged.connect(self._item_changed)
        lay.addWidget(self.list)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(box)
        self._menu.addAction(action)
        self.setMenu(self._menu)
        self._quiet = False
        self._update_text()

    def add_quick(self, text: str, fn: Callable[[], None]) -> QPushButton:
        b = QPushButton(text)
        b.setProperty("flat", True)
        b.clicked.connect(fn)
        self.quick_row.addWidget(b)
        return b

    def set_items(self, items: list[tuple[str, str, bool]]) -> None:
        """``(key, text, checked)`` per entry; the ticks of entries that stay are kept by the caller."""
        self._quiet = True
        self.list.clear()
        for key, text, checked in items:
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self.list.addItem(it)
        self._quiet = False
        self._update_text()

    def keys(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def checked(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]

    def set_checked(self, keys: Iterable[str]) -> None:
        wanted = {k.upper() for k in keys}
        self._quiet = True
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setCheckState(Qt.CheckState.Checked if str(it.data(Qt.ItemDataRole.UserRole)).upper() in wanted
                             else Qt.CheckState.Unchecked)
        self._quiet = False
        self._update_text()
        self.changed.emit()

    def _item_changed(self, _item) -> None:
        if not self._quiet:
            self._update_text()
            self.changed.emit()

    def _chrome(self) -> int:
        """The button's width beyond its text: padding and the menu arrow."""
        return self.sizeHint().width() - self.fontMetrics().horizontalAdvance(self.text())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        """On a narrow screen the button gives way down to its title and a few characters; the text is elided."""
        sh = super().minimumSizeHint()
        return QSize(min(sh.width(), self._chrome() + self.fontMetrics().horizontalAdvance(self.title + ": WWWWW")),
                     sh.height())

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        opt = QStyleOptionToolButton()
        self.initStyleOption(opt)
        room = self.width() - self._chrome()
        if opt.fontMetrics.horizontalAdvance(opt.text) > room:
            opt.text = opt.fontMetrics.elidedText(opt.text, Qt.TextElideMode.ElideRight, room)
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, opt)

    def _update_text(self) -> None:
        on, total = self.checked(), self.list.count()
        if not total:
            text = tr("none in the log")
        elif len(on) == total:
            text = tr("all") + f" ({total})"
        elif not on:
            text = tr("none")
        else:
            shown = ", ".join(on[:2]) + (f" +{len(on) - 2}" if len(on) > 2 else "")
            text = shown
        self.setText(f"{self.title}: {text}")


# ------------------------------------------------------------------ background work

class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)
    event = Signal(object)  # anything the work reports on the way (a page, a part, bytes received)


class Task:
    """Work on a daemon thread. Quitting NWOlogger never waits for it: the network and TQSL calls it runs can
    take minutes, and none of them touches the log database."""

    def __init__(self, fn: Callable[..., Any], *args: Any, **kw: Any):
        self.fn, self.args, self.kw = fn, args, kw
        self.signals = _Signals()
        self.thread = threading.Thread(target=self.run, name=getattr(fn, "__name__", "task"), daemon=True)

    def run(self) -> None:
        try:
            res = self.fn(*self.args, **self.kw)
        except Exception as e:  # noqa: BLE001 - reported to the UI
            # A NetError is an expected failure with a safe message; anything else gets its traceback.
            log.warning("Background task %s failed: %s", self.thread.name, e,
                        exc_info=not isinstance(e, net.NetError))
            msg = f"{e}"
            if not msg:
                msg = traceback.format_exc(limit=3)
            self._emit(self.signals.failed, msg)
            return
        self._emit(self.signals.done, res)

    @staticmethod
    def _emit(signal, value) -> None:
        try:
            signal.emit(value)
        except RuntimeError:  # the window was closed while the task ran
            pass


_alive: set[Task] = set()


def drop_tasks() -> None:
    """Forget the tasks still running (quitting): their threads are daemons and their signal objects may go now,
    while the application object exists; a late result is swallowed by :meth:`Task._emit`."""
    _alive.clear()


def _retire(t: Task) -> None:
    """Drop the GUI thread's reference to a finished task only once its thread has ended, so the task and its
    signals object are destroyed on the GUI thread. Destroyed on its own thread (the thread's last reference goes
    when it leaves ``run``), the signals would be disconnected from there inside Qt's connection mutex while
    taking the GIL, which deadlocks with a GUI thread holding the GIL inside a Qt call (see applog.Watchdog.stop)."""
    t.thread.join(timeout=0.2)
    if t.thread.is_alive():
        QTimer.singleShot(100, lambda: _retire(t))
        return
    _alive.discard(t)


def run_async(fn: Callable[..., Any], *args: Any, on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[str], None] | None = None, on_event: Callable[[Any], None] | None = None,
              **kw: Any) -> Task:
    """Run ``fn`` on a worker thread; callbacks run on the GUI thread. ``fn`` must not touch the DB. With
    ``on_event``, ``fn`` is called with an ``event`` keyword: a function it may call with any object, which reaches
    ``on_event`` on the GUI thread, in order, before ``on_done``."""
    t = Task(fn, *args, **kw)
    if on_event is not None:
        t.kw["event"] = lambda payload: t._emit(t.signals.event, payload)  # noqa: SLF001 - its own task
        t.signals.event.connect(on_event, Qt.ConnectionType.QueuedConnection)
    _alive.add(t)

    def _done(res: Any) -> None:
        _retire(t)
        if on_done:
            on_done(res)

    def _fail(msg: str) -> None:
        _retire(t)
        if on_error:
            on_error(msg)

    t.signals.done.connect(_done, Qt.ConnectionType.QueuedConnection)
    t.signals.failed.connect(_fail, Qt.ConnectionType.QueuedConnection)
    t.thread.start()
    return t


# ------------------------------------------------------------------ dropdowns

MODE_FIRST = ["SSB", "USB", "LSB", "CW", "FM", "AM", "FT8", "FT4", "RTTY", "PSK31", "JS8", "Q65", "MSK144",
              "JT65", "SSTV", "C4FM", "DSTAR", "DMR", "FREEDV", "M17"]
POPULAR_SATS = ["ARISS", "SO-50", "PO-101", "AO-91", "RS-44", "QO-100", "AO-123", "SO-124", "SO-125", "RS95S",
                "IO-117", "FO-29", "AO-7", "TEVEL5"]
OTHER = "__other__"


def all_modes() -> list[str]:
    """Every mode for the pickers: common ones first, then all ADIF modes and submodes."""
    rest = sorted(set(modes.all_mode_names()) - set(MODE_FIRST) - {"IMAGE"})
    return MODE_FIRST + rest


def mode_list(prefs) -> list[str]:
    chosen = [m for m in (prefs.get("mode_list") or []) if m] if prefs is not None else []
    return chosen or list(MODE_FIRST)


def sat_list(prefs, catalog, worked: list[str]) -> list[str]:
    """Satellites shown in the entry form: your list, or (until you choose) the ones you worked plus popular ones."""
    chosen = [s for s in (prefs.get("sat_list") or []) if s] if prefs is not None else []
    if chosen:
        return chosen
    out: list[str] = []
    for n in [*worked, *POPULAR_SATS]:
        n = (n or "").upper()
        if n and n not in out:
            out.append(n)
    return out


def mode_combo(prefs=None, *, blank: bool = False) -> QComboBox:
    """Your mode list (Preferences → Modes), then "Other mode…" for the rest. ``blank`` adds "—" (no mode)."""
    c = QComboBox()
    c.setMaxVisibleItems(24)
    c.setProperty("blank", blank)
    fill_mode_combo(c, prefs)
    return c


def fill_mode_combo(c: QComboBox, prefs=None) -> None:
    cur = c.currentData()
    c.blockSignals(True)
    c.clear()
    if c.property("blank"):
        c.addItem("—", "")
    for m in mode_list(prefs):
        c.addItem(f"{m} ({tr('generic')})" if m in ("DATA", "PHONE") else m, m)
    c.insertSeparator(c.count())
    c.addItem(tr("Other mode…"), OTHER)
    i = c.findData(cur) if cur and cur != OTHER else 0
    c.setCurrentIndex(i if i >= 0 else 0)
    c.blockSignals(False)


def set_combo_data(c: QComboBox, value, label: str | None = None) -> None:
    """Select the item with this data, adding it when missing (e.g. a value from an old log)."""
    v = value if value not in (None, "") else ""
    i = c.findData(v)
    if i < 0 and v != "":
        other = c.findData(OTHER)
        at = other - 1 if other > 0 else c.count()  # before the separator + "Other…"
        c.insertItem(at, label or str(v), v)
        i = at
    if i < 0:
        i = 0
    c.setCurrentIndex(i)


def combo_value(c: QComboBox) -> str | None:
    v = c.currentData()
    return v if v and v != OTHER else None


def prop_combo() -> QComboBox:
    from ..core import propagation  # noqa: PLC0415

    c = QComboBox()
    c.setMaxVisibleItems(24)
    c.addItem("—", "")
    for code, desc in propagation.LOTW.items():
        c.addItem(f"{code} · {tr(desc)}", code)
    c.insertSeparator(c.count())
    for code, desc in propagation.ADIF_ONLY.items():
        c.addItem(f"{code} · {tr(desc)} ({tr('not for LoTW')})", code)
    return c


def fill_sat_combo(c: QComboBox, catalog, names: list[str]) -> None:
    """The satellites in ``names`` (your list), then "Other satellite…" for the full list."""
    cur = c.currentData()
    c.blockSignals(True)
    c.clear()
    c.setMaxVisibleItems(24)
    c.addItem("—", "")
    for n in names:
        s = catalog.get(n)
        c.addItem(n, n)
        c.setItemData(c.count() - 1, s.description if s else tr("From your log"), Qt.ItemDataRole.ToolTipRole)
    c.insertSeparator(c.count())
    c.addItem(tr("Other satellite…"), OTHER)
    if cur and cur != OTHER:
        set_combo_data(c, cur)
    else:
        c.setCurrentIndex(0)
    c.blockSignals(False)
