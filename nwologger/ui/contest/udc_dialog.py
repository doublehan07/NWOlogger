"""User-defined contests: a simple form for common contests, and JSON for anything else."""

from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ... import i18n
from ...core.bands import BAND_NAMES
from ...core.contest.engine import ContestDef, save_user_def, validate_def
from .. import theme
from ..widgets import ScrollArea, fit_to_screen

tr = i18n.tr

BANDS = BAND_NAMES
EXCH = {
    "serial": ("Serial number", {"key": "serial", "label": "Nr", "kind": "serial"}),
    "cqz": ("CQ zone", {"key": "zone", "label": "CQ zone", "kind": "int", "min": 1, "max": 40, "prefill": "cqz",
                        "sent_from": "my_cq_zone", "adif": "cqz"}),
    "ituz": ("ITU zone", {"key": "zone", "label": "ITU zone", "kind": "int", "min": 1, "max": 90, "prefill": "ituz",
                          "sent_from": "my_itu_zone", "adif": "ituz"}),
    "state": ("Province / state", {"key": "qth", "label": "Province", "kind": "text", "sent_from": "my_state",
                                   "adif": "state", "prefill": "history"}),
    "grid": ("Grid (4 characters)", {"key": "grid", "label": "Grid", "kind": "grid", "sent_from": "my_gridsquare",
                                     "adif": "gridsquare", "prefill": "history"}),
    "name": ("Name", {"key": "name", "label": "Name", "kind": "text", "prefill": "history"}),
    "age": ("Age", {"key": "age", "label": "Age", "kind": "int", "min": 0, "max": 99}),
    "text": ("Free text", {"key": "exch", "label": "Exchange", "kind": "text", "prefill": "history"}),
}
HELP = (
    "Conditions in points / mults 'when' lists: same_dxcc, same_cont, same_ituz, sat, me_cont:AS, they_cont:NA, "
    "band:160m,80m, mode:CW|PHONE|DATA, me_dxcc_in:291,1, they_dxcc_in:291,1, exch_is_text:<key>, not:<cond>. "
    "Mult values: dxcc, wpx, cqz, ituz, exch:<key>, grid4:<key>; per: band, band_mode, mode, contest."
)


