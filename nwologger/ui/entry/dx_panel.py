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

from ... import applog, i18n
from ...core import (
    bands,
    calllists,
    callsign,
    devices,
    dupes,
    dxcc,
    geo,
    modes,
    regions,
    sats,
    similar,
    timeutil,
)
from ...core import qso as qsomod
from ...core.contest import scp as scpmod
from ...core.operators import OperatorSession, is_club
from .. import theme
from ..list_picker import ListPicker
from ..operator_field import OperatorField
from ..widgets import (
    OTHER,
    AutoLineEdit,
    Chip,
    Section,
    UtcDateTimeEdit,
    all_modes,
    combo_value,
    fill_mode_combo,
    fill_sat_combo,
    fmt,
    mode_combo,
    mode_text,
    primary,
    prop_combo,
    run_async,
    sat_list,
    set_combo_data,
)

tr = i18n.tr

TEXT_COLS = ["first_name", "last_name", "country", "state", "qth", "address", "cqz", "ituz", "dxcc", "gridsquare",
             "qsl_via", "email", "web", "iota", "skcc", "sota_ref", "pota_ref", "wwff_ref", "comment", "notes",
             "rst_sent", "rst_rcvd", "tx_pwr", "freq", "freq_rx"]
# Filled from your own earlier QSOs with the same call.
FROM_LOG_COLS = ["first_name", "last_name", "qth", "address", "state", "gridsquare", "email", "web", "qsl_via",
                 "iota"]
LOOKUP_COLS = ["first_name", "last_name", "qth", "address", "state", "cnty", "country", "gridsquare", "email",
               "web", "qsl_via", "cqz", "ituz", "dxcc", "iota"]
PROFILE_COLS = ["station_callsign", "my_gridsquare", "my_vucc_grids", "my_dxcc", "my_cq_zone",
                "my_itu_zone", "my_state", "my_cnty", "my_pota_ref", "my_sota_ref", "my_wwff_ref"]
# The Device section; when empty, the station profile's rig, antenna and power are logged.
DEVICE_COLS = {"rig": "my_rig", "antenna": "my_antenna", "power": "tx_pwr"}
EDIT_SETUPS = "__edit__"
PATTERN_ROWS = 50  # rows shown for a * / ? pattern in the call field (the header counts them all)


def _side_by_side(*items: tuple[QWidget, int]) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    for wdg, stretch in items:
        lay.addWidget(wdg, stretch)
    return w


