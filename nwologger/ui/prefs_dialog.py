"""Preferences: general, station profiles, logging, lookup, services, database."""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from ..core import devices, dxcc, sats
from ..core import secrets as keychain
from ..core.sync import tqsl
from . import theme
from .prefs import DEFAULTS, SECRET_KEYS
from .widgets import MODE_FIRST, all_modes, mode_list, sat_list

tr = i18n.tr
log = logging.getLogger("nwologger.prefs")


def describe_changes(before: dict, after: dict, secrets: list[str]) -> str:
    """What a Preferences save changed, for the log file: theme, language, numbers and switches with their values;
    lists (rigs, setups, modes…) as counts; account names and paths by name only; secrets as "changed"."""
    out = []
    for k, default in DEFAULTS.items():
        a, b = before.get(k), after.get(k)
        if a == b:
            continue
        if k in ("theme", "language") or isinstance(default, (bool, int)):
            out.append(f"{k} {a}→{b}")
        elif isinstance(default, list):
            out.append(f"{k} {len(a or [])}→{len(b or [])}")
        else:
            out.append(k)
    out += [f"{k} changed" for k in secrets]
    return "; ".join(out) or "nothing changed"

PROFILE_FIELDS = [
    ("name", "Profile name"), ("station_callsign", "Station callsign"), ("operator", "Operator (personal station)"),
    ("my_gridsquare", "My grid"), ("my_vucc_grids", "My VUCC grids"), ("my_cq_zone", "My CQ zone"),
    ("my_itu_zone", "My ITU zone"), ("my_dxcc", "My DXCC"), ("my_state", "My province"), ("my_cnty", "My county"),
    ("my_rig", "Rig"), ("my_antenna", "Antenna"), ("tx_pwr", "Default power (W)"), ("my_pota_ref", "My POTA"),
    ("my_sota_ref", "My SOTA"), ("my_wwff_ref", "My WWFF"), ("lotw_location", "TQSL station location"),
    ("eqsl_qth_nickname", "eQSL QTH nickname"),
]


