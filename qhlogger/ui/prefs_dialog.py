"""Preferences: general, station profiles, logging, lookup, services, database."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from ..core import dxcc, sats
from ..core.sync import tqsl
from . import theme

tr = i18n.tr

PROFILE_FIELDS = [
    ("name", "Profile name"), ("station_callsign", "Station callsign"), ("operator", "Operator (personal station)"),
    ("my_gridsquare", "My grid"), ("my_vucc_grids", "My VUCC grids"), ("my_cq_zone", "My CQ zone"),
    ("my_itu_zone", "My ITU zone"), ("my_dxcc", "My DXCC"), ("my_state", "My province"), ("my_cnty", "My county"),
    ("my_rig", "Rig"), ("my_antenna", "Antenna"), ("tx_pwr", "Default power (W)"), ("my_pota_ref", "My POTA"),
    ("my_sota_ref", "My SOTA"), ("my_wwff_ref", "My WWFF"), ("lotw_location", "TQSL station location"),
    ("eqsl_qth_nickname", "eQSL QTH nickname"),
]


def _password() -> QLineEdit:
    e = QLineEdit()
    e.setEchoMode(QLineEdit.EchoMode.Password)
    e.setPlaceholderText(tr("stored in the system keychain"))
    return e


class ProfilesPage(QWidget):
    def __init__(self, db):
        super().__init__()
        self.db = db
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._load)
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        add = QPushButton(tr("New"))
        add.setIcon(theme.icon("plus"))
        add.clicked.connect(self._new)
        rm = QPushButton(tr("Delete"))
        rm.setIcon(theme.icon("trash-2"))
        rm.clicked.connect(self._delete)
        row.addWidget(add)
        row.addWidget(rm)
        left.addLayout(row)
        lay.addLayout(left, 1)
        right = QVBoxLayout()
        form = QFormLayout()
        self.edits: dict[str, QLineEdit] = {}
        self.station_type = QComboBox()
        self.station_type.addItem(tr("Personal station: I am the operator"), "personal")
        self.station_type.addItem(tr("Club station: ask who is operating"), "club")
        self.station_type.currentIndexChanged.connect(self._type_changed)
        for key, label in PROFILE_FIELDS:
            e = QLineEdit()
            self.edits[key] = e
            form.addRow(tr(label), e)
            if key == "station_callsign":
                form.addRow(tr("Station type"), self.station_type)
                hint = QLabel(tr("Club stations ask for the operator at the first QSO, keep it while QHlogger "
                                 "runs, and ask again on a new UTC day or after a 3-hour break."))
                hint.setObjectName("Hint")
                hint.setWordWrap(True)
                hint.setMinimumHeight(34)
                form.addRow("", hint)
        self.edits["station_callsign"].editingFinished.connect(self._auto_zones)
        self.default = QCheckBox(tr("Default profile"))
        form.addRow("", self.default)
        right.addLayout(form)
        save = QPushButton(tr("Save profile"))
        save.setProperty("primary", True)
        save.clicked.connect(self._save)
        right.addWidget(save, 0, Qt.AlignmentFlag.AlignRight)
        right.addStretch(1)
        lay.addLayout(right, 2)
        self.current_id: int | None = None
        self._reload()

    def _reload(self, select: int | None = None) -> None:
        self.profiles = self.db.profiles()
        self.list.blockSignals(True)
        self.list.clear()
        for p in self.profiles:
            it = QListWidgetItem(("★ " if p.get("is_default") else "") + p["name"])
            it.setData(Qt.ItemDataRole.UserRole, p["id"])
            self.list.addItem(it)
        self.list.blockSignals(False)
        idx = 0
        if select is not None:
            for i, p in enumerate(self.profiles):
                if p["id"] == select:
                    idx = i
        if self.profiles:
            self.list.setCurrentRow(idx)
            self._load(idx)
        else:
            self._new()

    def _load(self, row: int) -> None:
        if row < 0 or row >= len(self.profiles):
            return
        p = self.profiles[row]
        self.current_id = p["id"]
        for k, e in self.edits.items():
            v = p.get(k)
            e.setText("" if v is None else str(v))
        self.station_type.setCurrentIndex(max(0, self.station_type.findData(p.get("station_type") or "personal")))
        self.default.setChecked(bool(p.get("is_default")))
        self._type_changed()

    def _type_changed(self) -> None:
        club = self.station_type.currentData() == "club"
        self.edits["operator"].setEnabled(not club)
        self.edits["operator"].setPlaceholderText(tr("asked when logging") if club else tr("your callsign"))

    def _new(self) -> None:
        self.current_id = None
        self.list.clearSelection()
        self.station_type.setCurrentIndex(0)
        for e in self.edits.values():
            e.clear()
        self.default.setChecked(not self.db.profiles())
        self.edits["name"].setFocus()

    def _auto_zones(self) -> None:
        call = self.edits["station_callsign"].text().strip().upper()
        self.edits["station_callsign"].setText(call)
        if not call:
            return
        if not self.edits["operator"].text() and self.station_type.currentData() != "club":
            self.edits["operator"].setText(call)
        r = dxcc.default().resolve(call)
        if r:
            for key, val in (("my_cq_zone", r.cqz), ("my_itu_zone", r.ituz), ("my_dxcc", r.dxcc)):
                if not self.edits[key].text():
                    self.edits[key].setText(str(val))
        if not self.edits["name"].text():
            self.edits["name"].setText(call)

    def _save(self) -> None:
        data = {k: (e.text().strip() or None) for k, e in self.edits.items()}
        if not data.get("station_callsign"):
            QMessageBox.warning(self, tr("Station profile"), tr("Enter the station callsign."))
            return
        data["station_callsign"] = data["station_callsign"].upper()
        for k in ("my_cq_zone", "my_itu_zone", "my_dxcc"):
            if data.get(k):
                try:
                    data[k] = int(data[k])
                except ValueError:
                    data[k] = None
        if data.get("tx_pwr"):
            try:
                data["tx_pwr"] = float(data["tx_pwr"])
            except ValueError:
                data["tx_pwr"] = None
        data["station_type"] = self.station_type.currentData()
        if data["station_type"] == "club":
            data["operator"] = None
        elif data.get("operator"):
            data["operator"] = data["operator"].upper()
        data["id"] = self.current_id
        data["is_default"] = self.default.isChecked()
        pid = self.db.save_profile(data)
        self._reload(select=pid)

    def _delete(self) -> None:
        if self.current_id is None:
            return
        self.db.delete_profile(self.current_id)
        self._reload()


class PrefsDialog(QDialog):
    def __init__(self, db, prefs, parent=None, page: str | None = None):
        super().__init__(parent)
        self.db, self.prefs = db, prefs
        self.setWindowTitle(tr("Preferences"))
        self.resize(820, 600)
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        # General
        g = QWidget()
        f = QFormLayout(g)
        self.lang = QComboBox()
        for code, name in i18n.LANGUAGES.items():
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(prefs.get("language"))))
        self.theme = QComboBox()
        for code, name in (("system", "Follow system"), ("light", "Light"), ("dark", "Dark")):
            self.theme.addItem(tr(name), code)
        self.theme.setCurrentIndex(max(0, self.theme.findData(prefs.get("theme"))))
        f.addRow(tr("Language"), self.lang)
        lang_hint = QLabel(tr("Language changes apply after restarting QHlogger."))
        lang_hint.setObjectName("Hint")
        f.addRow("", lang_hint)
        f.addRow(tr("Theme"), self.theme)
        self.tabs.addTab(g, tr("General"))

        # Station profiles
        self.profiles_page = ProfilesPage(db)
        self.tabs.addTab(self.profiles_page, tr("Station profiles"))

        # Logging
        lg = QWidget()
        f = QFormLayout(lg)
        self.block = QSpinBox()
        self.block.setRange(0, 3600)
        self.block.setSuffix(" s")
        self.block.setValue(prefs.get("block_seconds"))
        self.warn = QSpinBox()
        self.warn.setRange(0, 24 * 30)
        self.warn.setSuffix(" h")
        self.warn.setValue(prefs.get("warn_hours"))
        self.allow = QCheckBox(tr("Allow logging strict dupes (they are marked DUPE)"))
        self.allow.setChecked(prefs.get("allow_dupes"))
        self.auto_lookup = QCheckBox(tr("Look up calls automatically while typing"))
        self.auto_lookup.setChecked(prefs.get("auto_lookup"))
        f.addRow(tr("Dupe block window"), self.block)
        f.addRow("", self._hint(tr("Same call, band, mode and submode (and satellite) within this time: "
                                   "logging is blocked.")))
        f.addRow(tr("Dupe warning window"), self.warn)
        f.addRow("", self._hint(tr("Same mode later, or a related mode (SSB/USB, USB/LSB, FT8/FT4, DIGI/FT8), "
                                   "within this time: a warning only.")))
        f.addRow("", self.allow)
        f.addRow("", self.auto_lookup)
        self.tabs.addTab(lg, tr("Logging"))

        # Lookup
        lk = QWidget()
        f = QFormLayout(lk)
        self.qrz_user = QLineEdit(prefs.get("qrz_user"))
        self.qrz_pass = _password()
        self.ham_user = QLineEdit(prefs.get("hamqth_user"))
        self.ham_pass = _password()
        self.ttl = QSpinBox()
        self.ttl.setRange(0, 365)
        self.ttl.setSuffix(tr(" days"))
        self.ttl.setValue(prefs.get("lookup_ttl_days"))
        f.addRow(QLabel("<b>QRZ.com XML</b>"))
        f.addRow(tr("User"), self.qrz_user)
        f.addRow(tr("Password"), self.qrz_pass)
        f.addRow(QLabel("<b>HamQTH</b> " + tr("(fallback)")))
        f.addRow(tr("User"), self.ham_user)
        f.addRow(tr("Password"), self.ham_pass)
        f.addRow(tr("Cache lookups for"), self.ttl)
        self.tabs.addTab(lk, tr("Lookup"))

        # Services
        sv = QWidget()
        f = QFormLayout(sv)
        self.lotw_user = QLineEdit(prefs.get("lotw_user"))
        self.lotw_pass = _password()
        self.tqsl_path = QLineEdit(prefs.get("tqsl_path") or "")
        self.tqsl_path.setPlaceholderText(tqsl.find_tqsl() or tr("not found: install TQSL from arrl.org"))
        browse = QPushButton(tr("Browse…"))
        browse.clicked.connect(self._browse_tqsl)
        tq = QHBoxLayout()
        tq.addWidget(self.tqsl_path, 1)
        tq.addWidget(browse)
        self.eqsl_user = QLineEdit(prefs.get("eqsl_user"))
        self.eqsl_pass = _password()
        self.hrd_call = QLineEdit(prefs.get("hrdlog_call"))
        self.hrd_code = _password()
        self.qrz_keys = QTableWidget(0, 2)
        self.qrz_keys.setHorizontalHeaderLabels([tr("Station callsign"), tr("QRZ Logbook API key")])
        self.qrz_keys.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.qrz_keys.verticalHeader().setVisible(False)
        self.qrz_keys.setMaximumHeight(110)
        calls = sorted({p.get("station_callsign") for p in db.profiles() if p.get("station_callsign")})
        for c in calls:
            r = self.qrz_keys.rowCount()
            self.qrz_keys.insertRow(r)
            it = QTableWidgetItem(c)
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.qrz_keys.setItem(r, 0, it)
            self.qrz_keys.setItem(r, 1, QTableWidgetItem("••••••••" if prefs.qrz_api_key(c) else ""))
        upl = prefs.get("upload_on_log")
        self.up_qrz = QCheckBox("QRZ.com")
        self.up_qrz.setChecked(upl.get("qrz", False))
        self.up_eqsl = QCheckBox("eQSL")
        self.up_eqsl.setChecked(upl.get("eqsl", False))
        self.up_hrd = QCheckBox("HRDLog")
        self.up_hrd.setChecked(upl.get("hrdlog", False))
        up = QHBoxLayout()
        for cb in (self.up_qrz, self.up_eqsl, self.up_hrd):
            up.addWidget(cb)
        up.addStretch(1)
        f.addRow(QLabel("<b>LoTW</b>"))
        f.addRow(tr("User"), self.lotw_user)
        f.addRow(tr("Password"), self.lotw_pass)
        f.addRow(tr("TQSL program"), tq)
        f.addRow(QLabel("<b>eQSL</b>"))
        f.addRow(tr("User"), self.eqsl_user)
        f.addRow(tr("Password"), self.eqsl_pass)
        f.addRow(QLabel("<b>QRZ.com Logbook</b>"))
        f.addRow(self.qrz_keys)
        f.addRow(QLabel("<b>HRDLog.net</b>"))
        f.addRow(tr("Callsign"), self.hrd_call)
        f.addRow(tr("Upload code"), self.hrd_code)
        f.addRow(tr("Upload each QSO when logged"), up)
        self.tabs.addTab(sv, tr("Services"))

        # Satellite names
        st = QWidget()
        sl = QVBoxLayout(st)
        sl.addWidget(self._hint(tr("Satellites offered in the entry form, and the name sent to each service. Your "
                                   "log keeps one name per satellite. Add satellites that are not on the list "
                                   "yet.")))
        self.user_sats = list(prefs.get("user_sats") or [])
        sb = QHBoxLayout()
        add_sat = QPushButton(tr("Add satellite…"))
        add_sat.setIcon(theme.icon("plus"))
        add_sat.clicked.connect(self._add_sat)
        rm_sat = QPushButton(tr("Remove added satellite"))
        rm_sat.setIcon(theme.icon("trash-2"))
        rm_sat.clicked.connect(self._remove_sat)
        sb.addWidget(add_sat)
        sb.addWidget(rm_sat)
        sb.addStretch(1)
        sl.addLayout(sb)
        self.satmap = QTableWidget(0, 3)
        self.satmap.setHorizontalHeaderLabels([tr("Satellite (stored)"), "LoTW", "eQSL"])
        self.satmap.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.satmap.verticalHeader().setVisible(False)
        self.satmap.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._fill_satmap()
        sl.addWidget(self.satmap, 1)
        self.tabs.addTab(st, tr("Satellites"))

        # Database
        dbw = QWidget()
        f = QFormLayout(dbw)
        path = QLineEdit(str(db.path))
        path.setReadOnly(True)
        f.addRow(tr("Log file"), path)
        self.keep = QSpinBox()
        self.keep.setRange(1, 500)
        self.keep.setValue(prefs.get("backup_keep"))
        f.addRow(tr("Backups to keep"), self.keep)
        b = QHBoxLayout()
        now = QPushButton(tr("Back up now"))
        now.clicked.connect(self._backup)
        show = QPushButton(tr("Show backups folder"))
        show.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(db.backup_dir))))
        b.addWidget(now)
        b.addWidget(show)
        b.addStretch(1)
        f.addRow("", b)
        self.tabs.addTab(dbw, tr("Database"))

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Save).setProperty("primary", True)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if page:
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i) == tr(page):
                    self.tabs.setCurrentIndex(i)

    def _fill_satmap(self) -> None:
        cat = sats.load(overrides=self.prefs.get("sat_names"), extra=self.user_sats)
        added = {(s.get("name") or "").upper() for s in self.user_sats}
        self.satmap.setRowCount(0)
        for s in cat.sats:
            r = self.satmap.rowCount()
            self.satmap.insertRow(r)
            it = QTableWidgetItem(s.name + ("   ★" if s.name.upper() in added else ""))
            it.setData(Qt.ItemDataRole.UserRole, s.name)
            it.setToolTip(s.description or "")
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.satmap.setItem(r, 0, it)
            self.satmap.setItem(r, 1, QTableWidgetItem(cat.to_service("lotw", s.name)))
            self.satmap.setItem(r, 2, QTableWidgetItem(cat.to_service("eqsl", s.name)))

    def _add_sat(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Add satellite"))
        f = QFormLayout(dlg)
        name, desc, up, down = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        name.setPlaceholderText(tr("Name as LoTW / TQSL will expect it, e.g. SO-125"))
        up.setPlaceholderText(tr("MHz you transmit on"))
        down.setPlaceholderText(tr("MHz you receive on"))
        f.addRow(tr("Name"), name)
        f.addRow(tr("Description"), desc)
        f.addRow(tr("Uplink (TX MHz)"), up)
        f.addRow(tr("Downlink (RX MHz)"), down)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        f.addRow(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted or not name.text().strip():
            return

        def num(e: QLineEdit) -> float | None:
            try:
                return float(e.text().strip().replace(",", "."))
            except ValueError:
                return None

        self.user_sats = [s for s in self.user_sats if (s.get("name") or "").upper() != name.text().strip().upper()]
        self.user_sats.append({"name": name.text().strip().upper(), "description": desc.text().strip(),
                               "uplink": num(up), "downlink": num(down)})
        self._fill_satmap()

    def _remove_sat(self) -> None:
        row = self.satmap.currentRow()
        if row < 0:
            return
        name = self.satmap.item(row, 0).data(Qt.ItemDataRole.UserRole)
        before = len(self.user_sats)
        self.user_sats = [s for s in self.user_sats if (s.get("name") or "").upper() != (name or "").upper()]
        if len(self.user_sats) == before:
            QMessageBox.information(self, tr("Satellites"), tr("Only satellites you added (★) can be removed."))
            return
        self._fill_satmap()

    def _hint(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("Hint")
        lab.setWordWrap(True)
        return lab

    def _browse_tqsl(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, tr("Locate TQSL"), os.path.expanduser("~"))
        if p:
            self.tqsl_path.setText(p)

    def _backup(self) -> None:
        dest = self.db.backup("manual")
        QMessageBox.information(self, tr("Backup"), tr("Saved") + f"\n{dest}")

    def _save(self) -> None:
        p = self.prefs
        p.set("language", self.lang.currentData())
        p.set("theme", self.theme.currentData())
        p.set("block_seconds", self.block.value())
        p.set("warn_hours", self.warn.value())
        p.set("allow_dupes", self.allow.isChecked())
        p.set("auto_lookup", self.auto_lookup.isChecked())
        p.set("lookup_ttl_days", self.ttl.value())
        p.set("backup_keep", self.keep.value())
        for key, edit in (("qrz_user", self.qrz_user), ("hamqth_user", self.ham_user), ("lotw_user", self.lotw_user),
                          ("eqsl_user", self.eqsl_user), ("hrdlog_call", self.hrd_call)):
            p.set(key, edit.text().strip().upper() if key == "hrdlog_call" else edit.text().strip())
        p.set("tqsl_path", self.tqsl_path.text().strip())
        failed = []
        for key, edit in (("qrz_password", self.qrz_pass), ("hamqth_password", self.ham_pass),
                          ("lotw_password", self.lotw_pass), ("eqsl_password", self.eqsl_pass),
                          ("hrdlog_code", self.hrd_code)):
            if edit.text():
                if not p.set_secret(key, edit.text()):
                    failed.append(key)
        for r in range(self.qrz_keys.rowCount()):
            call = self.qrz_keys.item(r, 0).text()
            val = (self.qrz_keys.item(r, 1).text() if self.qrz_keys.item(r, 1) else "").strip()
            if val and not val.startswith("•"):
                if not p.set_qrz_api_key(call, val):
                    failed.append(f"QRZ key {call}")
        p.set("upload_on_log", {"qrz": self.up_qrz.isChecked(), "eqsl": self.up_eqsl.isChecked(),
                                "hrdlog": self.up_hrd.isChecked()})
        p.set("user_sats", self.user_sats)
        base = sats.load(extra=self.user_sats)
        over: dict[str, dict[str, str]] = {}
        for r in range(self.satmap.rowCount()):
            name = self.satmap.item(r, 0).data(Qt.ItemDataRole.UserRole)
            for c, svc in ((1, "lotw"), (2, "eqsl")):
                v = (self.satmap.item(r, c).text() if self.satmap.item(r, c) else "").strip().upper()
                if v and v != base.to_service(svc, name).upper():
                    over.setdefault(svc, {})[name] = v
        p.set("sat_names", over)
        if failed:
            QMessageBox.warning(self, tr("Keychain"), tr("Could not store these secrets in the system keychain:")
                                + "\n" + "\n".join(failed))
        self.accept()