class DXPanel(QWidget):
    logged = Signal(int)
    saved = Signal(int)
    message = Signal(str)
    lookup_requested = Signal(str)
    profiles_requested = Signal()
    devices_requested = Signal()

    def __init__(self, db, prefs, parent=None, ops: OperatorSession | None = None):
        super().__init__(parent)
        self.db = db
        self.prefs = prefs
        self.ops = ops or OperatorSession()
        self.cty = dxcc.default()
        self.sats = sats.load(overrides=prefs.get("sat_names"), extra=prefs.get("user_sats"))
        self.edit_id: int | None = None
        self.time_on_locked = False
        # Time Off: "live" follows the clock, "follow" = Time On + 1 min, "keep" keeps an edited QSO's duration,
        # "manual" = you set it.
        self.time_off_mode = "live"
        self._duration = 60.0
        self._hidden: dict[str, Any] = {}  # cont etc. from cty, not shown
        self._last_lookup_call = ""
        self.w: dict[str, Any] = {}
        self._index = similar.CallIndex(db.calls_last_worked(), db.cached_calls(),
                                        scpmod.Scp.from_file(scpmod.path_for(db.path)).calls)
        self._asked_online: set[str] = set()
        self._similar_key: tuple | None = None
        self._setups: list[dict] = []
        self._device_busy = False  # setting the Device fields from code: don't save or re-match each change
        self._session_device: dict = {}  # your Device fields while you edit a saved QSO
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
        self._index_timer = QTimer(self)
        self._index_timer.setSingleShot(True)
        self._index_timer.setInterval(300)
        self._index_timer.timeout.connect(lambda: self._index.set_worked(self.db.calls_last_worked()))
        self.db.add_listener(self._on_db_change)
        self.reload_profiles()
        self.reload_devices()
        self.reload_call_lists()
        saved_device = self.prefs.get("device") or {}
        self.clear_form(keep_sticky=False)
        self._set_device(saved_device)

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
        line = QHBoxLayout()
        line.setSpacing(6)
        line.addLayout(self.chip_row)
        self.compare_btn = QPushButton(tr("Compare…"))
        self.compare_btn.setProperty("flat", True)
        self.compare_btn.setIcon(theme.icon("copy-check"))
        self.compare_btn.setToolTip(tr("Show this QSO and the earlier one side by side"))
        self.compare_btn.clicked.connect(self._compare)
        self.compare_btn.hide()
        line.addWidget(self.compare_btn)
        line.addStretch(1)
        chips.addLayout(line)
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
        c.row(2, tr("TX MHz"), self.w["freq"])
        c.grid.addWidget(self.band_lab, 2, 2)
        self.w["freq_rx"] = AutoLineEdit(mono=True)
        self.w["freq_rx"].setPlaceholderText(tr("RX MHz (split / sat)"))
        self.band_rx_lab = QLabel("")
        self.band_rx_lab.setObjectName("BandLabel")
        self.rx_sync = QPushButton("= TX")
        self.rx_sync.setObjectName("SyncBtn")
        self.rx_sync.setCheckable(True)
        self.rx_sync.setToolTip(tr("Keep RX MHz the same as TX MHz. Turn off for split or satellite QSOs."))
        self.rx_sync.setChecked(bool(self.prefs.get("rx_sync")))
        self.rx_sync.toggled.connect(self._rx_sync_toggled)
        rxw = QWidget()
        rxl = QHBoxLayout(rxw)
        rxl.setContentsMargins(0, 0, 0, 0)
        rxl.setSpacing(4)
        rxl.addWidget(self.w["freq_rx"], 1)
        rxl.addWidget(self.rx_sync)
        c.row(3, tr("RX MHz"), rxw)
        c.grid.addWidget(self.band_rx_lab, 3, 2)
        self.mode = mode_combo(self.prefs, blank=True)
        self.mode.setToolTip(tr("Choose which modes are listed in Preferences → Modes"))
        self._mode_prev = "SSB"
        set_combo_data(self.mode, "SSB")
        c.row(4, tr("Mode"), self.mode)
        rst = QHBoxLayout()
        self.w["rst_sent"] = AutoLineEdit(mono=True)
        self.w["rst_rcvd"] = AutoLineEdit(mono=True)
        self.w["rst_sent"].setPlaceholderText(tr("Sent"))
        self.w["rst_rcvd"].setPlaceholderText(tr("Rcvd"))
        for k in ("rst_sent", "rst_rcvd"):
            rst.addWidget(self.w[k])
        rst_w = QWidget()
        rst_w.setLayout(rst)
        rst.setContentsMargins(0, 0, 0, 0)
        c.row(5, tr("RST"), rst_w)
        self.prop = prop_combo()
        self.prop.setToolTip(tr("Propagation mode (TQSL / LoTW list)"))
        self.prop.currentIndexChanged.connect(self._prop_changed)
        c.row(6, tr("Propagation"), self.prop)
        self.sat = QComboBox()
        self._sat_prev = ""
        fill_sat_combo(self.sat, self.sats, sat_list(self.prefs, self.sats, self.db.distinct("sat_name")))
        self.sat.setToolTip(tr("Choose which satellites are listed in Preferences → Satellites"))
        self.sat.currentIndexChanged.connect(self._sat_changed)
        c.row(7, tr("Satellite"), self.sat)
        self.op_field = OperatorField(self.ops)
        self.op_field.message.connect(self.message)
        self.op_field.returnPressed.connect(self.commit)
        c.row(8, tr("Operator"), self.op_field)
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
        self.w["comment"].setPlaceholderText(tr("e.g. your name, tnx QSO 73"))
        self.w["comment"].setToolTip(tr("Uploaded with the QSO to eQSL, QRZ.com and HRDLog (ADIF COMMENT): for "
                                        "the other station, e.g. your name or a thank-you."))
        m.row(5, tr("Comment (upload)"), self.w["comment"])
        self.w["notes"] = AutoLineEdit()
        self.w["notes"].setPlaceholderText(tr("e.g. repeater; their rig, antenna, power; my QTH"))
        self.w["notes"].setToolTip(tr("Only for you, never uploaded (ADIF NOTES): e.g. the repeater you used, "
                                      "their rig, antenna and power, or where you operated from."))
        m.row(6, tr("Notes (local)"), self.w["notes"])
        m.grid.setColumnStretch(1, 1)

        d = Section(tr("Device"))
        self.setup = QComboBox()
        self.setup.setToolTip(tr("A setup fills rig, antenna and power in one pick; you can still change each one. "
                                 "Put setups together in Preferences → Devices."))
        self.setup.activated.connect(self._setup_picked)
        self.w["tx_pwr"] = AutoLineEdit(mono=True)
        self.w["tx_pwr"].setPlaceholderText(tr("W"))
        self.w["tx_pwr"].setToolTip(tr("Power (W)"))
        self.w["tx_pwr"].setMaximumWidth(72)
        self.rig = QComboBox()
        self.antenna = QComboBox()
        for cb, tip in ((self.rig, tr("My rig (ADIF MY_RIG). Your rigs are listed in Preferences → Devices.")),
                        (self.antenna, tr("My antenna (ADIF MY_ANTENNA). Your antennas are listed in Preferences → "
                                          "Devices."))):
            cb.setEditable(True)
            cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            cb.setToolTip(tip)
            cb.setMinimumContentsLength(6)
            cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            cb.editTextChanged.connect(lambda *_a: self._device_edited())
            cb.lineEdit().returnPressed.connect(self.commit)
        self.w["tx_pwr"].textChanged.connect(lambda *_a: self._device_edited())
        d.row(0, tr("Setup · Power"), _side_by_side((self.setup, 1), (self.w["tx_pwr"], 0)))
        d.row(1, tr("Rig · Antenna"), _side_by_side((self.rig, 1), (self.antenna, 1)))
        d.grid.setColumnStretch(1, 1)
        col2 = QWidget()
        col2_lay = QVBoxLayout(col2)
        col2_lay.setContentsMargins(0, 0, 0, 0)
        col2_lay.setSpacing(8)
        col2_lay.addWidget(m, 1)
        col2_lay.addWidget(d)
        grid.addWidget(col2, 0, 2)

        wb = Section(tr("Worked before"))
        self.similar = QTableWidget(0, 2)
        self.similar.setHorizontalHeaderLabels([tr("Similar calls"), tr("Last QSO or where it is known")])
        self.similar.setToolTip(tr("Your log first (most recent first), then calls QRZ.com / HamQTH know, then LoTW "
                                   "users and eQSL AG members (File → Update LoTW and eQSL call lists), then "
                                   "MASTER.SCP. Click a call to use it. With * or ? the call field is a pattern, as "
                                   "in the log's search: BA*K finds BA1PK and BA8BLK."))
        self.similar.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.similar.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.similar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.similar.verticalHeader().setVisible(False)
        self.similar.verticalHeader().setDefaultSectionSize(20)
        self.similar.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.similar.horizontalHeader().setStretchLastSection(True)
        self.similar.hide()
        self.similar.cellClicked.connect(self._pick_similar)
        wb.add(self.similar)
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
        clear.setToolTip(tr("Clear the whole form, including frequency, mode, reports, power, propagation and "
                            "satellite (Esc). Keep only applies after Log QSO."))
        clear.clicked.connect(lambda: self.clear_form(full=True))
        bar.addWidget(self.lookup_btn)
        bar.addWidget(prev)
        bar.addWidget(clear)
        bar.addSpacing(12)
        sticky_lab = QLabel(tr("Keep:"))
        sticky_lab.setProperty("muted", True)
        bar.addWidget(sticky_lab)
        self.sticky: dict[str, QCheckBox] = {}
        on = {"device" if k == "power" else k for k in self.prefs.get("sticky")}  # "power" before 0.1.2
        for key, label in (("freq", "Freq"), ("mode", "Mode"), ("sat", "Sat"), ("device", "Device")):
            cb = QCheckBox(tr(label))
            if key == "device":
                cb.setToolTip(tr("Keep rig, antenna and power after Log QSO. Off: back to the station "
                                 "profile's."))
            cb.setChecked(key in on)
            cb.toggled.connect(self._sticky_changed)
            self.sticky[key] = cb
            bar.addWidget(cb)
        bar.addStretch(1)
        self.delete_btn = QPushButton(tr("Delete"))
        self.delete_btn.setProperty("danger", True)
        self.delete_btn.clicked.connect(self._delete_editing)
        self.cancel_btn = QPushButton(tr("Cancel"))
        self.cancel_btn.clicked.connect(lambda: self.clear_form(full=True))
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
        self.w["freq"].textChanged.connect(self._sync_rx)
        self.w["freq_rx"].textChanged.connect(self._freq_changed)
        self.mode.currentIndexChanged.connect(self._mode_picked)
        for wdg in self.w.values():
            if isinstance(wdg, AutoLineEdit):
                wdg.returnPressed.connect(self.commit)
        self.call.returnPressed.connect(self.commit)
        self._rx_sync_toggled(self.rx_sync.isChecked())
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=lambda: self.clear_form(full=True))

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
        self._update_operator()
        self._power_hint()
        self._check_timer.start()

    def current_profile(self) -> dict | None:
        pid = self.profile.currentData()
        return self.db.profile(pid) if pid not in (None, -1) else self.db.default_profile()

    # ------------------------------------------------------------------ time
    def _tick(self) -> None:
        if self.edit_id is not None:
            return
        if not self.time_on_locked:
            self.w["time_on"].set_iso(timeutil.now_iso())
        if self.time_off_mode == "live":
            self.w["time_off"].set_iso(timeutil.now_iso())
        self._check_times()

    def _time_on_changed(self) -> None:
        """You changed Time On: Time Off follows (+1 min, or the edited QSO's duration) unless you set it."""
        self.time_on_locked = True
        if self.time_off_mode in ("live", "follow"):
            self.time_off_mode = "follow"
            self.w["time_off"].set_iso(timeutil.shift_iso(self.w["time_on"].iso(), 60))
        elif self.time_off_mode == "keep":
            self.w["time_off"].set_iso(timeutil.shift_iso(self.w["time_on"].iso(), self._duration))
        self._check_times()
        self._check_timer.start()

    def _time_edited(self, which: str) -> None:
        if which == "on":
            self._time_on_changed()
        else:
            self.time_off_mode = "manual"
            self._check_times()

    def stamp(self, which: str) -> None:
        now = timeutil.now_iso()
        if which == "on":
            # Back to the default: Time On is now (held once a call is typed) and Time Off follows the clock.
            self.w["time_on"].set_iso(now)
            self.w["time_off"].set_iso(now)
            if self.edit_id is None:
                self.time_on_locked = bool(callsign.clean(self.call.text()))
                self.time_off_mode = "live"
            else:
                self.time_off_mode = "follow"  # no live clock while editing a saved QSO
            self._check_times()
            self._check_timer.start()
        else:
            self.w["time_off"].set_iso(now)
            self.time_off_mode = "manual"
            self._check_times()

    def times_ok(self) -> bool:
        return self.w["time_off"].iso() >= self.w["time_on"].iso()

    def _check_times(self) -> None:
        bad = not self.times_ok()
        w = self.w["time_off"]
        if bool(w.property("invalid")) != bad:
            w.setProperty("invalid", bad)
            w.setToolTip(tr("Time Off is earlier than Time On") if bad else "")
            theme.repolish(w)

    # ------------------------------------------------------------------ frequency / satellite / propagation
    def _rx_sync_toggled(self, on: bool) -> None:
        self.w["freq_rx"].setReadOnly(on)
        if on:
            self._sync_rx()

    def _sync_rx(self) -> None:
        if self.rx_sync.isChecked():
            w = self.w["freq_rx"]
            w.setText(self.w["freq"].text())
            w._edited("")

    def _prop_changed(self) -> None:
        # The satellite name is kept even when propagation is not SAT; a chip warns about it.
        self._check_timer.start()

    def sat_prop_warning(self) -> str | None:
        sat, prop = combo_value(self.sat), combo_value(self.prop)
        if sat and prop and prop != "SAT":
            return tr("Satellite is set, but propagation is not SAT")
        if prop == "SAT" and not sat:
            return tr("Propagation is SAT, but no satellite is chosen")
        return None

    def reload_sats(self) -> None:
        self.sats = sats.load(overrides=self.prefs.get("sat_names"), extra=self.prefs.get("user_sats"))
        fill_sat_combo(self.sat, self.sats, sat_list(self.prefs, self.sats, self.db.distinct("sat_name")))

    def reload_lists(self) -> None:
        fill_mode_combo(self.mode, self.prefs)
        self.reload_sats()
        self.reload_devices()

    # ------------------------------------------------------------------ device (rig, antenna, power)
    def reload_devices(self) -> None:
        """Your rigs, antennas and setups from Preferences → Devices."""
        self._setups = devices.clean_setups(self.prefs.get("setups"))
        self._device_busy = True
        try:
            for cb, key in ((self.rig, "rigs"), (self.antenna, "antennas")):
                text = cb.currentText()
                cb.clear()
                cb.addItems(devices.clean_names(self.prefs.get(key)))
                cb.setEditText(text)
            self.setup.blockSignals(True)
            self.setup.clear()
            self.setup.addItem("—", None)
            for i, st in enumerate(self._setups):
                self.setup.addItem(st["name"], i)
                tip = " · ".join(x for x in (st["rig"], st["antenna"], f"{fmt('tx_pwr', st['power'])} W"
                                             if st["power"] is not None else "") if x)
                self.setup.setItemData(self.setup.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
            self.setup.insertSeparator(self.setup.count())
            self.setup.addItem(tr("Edit setups…"), EDIT_SETUPS)
            self.setup.blockSignals(False)
        finally:
            self._device_busy = False
        self._match_setup()

    def _setup_picked(self, index: int) -> None:
        data = self.setup.itemData(index)
        if data == EDIT_SETUPS:
            self._match_setup()
            self.devices_requested.emit()
            return
        if data is None:  # "—" keeps what the fields hold
            return
        st = self._setups[data]
        power = st["power"] if st["power"] is not None else self._get("tx_pwr")
        self._set_device({"rig": st["rig"], "antenna": st["antenna"], "power": power})

    def device_values(self) -> dict:
        return {"rig": self.rig.currentText().strip(), "antenna": self.antenna.currentText().strip(),
                "power": self._get("tx_pwr")}

    def _apply_device(self, d: dict) -> None:
        busy, self._device_busy = self._device_busy, True
        try:
            self.rig.setEditText(d.get("rig") or "")
            self.antenna.setEditText(d.get("antenna") or "")
            self._set("tx_pwr", devices.power_value(d.get("power")))
        finally:
            self._device_busy = busy

    def _set_device(self, d: dict) -> None:
        self._apply_device(d)
        self._device_edited()

    def _device_edited(self) -> None:
        if self._device_busy:
            return
        self._match_setup()
        if self.edit_id is None:
            self.prefs.set("device", self.device_values())

    def _match_setup(self) -> None:
        """The Setup dropdown names the setup the fields equal, or shows "—" once you change one of them."""
        cur = self.device_values()
        i = devices.matching(self._setups, cur["rig"], cur["antenna"], cur["power"])
        self.setup.blockSignals(True)
        self.setup.setCurrentIndex(0 if i is None else i + 1)
        self.setup.blockSignals(False)

    def _mode_picked(self, *_a) -> None:
        if self.mode.currentData() == OTHER:
            dlg = ListPicker(tr("Other mode"), [(m, "") for m in all_modes()], self,
                             add_label=tr("Also add it to my mode list"))
            chosen = dlg.value if dlg.exec() else None
            self.mode.blockSignals(True)
            if chosen:
                if dlg.add.isChecked():
                    lst = [m for m in (self.prefs.get("mode_list") or []) if m] or self._default_modes()
                    if chosen not in lst:
                        lst.append(chosen)
                    self.prefs.set("mode_list", lst)
                    fill_mode_combo(self.mode, self.prefs)
                set_combo_data(self.mode, chosen)
            else:
                set_combo_data(self.mode, self._mode_prev)
            self.mode.blockSignals(False)
        self._mode_prev = combo_value(self.mode) or self._mode_prev
        self._mode_changed(combo_value(self.mode) or "")

    @staticmethod
    def _default_modes() -> list[str]:
        from ..widgets import MODE_FIRST  # noqa: PLC0415

        return list(MODE_FIRST)

    # ------------------------------------------------------------------ operator
    def _update_operator(self) -> None:
        if self.edit_id is None:
            self.op_field.show_for(self.current_profile())

    def change_operator(self) -> None:
        self.op_field.setFocus()
        self.op_field.selectAll()

    def _compare(self) -> None:
        hit = self.current_dupe()
        if not hit:
            return
        from ..dupe_dialog import DupeCompareDialog  # noqa: PLC0415

        draft = qsomod.normalize(self.values())
        if self.edit_id is not None:
            draft["id"] = self.edit_id
        DupeCompareDialog(self.db, self.prefs, draft=draft, parent=self).exec()

    # ------------------------------------------------------------------ field access
    def _get(self, col: str) -> Any:
        if col == "mode":
            return combo_value(self.mode)
        if col == "sat_name":
            return combo_value(self.sat)
        if col == "prop_mode":
            return combo_value(self.prop)
        w = self.w.get(col)
        if w is None:
            return None
        if isinstance(w, UtcDateTimeEdit):
            return w.iso()
        return w.text().strip() or None

    def _set(self, col: str, v: Any, *, auto: bool = False, guess: bool = False, source: str = "") -> None:
        if col == "mode":
            set_combo_data(self.mode, mode_text({"mode": v}) if v else "SSB")
            return
        if col == "sat_name":
            set_combo_data(self.sat, (v or "").upper())
            return
        if col == "prop_mode":
            set_combo_data(self.prop, (v or "").upper())
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
        dev = self.device_values()
        q["my_rig"], q["my_antenna"] = dev["rig"] or None, dev["antenna"] or None
        q.update(self._hidden)
        return q

    # ------------------------------------------------------------------ call handling
    def _call_edited(self, text: str) -> None:
        up = text.upper().replace(" ", "")
        if up != text:
            pos = self.call.cursorPosition()
            self.call.setText(up)
            self.call.setCursorPosition(pos)
        pattern = similar.has_wildcards(up)  # only searches Similar calls: no Time On, cty fill or lookup
        if up and not pattern and not self.time_on_locked and self.edit_id is None:
            self.w["time_on"].set_iso(timeutil.now_iso())
            self.time_on_locked = True
        if not up or pattern:
            if self.edit_id is None and not up:
                self.time_on_locked = False
            self._clear_auto()
        if not pattern:
            self._resolve_call()
        self._check_timer.start()
        if self.prefs.get("auto_lookup") and not pattern:
            self._lookup_timer.start()

    def is_pattern(self) -> bool:
        """The call field holds a * / ? pattern: it searches Similar calls and can't be logged."""
        return similar.has_wildcards(self.call.text())

    def _call_finished(self) -> None:
        if self.prefs.get("auto_lookup") and self.call.text().strip() and not self.is_pattern():
            self._lookup_timer.stop()
            self.request_lookup()

    def _auto_lookup(self) -> None:
        if len(self.call.text().strip()) >= 3:
            self.request_lookup()

    def request_lookup(self, force: bool = False) -> None:
        c = callsign.clean(self.call.text())
        if not c or self.is_pattern() or (c == self._last_lookup_call and not force):
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
        if not m:  # no mode chosen yet: no default report either
            for col in ("rst_sent", "rst_rcvd"):
                if self.w[col].is_auto():
                    self.w[col].clear_all()
            self._check_timer.start()
            return
        rst = modes.default_rst(m, s)
        for col in ("rst_sent", "rst_rcvd"):
            w = self.w[col]
            if not w.text() or w.is_auto():
                w.set_auto(rst)
        self._check_timer.start()

    def _sat_changed(self, *_a) -> None:
        if self.sat.currentData() == OTHER:
            items = [(s.name, s.description) for s in self.sats.sats]
            dlg = ListPicker(tr("Other satellite"), items, self, add_label=tr("Also add it to my satellite list"))
            chosen = dlg.value if dlg.exec() else None
            self.sat.blockSignals(True)
            if chosen and dlg.add.isChecked():
                lst = sat_list(self.prefs, self.sats, self.db.distinct("sat_name"))
                if chosen not in lst:
                    lst.append(chosen)
                self.prefs.set("sat_list", lst)
                fill_sat_combo(self.sat, self.sats, lst)
            set_combo_data(self.sat, chosen or self._sat_prev)
            self.sat.blockSignals(False)
            if not chosen:
                return
        self._sat_prev = combo_value(self.sat) or ""
        name = combo_value(self.sat)
        if not name:
            if combo_value(self.prop) == "SAT":
                set_combo_data(self.prop, "")
            if self.prefs.get("rx_sync") and not self.rx_sync.isChecked():
                self.rx_sync.setChecked(True)
            self._check_timer.start()
            return
        self.prop.blockSignals(True)
        set_combo_data(self.prop, "SAT")
        self.prop.blockSignals(False)
        self.rx_sync.setChecked(False)  # satellites are split
        last = self.db.last_qso("sat_name = ?", [name])
        sat = self.sats.get(name)
        up = last.get("freq") if last else (sat.uplink if sat else None)
        down = last.get("freq_rx") if last else (sat.downlink if sat else None)
        mode = (mode_text(last) if last else None) or (sat.mode if sat else None)
        if up:
            self._set("freq", up)
        if down:
            self._set("freq_rx", down)
        if mode:
            set_combo_data(self.mode, mode)
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

    def current_dupe(self) -> dupes.DupeHit | None:
        q = qsomod.normalize(self.values())
        if not q.get("call") or not q.get("band"):
            return None
        return dupes.check(self.db, q, block_s=self.prefs.get("block_seconds"),
                           warn_s=self.prefs.get("warn_hours") * 3600, exclude_id=self.edit_id)

    @applog.timed("Entry form checks")
    def _refresh_checks(self) -> None:
        c = callsign.clean(self.call.text())
        pattern = similar.has_wildcards(c)
        q = qsomod.normalize(self.values())
        chips: list[tuple[str, str]] = []
        hit = self.current_dupe() if c and not pattern else None
        blocked = pattern
        if pattern:
            chips.append((tr("Wildcard search: pick a call below"), "info"))
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
        if c and not pattern:
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
        warn = self.sat_prop_warning()
        if warn:
            chips.append((warn, "warn"))
        self._set_chips(chips)
        self.compare_btn.setVisible(hit is not None)
        self.summary.setText("   —   ".join(summary_parts))
        self._refresh_history("" if pattern else c, q.get("band"))
        self._refresh_similar(c)

    # ------------------------------------------------------------------ similar calls
    def _on_db_change(self, kind: str, _ids: list[int]) -> None:
        if kind in ("insert", "delete", "reset", "update"):
            self._index_timer.start()

    def _refresh_similar(self, text: str) -> None:
        key = (text, self._index.version, self.edit_id)
        if key == self._similar_key:
            return
        self._similar_key = key
        if similar.has_wildcards(text):
            found, capped = self._index.find_pattern(text)
            matches = found[:PATTERN_ROWS]
            self.similar.setHorizontalHeaderItem(0, QTableWidgetItem(
                tr("Matches") + f" ({len(found)}{'+' if capped else ''})" if found else tr("No matches")))
        else:
            matches = self._index.find(text) if len(text) >= 2 else []
            self.similar.setHorizontalHeaderItem(0, QTableWidgetItem(tr("Similar calls")))
        info = self.db.call_summaries([m.call for m in matches if m.tier == similar.WORKED])
        t = theme.tokens()
        self.similar.setRowCount(len(matches))
        for i, m in enumerate(matches):
            if m.tier == similar.WORKED:
                d = info.get(m.call, {})
                parts = [f"{d.get('n', 0)}×", fmt("time_on", d.get("time_on"))[:10],
                         f"{bands.display_band(d.get('band'))} {mode_text(d)}".strip()]
                if d.get("sat_name"):
                    parts.append(d["sat_name"])
                if d.get("confirmed"):
                    parts.append(d["confirmed"])
                detail = " · ".join(x for x in parts if x)
            elif m.tier == similar.KNOWN:
                detail = tr("not worked") + " · " + (m.source or "QRZ.com / HamQTH")
            elif m.tier == similar.LISTED:
                parts = []
                if m.lotw is not None:
                    parts.append(tr("uses LoTW") + (f", {tr('last upload')} {m.lotw}" if m.lotw else ""))
                if m.ag:
                    parts.append(tr("eQSL AG member"))
                detail = tr("not worked") + " · " + " · ".join(parts)
            else:
                detail = tr("not worked") + " · MASTER.SCP"
            call_it = QTableWidgetItem(m.call)
            font = theme.mono_font()
            font.setBold(m.kind == similar.EXACT)
            call_it.setFont(font)
            det_it = QTableWidgetItem(detail)
            if m.tier == similar.WORKED:
                call_it.setForeground(QColor(t.accent))
            else:
                for it in (call_it, det_it):
                    it.setForeground(QColor(t.muted))
            self.similar.setItem(i, 0, call_it)
            self.similar.setItem(i, 1, det_it)
        # Only as tall as its rows (up to 4, the rest scroll), so the matrix and history keep their room.
        self.similar.setVisible(bool(matches))
        if matches:
            head = self.similar.horizontalHeader().sizeHint().height()
            self.similar.setFixedHeight(head + min(len(matches), 4) * 20 + 2)

    def _pick_similar(self, row: int, _col: int) -> None:
        it = self.similar.item(row, 0)
        if not it:
            return
        self.call.setText(it.text())
        self._call_edited(it.text())
        self.call.setFocus()

    def note_known(self, call: str, source: str) -> None:
        """A QRZ.com / HamQTH lookup found this call: list it as known from now on."""
        self._index.add_known({call: source})
        self._check_timer.start()

    def online_candidates(self, call: str) -> list[str]:
        """Up to 3 MASTER.SCP calls one character away to check online; each is only asked once per run."""
        out = self._index.online_candidates(call, skip=self._asked_online)
        self._asked_online.update(out)
        return out

    def reload_call_lists(self) -> None:
        """Load the LoTW user and eQSL AG lists next to the log on a worker thread (about a second)."""
        folder = calllists.folder_for(self.db.path)

        def done(lists: calllists.CallLists) -> None:
            if not len(lists):
                return  # none downloaded yet
            try:
                self._index.set_lists(lists)
                self._check_timer.start()
            except RuntimeError:  # the window was closed meanwhile
                pass

        run_async(calllists.load, folder, on_done=done)

    def reload_scp(self) -> None:
        self._index.set_scp(scpmod.Scp.from_file(scpmod.path_for(self.db.path)).calls)
        self._check_timer.start()

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

    def form_state(self) -> dict:
        """Everything the form holds, so a window built again (a language switch) shows the same QSO in progress."""
        fields = {}
        for col, w in self.w.items():
            if isinstance(w, UtcDateTimeEdit):
                fields[col] = ("time", w.iso())
            elif isinstance(w, AutoLineEdit):
                fields[col] = ("text", w.text(), w.is_auto(), bool(w.property("guess")))
        return {"edit_id": self.edit_id, "session_device": dict(self._session_device), "call": self.call.text(),
                "fields": fields, "mode": combo_value(self.mode), "sat": combo_value(self.sat),
                "prop": combo_value(self.prop), "device": self.device_values(), "rx_sync": self.rx_sync.isChecked(),
                "time_on_locked": self.time_on_locked, "time_off_mode": self.time_off_mode,
                "duration": self._duration, "hidden": dict(self._hidden), "last_lookup": self._last_lookup_call,
                "operator": self.op_field.text()}

    def restore_form(self, st: dict) -> None:
        if st.get("edit_id") is not None and self.db.get(st["edit_id"]):
            self.load_qso(st["edit_id"])  # the banner and the Save / Delete buttons
            self._session_device = st.get("session_device") or {}
        self.rx_sync.setChecked(bool(st.get("rx_sync")))
        for w, v in ((self.mode, st.get("mode")), (self.sat, st.get("sat")), (self.prop, st.get("prop"))):
            w.blockSignals(True)
            set_combo_data(w, v or "")
            w.blockSignals(False)
        for col, f in (st.get("fields") or {}).items():
            w = self.w.get(col)
            if w is None:
                continue
            w.blockSignals(True)
            if f[0] == "time":
                w.set_iso(f[1])
            elif f[2]:  # filled from a lookup, cty or your log: still replaced when the call changes
                w.clear_all()
                w.set_auto(f[1], guess=f[3])
            else:
                w.setText(f[1])
                w.setCursorPosition(0)
            w.blockSignals(False)
        self._apply_device(st.get("device") or {})
        self._match_setup()
        self.call.blockSignals(True)
        self.call.setText(st.get("call") or "")
        self.call.blockSignals(False)
        self.time_on_locked = bool(st.get("time_on_locked"))
        self.time_off_mode = st.get("time_off_mode") or "live"
        self._duration = st.get("duration") or 60.0
        self._hidden = dict(st.get("hidden") or {})
        self._last_lookup_call = st.get("last_lookup") or ""
        if st.get("operator"):
            self.op_field.setText(st["operator"])
        self._mode_changed(combo_value(self.mode) or "")
        self._freq_changed()
        self._power_hint()
        self._refresh_checks()

    # ------------------------------------------------------------------ actions
    def clear_form(self, keep_sticky: bool = True, *, full: bool = False) -> None:
        """Reset the form for the next QSO. ``full`` (Clear, Esc, Cancel) also empties frequency, mode, reports,
        propagation and satellite, whatever Keep says; the Device section (rig, antenna, power) stays."""
        keep = {k for k, cb in self.sticky.items() if cb.isChecked()} if keep_sticky and not full else set()
        freq, freq_rx = self._get("freq"), self._get("freq_rx")
        mode, device = combo_value(self.mode), self.device_values()
        sat, prop = combo_value(self.sat), combo_value(self.prop)
        was_editing = self.edit_id is not None
        self._device_busy = True
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
        self.prop.blockSignals(True)
        set_combo_data(self.sat, sat if "sat" in keep else "")
        set_combo_data(self.prop, prop if "sat" in keep or "freq" in keep else "")
        self.sat.blockSignals(False)
        self.prop.blockSignals(False)
        if "freq" in keep or ("sat" in keep and sat):
            self._set("freq", freq)
            if not self.rx_sync.isChecked():
                self._set("freq_rx", freq_rx)
        if "mode" in keep or ("sat" in keep and sat):
            set_combo_data(self.mode, mode or "SSB")
        if full:
            self.mode.blockSignals(True)
            set_combo_data(self.mode, "")
            self.mode.blockSignals(False)
            self.rx_sync.setChecked(bool(self.prefs.get("rx_sync")))
        # The Device section is your station, not the contact: Clear keeps it. After Log QSO it stays when Keep
        # Device is on, else it goes back to the station profile's (shown greyed in the empty fields).
        if was_editing:
            self._apply_device(self._session_device)
        elif full or "device" in keep:
            self._apply_device(device)
        elif keep_sticky:
            self._apply_device({})
        self._device_busy = False
        self._match_setup()
        if keep_sticky or was_editing:
            self._device_edited()
        self._power_hint()
        self._mode_changed(combo_value(self.mode) or "")
        self.time_on_locked = False
        self.time_off_mode = "live"
        self._duration = 60.0
        self._tick()
        if was_editing or not self.op_field.text():
            self._update_operator()
        self.edit_banner.hide()
        self.delete_btn.hide()
        self.cancel_btn.hide()
        self.log_btn.setText(tr("Log QSO"))
        self._freq_changed()
        self._refresh_checks()
        self.call.setFocus()

    def _power_hint(self) -> None:
        """Empty Device fields show the station profile's rig, antenna and power: what is logged if you leave them
        empty."""
        prof = self.current_profile() or {}
        d = prof.get("tx_pwr")
        self.w["tx_pwr"].setPlaceholderText(f"{fmt('tx_pwr', d)} W" if d else tr("W"))
        self.rig.lineEdit().setPlaceholderText(prof.get("my_rig") or tr("Rig"))
        self.antenna.lineEdit().setPlaceholderText(prof.get("my_antenna") or tr("Antenna"))

    def recall_previous(self) -> None:
        last = self.db.last_qso()
        if not last:
            return
        self.sat.blockSignals(True)
        self.prop.blockSignals(True)
        set_combo_data(self.sat, last.get("sat_name") or "")
        set_combo_data(self.prop, last.get("prop_mode") or "")
        self.sat.blockSignals(False)
        self.prop.blockSignals(False)
        self.rx_sync.setChecked(not last.get("freq_rx") or last.get("freq_rx") == last.get("freq"))
        for col in ("freq", "freq_rx"):
            self._set(col, last.get(col))
        self._set_device({"rig": last.get("my_rig"), "antenna": last.get("my_antenna"), "power": last.get("tx_pwr")})
        set_combo_data(self.mode, mode_text(last) or "SSB")
        self._freq_changed()

    def load_qso(self, qid: int) -> None:
        q = self.db.get(qid)
        if not q:
            return
        if self.edit_id is None:
            self._session_device = self.device_values()
        self.clear_form(keep_sticky=False)
        self.edit_id = qid
        self.call.setText(q.get("call") or "")
        self.rx_sync.blockSignals(True)
        self.rx_sync.setChecked(not q.get("freq_rx") or q.get("freq_rx") == q.get("freq"))
        self.w["freq_rx"].setReadOnly(self.rx_sync.isChecked())
        self.rx_sync.blockSignals(False)
        for col in TEXT_COLS:
            self._set(col, q.get(col))
        self._set_device({"rig": q.get("my_rig"), "antenna": q.get("my_antenna"), "power": q.get("tx_pwr")})
        self.w["time_on"].set_iso(q.get("time_on"))
        self.w["time_off"].set_iso(q.get("time_off") or q.get("time_on"))
        self.time_on_locked = True
        self.time_off_mode = "keep"
        self._duration = max(0.0, timeutil.seconds_between(q.get("time_on") or "", q.get("time_off") or "")
                             if q.get("time_off") else 60.0)
        for w, col in ((self.mode, "mode"), (self.sat, "sat_name"), (self.prop, "prop_mode")):
            w.blockSignals(True)
            set_combo_data(w, mode_text(q) if col == "mode" else (q.get(col) or ""))
            w.blockSignals(False)
        self._check_times()
        self._hidden = {"cont": q.get("cont")} if q.get("cont") else {}
        self.op_field.setText(q.get("operator") or "")
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
        if not c or self.is_pattern():
            if c:
                self.message.emit(tr("* and ? only search Similar calls: pick a call to log."))
            self.call.setFocus()
            return
        q = self.values()
        if self.edit_id is None and self.time_off_mode == "live":
            q["time_off"] = max(timeutil.now_iso(), q["time_on"])
        nq = qsomod.normalize(q)
        if not nq.get("band") and nq.get("freq") is None:
            QMessageBox.warning(self, tr("Missing frequency"), tr("Enter the frequency (MHz) so the band is known."))
            self.w["freq"].setFocus()
            return
        if self.edit_id is None and not nq.get("mode"):
            QMessageBox.warning(self, tr("Missing mode"), tr("Choose the mode."))
            self.mode.setFocus()
            return
        if nq.get("time_off") and nq.get("time_on") and nq["time_off"] < nq["time_on"]:
            QMessageBox.warning(self, tr("Check the times"), tr("Time Off is earlier than Time On. Fix the times; "
                                                                "the QSO was not logged."))
            self.w["time_off"].setFocus()
            return
        hit = self.current_dupe()
        if hit and hit.level == "DUPE" and not self.prefs.get("allow_dupes"):
            self.message.emit(tr("Dupe: logging is blocked for the same mode within the block window."))
            return
        warn = self.sat_prop_warning()
        if warn and QMessageBox.question(
                self, tr("Satellite and propagation"),
                warn + ".\n\n" + tr("Log it anyway? The satellite name and propagation are saved as shown.")
        ) != QMessageBox.StandardButton.Yes:
            return
        if self.edit_id is not None:
            before = self.db.get(self.edit_id) or {}
            op = callsign.clean(self.op_field.text())
            if op != (before.get("operator") or ""):
                nq["operator"] = op or None
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
        op = self.op_field.resolve(prof) if prof else callsign.clean(self.op_field.text()) or None
        if prof and op is None and is_club(prof):
            return
        for col in PROFILE_COLS:
            if prof.get(col) not in (None, ""):
                nq[col] = prof[col]
        for col in DEVICE_COLS.values():  # an empty Device field logs the profile's
            if nq.get(col) in (None, "") and prof.get(col) not in (None, ""):
                nq[col] = prof[col]
        if op:
            nq["operator"] = op
        if prof.get("id"):
            nq["station_profile_id"] = prof["id"]
        if hit:
            nq["dupe_flag"], nq["dupe_of"] = hit.level, hit.other_id
        with self.db.batch(tr("Log QSO")):
            qid = self.db.insert(nq)
        self._index.add_worked({nq["call"]: nq.get("time_on") or ""})
        self.ops.touch(prof)
        self.message.emit(tr("Logged") + f" {qsomod.summary(nq)}")
        self.logged.emit(qid)
        self.clear_form()