class ChoiceEdit(QComboBox):
    """An editable dropdown with QLineEdit's text API, for a field with known values (TQSL station locations)."""

    def __init__(self, choices: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.addItems(choices)
        self.setCurrentIndex(-1)

    def text(self) -> str:
        return self.currentText()

    def setText(self, text: str | None) -> None:  # noqa: N802 - QLineEdit API
        self.setEditText(text or "")

    def clear(self) -> None:  # only the text: the choices stay
        self.setEditText("")

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802 - QLineEdit API
        self.lineEdit().setPlaceholderText(text)


class SecretField(QWidget):
    """A password field that never shows the saved value: type to replace it, Forget to remove it on Save."""

    def __init__(self, stored: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.stored, self.forget = stored, False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.textEdited.connect(lambda _t: self._update())
        self.forget_btn = QPushButton(tr("Forget"))
        self.forget_btn.setToolTip(tr("Remove the saved secret from the system keychain when you save"))
        self.forget_btn.clicked.connect(self._forget)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.forget_btn)
        self._update()

    def _forget(self) -> None:
        self.forget = True
        self.edit.clear()
        self._update()

    def _update(self) -> None:
        if self.forget:
            ph = tr("removed from the keychain when you save")
        elif self.stored:
            ph = tr("saved in the keychain; leave blank to keep it")
        else:
            ph = tr("not set")
        self.edit.setPlaceholderText(ph)
        self.forget_btn.setEnabled(self.stored and not self.forget)

    def action(self) -> tuple[str, str | None]:
        """("set", value), ("forget", None) or ("keep", None)."""
        if self.edit.text():
            return "set", self.edit.text()
        return ("forget", None) if self.forget else ("keep", None)


KEY_ROLE = Qt.ItemDataRole.UserRole  # a newly typed QRZ Logbook API key (never the saved one)
STATE_ROLE = Qt.ItemDataRole.UserRole + 1  # "stored", "new", "forget" or ""
MASKED = "••••••••"


class ApiKeyDelegate(QStyledItemDelegate):
    """QRZ Logbook API keys are typed in a password field and always shown as dots."""

    def createEditor(self, parent, _option, _index):  # noqa: N802 - Qt API
        e = QLineEdit(parent)
        e.setEchoMode(QLineEdit.EchoMode.Password)
        e.setPlaceholderText(tr("paste the API key"))
        return e

    def setEditorData(self, editor, _index):  # noqa: N802 - Qt API
        editor.clear()

    def setModelData(self, editor, model, index):  # noqa: N802 - Qt API
        v = editor.text().strip()
        if v:
            model.setData(index, v, KEY_ROLE)
            model.setData(index, "new", STATE_ROLE)
            model.setData(index, MASKED, Qt.ItemDataRole.DisplayRole)


class ProfilesPage(QWidget):
    changed = Signal()  # a profile was saved or deleted (its callsign may have changed)

    def __init__(self, db, prefs=None):
        super().__init__()
        self.db, self.prefs = db, prefs
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._load)
        self.list.itemDoubleClicked.connect(self._make_default)
        self.list.setToolTip(tr("Double-click a profile to make it the default"))
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
        locations = tqsl.station_locations()
        for key, label in PROFILE_FIELDS:
            if key == "lotw_location":
                e = ChoiceEdit(locations or [])
                e.setPlaceholderText(tr("choose one of your TQSL station locations") if locations else
                                     tr("as named in TQSL (no TQSL station locations found)"))
                e.setToolTip(tr("LoTW uploads are signed by TQSL with this station location"))
            else:
                e = QLineEdit()
            if key == "eqsl_qth_nickname":
                e.setPlaceholderText(tr("as on eQSL.cc → My Profile; needed for eQSL"))
            self.edits[key] = e
            form.addRow(tr(label), e)
            if key == "station_callsign":
                form.addRow(tr("Station type"), self.station_type)
                hint = QLabel(tr("Club stations ask for the operator at the first QSO, keep it while NWOlogger "
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
        old = ((self.db.profile(self.current_id) or {}).get("station_callsign") or "").upper() \
            if self.current_id is not None else ""
        pid = self.db.save_profile(data)
        self._call_changed(old, data["station_callsign"])
        self._reload(select=pid)
        self.changed.emit()

    def _call_used(self, call: str) -> bool:
        return any((p.get("station_callsign") or "").upper() == call for p in self.db.profiles())

    def _call_changed(self, old: str, new: str) -> None:
        """A QRZ.com Logbook key belongs to a callsign. Once no profile uses the old call, its key would be left in
        the keychain where nothing can reach it: it moves to the new call (a corrected callsign) or is removed (a
        callsign with its own logbook), as you choose."""
        if self.prefs is None or not old or old == new or self._call_used(old):
            return
        key = self.prefs.qrz_api_key(old)
        if not key:
            return
        if not self.prefs.qrz_api_key(new) and QMessageBox.question(
                self, tr("QRZ.com Logbook key"),
                tr("No station profile uses this callsign now:") + f" {old}\n\n" +
                tr("Use its QRZ.com Logbook API key for") + f" {new}?\n\n" +
                tr("Choose No when the new callsign has its own logbook: the old key is then removed.")
        ) == QMessageBox.StandardButton.Yes:
            self.prefs.set_qrz_api_key(new, key)
        self.prefs.set_qrz_api_key(old, None)

    def _make_default(self, item) -> None:
        pid = item.data(Qt.ItemDataRole.UserRole)
        p = self.db.profile(pid)
        if p:
            p["is_default"] = True
            self.db.save_profile(p)
            self._reload(select=pid)

    def _delete(self) -> None:
        if self.current_id is None:
            return
        call = ((self.db.profile(self.current_id) or {}).get("station_callsign") or "").upper()
        self.db.delete_profile(self.current_id)
        # The QRZ Logbook key belongs to the callsign: remove it once no profile uses that callsign.
        if self.prefs is not None and call and not self._call_used(call):
            self.prefs.set_qrz_api_key(call, None)
        self._reload()
        self.changed.emit()


class ChoiceDelegate(QStyledItemDelegate):
    """A setup's rig or antenna cell: pick one from your list, or type another name."""

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.items = items

    def createEditor(self, parent, _option, _index):  # noqa: N802 - Qt API
        c = QComboBox(parent)
        c.setEditable(True)
        c.addItems(self.items())
        return c

    def setEditorData(self, editor, index):  # noqa: N802 - Qt API
        editor.setEditText(index.data() or "")

    def setModelData(self, editor, model, index):  # noqa: N802 - Qt API
        model.setData(index, editor.currentText().strip())


class DevicesPage(QWidget):
    """Your rigs, antennas and setups (rig + antenna + power) for the entry form's Device section."""

    def __init__(self, db, prefs):
        super().__init__()
        self.db = db
        lay = QVBoxLayout(self)
        hint = QLabel(tr("Your rigs and antennas are listed in the entry form's Device section. A setup puts a rig, "
                         "an antenna and a power together: one pick in the form fills all three, and you can still "
                         "change each one. They are logged as ADIF MY_RIG, MY_ANTENNA and TX_PWR; when the form "
                         "leaves one empty, the station profile's is logged."))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lists = QHBoxLayout()
        self.rigs = self._list_box(lists, tr("Rigs"), prefs.get("rigs"), "my_rig")
        self.antennas = self._list_box(lists, tr("Antennas"), prefs.get("antennas"), "my_antenna")
        lay.addLayout(lists, 1)
        lay.addWidget(QLabel("<b>" + tr("Setups") + "</b>"))
        self.setups = QTableWidget(0, 4)
        self.setups.setHorizontalHeaderLabels([tr("Setup name"), tr("Rig"), tr("Antenna"), tr("Power (W)")])
        self.setups.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.setups.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.setups.verticalHeader().setVisible(False)
        self.setups.setItemDelegateForColumn(1, ChoiceDelegate(lambda: self.names(self.rigs), self.setups))
        self.setups.setItemDelegateForColumn(2, ChoiceDelegate(lambda: self.names(self.antennas), self.setups))
        for st in devices.clean_setups(prefs.get("setups")):
            self._add_setup(st)
        lay.addWidget(self.setups, 1)
        row = QHBoxLayout()
        add = QPushButton(tr("Add setup"))
        add.setIcon(theme.icon("plus"))
        add.clicked.connect(self._new_setup)
        rm = QPushButton(tr("Remove setup"))
        rm.setIcon(theme.icon("trash-2"))
        rm.clicked.connect(lambda: self.setups.removeRow(self.setups.currentRow())
                           if self.setups.currentRow() >= 0 else None)
        row.addWidget(add)
        row.addWidget(rm)
        row.addStretch(1)
        lay.addLayout(row)

    def _list_box(self, parent: QHBoxLayout, title: str, names, col: str) -> QListWidget:
        box = QVBoxLayout()
        box.addWidget(QLabel(f"<b>{title}</b>"))
        lw = QListWidget()
        lw.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        lw.setToolTip(tr("Double-click to rename; drag to reorder"))
        for n in devices.clean_names(names):
            self._add_item(lw, n)
        box.addWidget(lw, 1)
        row = QHBoxLayout()
        add = QPushButton(tr("Add"))
        add.clicked.connect(lambda: self._new_item(lw))
        rm = QPushButton(tr("Remove"))
        rm.clicked.connect(lambda: [lw.takeItem(lw.row(i)) for i in lw.selectedItems()])
        from_log = QPushButton(tr("Add the ones in your log"))
        from_log.setToolTip(tr("Adds every name your QSOs already use (e.g. from a MacLoggerDX import)"))
        from_log.clicked.connect(lambda: self._from_log(lw, col))
        for b in (add, rm, from_log):
            row.addWidget(b)
        row.addStretch(1)
        box.addLayout(row)
        parent.addLayout(box, 1)
        return lw

    @staticmethod
    def _add_item(lw: QListWidget, text: str) -> QListWidgetItem:
        it = QListWidgetItem(text)
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
        lw.addItem(it)
        return it

    def _new_item(self, lw: QListWidget) -> None:
        it = self._add_item(lw, "")
        lw.setCurrentItem(it)
        lw.editItem(it)

    def _from_log(self, lw: QListWidget, col: str) -> None:
        have = {n.lower() for n in self.names(lw)}
        for v in self.db.distinct(col):
            v = (v or "").strip()
            if v and v.lower() not in have:
                have.add(v.lower())
                self._add_item(lw, v)

    @staticmethod
    def names(lw: QListWidget) -> list[str]:
        return devices.clean_names(lw.item(i).text() for i in range(lw.count()))

    def _add_setup(self, st: dict) -> int:
        r = self.setups.rowCount()
        self.setups.insertRow(r)
        power = st.get("power")
        for c, v in enumerate([st.get("name"), st.get("rig"), st.get("antenna"),
                               f"{power:g}" if isinstance(power, float) else (power or "")]):
            self.setups.setItem(r, c, QTableWidgetItem(v or ""))
        return r

    def _new_setup(self) -> None:
        r = self._add_setup({"name": f"{tr('Setup')} {self.setups.rowCount() + 1}"})
        self.setups.setCurrentCell(r, 0)
        self.setups.editItem(self.setups.item(r, 0))

    def setups_value(self) -> list[dict]:
        rows = []
        for r in range(self.setups.rowCount()):
            cell = [(self.setups.item(r, c).text() if self.setups.item(r, c) else "").strip() for c in range(4)]
            rows.append({"name": cell[0], "rig": cell[1], "antenna": cell[2], "power": cell[3]})
        return devices.clean_setups(rows)

    def save(self, prefs) -> None:
        prefs.set("rigs", self.names(self.rigs))
        prefs.set("antennas", self.names(self.antennas))
        prefs.set("setups", self.setups_value())


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
        self._theme_at_open = prefs.get("theme")
        self.theme.currentIndexChanged.connect(self._preview_theme)
        f.addRow(tr("Language"), self.lang)
        lang_hint = QLabel(tr("A new language applies when you press OK."))
        lang_hint.setObjectName("Hint")
        f.addRow("", lang_hint)
        f.addRow(tr("Theme"), self.theme)
        self.tabs.addTab(g, tr("General"))

        # Station profiles
        self.profiles_page = ProfilesPage(db, prefs)
        self.tabs.addTab(self.profiles_page, tr("Station profiles"))
        self.devices_page = DevicesPage(db, prefs)
        self.tabs.addTab(self.devices_page, tr("Devices"))

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
        self.qrz_pass = SecretField(prefs.has_secret("qrz_password"))
        self.ham_user = QLineEdit(prefs.get("hamqth_user"))
        self.ham_pass = SecretField(prefs.has_secret("hamqth_password"))
        self.ttl = QSpinBox()
        self.ttl.setRange(0, 365)
        self.ttl.setSuffix(tr(" days"))
        self.ttl.setValue(prefs.get("lookup_ttl_days"))
        if not keychain.available():
            f.addRow(self._keychain_warning())
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
        self.lotw_pass = SecretField(prefs.has_secret("lotw_password"))
        self.tqsl_path = QLineEdit(prefs.get("tqsl_path") or "")
        self.tqsl_path.setPlaceholderText(tqsl.find_tqsl() or tr("not found: install TQSL from arrl.org"))
        browse = QPushButton(tr("Browse…"))
        browse.clicked.connect(self._browse_tqsl)
        tq = QHBoxLayout()
        tq.addWidget(self.tqsl_path, 1)
        tq.addWidget(browse)
        self.eqsl_user = QLineEdit(prefs.get("eqsl_user"))
        self.eqsl_pass = SecretField(prefs.has_secret("eqsl_password"))
        self.hrd_call = QLineEdit(prefs.get("hrdlog_call"))
        self.hrd_code = SecretField(prefs.has_secret("hrdlog_code"))
        self.qrz_keys = QTableWidget(0, 3)
        self.qrz_keys.setHorizontalHeaderLabels([tr("Station callsign"), tr("QRZ Logbook API key"), ""])
        self.qrz_keys.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.qrz_keys.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.qrz_keys.verticalHeader().setVisible(False)
        self.qrz_keys.setMaximumHeight(110)
        self.qrz_keys.setItemDelegateForColumn(1, ApiKeyDelegate(self.qrz_keys))
        self.qrz_keys.setToolTip(tr("Double-click a key cell to type a new key. Saved keys are never shown."))
        self._fill_qrz_keys()
        self.profiles_page.changed.connect(self._fill_qrz_keys)
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
        if not keychain.available():
            f.addRow(self._keychain_warning())
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

        # Modes shown in the entry form
        mw = QWidget()
        ml = QVBoxLayout(mw)
        ml.addWidget(self._hint(tr("Tick the modes to list in the entry form, and drag them into the order you "
                                   "like. Every other mode stays available under “Other mode…”.")))
        mrow = QHBoxLayout()
        self.mode_find = QLineEdit()
        self.mode_find.setPlaceholderText(tr("Search…"))
        self.mode_find.setClearButtonEnabled(True)
        self.mode_find.textChanged.connect(self._filter_modes)
        reset = QPushButton(tr("Reset to default"))
        reset.clicked.connect(lambda: self._fill_modes(list(MODE_FIRST)))
        mrow.addWidget(self.mode_find, 1)
        mrow.addWidget(reset)
        ml.addLayout(mrow)
        self.mode_list = QListWidget()
        self.mode_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        ml.addWidget(self.mode_list, 1)
        self._fill_modes(mode_list(prefs))
        self.tabs.addTab(mw, tr("Modes"))

        # Satellite names
        st = QWidget()
        sl = QVBoxLayout(st)
        sl.addWidget(self._hint(tr("Tick the satellites to list in the entry form (the others stay under “Other "
                                   "satellite…”), and set the name sent to each service. Your log keeps one name per "
                                   "satellite. Add satellites that are not on the list yet.")))
        self.user_sats = list(prefs.get("user_sats") or [])
        cat0 = sats.load(overrides=prefs.get("sat_names"), extra=self.user_sats)
        self.worked_sats = [n.upper() for n in db.distinct("sat_name")]
        self.sat_checked = set(sat_list(prefs, cat0, self.worked_sats))
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
        self.satmap.setHorizontalHeaderLabels([tr("In form · satellite (stored)"), "LoTW", "eQSL"])
        self.satmap.itemChanged.connect(self._sat_check_changed)
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
        f.addRow("", self._hint(tr("A copy is made each time NWOlogger opens the log (skipped when nothing changed "
                                   "since the last copy) and when you choose Back up now. The last 5 copies made "
                                   "before an import or a bulk edit are kept as well.")))
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

    def _fill_modes(self, chosen: list[str]) -> None:
        self.mode_list.clear()
        order = [*chosen, *[m for m in all_modes() if m not in chosen]]
        for m in order:
            it = QListWidgetItem(m)
            it.setData(Qt.ItemDataRole.UserRole, m)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            it.setCheckState(Qt.CheckState.Checked if m in chosen else Qt.CheckState.Unchecked)
            self.mode_list.addItem(it)

    def _filter_modes(self, text: str) -> None:
        t = text.strip().upper()
        for i in range(self.mode_list.count()):
            it = self.mode_list.item(i)
            it.setHidden(bool(t) and t not in it.text().upper())

    def _sat_check_changed(self, it) -> None:
        if it.column() != 0:
            return
        name = it.data(Qt.ItemDataRole.UserRole)
        if it.checkState() == Qt.CheckState.Checked:
            self.sat_checked.add(name)
        else:
            self.sat_checked.discard(name)

    def _fill_satmap(self) -> None:
        cat = sats.load(overrides=self.prefs.get("sat_names"), extra=self.user_sats)
        added = {(s.get("name") or "").upper() for s in self.user_sats}
        known = {s.name.upper() for s in cat.sats}
        rows = [(s.name, s.description) for s in cat.sats]
        rows += [(n, tr("From your log")) for n in self.worked_sats if n not in known]
        self.satmap.blockSignals(True)
        self.satmap.setRowCount(0)
        for name, desc in rows:
            r = self.satmap.rowCount()
            self.satmap.insertRow(r)
            it = QTableWidgetItem(name + ("   ★" if name.upper() in added else ""))
            it.setData(Qt.ItemDataRole.UserRole, name)
            it.setToolTip(desc or "")
            it.setFlags((it.flags() & ~Qt.ItemFlag.ItemIsEditable) | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if name in self.sat_checked else Qt.CheckState.Unchecked)
            self.satmap.setItem(r, 0, it)
            self.satmap.setItem(r, 1, QTableWidgetItem(cat.to_service("lotw", name)))
            self.satmap.setItem(r, 2, QTableWidgetItem(cat.to_service("eqsl", name)))
        self.satmap.blockSignals(False)

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
        self.sat_checked.add(name.text().strip().upper())
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

    def _preview_theme(self) -> None:
        """The theme you pick is shown at once; Cancel puts the one you had back."""
        theme.apply(QApplication.instance(), self.theme.currentData())

    def reject(self) -> None:
        if self.theme.currentData() != self._theme_at_open:
            theme.apply(QApplication.instance(), self._theme_at_open)
        super().reject()

    def _fill_qrz_keys(self) -> None:
        """One row per station callsign in your profiles; a key typed or forgotten but not yet saved stays."""
        pending = {}
        for r in range(self.qrz_keys.rowCount()):
            item = self.qrz_keys.item(r, 1)
            if item is not None and item.data(STATE_ROLE) in ("new", "forget"):
                pending[self.qrz_keys.item(r, 0).text()] = (item.text(), item.data(STATE_ROLE), item.data(KEY_ROLE))
        self.qrz_keys.setRowCount(0)
        calls = sorted({p.get("station_callsign") for p in self.db.profiles() if p.get("station_callsign")})
        for c in calls:
            r = self.qrz_keys.rowCount()
            self.qrz_keys.insertRow(r)
            it = QTableWidgetItem(c)
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.qrz_keys.setItem(r, 0, it)
            stored = bool(self.prefs.qrz_api_key(c))
            key = QTableWidgetItem(MASKED if stored else "")
            key.setData(STATE_ROLE, "stored" if stored else "")
            if c in pending:
                text, state, value = pending[c]
                key.setText(text)
                key.setData(STATE_ROLE, state)
                key.setData(KEY_ROLE, value)
            self.qrz_keys.setItem(r, 1, key)
            btn = QPushButton(tr("Forget"))
            btn.setToolTip(tr("Remove this key from the system keychain when you save"))
            btn.clicked.connect(lambda _=False, item=key: self._forget_key(item))
            self.qrz_keys.setCellWidget(r, 2, btn)

    def _keychain_warning(self) -> QLabel:
        lab = QLabel(tr("No secure system keychain is available, so passwords and keys cannot be saved.") +
                     (f" ({keychain.backend_name()})" if keychain.backend_name() else ""))
        lab.setWordWrap(True)
        lab.setProperty("muted", True)
        return lab

    def _forget_key(self, item: QTableWidgetItem) -> None:
        item.setData(KEY_ROLE, None)
        item.setData(STATE_ROLE, "forget")
        item.setText(tr("removed when you save"))

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
        before = {k: p.get(k) for k in DEFAULTS}
        touched: list[str] = []  # secrets written or forgotten (never their values)
        p.set("language", self.lang.currentData())
        p.set("theme", self.theme.currentData())
        p.set("block_seconds", self.block.value())
        p.set("warn_hours", self.warn.value())
        p.set("allow_dupes", self.allow.isChecked())
        p.set("auto_lookup", self.auto_lookup.isChecked())
        p.set("lookup_ttl_days", self.ttl.value())
        p.set("backup_keep", self.keep.value())
        old_users = {key: p.get(key) or "" for key in ("qrz_user", "hamqth_user", "lotw_user", "eqsl_user",
                                                         "hrdlog_call")}
        for key, edit in (("qrz_user", self.qrz_user), ("hamqth_user", self.ham_user), ("lotw_user", self.lotw_user),
                          ("eqsl_user", self.eqsl_user), ("hrdlog_call", self.hrd_call)):
            p.set(key, edit.text().strip().upper() if key == "hrdlog_call" else edit.text().strip())
        p.set("tqsl_path", self.tqsl_path.text().strip())
        failed = []
        fields = {"qrz_password": self.qrz_pass, "hamqth_password": self.ham_pass, "lotw_password": self.lotw_pass,
                  "eqsl_password": self.eqsl_pass, "hrdlog_code": self.hrd_code}
        for key, field in fields.items():
            user_key = SECRET_KEYS[key][1]
            if not p.account_renamed(key, old_users[user_key], p.get(user_key) or ""):
                failed.append(key)
            what, value = field.action()
            if what == "forget" and not p.get(user_key):
                continue  # the account name was cleared: its entry went with the rename above
            if what != "keep":
                touched.append(key)
                if not p.set_secret(key, value):
                    failed.append(key)
        for r in range(self.qrz_keys.rowCount()):
            call = self.qrz_keys.item(r, 0).text()
            item = self.qrz_keys.item(r, 1)
            state = item.data(STATE_ROLE) if item else ""
            if state in ("new", "forget"):
                touched.append("qrz_logbook_key")
                if not p.set_qrz_api_key(call, item.data(KEY_ROLE) if state == "new" else None):
                    failed.append(f"QRZ key {call}")
        p.set("upload_on_log", {"qrz": self.up_qrz.isChecked(), "eqsl": self.up_eqsl.isChecked(),
                                "hrdlog": self.up_hrd.isChecked()})
        p.set("user_sats", self.user_sats)
        self.devices_page.save(p)
        modes_chosen = [self.mode_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.mode_list.count())
                        if self.mode_list.item(i).checkState() == Qt.CheckState.Checked]
        p.set("mode_list", modes_chosen or list(MODE_FIRST))
        p.set("sat_list", [self.satmap.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in range(self.satmap.rowCount())
                           if self.satmap.item(r, 0).checkState() == Qt.CheckState.Checked])
        base = sats.load(extra=self.user_sats)
        over: dict[str, dict[str, str]] = {}
        for r in range(self.satmap.rowCount()):
            name = self.satmap.item(r, 0).data(Qt.ItemDataRole.UserRole)
            for c, svc in ((1, "lotw"), (2, "eqsl")):
                v = (self.satmap.item(r, c).text() if self.satmap.item(r, c) else "").strip().upper()
                if v and v != base.to_service(svc, name).upper():
                    over.setdefault(svc, {})[name] = v
        p.set("sat_names", over)
        log.info("Preferences saved: %s", describe_changes(before, {k: p.get(k) for k in DEFAULTS}, touched))
        if failed:
            QMessageBox.warning(self, tr("Keychain"), tr("Could not store these secrets in the system keychain:")
                                + "\n" + "\n".join(failed))
        self.accept()