class UdcDialog(QDialog):
    def __init__(self, defs: dict[str, ContestDef], user_dir: Path, parent=None):
        super().__init__(parent)
        self.defs, self.user_dir = defs, Path(user_dir)
        self.setWindowTitle(tr("Contest definitions"))
        self.resize(980, 640)
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._load)
        left.addWidget(self.list, 1)
        new = QPushButton(tr("New contest"))
        new.setIcon(theme.icon("plus"))
        new.clicked.connect(self._new)
        dup = QPushButton(tr("Copy as new"))
        dup.clicked.connect(self._copy)
        self.delete = QPushButton(tr("Delete"))
        self.delete.setIcon(theme.icon("trash-2"))
        self.delete.clicked.connect(self._delete)
        for b in (new, dup, self.delete):
            left.addWidget(b)
        lay.addLayout(left, 1)
        right = QVBoxLayout()
        self.tabs = QTabWidget()
        right.addWidget(self.tabs, 1)
        form_w = QWidget()
        f = QFormLayout(form_w)
        self.f_name = QLineEdit()
        self.f_cab = QLineEdit()
        self.f_id = QLineEdit()
        f.addRow(tr("Name"), self.f_name)
        f.addRow(tr("Cabrillo contest name"), self.f_cab)
        f.addRow(tr("ID"), self.f_id)
        mrow = QHBoxLayout()
        self.f_modes = {}
        for g, label in (("CW", "CW"), ("PHONE", "Phone"), ("DATA", "Data")):
            cb = QCheckBox(tr(label))
            self.f_modes[g] = cb
            mrow.addWidget(cb)
        mrow.addStretch(1)
        f.addRow(tr("Modes"), mrow)
        bgrid = QGridLayout()
        self.f_bands = {}
        for i, b in enumerate(BANDS):
            cb = QCheckBox(b.upper())
            self.f_bands[b] = cb
            bgrid.addWidget(cb, i // 6, i % 6)
        f.addRow(tr("Bands"), bgrid)
        self.f_exch = QComboBox()
        for k, (label, _) in EXCH.items():
            self.f_exch.addItem(tr(label), k)
        f.addRow(tr("Exchange (after RST)"), self.f_exch)
        self.f_dupe = QComboBox()
        for k, label in (("band", "Once per band"), ("band_mode", "Once per band and mode"),
                         ("once", "Once per contest")):
            self.f_dupe.addItem(tr(label), k)
        f.addRow(tr("Dupes"), self.f_dupe)
        prow = QHBoxLayout()
        self.p_same_c = QSpinBox()
        self.p_same_k = QSpinBox()
        self.p_other = QSpinBox()
        for sb, label in ((self.p_same_c, "same country"), (self.p_same_k, "same continent"),
                          (self.p_other, "other continent")):
            sb.setRange(0, 100)
            prow.addWidget(QLabel(tr(label)))
            prow.addWidget(sb)
        prow.addStretch(1)
        f.addRow(tr("Points"), prow)
        mrow2 = QHBoxLayout()
        self.f_mults = {}
        for k, label in (("dxcc", "DXCC"), ("wpx", "WPX prefix"), ("cqz", "CQ zone"), ("ituz", "ITU zone"),
                         ("exch", "Exchange value"), ("grid", "Grid")):
            cb = QCheckBox(tr(label))
            self.f_mults[k] = cb
            mrow2.addWidget(cb)
        mrow2.addStretch(1)
        f.addRow(tr("Multipliers"), mrow2)
        self.f_per = QComboBox()
        for k, label in (("band", "per band"), ("band_mode", "per band and mode"), ("mode", "per mode"),
                         ("contest", "once per contest")):
            self.f_per.addItem(tr(label), k)
        f.addRow(tr("Count multipliers"), self.f_per)
        gen = QPushButton(tr("Update JSON from the form"))
        gen.clicked.connect(self._form_to_json)
        f.addRow("", gen)
        self.tabs.addTab(ScrollArea(form_w), tr("Simple"))
        jw = QWidget()
        jl = QVBoxLayout(jw)
        self.json = QPlainTextEdit()
        self.json.setFont(theme.mono_font())
        jl.addWidget(self.json, 1)
        h = QLabel(HELP)
        h.setObjectName("Hint")
        h.setWordWrap(True)
        jl.addWidget(h)
        self.tabs.addTab(ScrollArea(jw), tr("JSON (advanced)"))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        self.save_btn = bb.button(QDialogButtonBox.StandardButton.Save)
        self.save_btn.setProperty("primary", True)
        self.save_btn.clicked.connect(self._save)
        bb.rejected.connect(self.reject)
        right.addWidget(bb)
        lay.addLayout(right, 3)
        self._reload()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        fit_to_screen(self)

    def _reload(self, select: str | None = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        self.items = sorted(self.defs.values(), key=lambda d: (not d.user, d.name))
        for d in self.items:
            it = QListWidgetItem(("★ " if d.user else "") + d.name)
            it.setData(Qt.ItemDataRole.UserRole, d.id)
            self.list.addItem(it)
        self.list.blockSignals(False)
        idx = next((i for i, d in enumerate(self.items) if d.id == select), 0)
        if self.items:
            self.list.setCurrentRow(idx)
            self._load(idx)

    def _load(self, row: int) -> None:
        if row < 0 or row >= len(self.items):
            return
        d = self.items[row]
        self.current = d
        self.json.setPlainText(json.dumps(d.to_dict(), indent=1, ensure_ascii=False))
        self._json_to_form(d.to_dict())
        editable = d.user
        self.save_btn.setEnabled(editable)
        self.delete.setEnabled(editable)
        self.json.setReadOnly(not editable)
        if not editable:
            self.tabs.setTabText(0, tr("Simple") + " (" + tr("built-in: use Copy as new") + ")")
        else:
            self.tabs.setTabText(0, tr("Simple"))

    def _json_to_form(self, d: dict) -> None:
        self.f_name.setText(d.get("name", ""))
        self.f_cab.setText(d.get("cabrillo", ""))
        self.f_id.setText(d.get("id", ""))
        for g, cb in self.f_modes.items():
            cb.setChecked(g in d.get("modes", []))
        for b, cb in self.f_bands.items():
            cb.setChecked(b in d.get("bands", []))
        self.f_dupe.setCurrentIndex(max(0, self.f_dupe.findData(d.get("dupe", "band"))))
        keys = [f.get("key") for f in d.get("exchange", []) if f.get("kind") != "rst"]
        guess = {"serial": "serial", "zone": "cqz", "qth": "state", "grid": "grid", "name": "name", "age": "age"}
        self.f_exch.setCurrentIndex(max(0, self.f_exch.findData(guess.get(keys[0] if keys else "", "text"))))
        pts = {tuple(r.get("when", [])): r.get("points", 0) for r in d.get("points", [])}
        self.p_same_c.setValue(int(pts.get(("same_dxcc",), pts.get((), 1))))
        self.p_same_k.setValue(int(pts.get(("same_cont",), pts.get((), 1))))
        self.p_other.setValue(int(pts.get((), 1)))
        vals = {m.get("value") for m in d.get("mults", [])}
        self.f_mults["dxcc"].setChecked("dxcc" in vals)
        self.f_mults["wpx"].setChecked("wpx" in vals)
        self.f_mults["cqz"].setChecked("cqz" in vals or "exch:zone" in vals)
        self.f_mults["ituz"].setChecked("ituz" in vals)
        self.f_mults["exch"].setChecked(any(str(v).startswith("exch:") and v != "exch:zone" for v in vals))
        self.f_mults["grid"].setChecked(any(str(v).startswith("grid4:") for v in vals))
        per = next((m.get("per") for m in d.get("mults", [])), "band")
        self.f_per.setCurrentIndex(max(0, self.f_per.findData(per)))

    def _form_to_json(self) -> dict:
        name = self.f_name.text().strip() or tr("My contest")
        cid = self.f_id.text().strip() or re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").upper()
        exch_key = self.f_exch.currentData()
        ex = [{"key": "rst", "label": "RST", "kind": "rst"}, dict(EXCH[exch_key][1])]
        per = self.f_per.currentData()
        mults = []
        mk = ex[1]["key"]
        for k, cb in self.f_mults.items():
            if not cb.isChecked():
                continue
            if k == "dxcc":
                mults.append({"key": "dxcc", "label": "DXCC", "value": "dxcc", "per": per})
            elif k == "wpx":
                mults.append({"key": "wpx", "label": "Prefixes", "value": "wpx", "per": per})
            elif k == "cqz":
                mults.append({"key": "cqz", "label": "CQ zones",
                              "value": "exch:zone" if exch_key == "cqz" else "cqz", "per": per})
            elif k == "ituz":
                mults.append({"key": "ituz", "label": "ITU zones",
                              "value": "exch:zone" if exch_key == "ituz" else "ituz", "per": per})
            elif k == "exch":
                mults.append({"key": "exch", "label": ex[1]["label"], "value": f"exch:{mk}", "per": per})
            elif k == "grid":
                mults.append({"key": "grid", "label": "Grids", "value": f"grid4:{mk}" if exch_key == "grid" else
                              "grid4:grid", "per": per})
        d = {
            "id": cid, "name": name, "cabrillo": self.f_cab.text().strip() or cid, "sponsor": "",
            "notes": tr("User-defined contest."),
            "modes": [g for g, cb in self.f_modes.items() if cb.isChecked()] or ["CW", "PHONE", "DATA"],
            "bands": [b for b, cb in self.f_bands.items() if cb.isChecked()] or ["20m"],
            "dupe": self.f_dupe.currentData(), "exchange": ex,
            "points": [{"when": ["same_dxcc"], "points": self.p_same_c.value()},
                       {"when": ["same_cont"], "points": self.p_same_k.value()},
                       {"points": self.p_other.value()}],
            "mults": mults,
        }
        self.json.setReadOnly(False)
        self.json.setPlainText(json.dumps(d, indent=1, ensure_ascii=False))
        return d

    def _new(self) -> None:
        self._json_to_form({"name": tr("My contest"), "modes": ["CW", "PHONE"], "bands": ["40m", "20m", "15m"],
                            "exchange": [{"key": "rst", "kind": "rst"}, {"key": "serial", "kind": "serial"}],
                            "points": [{"points": 1}], "mults": [{"value": "dxcc", "per": "band"}]})
        self.f_id.clear()
        self._form_to_json()
        self.current = None
        self.save_btn.setEnabled(True)
        self.tabs.setCurrentIndex(0)

    def _copy(self) -> None:
        d = self.current.to_dict() if getattr(self, "current", None) else {}
        d["id"] = d.get("id", "CONTEST") + "-MY"
        d["name"] = d.get("name", "") + " (" + tr("copy") + ")"
        self.json.setReadOnly(False)
        self.json.setPlainText(json.dumps(d, indent=1, ensure_ascii=False))
        self._json_to_form(d)
        self.current = None
        self.save_btn.setEnabled(True)
        self.tabs.setCurrentIndex(1)

    def _save(self) -> None:
        if self.tabs.currentIndex() == 0:
            self._form_to_json()
        try:
            d = json.loads(self.json.toPlainText())
        except json.JSONDecodeError as e:
            QMessageBox.warning(self, tr("Contest definitions"), tr("The JSON is not valid") + f":\n{e}")
            return
        problems = validate_def(d)
        if problems:
            QMessageBox.warning(self, tr("Contest definitions"), "\n".join(problems))
            return
        cdef = ContestDef.from_dict(d, user=True)
        builtin = self.defs.get(cdef.id)
        if builtin is not None and not builtin.user:
            QMessageBox.warning(self, tr("Contest definitions"), tr("That ID belongs to a built-in contest. "
                                                                    "Choose another ID."))
            return
        path = save_user_def(cdef, self.user_dir)
        cdef.path = str(path)
        self.defs[cdef.id] = cdef
        self._reload(select=cdef.id)

    def _delete(self) -> None:
        d = getattr(self, "current", None)
        if not d or not d.user or not d.path:
            return
        if QMessageBox.question(self, tr("Delete"), tr("Delete the contest definition") + f" {d.name}?"
                                ) == QMessageBox.StandardButton.Yes:
            Path(d.path).unlink(missing_ok=True)
            self.defs.pop(d.id, None)
            self._reload()
