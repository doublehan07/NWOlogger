"""Contest mode (N1MM style): entry line, live dupe check and score, Check Partial, N+1, mults and rate panes."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import i18n
from ...core import bands, callsign, dxcc, modes, regions, timeutil
from ...core import qso as qsomod
from ...core.contest import cabrillo
from ...core.contest import scp as scpmod
from ...core.contest.engine import (
    ContestDef,
    Engine,
    Me,
    exchange_values,
    load_all,
    mode_category,
    store_exchange,
    validate_value,
)
from ...core.contest.scp import Scp
from ...core.operators import is_club
from .. import theme
from ..operator_field import OperatorField
from ..widgets import Chip, Section, primary, run_async

tr = i18n.tr

MODE_WORDS = {"CW", "SSB", "USB", "LSB", "FM", "AM", "RTTY", "RY", "FT8", "FT4", "PSK31", "DIGI", "DATA", "PH"}
FREQ_RE = re.compile(r"^\d{3,6}(\.\d{1,3})?$")


def user_contest_dir(db) -> Path:
    return Path(db.path).parent / "contests"


class _Keys(QObject):
    """Space jumps fields; Enter steps through (ESM without a keyer)."""

    def __init__(self, panel: ContestPanel):
        super().__init__(panel)
        self.p = panel

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if ev.type() == QEvent.Type.KeyPress:
            k = ev.key()
            if k == Qt.Key.Key_Space:
                self.p.next_field(obj)
                return True
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                force = bool(ev.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
                self.p.enter(obj, force=force)
                return True
            if k == Qt.Key.Key_Escape:
                self.p.wipe()
                return True
        return False


class ContestPanel(QWidget):
    message = Signal(str)
    session_changed = Signal(object)
    scp_updated = Signal()

    def __init__(self, db, prefs, win):
        super().__init__(win)
        self.db, self.prefs, self.win = db, prefs, win
        self.cty = dxcc.default()
        self.defs: dict[str, ContestDef] = {}
        self.session: dict | None = None
        self.cdef: ContestDef | None = None
        self.engine: Engine | None = None
        self.edit_id: int | None = None
        self.fields: dict[str, QLineEdit] = {}
        self._auto: set[str] = set()
        self.scp = Scp()
        self._keys = _Keys(self)
        self._build()
        self._build_docks()
        self._check = QTimer(self)
        self._check.setSingleShot(True)
        self._check.setInterval(120)
        self._check.timeout.connect(self.refresh_checks)
        self._rebuild = QTimer(self)
        self._rebuild.setSingleShot(True)
        self._rebuild.setInterval(300)
        self._rebuild.timeout.connect(self.rebuild)
        self._rate = QTimer(self)
        self._rate.timeout.connect(self._refresh_rate)
        self._rate.start(15000)
        db.add_listener(self._on_db)
        self.reload_defs()
        self.reload_sessions()
        self._load_scp()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.sess = QComboBox()
        self.sess.setMinimumWidth(300)
        self.sess.currentIndexChanged.connect(self._session_picked)
        new = primary(tr("New contest…"))
        new.setIcon(theme.icon("plus", "#FFFFFF"))
        new.clicked.connect(self.new_session)
        edit = QPushButton(tr("Settings…"))
        edit.setIcon(theme.icon("sliders-horizontal"))
        edit.clicked.connect(self.edit_session)
        cab = QPushButton(tr("Export Cabrillo…"))
        cab.setIcon(theme.icon("file-up"))
        cab.clicked.connect(self.export_cabrillo)
        udc = QPushButton(tr("Contest definitions…"))
        udc.setIcon(theme.icon("book-open"))
        udc.clicked.connect(self.edit_definitions)
        top.addWidget(QLabel(tr("Contest")))
        top.addWidget(self.sess)
        top.addSpacing(10)
        top.addWidget(QLabel(tr("Operator")))
        self.op_field = OperatorField(self.win.ops)
        self.op_field.setMinimumWidth(110)
        self.op_field.setMaximumWidth(140)
        self.op_field.message.connect(self.message)
        self.op_field.returnPressed.connect(lambda: self.call.setFocus())
        top.addWidget(self.op_field)
        top.addWidget(new)
        top.addWidget(edit)
        top.addWidget(cab)
        top.addWidget(udc)
        top.addStretch(1)
        self.score_lab = QLabel("")
        self.score_lab.setObjectName("Score")
        self.score_sub = QLabel("")
        self.score_sub.setProperty("muted", True)
        sc = QVBoxLayout()
        sc.setSpacing(0)
        sc.addWidget(self.score_lab, 0, Qt.AlignmentFlag.AlignRight)
        sc.addWidget(self.score_sub, 0, Qt.AlignmentFlag.AlignRight)
        top.addLayout(sc)
        root.addLayout(top)

        sec = Section(tr("Entry"))
        self.entry_grid = QGridLayout()
        self.entry_grid.setHorizontalSpacing(8)
        self.entry_grid.setVerticalSpacing(2)
        sec.add(self.entry_grid)
        self.band = QComboBox()
        self.mode = QComboBox()
        self.freq = QLineEdit()
        self.freq.setObjectName("ContestField")
        self.freq.setPlaceholderText(tr("kHz"))
        self.freq.setMaximumWidth(120)
        self.call = QLineEdit()
        self.call.setObjectName("ContestCall")
        self.call.setPlaceholderText(tr("Call"))
        self.call.setMinimumWidth(200)
        self.call.textEdited.connect(self._call_edited)
        self.call.installEventFilter(self._keys)
        self.band.currentIndexChanged.connect(self._band_changed)
        self.mode.currentTextChanged.connect(lambda *_: self._check.start())
        self.freq.editingFinished.connect(self._freq_set)
        sec.add(self._status_row())
        root.addWidget(sec)
        self.sent_lab = QLabel("")
        self.sent_lab.setObjectName("Hint")
        bar = QHBoxLayout()
        bar.addWidget(self.sent_lab)
        bar.addStretch(1)
        edit_last = QPushButton(tr("Edit last"))
        edit_last.setToolTip("Alt+E")
        edit_last.clicked.connect(self.edit_last)
        wipe = QPushButton(tr("Wipe"))
        wipe.setToolTip("Esc / Alt+W")
        wipe.clicked.connect(self.wipe)
        self.log_btn = primary(tr("Log"))
        self.log_btn.setMinimumWidth(120)
        self.log_btn.setToolTip(tr("Enter: next empty field, then log. Ctrl+Enter: log even if the exchange "
                                   "looks wrong."))
        self.log_btn.clicked.connect(lambda: self.commit(force=False))
        bar.addWidget(edit_last)
        bar.addWidget(wipe)
        bar.addWidget(self.log_btn)
        root.addLayout(bar)
        hint = QLabel(tr("Space: next field · Enter: next empty field, then log · type a frequency (14025) or a mode "
                         "(CW, SSB, RTTY) in the call field and press Enter · Esc: wipe · ⌘/Ctrl+Z: undo last QSO"))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        root.addWidget(hint)
        root.addStretch(1)
        QShortcut(QKeySequence("Alt+W"), self, activated=self.wipe)
        QShortcut(QKeySequence("Alt+E"), self, activated=self.edit_last)

    def _status_row(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        self.chip_row = QHBoxLayout()
        self.chip_row.setSpacing(5)
        lay.addLayout(self.chip_row)
        self.info = QLabel("")
        self.info.setObjectName("Summary")
        lay.addWidget(self.info, 1)
        return w

    def _build_docks(self) -> None:
        self.docks: list[QDockWidget] = []

        def dock(title: str, widget: QWidget, name: str) -> QDockWidget:
            d = QDockWidget(tr(title), self.win)
            d.setObjectName(name)
            d.setWidget(widget)
            d.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea |
                              Qt.DockWidgetArea.BottomDockWidgetArea)
            self.win.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, d)
            d.hide()
            self.docks.append(d)
            return d

        cp = QWidget()
        cl = QVBoxLayout(cp)
        cl.setContentsMargins(6, 6, 6, 6)
        self.partial = QListWidget()
        self.partial.itemClicked.connect(lambda it: self._pick_call(it.text().split()[0]))
        upd = QPushButton(tr("Update MASTER.SCP"))
        upd.setProperty("flat", True)
        upd.clicked.connect(self._download_scp)
        self.scp_lab = QLabel("")
        self.scp_lab.setObjectName("Hint")
        cl.addWidget(self.partial, 1)
        row = QHBoxLayout()
        row.addWidget(self.scp_lab, 1)
        row.addWidget(upd)
        cl.addLayout(row)
        d1 = dock("Check Partial", cp, "dock_partial")
        self.nplus = QListWidget()
        self.nplus.itemClicked.connect(lambda it: self._pick_call(it.text().split()[0]))
        d2 = dock("N+1", self.nplus, "dock_nplus")
        self.score_table = QTableWidget(0, 0)
        self.score_table.verticalHeader().setVisible(False)
        self.score_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.score_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        d3 = dock("Score", self.score_table, "dock_score")
        self.mults = QLabel("")
        self.mults.setWordWrap(True)
        self.mults.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.mults.setTextFormat(Qt.TextFormat.RichText)
        mw = QWidget()
        ml = QVBoxLayout(mw)
        ml.addWidget(self.mults)
        ml.addStretch(1)
        d4 = dock("Multipliers", mw, "dock_mults")
        rw = QWidget()
        rl = QGridLayout(rw)
        self.rate_labels = {}
        for i, (key, label) in enumerate((("last10", "Last 10 QSOs"), ("last100", "Last 100 QSOs"),
                                          ("hour", "Last 60 minutes"))):
            big = QLabel("0")
            big.setObjectName("Big")
            lab = QLabel(tr(label))
            lab.setProperty("muted", True)
            rl.addWidget(big, 0, i)
            rl.addWidget(lab, 1, i)
            self.rate_labels[key] = big
        rl.setRowStretch(2, 1)
        d5 = dock("Rate", rw, "dock_rate")
        self.win.splitDockWidget(d1, d2, Qt.Orientation.Vertical)
        self.win.splitDockWidget(d2, d3, Qt.Orientation.Vertical)
        self.win.tabifyDockWidget(d3, d4)
        self.win.tabifyDockWidget(d4, d5)
        d3.raise_()

    # ------------------------------------------------------------------ lifecycle
    def activate(self) -> None:
        for d in self.docks:
            d.show()
        state = self.prefs.value("contest/docks")
        if state:
            self.win.restoreState(state)
            for d in self.docks:
                d.show()
        if not self.session:
            QTimer.singleShot(0, self.new_session)
        self.op_field.show_for(self.profile())
        self.call.setFocus()

    def deactivate(self) -> None:
        self.save_state()
        for d in self.docks:
            d.hide()

    def save_state(self) -> None:
        if any(d.isVisible() for d in self.docks):
            self.prefs.set_value("contest/docks", self.win.saveState())

    def shutdown(self) -> None:
        self._check.stop()
        self._rebuild.stop()
        self._rate.stop()
        self.db.remove_listener(self._on_db)

    def reload_defs(self) -> None:
        self.defs = load_all(user_contest_dir(self.db))

    def reload_sessions(self, select: int | None = None) -> None:
        cur = select if select is not None else self.prefs.get("current_contest")
        self.sess.blockSignals(True)
        self.sess.clear()
        for s in self.db.contest_sessions():
            self.sess.addItem(f"{s['name']}  ·  {s['started_at'][:10]}", s["id"])
        i = self.sess.findData(cur) if cur is not None else -1
        self.sess.setCurrentIndex(i if i >= 0 else 0)
        self.sess.blockSignals(False)
        self._session_picked()

    def _session_picked(self) -> None:
        sid = self.sess.currentData()
        self.session = self.db.contest_session(sid) if sid is not None else None
        self.cdef = self.defs.get(self.session["contest_def"]) if self.session else None
        if self.session and not self.cdef:
            self.message.emit(tr("Contest definition not found") + f": {self.session['contest_def']}")
        self.prefs.set("current_contest", sid)
        self.op_field.show_for(self.profile())
        self._setup_entry()
        self.rebuild()
        self.session_changed.emit(sid)

    def profile(self) -> dict | None:
        pid = (self.session or {}).get("station_profile_id")
        return self.db.profile(pid) if pid else self.db.default_profile()

    # ------------------------------------------------------------------ entry line
    def _setup_entry(self) -> None:
        g = self.entry_grid
        while g.count():
            w = g.takeAt(0).widget()
            if w and w not in (self.band, self.mode, self.freq, self.call):
                w.deleteLater()
        for f in self.fields.values():
            f.removeEventFilter(self._keys)
        self.fields = {}
        cdef = self.cdef
        enabled = cdef is not None
        for w in (self.band, self.mode, self.freq, self.call, self.log_btn):
            w.setEnabled(enabled)
        col = 0
        for label, w in ((tr("Band"), self.band), (tr("Mode"), self.mode), (tr("Freq"), self.freq),
                         (tr("Call"), self.call)):
            lab = QLabel(label)
            lab.setObjectName("Hint")
            g.addWidget(lab, 0, col)
            g.addWidget(w, 1, col)
            col += 1
        if not cdef:
            self.sent_lab.setText(tr("Create a contest to start logging."))
            return
        self.band.blockSignals(True)
        self.band.clear()
        for b in cdef.bands:
            self.band.addItem(bands.display_band(b), b)
        last_band = (self.session.get("data") or {}).get("band")
        i = self.band.findData(last_band) if last_band else 0
        self.band.setCurrentIndex(max(0, i))
        self.band.blockSignals(False)
        self.mode.blockSignals(True)
        self.mode.clear()
        self.mode.addItems(cdef.mode_choices())
        last_mode = (self.session.get("data") or {}).get("mode")
        if last_mode:
            self.mode.setCurrentText(last_mode)
        self.mode.blockSignals(False)
        last_freq = (self.session.get("data") or {}).get("freq")
        self.freq.setText(last_freq or "")
        for f in cdef.exchange:
            e = QLineEdit()
            e.setObjectName("ContestField")
            e.setMaximumWidth(110 if f.get("kind") in ("rst", "serial", "int") else 160)
            e.installEventFilter(self._keys)
            e.textEdited.connect(lambda _t, k=f["key"]: (self._auto.discard(k), self._check.start()))
            lab = QLabel(tr(f.get("label", f["key"])))
            lab.setObjectName("Hint")
            g.addWidget(lab, 0, col)
            g.addWidget(e, 1, col)
            self.fields[f["key"]] = e
            col += 1
        g.setColumnStretch(col, 1)
        self._update_sent()
        self.wipe()

    def sent_values(self) -> dict[str, str]:
        data = (self.session or {}).get("data") or {}
        return dict(data.get("sent") or {})

    def _update_sent(self) -> None:
        if not self.cdef or not self.session:
            return
        sent = self.sent_values()
        parts = []
        for f in self.cdef.exchange:
            k = f["key"]
            if f.get("kind") == "serial" or sent.get(k) == "#":
                parts.append(f"#{self.session['next_serial']:03d}")
            else:
                parts.append(sent.get(k) or "—")
        self.sent_lab.setText(tr("Sent") + ": " + "  ".join(parts) + f"   ·   {self.cdef.name}")

    def _band_changed(self) -> None:
        b = self.band.currentData()
        try:
            f = float(self.freq.text()) / 1000 if self.freq.text() else None
        except ValueError:
            f = None
        if b and (f is None or bands.band_for_freq(f) != b):
            lo = bands.default_freq(b)
            self.freq.setText(str(int(lo * 1000)) if lo and lo < 100 else (f"{lo:g}" if lo else ""))
        self._remember()
        self._check.start()

    def _freq_set(self) -> None:
        t = self.freq.text().strip()
        if not t:
            return
        try:
            f = float(t)
        except ValueError:
            return
        mhz = f / 1000 if f > 1000 else f
        b = bands.band_for_freq(mhz)
        if b:
            i = self.band.findData(b)
            if i >= 0:
                self.band.blockSignals(True)
                self.band.setCurrentIndex(i)
                self.band.blockSignals(False)
        self._remember()
        self._check.start()

    def _remember(self) -> None:
        if self.session:
            data = dict(self.session.get("data") or {})
            data.update(band=self.band.currentData(), mode=self.mode.currentText(), freq=self.freq.text())
            self.session["data"] = data
            self.db.update_contest_session(self.session["id"], data=data)

    def freq_mhz(self) -> float | None:
        t = self.freq.text().strip()
        try:
            f = float(t)
        except ValueError:
            b = self.band.currentData()
            return bands.default_freq(b) if b else None
        mhz = f / 1000 if f > 1000 else f
        b = self.band.currentData()
        if bands.band_for_freq(mhz) != b:
            return bands.default_freq(b) if b else mhz
        return mhz

    def _call_edited(self, text: str) -> None:
        up = text.upper().replace(" ", "")
        if up != text:
            pos = self.call.cursorPosition()
            self.call.setText(up)
            self.call.setCursorPosition(pos)
        self._prefill()
        self._check.start()

    def _prefill(self) -> None:
        if not self.cdef:
            return
        c = callsign.clean(self.call.text())
        r = self.cty.resolve(c) if len(c) >= 3 else None
        hist = None
        for f in self.cdef.exchange:
            k = f["key"]
            e = self.fields[k]
            if e.text() and k not in self._auto:
                continue
            val = ""
            kind = f.get("kind")
            if kind == "rst":
                m, s = modes.normalize(self.mode.currentText())
                val = "599" if modes.group(m, s) != "PHONE" else "59"
            elif f.get("prefill") in ("cqz", "ituz") and r:
                val = str(r.cqz if f["prefill"] == "cqz" else r.ituz)
            elif f.get("prefill") == "history" or kind in ("text", "grid"):
                if hist is None:
                    hist = self._history(c)
                val = hist.get(k, "")
            if val:
                e.setText(val)
                self._auto.add(k)
            elif k in self._auto:
                e.clear()
                self._auto.discard(k)

    def _history(self, call: str) -> dict[str, str]:
        """Exchange from your last QSO with this call in a contest of the same kind."""
        if not call or not self.cdef:
            return {}
        q = self.db.last_qso("call = ? AND contest_id = ? AND srx_string IS NOT NULL", [call, self.cdef.cabrillo])
        if q:
            ex = exchange_values(self.cdef, q)
            return {k: v for k, v in ex.items() if v and v != "-" and self.cdef.field(k).get("kind") not in
                    ("rst", "serial")}
        g = self.db.last_qso("call = ? AND gridsquare IS NOT NULL", [call])
        return {"grid": (g["gridsquare"] or "")[:4]} if g else {}

    def _order(self) -> list[QLineEdit]:
        return [self.call, *self.fields.values()]

    def next_field(self, current) -> None:
        order = self._order()
        if current is self.call:
            for f in self.cdef.exchange if self.cdef else []:
                e = self.fields[f["key"]]
                if f.get("kind") == "rst" and e.text():
                    continue
                e.setFocus()
                e.selectAll()
                return
        if current in order:
            i = order.index(current)
            nxt = order[(i + 1) % len(order)]
            nxt.setFocus()
            nxt.selectAll()

    def enter(self, current, force: bool = False) -> None:
        if current is self.call:
            t = self.call.text().strip().upper()
            if FREQ_RE.match(t) and not callsign.looks_like_call(t):
                self.freq.setText(t)
                self.call.clear()
                self._freq_set()
                self.message.emit(tr("Frequency set") + f": {t}")
                return
            if t in MODE_WORDS:
                self.mode.setCurrentText({"RY": "RTTY", "PH": "SSB", "DIGI": "FT8", "DATA": "FT8"}.get(t, t))
                self.call.clear()
                self._remember()
                self.message.emit(tr("Mode set") + f": {self.mode.currentText()}")
                return
            if not t:
                return
        missing = self._first_invalid()
        if missing is not None and not force:
            e = self.fields[missing]
            e.setFocus()
            e.selectAll()
            return
        self.commit(force=force)

    def _first_invalid(self) -> str | None:
        if not self.cdef:
            return None
        x = self._ctx_preview()
        for f in self.cdef.exchange:
            if f.get("only_if") and self.engine and not self.engine.all(f["only_if"], x):
                continue
            if not validate_value(f, self.fields[f["key"]].text()):
                return f["key"]
        return None

    def _pick_call(self, call: str) -> None:
        self.call.setText(call)
        self._call_edited(call)
        self.call.setFocus()

    def wipe(self) -> None:
        self.edit_id = None
        self.call.clear()
        for e in self.fields.values():
            e.clear()
            e.setProperty("invalid", False)
            theme.repolish(e)
        self._auto.clear()
        self.log_btn.setText(tr("Log"))
        self._prefill()
        self.refresh_checks()
        self.call.setFocus()

    # ------------------------------------------------------------------ checks
    def _values(self) -> dict[str, str]:
        return {k: e.text().strip().upper() for k, e in self.fields.items()}

    def _draft(self) -> dict:
        m, s = modes.normalize(self.mode.currentText())
        f = self.freq_mhz()
        q = {"call": callsign.clean(self.call.text()), "band": self.band.currentData(), "mode": m, "submode": s,
             "freq": f, "_exch": self._values()}
        return q

    def _ctx_preview(self) -> dict:
        return self.engine.ctx(self._draft()) if self.engine else {}

    def refresh_checks(self) -> None:
        chips: list[tuple[str, str]] = []
        c = callsign.clean(self.call.text())
        info = ""
        dupe = False
        if self.engine and c:
            score = self.engine.preview(self._draft(), exclude_id=self.edit_id)
            if score.dupe:
                dupe = True
                chips.append((tr("DUPE"), "bad"))
            else:
                for label, val in score.new_mults:
                    chips.append((f"{tr('NEW')} {tr(label)}: {val}", "ok"))
                chips.append((f"+{score.points} " + tr("pts"), "info"))
            r = self.cty.resolve(c)
            if r:
                info = f"{r.country} · {r.cont} · CQ {r.cqz} · ITU {r.ituz}"
                if r.dxcc == regions.CHINA_DXCC and regions.guess_cn_province(c):
                    info += f" · {regions.guess_cn_province(c)}"
            other = [q for q in self.db.worked_before(c, 50) if q.get("contest_session_id") == self.session["id"]]
            if other:
                worked = sorted({f"{bands.display_band(q['band'])} {mode_category(q['mode'], q['submode'])}"
                                 for q in other})
                info += "   —   " + tr("worked") + ": " + ", ".join(worked)
        self.call.setProperty("dupe", "DUPE" if dupe else "")
        theme.repolish(self.call)
        blocked = dupe and not self.prefs.get("allow_dupes")
        self.log_btn.setEnabled(bool(c) and not blocked and self.cdef is not None)
        for k, e in self.fields.items():
            f = self.cdef.field(k) if self.cdef else None
            bad = bool(e.text()) and f is not None and not validate_value(f, e.text())
            if e.property("invalid") != bad:
                e.setProperty("invalid", bad)
                theme.repolish(e)
        while self.chip_row.count():
            it = self.chip_row.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for text, kind in chips:
            self.chip_row.addWidget(Chip(text, kind))
        self.info.setText(info)
        self._refresh_partials(c)

    def _refresh_partials(self, c: str) -> None:
        worked_here = set()
        if self.engine:
            band = self.band.currentData()
            modecat = mode_category(*modes.normalize(self.mode.currentText()))
            for key in self.engine.dupe_keys:
                if self.cdef.dupe == "once" or (key[1] == band and (self.cdef.dupe != "band_mode" or
                                                                     key[2] == modecat)):
                    worked_here.add(key[0])
        t = theme.tokens()
        for widget, calls in ((self.partial, self.scp.partial(c)), (self.nplus, self.scp.n_plus_one(c))):
            widget.clear()
            for call in calls:
                it = QListWidgetItem(call + ("   " + tr("dupe") if call in worked_here else ""))
                it.setFont(theme.mono_font())
                if call in worked_here:
                    it.setForeground(QColor(t.bad))
                elif call == c:
                    it.setForeground(QColor(t.accent))
                widget.addItem(it)

    # ------------------------------------------------------------------ logging
    def commit(self, force: bool = False) -> None:
        if not self.cdef or not self.session or not self.engine:
            return
        c = callsign.clean(self.call.text())
        if not c:
            return
        bad = self._first_invalid()
        if bad is not None and not force:
            self.fields[bad].setFocus()
            self.message.emit(tr("Check the exchange") + f": {tr(self.cdef.field(bad).get('label', bad))}"
                              + " · " + tr("Ctrl+Enter logs anyway"))
            return
        draft = self._draft()
        pre = self.engine.preview(draft, exclude_id=self.edit_id)
        if pre.dupe and not self.prefs.get("allow_dupes"):
            self.message.emit(tr("Dupe: already worked under this contest's rules."))
            return
        r = self.cty.resolve(c)
        they_wve = bool(r and r.dxcc in (291, 1))
        now = timeutil.now_iso()
        serial = self.session["next_serial"]
        q = {"call": c, "band": draft["band"], "freq": draft["freq"], "mode": draft["mode"],
             "submode": draft["submode"], "contest_session_id": self.session["id"]}
        if r:
            q.update(country=r.country, dxcc=r.dxcc, cqz=r.cqz, ituz=r.ituz, cont=r.cont)
        sent = self.sent_values()
        if self.edit_id is not None:
            old = self.db.get(self.edit_id) or {}
            serial = old.get("stx")
        q.update(store_exchange(self.cdef, self._values(), sent, serial, they_wve))
        if r and r.dxcc == regions.CHINA_DXCC and not q.get("state"):
            g = regions.guess_cn_province(c)
            if g:
                q["state"] = g
        if pre.dupe:
            q["dupe_flag"], q["dupe_of"] = "DUPE", pre.dupe_of
        if self.edit_id is not None:
            with self.db.batch(tr("Edit contest QSO")):
                self.db.update(self.edit_id, {k: v for k, v in q.items() if k != "contest_session_id"})
            self.message.emit(tr("Saved") + f" {c}")
            self.wipe()
            return
        q["time_on"] = now
        q["time_off"] = now
        prof = self.profile() or {}
        op = self.op_field.resolve(prof) if prof else None
        if prof and op is None and is_club(prof):
            return
        for col in ("station_callsign", "my_gridsquare", "my_cq_zone", "my_itu_zone", "my_dxcc",
                    "my_state", "my_rig", "my_antenna"):
            if prof.get(col) not in (None, ""):
                q[col] = prof[col]
        if op:
            q["operator"] = op
        if prof.get("id"):
            q["station_profile_id"] = prof["id"]
        if prof.get("tx_pwr"):
            q["tx_pwr"] = prof["tx_pwr"]
        with self.db.batch(tr("Log QSO") + f" {c}"):
            qid = self.db.insert(q)
        self.win.ops.touch(prof)
        self.session["next_serial"] = serial + 1
        self.db.update_contest_session(self.session["id"], next_serial=serial + 1)
        row = self.db.get(qid)
        res = self.engine.add(row) if row else pre
        self.message.emit(tr("Logged") + f" {c}  +{res.points} " + tr("pts") +
                          (f"  · {tr('new mults')}: " + ", ".join(v for _, v in res.new_mults) if res.new_mults else ""))
        self._update_sent()
        self._refresh_score()
        self.scp.add([c])
        self.wipe()

    def edit_last(self) -> None:
        if not self.session:
            return
        q = self.db.last_qso("contest_session_id = ?", [self.session["id"]])
        if q:
            self.edit_qso(q["id"])

    def edit_qso(self, qid: int) -> None:
        q = self.db.get(qid)
        if not q or not self.cdef:
            return
        self.wipe()
        self.edit_id = qid
        self.call.setText(q.get("call") or "")
        ex = exchange_values(self.cdef, q)
        for k, e in self.fields.items():
            e.setText(ex.get(k, "") if ex.get(k) != "-" else "")
        i = self.band.findData(q.get("band"))
        if i >= 0:
            self.band.setCurrentIndex(i)
        self.mode.setCurrentText(modes.display(q.get("mode"), q.get("submode")))
        self.log_btn.setText(tr("Save"))
        self.refresh_checks()
        self.call.setFocus()

    def apply_lookup(self, call: str, data: dict) -> None:  # lookups are not used in contest mode
        return

    # ------------------------------------------------------------------ score
    def _on_db(self, kind: str, ids: list[int]) -> None:
        if kind in ("delete", "reset", "update"):
            self._rebuild.start()

    def rebuild(self) -> None:
        if not self.session or not self.cdef:
            self.engine = None
            self.score_lab.setText("")
            self.score_sub.setText("")
            self._refresh_score()
            return
        self.engine = Engine(self.cdef, Me.from_profile(self.profile()))
        self.engine.rebuild(list(self.db.iter_all("contest_session_id = ?", [self.session["id"]])))
        self._refresh_score()
        self.refresh_checks()

    def _refresh_score(self) -> None:
        e = self.engine
        if not e:
            self.score_table.setRowCount(0)
            self.mults.setText("")
            return
        self.score_lab.setText(f"{e.score:,}")
        self.score_sub.setText(f"{e.qso_count} {tr('QSOs')}  ·  {e.points_total} {tr('pts')}  ·  "
                               f"{e.mult_total} {tr('mults')}" + (f"  ·  {e.dupe_count} {tr('dupes')}"
                                                                 if e.dupe_count else ""))
        keys = [m["key"] for m in self.cdef.mults]
        labels = [tr(m.get("label", m["key"])) for m in self.cdef.mults]
        heads = [tr("Band"), tr("QSOs"), tr("Points"), *labels]
        self.score_table.setColumnCount(len(heads))
        self.score_table.setHorizontalHeaderLabels(heads)
        rows = [b for b in self.cdef.bands if b in e.rows]
        self.score_table.setRowCount(len(rows) + 1)
        for i, b in enumerate(rows):
            r = e.rows[b]
            vals = [bands.display_band(b), r.qsos, r.points, *[r.mults.get(k, 0) for k in keys]]
            for j, v in enumerate(vals):
                self.score_table.setItem(i, j, QTableWidgetItem(str(v)))
        totals = e.mult_counts()
        vals = [tr("Total"), e.qso_count, e.points_total, *[totals.get(k, 0) for k in keys]]
        for j, v in enumerate(vals):
            it = QTableWidgetItem(str(v))
            f = it.font()
            f.setBold(True)
            it.setFont(f)
            self.score_table.setItem(len(rows), j, it)
        band = self.band.currentData()
        modecat = mode_category(*modes.normalize(self.mode.currentText()))
        parts = []
        for m in self.cdef.mults:
            worked = e.worked_mults(m["key"], band, modecat)
            scope = {"band": bands.display_band(band), "band_mode": f"{bands.display_band(band)} {modecat}",
                     "mode": modecat, "contest": tr("whole contest")}[m.get("per", "band")]
            line = f"<b>{tr(m.get('label', m['key']))}</b> · {scope}: {len(worked)} {tr('worked')}"
            if m.get("value") == "exch:zone" or m.get("value") in ("cqz", "ituz"):
                top = 40 if (self.cdef.field("zone") or {}).get("max", 40) <= 40 else 90
                need = [str(z) for z in range(1, top + 1) if str(z) not in worked]
                if len(need) < top:
                    line += f"<br><span style='color:{theme.tokens().muted}'>{tr('needed')}: " + \
                            " ".join(need) + "</span>"
            elif worked:
                line += f"<br><span style='color:{theme.tokens().muted}'>" + " ".join(sorted(worked)[:80]) + "</span>"
            parts.append(line)
        self.mults.setText("<br><br>".join(parts))
        self._refresh_rate()

    def _refresh_rate(self) -> None:
        if not self.engine:
            return
        r = self.engine.rate()
        for k, lab in self.rate_labels.items():
            lab.setText(f"{r[k]:.0f}" + ("" if k == "hour" else "/h"))

    # ------------------------------------------------------------------ SCP
    def _scp_path(self) -> Path:
        return scpmod.path_for(self.db.path)

    def _load_scp(self) -> None:
        self.scp = Scp.from_file(self._scp_path())
        n_master = len(self.scp.calls)
        self.scp.add(self.db.distinct("call", limit=200000))
        self.scp_lab.setText(f"MASTER.SCP: {n_master:,} · " + tr("with your log") + f": {len(self.scp.calls):,}")

    def _download_scp(self) -> None:
        path = self._scp_path()

        def done(_n):
            self._load_scp()
            self.message.emit(tr("MASTER.SCP updated"))
            self.scp_updated.emit()

        run_async(scpmod.download, path, on_done=done, on_error=lambda m: QMessageBox.warning(self, "MASTER.SCP", m))

    # ------------------------------------------------------------------ sessions
    def new_session(self) -> None:
        from .setup_dialog import SetupDialog  # noqa: PLC0415

        self.reload_defs()
        dlg = SetupDialog(self.db, self.defs, None, self)
        if dlg.exec():
            sid = dlg.save()
            self.reload_sessions(select=sid)

    def edit_session(self) -> None:
        if not self.session:
            self.new_session()
            return
        from .setup_dialog import SetupDialog  # noqa: PLC0415

        dlg = SetupDialog(self.db, self.defs, self.session, self)
        if dlg.exec():
            sid = dlg.save()
            self.reload_sessions(select=sid)

    def edit_definitions(self) -> None:
        from .udc_dialog import UdcDialog  # noqa: PLC0415

        dlg = UdcDialog(self.defs, user_contest_dir(self.db), self)
        dlg.exec()
        self.reload_defs()
        self._session_picked()

    def export_cabrillo(self) -> None:
        if not self.session or not self.cdef or not self.engine:
            return
        me = Me.from_profile(self.profile())
        default = Path(self.db.path).parent / f"{me.call or 'log'}-{self.cdef.cabrillo}-" \
                                             f"{self.session['started_at'][:10]}.cbr".replace("/", "-")
        path, _ = QFileDialog.getSaveFileName(self, tr("Export Cabrillo"), str(default), "Cabrillo (*.cbr *.log)")
        if not path:
            return
        qsos = list(self.db.iter_all("contest_session_id = ?", [self.session["id"]]))
        n = cabrillo.export(path, self.cdef, self.session, qsos, me, self.engine.score)
        self.message.emit(tr("Exported") + f" {n} " + tr("QSOs to") + f" {path}")
        QMessageBox.information(self, tr("Cabrillo"), tr("Exported") + f" {n} " + tr("QSOs") + f"\n{path}\n\n" +
                                tr("Claimed score") + f": {self.engine.score:,}\n" + tr(self.cdef.notes or ""))

    def qso_summary(self, q: dict) -> str:
        return qsomod.summary(q)
