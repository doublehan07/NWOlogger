"""New / edit a contest session: contest chosen from a dropdown, sent exchange, Cabrillo categories."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ... import i18n
from ...core import timeutil
from ...core.contest.cabrillo import CATEGORY_CHOICES, CATEGORY_KEYS
from ...core.contest.engine import ContestDef, Me
from ..widgets import UtcDateTimeEdit

tr = i18n.tr


class SetupDialog(QDialog):
    def __init__(self, db, defs: dict[str, ContestDef], session: dict | None, parent=None):
        super().__init__(parent)
        self.db, self.defs, self.session = db, defs, session
        self.setWindowTitle(tr("Contest settings") if session else tr("New contest"))
        self.resize(640, 620)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        page = QWidget()
        f = QFormLayout(page)
        self.contest = QComboBox()
        for d in sorted(defs.values(), key=lambda d: (d.user, d.name)):
            self.contest.addItem(("★ " if d.user else "") + d.name, d.id)
        self.notes = QLabel()
        self.notes.setObjectName("Hint")
        self.notes.setWordWrap(True)
        self.name = QLineEdit()
        self.start = UtcDateTimeEdit()
        self.profile = QComboBox()
        for p in db.profiles():
            self.profile.addItem(p["name"], p["id"])
        f.addRow(tr("Contest"), self.contest)
        f.addRow("", self.notes)
        f.addRow(tr("Session name"), self.name)
        f.addRow(tr("Start (UTC)"), self.start)
        f.addRow(tr("Station profile"), self.profile)
        self.sent_box = QGridLayout()
        sent_w = QWidget()
        sent_w.setLayout(self.sent_box)
        f.addRow(tr("Sent exchange"), sent_w)
        tabs.addTab(page, tr("Contest"))

        cab = QWidget()
        cf = QFormLayout(cab)
        self.cats: dict[str, QComboBox] = {}
        for k in CATEGORY_KEYS:
            c = QComboBox()
            c.addItems(CATEGORY_CHOICES[k])
            self.cats[k] = c
            cf.addRow(k.replace("CATEGORY-", "").title(), c)
        self.extra: dict[str, QLineEdit] = {}
        for key, label in (("operators", "Operators"), ("club", "Club"), ("name", "Name"), ("email", "Email"),
                           ("location", "Location (ARRL section / DX)")):
            e = QLineEdit()
            self.extra[key] = e
            cf.addRow(tr(label), e)
        self.address = QPlainTextEdit()
        self.address.setMaximumHeight(70)
        self.soapbox = QPlainTextEdit()
        self.soapbox.setMaximumHeight(70)
        cf.addRow(tr("Address"), self.address)
        cf.addRow(tr("Soapbox"), self.soapbox)
        tabs.addTab(cab, tr("Cabrillo"))

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Start") if not session else tr("Save"))
        bb.button(QDialogButtonBox.StandardButton.Ok).setProperty("primary", True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.sent_edits: dict[str, QLineEdit] = {}
        self.contest.currentIndexChanged.connect(self._contest_changed)
        self.profile.currentIndexChanged.connect(self._contest_changed)
        if session:
            i = self.contest.findData(session["contest_def"])
            self.contest.setCurrentIndex(max(0, i))
            self.contest.setEnabled(False)
            self.name.setText(session["name"])
            self.start.set_iso(session["started_at"])
            j = self.profile.findData(session.get("station_profile_id"))
            if j >= 0:
                self.profile.setCurrentIndex(j)
            data = session.get("data") or {}
            for k, c in self.cats.items():
                v = (data.get("categories") or {}).get(k)
                if v is not None:
                    c.setCurrentText(v)
            for k, e in self.extra.items():
                e.setText(data.get(k) or "")
            self.address.setPlainText(data.get("address") or "")
            self.soapbox.setPlainText(data.get("soapbox") or "")
            self._contest_changed()
            for k, v in (data.get("sent") or {}).items():
                if k in self.sent_edits:
                    self.sent_edits[k].setText(v)
        else:
            last = db.meta("last_contest_def") or "CQWW-CW"
            i = self.contest.findData(last)
            if i >= 0:
                self.contest.setCurrentIndex(i)
            self.start.set_iso(timeutil.now_iso())
            self._contest_changed()

    def cdef(self) -> ContestDef:
        return self.defs[self.contest.currentData()]

    def _contest_changed(self) -> None:
        d = self.cdef()
        self.notes.setText(d.notes)
        if not self.session:
            self.name.setText(f"{d.name} {timeutil.now_utc().year}")
            mode = {"CW": "CW", "PHONE": "SSB", "DATA": "RTTY" if (d.default_mode or "") == "RTTY" else "DIGI"}
            self.cats["CATEGORY-MODE"].setCurrentText(mode.get(d.modes[0], "MIXED") if len(d.modes) == 1 else "MIXED")
            self.cats["CATEGORY-BAND"].setCurrentText("ALL" if len(d.bands) > 1 else d.bands[0].upper())
            self.cats["CATEGORY-POWER"].setCurrentText("LOW")
        while self.sent_box.count():
            w = self.sent_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        prof = self.db.profile(self.profile.currentData()) or {}
        me = Me.from_profile(prof)
        wve_xe = me.dxcc in (291, 6, 110, 1, 50)
        self.sent_edits = {}
        for i, f in enumerate(d.exchange):
            if f.get("kind") == "serial":
                lab = QLabel(tr(f.get("label", f["key"])))
                val = QLabel(tr("automatic serial number"))
                val.setObjectName("Hint")
                self.sent_box.addWidget(lab, i, 0)
                self.sent_box.addWidget(val, i, 1)
                continue
            e = QLineEdit()
            src = f.get("sent_from")
            v = ""
            if f.get("kind") == "rst":
                v = "59" if d.modes == ["PHONE"] else "599"
            elif f.get("serial_if_dx") and not wve_xe:
                v = "#"
            elif src in ("my_cq_zone", "my_itu_zone"):
                v = str((me.cqz if src == "my_cq_zone" else me.ituz) or "")
            elif src == "my_gridsquare":
                v = (me.grid or "")[:4]
            elif src == "my_state":
                v = me.state or ""
            elif src == "tx_pwr" and me.dxcc not in (291, 1):
                v = str(int(prof["tx_pwr"])) if prof.get("tx_pwr") else "100"
            elif src == "tx_pwr":
                v = me.state or ""
            e.setText(v)
            e.setPlaceholderText(tr("# = serial number") if f.get("serial_if_dx") else "")
            self.sent_box.addWidget(QLabel(tr(f.get("label", f["key"]))), i, 0)
            self.sent_box.addWidget(e, i, 1)
            self.sent_edits[f["key"]] = e

    def save(self) -> int:
        d = self.cdef()
        data = dict((self.session or {}).get("data") or {})
        data["sent"] = {k: e.text().strip().upper() for k, e in self.sent_edits.items()}
        data["categories"] = {k: c.currentText() for k, c in self.cats.items() if c.currentText()}
        for k, e in self.extra.items():
            data[k] = e.text().strip()
        data["address"] = self.address.toPlainText().strip()
        data["soapbox"] = self.soapbox.toPlainText().strip()
        if self.session:
            self.db.update_contest_session(self.session["id"], name=self.name.text().strip() or d.name,
                                           started_at=self.start.iso(), station_profile_id=self.profile.currentData(),
                                           data=data)
            return self.session["id"]
        self.db.set_meta("last_contest_def", d.id)
        return self.db.create_contest_session(d.id, self.name.text().strip() or d.name, self.start.iso(), data,
                                              self.profile.currentData())
