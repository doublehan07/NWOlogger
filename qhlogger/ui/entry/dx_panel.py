"""DX entry panel (MacLoggerDX-like workflow, no rig panel)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import i18n
from ...core import bands, callsign, dupes, dxcc, geo, modes, regions, sats, timeutil
from ...core import qso as qsomod
from .. import theme
from ..widgets import AutoLineEdit, Chip, Section, UtcDateTimeEdit, fmt, mode_text, primary

tr = i18n.tr

TEXT_COLS = ["first_name", "last_name", "country", "state", "qth", "address", "cqz", "ituz", "dxcc", "gridsquare",
             "qsl_via", "email", "web", "iota", "skcc", "sota_ref", "pota_ref", "wwff_ref", "comment", "notes",
             "rst_sent", "rst_rcvd", "tx_pwr", "freq", "freq_rx", "sat_mode"]
# Filled from your own earlier QSOs with the same call.
FROM_LOG_COLS = ["first_name", "last_name", "qth", "address", "state", "gridsquare", "email", "web", "qsl_via",
                 "iota"]
LOOKUP_COLS = ["first_name", "last_name", "qth", "address", "state", "cnty", "country", "gridsquare", "email",
               "web", "qsl_via", "cqz", "ituz", "dxcc", "iota"]
PROFILE_COLS = ["station_callsign", "operator", "my_gridsquare", "my_vucc_grids", "my_dxcc", "my_cq_zone",
                "my_itu_zone", "my_state", "my_cnty", "my_pota_ref", "my_sota_ref", "my_wwff_ref", "my_rig",
                "my_antenna"]


class DXPanel(QWidget):
    logged = Signal(int)
    saved = Signal(int)
    message = Signal(str)
    lookup_requested = Signal(str)
    profiles_requested = Signal()

    def __init__(self, db, prefs, parent=None):
        super().__init__(parent)
        self.db = db
        self.prefs = prefs
        self.cty = dxcc.default()
        self.sats = sats.default()
        self.edit_id: int | None = None
        self.time_on_locked = False
        self.time_off_locked = False
        self._hidden: dict[str, Any] = {}  # cont etc. from cty, not shown
        self._last_lookup_call = ""
        self.w: dict[str, Any] = {}
        self._build()
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick)
        self._clock.start(1000)
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(150)
        self._check_timer.timeout.connect(self._refresh_checks)
        self._lookup_timer = QTimer(self)
        self._lookup_timer.setSingleShot(True)
        self._lookup_timer.setInterval(900)
        self._lookup_timer.timeout.connect(self._auto_lookup)
        self.reload_profiles()
        self.clear_form(keep_sticky=False)

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 6)
        root.setSpacing(8)

        # Call row
        top = QHBoxLayout()
        top.setSpacing(10)
        self.call = AutoLineEdit()
        self.call.setObjectName("CallEdit")
        self.call.setPlaceholderText(tr("Call"))
        self.call.setMaximumWidth(260)
        self.call.setMinimumWidth(200)
        self.call.textEdited.connect(self._call_edited)
        self.call.editingFinished.connect(self._call_finished)
        top.addWidget(self.call)
        chips = QVBoxLayout()
        chips.setSpacing(3)
        self.chip_row = QHBoxLayout()
        self.chip_row.setSpacing(5)
        self.chips: list[Chip] = []
        chips.addLayout(self.chip_row)
        self.summary = QLabel("")
        self.summary.setObjectName("Summary")
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        chips.addWidget(self.summary)
        top.addLayout(chips, 1)
        self.edit_banner = Chip("", "info")
        self.edit_banner.hide()
        top.addWidget(self.edit_banner)
        prof_box = QVBoxLayout()
        prof_box.setSpacing(2)
        lab = QLabel(tr("Station"))
        lab.setObjectName("Hint")
        self.profile = QComboBox()
        self.profile.setMinimumWidth(200)
        self.profile.currentIndexChanged.connect(self._profile_changed)
        prof_box.addWidget(lab)
        prof_box.addWidget(self.profile)
        top.addLayout(prof_box)
        root.addLayout(top)

        # Sections
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        root.addLayout(grid, 1)

        c = Section(tr("Contact"))
        self.w["time_on"] = UtcDateTimeEdit()
        self.w["time_on"].dateTimeChanged.connect(lambda *_: self._time_edited("on"))
        on_now = QPushButton(tr("Now"))
        on_now.setToolTip(tr("Set Time On to now (UTC)"))
        on_now.clicked.connect(lambda: self.stamp("on"))
        self.w["time_off"] = UtcDateTimeEdit()
        self.w["time_off"].dateTimeChanged.connect(lambda *_: self._time_edited("off"))
        off_now = QPushButton(tr("Now"))
        off_now.setToolTip(tr("Set Time Off to now (UTC)"))
        off_now.clicked.connect(lambda: self.stamp("off"))
        c.row(0, tr("Time On"), self.w["time_on"])
        c.grid.addWidget(on_now, 0, 2)
        c.row(1, tr("Time Off"), self.w["time_off"])
        c.grid.addWidget(off_now, 1, 2)
        self.w["freq"] = AutoLineEdit(mono=True)
        self.w["freq"].setPlaceholderText(tr("TX MHz"))
        self.band_lab = QLabel("")
        self.band_lab.setObjectName("BandLabel")
        self.band_lab.setMinimumWidth(44)
        c.row(2, tr("MHz"), self.w["freq"])
        c.grid.addWidget(self.band_lab, 2, 2)
        self.w["freq_rx"] = AutoLineEdit(mono=True)
        self.w["freq_rx"].setPlaceholderText(tr("RX MHz (split / sat)"))
        self.band_rx_lab = QLabel("")
        self.band_rx_lab.setObjectName("BandLabel")
        c.row(3, tr("RX MHz"), self.w["freq_rx"])
        c.grid.addWidget(self.band_rx_lab, 3, 2)
        self.mode = QComboBox()
        self.mode.setEditable(True)
        self.mode.addItems(modes.COMMON_MODES)
        self.mode.setCurrentText("SSB")
        c.row(4, tr("Mode"), self.mode)
        self.w["tx_pwr"] = AutoLineEdit(mono=True)
        c.row(5, tr("Power"), self.w["tx_pwr"])
        rst = QHBoxLayout()
        self.w["rst_sent"] = AutoLineEdit(mono=True)
        self.w["rst_rcvd"] = AutoLineEdit(mono=True)
        self.w["rst_sent"].setPlaceholderText(tr("Sent"))
        self.w["rst_rcvd"].setPlaceholderText(tr("Rcvd"))
        rst.addWidget(self.w["rst_sent"])
        rst.addWidget(self.w["rst_rcvd"])
        rst_w = QWidget()
        rst_w.setLayout(rst)
        rst.setContentsMargins(0, 0, 0, 0)
        c.row(6, tr("RST"), rst_w)
        self.sat = QComboBox()
        self.sat.setEditable(True)
        self.sat.addItem("")
        self.sat.addItems(self.sats.names())
        self.sat.lineEdit().setPlaceholderText(tr("none"))
        self.sat.currentTextChanged.connect(self._sat_changed)
        c.row(7, tr("Satellite"), self.sat)
        sm = QHBoxLayout()
        sm.setContentsMargins(0, 0, 0, 0)
        self.w["sat_mode"] = AutoLineEdit(mono=True)
        self.w["sat_mode"].setPlaceholderText(tr("Sat mode"))
        self.prop = QComboBox()
        self.prop.setEditable(True)
        self.prop.addItems(["", "SAT", "EME", "ES", "F2", "TR", "MS", "AUR", "RPT", "ION", "INTERNET"])
        self.prop.setToolTip(tr("Propagation mode"))
        sm.addWidget(self.w["sat_mode"])
        sm.addWidget(self.prop)
        sm_w = QWidget()
        sm_w.setLayout(sm)
        c.row(8, tr("Sat mode"), sm_w)
        c.grid.setColumnStretch(1, 1)
        grid.addWidget(c, 0, 0)

        s = Section(tr("Station & location"))
        for r, (col, label) in enumerate([("first_name", "First"), ("last_name", "Last"), ("country", "Country"),
                                          ("state", "Province"), ("qth", "City"), ("address", "Address")]):
            self.w[col] = AutoLineEdit()
            s.row(r, tr(label), self.w[col])
        self.w["state"].setPlaceholderText(tr("e.g. BJ, SC, CA"))
        zones = QHBoxLayout()
        zones.setContentsMargins(0, 0, 0, 0)
        for col, ph in (("cqz", "CQ"), ("ituz", "ITU"), ("dxcc", "DXCC")):
            self.w[col] = AutoLineEdit(mono=True)
            self.w[col].setPlaceholderText(ph)
            self.w[col].setToolTip(tr(ph + " zone") if col != "dxcc" else tr("DXCC entity number"))
            zones.addWidget(self.w[col])
        zw = QWidget()
        zw.setLayout(zones)
        s.row(6, tr("Zones"), zw)
        self.w["gridsquare"] = AutoLineEdit(mono=True)
        self.w["gridsquare"].textChanged.connect(lambda *_: self._check_timer.start())
        s.row(7, tr("Grid"), self.w["gridsquare"])
        s.grid.setColumnStretch(1, 1)
        grid.addWidget(s, 0, 1)

        m = Section(tr("References & notes"))
        for r, (col, label) in enumerate([("qsl_via", "QSL Via"), ("email", "Email"), ("web", "Web")]):
            self.w[col] = AutoLineEdit()
            m.row(r, tr(label), self.w[col])
        refs = QHBoxLayout()
        refs.setContentsMargins(0, 0, 0, 0)
        for col, ph in (("iota", "IOTA"), ("sota_ref", "SOTA"), ("pota_ref", "POTA")):
            self.w[col] = AutoLineEdit(mono=True)
            self.w[col].setPlaceholderText(ph)
            refs.addWidget(self.w[col])
        rw = QWidget()
        rw.setLayout(refs)
        m.row(3, tr("Refs"), rw)
        refs2 = QHBoxLayout()
        refs2.setContentsMargins(0, 0, 0, 0)
        for col, ph in (("wwff_ref", "WWFF"), ("skcc", "SKCC")):
            self.w[col] = AutoLineEdit(mono=True)
            self.w[col].setPlaceholderText(ph)
            refs2.addWidget(self.w[col])
        rw2 = QWidget()
        rw2.setLayout(refs2)
        m.row(4, "", rw2)
        self.w["comment"] = AutoLineEdit()
        self.w["comment"].setToolTip(tr("Comment: may be sent to QSL services"))
        m.row(5, tr("Comment"), self.w["comment"])
        self.w["notes"] = AutoLineEdit()
        self.w["notes"].setToolTip(tr("Notes: private, never uploaded"))
        m.row(6, tr("Notes"), self.w["notes"])
        m.grid.setColumnStretch(1, 1)
        grid.addWidget(m, 0, 2)

        wb = Section(tr("Worked before"))
        self.matrix = QTableWidget(0, 0)
        self.matrix.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.matrix.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.matrix.verticalHeader().setDefaultSectionSize(20)
        self.matrix.horizontalHeader().setDefaultSectionSize(54)
        self.matrix.setMaximumHeight(118)
        self.matrix.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        wb.add(self.matrix)
        self.history = QTableWidget(0, 5)
        self.history.setHorizontalHeaderLabels([tr("Time On"), tr("Band"), tr("Mode"), tr("Sat"), tr("Confirmed")])
        self.history.horizontalHeaderItem(4).setToolTip(tr("L = LoTW, E = eQSL, Q = QRZ.com, P = paper card"))
        self.history.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history.verticalHeader().setVisible(False)
        self.history.verticalHeader().setDefaultSectionSize(20)
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.history.horizontalHeader().setStretchLastSection(True)
        self.history.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.history.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        wb.add(self.history, 1)
        grid.addWidget(wb, 0, 3)
        for i, stretch in enumerate((5, 5, 5, 6)):
            grid.setColumnStretch(i, stretch)

        # Buttons
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.lookup_btn = QPushButton(tr("Lookup"))
        self.lookup_btn.setIcon(theme.icon("search"))
        self.lookup_btn.setToolTip(tr("Look up the call on QRZ.com / HamQTH"))
        self.lookup_btn.clicked.connect(lambda: self.request_lookup(force=True))
        prev = QPushButton(tr("Previous"))
        prev.setIcon(theme.icon("history"))
        prev.setToolTip(tr("Copy the fields of your last QSO"))
        prev.clicked.connect(self.recall_previous)
        clear = QPushButton(tr("Clear"))
        clear.setToolTip(tr("Clear the form (Esc)"))
        clear.clicked.connect(lambda: self.clear_form())
        bar.addWidget(self.lookup_btn)
        bar.addWidget(prev)
        bar.addWidget(clear)
        bar.addSpacing(12)
        sticky_lab = QLabel(tr("Keep:"))
        sticky_lab.setProperty("muted", True)
        bar.addWidget(sticky_lab)
        self.sticky: dict[str, QCheckBox] = {}
        on = set(self.prefs.get("sticky"))
        for key, label in (("freq", "Freq"), ("mode", "Mode"), ("sat", "Sat"), ("power", "Power")):
            cb = QCheckBox(tr(label))
            cb.setChecked(key in on)
            cb.toggled.connect(self._sticky_changed)
            self.sticky[key] = cb
            bar.addWidget(cb)
        bar.addStretch(1)
        self.delete_btn = QPushButton(tr("Delete"))
        self.delete_btn.setProperty("danger", True)
        self.delete_btn.clicked.connect(self._delete_editing)
        self.cancel_btn = QPushButton(tr("Cancel"))
        self.cancel_btn.clicked.connect(lambda: self.clear_form())
        self.log_btn = primary(tr("Log QSO"))
        self.log_btn.setMinimumWidth(140)
        self.log_btn.setToolTip(tr("Log the QSO (Enter)"))
        self.log_btn.clicked.connect(self.commit)
        bar.addWidget(self.delete_btn)
        bar.addWidget(self.cancel_btn)
        bar.addWidget(self.log_btn)
        root.addLayout(bar)

        # Signals for live checks
        self.w["freq"].textChanged.connect(self._freq_changed)
        self.w["freq_rx"].textChanged.connect(self._freq_changed)
        self.mode.currentTextChanged.connect(self._mode_changed)
        self.prop.currentTextChanged.connect(lambda *_: self._check_timer.start())
        for wdg in self.w.values():
            if isinstance(wdg, AutoLineEdit):
                wdg.returnPressed.connect(self.commit)
        self.call.returnPressed.connect(self.commit)
        for cb in (self.mode, self.sat, self.prop):
            cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            cb.lineEdit().returnPressed.connect(self.commit)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=lambda: self.clear_form())

    # ------------------------------------------------------------------ profile
    def reload_profiles(self) -> None:
        cur = self.prefs.value("current_profile")
        self.profile.blockSignals(True)
        self.profile.clear()
        for p in self.db.profiles():
            label = p.get("name") or p.get("station_callsign") or "?"
            if p.get("my_gridsquare") and p["my_gridsquare"] not in label:
                label += f"  ·  {p['my_gridsquare']}"
            self.profile.addItem(label, p["id"])
        self.profile.addItem(tr("Edit station profiles…"), -1)
        i = self.profile.findData(int(cur)) if cur not in (None, "") else -1
        self.profile.setCurrentIndex(i if i >= 0 else 0)
        self.profile.blockSignals(False)

    def _profile_changed(self) -> None:
        pid = self.profile.currentData()
        if pid == -1:
            self.reload_profiles()
            self.profiles_requested.emit()
            return
        self.prefs.set_value("current_profile", pid)
        self._check_timer.start()

    def current_profile(self) -> dict | None:
        pid = self.profile.currentData()
        return self.db.profile(pid) if pid not in (None, -1) else self.db.default_profile()

    # ------------------------------------------------------------------ time
    def _tick(self) -> None:
        if not self.time_on_locked and self.edit_id is None:
            self.w["time_on"].set_iso(timeutil.now_iso())
        if not self.time_off_locked and self.edit_id is None:
            self.w["time_off"].set_iso(timeutil.now_iso())

    def _time_edited(self, which: str) -> None:
        if which == "on":
            self.time_on_locked = True
            self._check_timer.start()
        else:
            self.time_off_locked = True

    def stamp(self, which: str) -> None:
        now = timeutil.now_iso()
        if which == "on":
            self.w["time_on"].set_iso(now)
            self.time_on_locked = True
            self._check_timer.start()
        else:
            self.w["time_off"].set_iso(now)
            self.time_off_locked = True

    # ------------------------------------------------------------------ field access
    def _get(self, col: str) -> Any:
        if col == "mode":
            return self.mode.currentText().strip() or None
        if col == "sat_name":
            return self.sat.currentText().strip().upper() or None
        if col == "prop_mode":
            return self.prop.currentText().strip().upper() or None
        w = self.w.get(col)
        if w is None:
            return None
        if isinstance(w, UtcDateTimeEdit):
            return w.iso()
        return w.text().strip() or None

    def _set(self, col: str, v: Any, *, auto: bool = False, guess: bool = False, source: str = "") -> None:
        if col == "mode":
            self.mode.setCurrentText(mode_text({"mode": v}) if v else "")
            return
        if col == "sat_name":
            self.sat.setCurrentText(v or "")
            return
        if col == "prop_mode":
            self.prop.setCurrentText(v or "")
            return
        w = self.w.get(col)
        if w is None:
            return
        if isinstance(w, UtcDateTimeEdit):
            w.set_iso(v)
            return
        txt = fmt(col, v) if col in ("freq", "freq_rx", "tx_pwr") else ("" if v is None else str(v))
        if col == "freq" or col == "freq_rx":
            txt = txt.rstrip("0").rstrip(".") if txt else txt
        if auto:
            w.set_auto(txt, guess=guess, source=source)
        else:
            w.setText(txt)
            w.setCursorPosition(0)
            w._edited("")

    def values(self) -> dict:
        q: dict[str, Any] = {"call": callsign.clean(self.call.text()) or None}
        for col in TEXT_COLS:
            q[col] = self._get(col)
        q["time_on"] = self._get("time_on")
        q["time_off"] = self._get("time_off")
        m, s = modes.normalize(self._get("mode"))
        q["mode"], q["submode"] = m, s
        q["sat_name"] = self._get("sat_name")
        q["prop_mode"] = self._get("prop_mode") or ("SAT" if q["sat_name"] else None)
        q.update(self._hidden)
        return q

    # ------------------------------------------------------------------ call handling
    def _call_edited(self, text: str) -> None:
        up = text.upper().replace(" ", "")
        if up != text:
            pos = self.call.cursorPosition()
            self.call.setText(up)
            self.call.setCursorPosition(pos)
        if up and not self.time_on_locked and self.edit_id is None:
            self.w["time_on"].set_iso(timeutil.now_iso())
            self.time_on_locked = True
        if not up:
            if self.edit_id is None:
                self.time_on_locked = False
            self._clear_auto()
        self._resolve_call()
        self._check_timer.start()
        if self.prefs.get("auto_lookup"):
            self._lookup_timer.start()

    def _call_finished(self) -> None:
        if self.prefs.get("auto_lookup") and self.call.text().strip():
            self._lookup_timer.stop()
            self.request_lookup()

    def _auto_lookup(self) -> None:
        if len(self.call.text().strip()) >= 3:
            self.request_lookup()

    def request_lookup(self, force: bool = False) -> None:
        c = callsign.clean(self.call.text())
        if not c or (c == self._last_lookup_call and not force):
            return
        self._last_lookup_call = c
        self.lookup_requested.emit(c)

    def apply_lookup(self, call: str, data: dict, source: str) -> None:
        """Fill empty fields from a QRZ / HamQTH result (tinted)."""
        if callsign.clean(self.call.text()) != call.upper():
            return  # stale
        for col in LOOKUP_COLS:
            v = data.get(col)
            if v in (None, ""):
                continue
            if col == "state" and (data.get("dxcc") == regions.CHINA_DXCC or self._get("dxcc") == "318"):
                v = regions.normalize_cn(str(v)) or v
            self._set(col, v, auto=True, source=source)
        self._check_timer.start()

    def _clear_auto(self) -> None:
        for w in self.w.values():
            if isinstance(w, AutoLineEdit) and w.is_auto():
                w.clear_all()
        self._hidden = {}
        self._last_lookup_call = ""

    def _resolve_call(self) -> None:
        c = callsign.clean(self.call.text())
        if not c:
            return
        r = self.cty.resolve(c)
        self._hidden = {}
        if r:
            self._set("country", r.country, auto=True, source="cty")
            self._set("dxcc", r.dxcc, auto=True, source="cty")
            self._set("cqz", r.cqz, auto=True, source="cty")
            self._set("ituz", r.ituz, auto=True, source="cty")
            self._hidden["cont"] = r.cont
            if r.dxcc == regions.CHINA_DXCC:
                g = regions.guess_cn_province(c)
                if g:
                    self._set("state", g, auto=True, guess=True)
        # Fill from your own earlier QSOs with this call.
        if self.edit_id is None and callsign.looks_like_call(c):
            last = self.db.last_qso("call = ?", [c])
            if last:
                for col in FROM_LOG_COLS:
                    if last.get(col):
                        self._set(col, last[col], auto=True, source=tr("your log"))

    # ------------------------------------------------------------------ live checks
    def _freq_changed(self) -> None:
        for col, lab in (("freq", self.band_lab), ("freq_rx", self.band_rx_lab)):
            try:
                b = bands.band_for_freq(float(self._get(col) or ""))
            except ValueError:
                b = None
            lab.setText(bands.display_band(b) if b else "")
        self._check_timer.start()

    def _mode_changed(self, text: str) -> None:
        m, s = modes.normalize(text)
        rst = modes.default_rst(m, s)
        for col in ("rst_sent", "rst_rcvd"):
            w = self.w[col]
            if not w.text() or w.is_auto():
                w.set_auto(rst)
        self._check_timer.start()

    def _sat_changed(self, name: str) -> None:
        name = name.strip().upper()
        if not name:
            if self.prop.currentText() == "SAT":
                self.prop.setCurrentText("")
            self._check_timer.start()
            return
        self.prop.setCurrentText("SAT")
        last = self.db.last_qso("sat_name = ?", [name])
        sat = self.sats.get(name)
        up = last.get("freq") if last else (sat.uplink if sat else None)
        down = last.get("freq_rx") if last else (sat.downlink if sat else None)
        mode = (mode_text(last) if last else None) or (sat.mode if sat else None)
        smode = (last.get("sat_mode") if last else None) or (sat.sat_mode if sat else None)
        if up:
            self._set("freq", up)
        if down:
            self._set("freq_rx", down)
        if mode:
            self.mode.setCurrentText(mode)
        if smode:
            self._set("sat_mode", smode)
        self._check_timer.start()

    def _sticky_changed(self) -> None:
        self.prefs.set("sticky", [k for k, cb in self.sticky.items() if cb.isChecked()])

    def _set_chips(self, chips: list[tuple[str, str]]) -> None:
        while self.chip_row.count():
            item = self.chip_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.chips = []
        for text, kind in chips:
            ch = Chip(text, kind)
            self.chip_row.addWidget(ch)
            self.chips.append(ch)
        self.chip_row.addStretch(1)

    def current_dupe(self) -> dupes.DupeHit | None:
        q = qsomod.normalize(self.values())
        if not q.get("call") or not q.get("band"):
            return None
        return dupes.check(self.db, q, block_s=self.prefs.get("block_seconds"),
                           warn_s=self.prefs.get("warn_hours") * 3600, exclude_id=self.edit_id)

    def _refresh_checks(self) -> None:
        c = callsign.clean(self.call.text())
        q = qsomod.normalize(self.values())
        chips: list[tuple[str, str]] = []
        hit = self.current_dupe() if c else None
        blocked = False
        if hit:
            when = fmt("time_on", hit.other.get("time_on"))
            if hit.level == "DUPE":
                blocked = not self.prefs.get("allow_dupes")
                chips.append((tr("DUPE"), "bad"))
                self.call.setToolTip(tr("Same mode within the block window as the QSO at") + f" {when}")
            else:
                chips.append((tr("Possible dupe"), "warn"))
                self.call.setToolTip(tr("Close to the QSO at") + f" {when} {mode_text(hit.other)}")
        else:
            self.call.setToolTip("")
        self.call.setProperty("dupe", hit.level if hit else "")
        theme.repolish(self.call)
        self.log_btn.setEnabled(bool(c) and not blocked)
        summary_parts: list[str] = []
        if c:
            dx = q.get("dxcc")
            band = q.get("band")
            group = q.get("mode_group")
            n = self.db.count("call = ?" + (" AND id != ?" if self.edit_id else ""),
                              [c, self.edit_id] if self.edit_id else [c])
            if dx:
                if self.db.count("dxcc = ?" + (" AND id != ?" if self.edit_id else ""),
                                 [dx, self.edit_id] if self.edit_id else [dx]) == 0:
                    chips.append((tr("NEW DXCC"), "ok"))
                else:
                    if band and self.db.count("dxcc = ? AND band = ?", [dx, band]) == 0:
                        chips.append((tr("NEW BAND"), "ok"))
                    if group and self.db.count("dxcc = ? AND mode_group = ?", [dx, group]) == 0:
                        chips.append((tr("NEW MODE"), "ok"))
            if n:
                chips.append((tr("Worked") + f" {n}×", "info"))
            r = self.cty.resolve(c)
            if r:
                summary_parts.append(f"{r.country} · {r.cont} · CQ {r.cqz} · ITU {r.ituz}")
            conf = self.db.last_qso(
                "call = ? AND (lotw_qsl_rcvd='Y' OR eqsl_qsl_rcvd='Y' OR qrz_qsl_rcvd='Y' OR qsl_rcvd='Y')", [c])
            if conf:
                d = conf.get("lotw_qslrdate") or conf.get("qrz_qslrdate") or conf.get("qslrdate") or \
                    conf.get("eqsl_qslrdate") or (conf.get("time_on") or "")[:10]
                summary_parts.append(f"{tr('Confirmed')} {d} {tr('on')} {bands.display_band(conf.get('band'))} "
                                     f"{mode_text(conf)}")
            prof = self.current_profile() or {}
            p = geo.path(prof.get("my_gridsquare"), q.get("gridsquare"),
                         None if q.get("gridsquare") else ((r.lat, r.lon) if r else None))
            if p:
                summary_parts.append(f"Az {p[0]:.0f}° · LP {p[1]:.0f}° · {p[2]:,.0f} km")
        self._set_chips(chips)
        self.summary.setText("   —   ".join(summary_parts))
        self._refresh_history(c, q.get("band"))

    def _refresh_history(self, call: str, band: str | None) -> None:
        rows = self.db.worked_before(call, 60) if call else []
        if self.edit_id:
            rows = [r for r in rows if r["id"] != self.edit_id]
        self.history.setRowCount(len(rows))
        t = theme.tokens()
        for i, r in enumerate(rows):
            cfm = "".join(letter for col, letter in (("lotw_qsl_rcvd", "L"), ("eqsl_qsl_rcvd", "E"),
                                                    ("qrz_qsl_rcvd", "Q"), ("qsl_rcvd", "P")) if r.get(col) == "Y")
            vals = [fmt("time_on", r.get("time_on"))[:16], bands.display_band(r.get("band")), mode_text(r),
                    r.get("sat_name") or "", cfm]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j == 4 and v:
                    it.setForeground(QColor(t.ok))
                if j == 0:
                    it.setFont(theme.mono_font())
                self.history.setItem(i, j, it)
        # Band × mode-group matrix
        groups = ["CW", "PHONE", "DATA", "IMAGE"]
        used = sorted({r.get("band") for r in rows if r.get("band")} | ({band} if band else set()),
                      key=bands.band_order)
        self.matrix.setColumnCount(len(used))
        self.matrix.setRowCount(len(groups))
        self.matrix.setHorizontalHeaderLabels([bands.display_band(b) for b in used])
        self.matrix.setVerticalHeaderLabels([tr(x) for x in ("CW", "Phone", "Data", "Image")])
        for gi, g in enumerate(groups):
            for bi, b in enumerate(used):
                worked = [r for r in rows if r.get("band") == b and r.get("mode_group") == g]
                cfm = any(r.get(c) == "Y" for r in worked for c in ("lotw_qsl_rcvd", "eqsl_qsl_rcvd", "qrz_qsl_rcvd",
                                                                      "qsl_rcvd"))
                it = QTableWidgetItem("✓" if cfm else ("•" if worked else ""))
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if cfm:
                    it.setForeground(QColor(t.ok))
                if b == band:
                    it.setBackground(QColor(t.accent_soft))
                self.matrix.setItem(gi, bi, it)

    # ------------------------------------------------------------------ actions
    def clear_form(self, keep_sticky: bool = True) -> None:
        keep = {k for k, cb in self.sticky.items() if cb.isChecked()} if keep_sticky else set()
        freq, freq_rx = self._get("freq"), self._get("freq_rx")
        mode, power = self.mode.currentText(), self._get("tx_pwr")
        sat, smode, prop = self.sat.currentText(), self._get("sat_mode"), self.prop.currentText()
        self.edit_id = None
        self.call.blockSignals(True)
        self.call.clear()
        self.call.blockSignals(False)
        for w in self.w.values():
            if isinstance(w, AutoLineEdit):
                w.clear_all()
        self._hidden = {}
        self._last_lookup_call = ""
        self.sat.blockSignals(True)
        self.sat.setCurrentText(sat if "sat" in keep else "")
        self.sat.blockSignals(False)
        if "sat" in keep:
            self._set("sat_mode", smode)
            self.prop.setCurrentText(prop)
        else:
            self.prop.setCurrentText("")
        if "freq" in keep or ("sat" in keep and sat):
            self._set("freq", freq)
            self._set("freq_rx", freq_rx)
        if "mode" in keep or ("sat" in keep and sat):
            self.mode.setCurrentText(mode)
        if "power" in keep:
            self._set("tx_pwr", power)
        elif (self.current_profile() or {}).get("tx_pwr"):
            self._set("tx_pwr", self.current_profile()["tx_pwr"])
        self._mode_changed(self.mode.currentText())
        self.time_on_locked = False
        self.time_off_locked = False
        self._tick()
        self.edit_banner.hide()
        self.delete_btn.hide()
        self.cancel_btn.hide()
        self.log_btn.setText(tr("Log QSO"))
        self._freq_changed()
        self._refresh_checks()
        self.call.setFocus()

    def recall_previous(self) -> None:
        last = self.db.last_qso()
        if not last:
            return
        for col in ("freq", "freq_rx", "tx_pwr", "sat_mode"):
            self._set(col, last.get(col))
        self.mode.setCurrentText(mode_text(last))
        self.sat.blockSignals(True)
        self.sat.setCurrentText(last.get("sat_name") or "")
        self.sat.blockSignals(False)
        self.prop.setCurrentText(last.get("prop_mode") or "")
        self._freq_changed()

    def load_qso(self, qid: int) -> None:
        q = self.db.get(qid)
        if not q:
            return
        self.clear_form(keep_sticky=False)
        self.edit_id = qid
        self.call.setText(q.get("call") or "")
        for col in TEXT_COLS:
            self._set(col, q.get(col))
        self.w["time_on"].set_iso(q.get("time_on"))
        self.w["time_off"].set_iso(q.get("time_off") or q.get("time_on"))
        self.time_on_locked = self.time_off_locked = True
        self.mode.blockSignals(True)
        self.mode.setCurrentText(mode_text(q))
        self.mode.blockSignals(False)
        self.sat.blockSignals(True)
        self.sat.setCurrentText(q.get("sat_name") or "")
        self.sat.blockSignals(False)
        self.prop.setCurrentText(q.get("prop_mode") or "")
        self._hidden = {"cont": q.get("cont")} if q.get("cont") else {}
        self.edit_banner.setText(tr("Editing") + f"  {qsomod.summary(q)}")
        self.edit_banner.show()
        self.delete_btn.show()
        self.cancel_btn.show()
        self.log_btn.setText(tr("Save changes"))
        self._freq_changed()
        self._refresh_checks()
        self.call.setFocus()

    def _delete_editing(self) -> None:
        if self.edit_id is None:
            return
        q = self.db.get(self.edit_id)
        if q and QMessageBox.question(self, tr("Delete QSO"), tr("Delete this QSO?") + f"\n{qsomod.summary(q)}"
                                      ) == QMessageBox.StandardButton.Yes:
            self.db.delete([self.edit_id], label=tr("Delete QSO"))
            self.message.emit(tr("Deleted") + f" {qsomod.summary(q)}")
            self.clear_form()

    def commit(self) -> None:
        c = callsign.clean(self.call.text())
        if not c:
            self.call.setFocus()
            return
        q = self.values()
        if self.edit_id is None and not self.time_off_locked:
            q["time_off"] = timeutil.now_iso()
        nq = qsomod.normalize(q)
        if not nq.get("band") and nq.get("freq") is None:
            QMessageBox.warning(self, tr("Missing frequency"), tr("Enter the frequency (MHz) so the band is known."))
            self.w["freq"].setFocus()
            return
        if nq.get("time_off") and nq.get("time_on") and nq["time_off"] < nq["time_on"]:
            nq["time_off"] = nq["time_on"]
        hit = self.current_dupe()
        if hit and hit.level == "DUPE" and not self.prefs.get("allow_dupes"):
            self.message.emit(tr("Dupe: logging is blocked for the same mode within the block window."))
            return
        if self.edit_id is not None:
            before = self.db.get(self.edit_id) or {}
            changes = {k: v for k, v in nq.items() if k in before and before.get(k) != v}
            for k in ("dupe_flag", "dupe_of"):
                changes.pop(k, None)
            with self.db.batch(tr("Edit QSO")):
                self.db.update(self.edit_id, changes)
                self.db.update(self.edit_id, {"dupe_flag": hit.level if hit else None,
                                              "dupe_of": hit.other_id if hit else None},
                               track_service=False, normalized=True)
            qid = self.edit_id
            self.message.emit(tr("Saved") + f" {qsomod.summary(nq)}")
            self.saved.emit(qid)
            self.clear_form()
            return
        prof = self.current_profile() or {}
        for col in PROFILE_COLS:
            if prof.get(col) not in (None, ""):
                nq[col] = prof[col]
        if prof.get("id"):
            nq["station_profile_id"] = prof["id"]
        if hit:
            nq["dupe_flag"], nq["dupe_of"] = hit.level, hit.other_id
        with self.db.batch(tr("Log QSO")):
            qid = self.db.insert(nq)
        self.message.emit(tr("Logged") + f" {qsomod.summary(nq)}")
        self.logged.emit(qid)
        self.clear_form()
