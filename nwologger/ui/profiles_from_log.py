"""Preferences → Station profiles → Add from my log…: the station callsign + grid pairs your QSOs carry, with
their counts; a profile is created for each pair you tick, named as the MacLoggerDX import names them
(``BA1PK @ ON80eb``), with the callsign's zones and DXCC."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import i18n
from ..core import dxcc, grids
from ..core import qso as qsomod

tr = i18n.tr


def place_text(place: str) -> str:
    """A location key (``OM32,OM33``) as the Grid field shows it (``OM32, OM33``)."""
    return ", ".join(qsomod.split_grids(place))


def profile_for(call: str, place: str) -> dict:
    """A station profile for a callsign at a place: ``BI1NWO @ ON80ea``, operator = callsign, the grids in
    MY_GRIDSQUARE and MY_VUCC_GRIDS, and the callsign's CQ zone, ITU zone and DXCC."""
    where = place_text(place)
    data = {"name": f"{call} @ {where}" if where else call, "station_callsign": call, "operator": call,
            "station_type": "personal"}
    data.update(qsomod.grid_columns(place, "my_gridsquare"))
    r = dxcc.default().resolve(call)
    if r:
        data.update({k: int(v) for k, v in (("my_cq_zone", r.cqz), ("my_itu_zone", r.ituz), ("my_dxcc", r.dxcc)) if v})
    return data


def existing_pairs(db) -> set[tuple[str, str]]:
    """The (callsign, place) pairs that already have a station profile."""
    return {((p.get("station_callsign") or "").upper(), grids.location_key(p)) for p in db.profiles()}


class ProfilesFromLogDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.created: list[int] = []  # the ids of the profiles made, in order
        self.setWindowTitle(tr("Add station profiles from my log"))
        self.resize(620, 460)
        lay = QVBoxLayout(self)
        intro = QLabel(tr("Every station callsign and grid your QSOs carry, most QSOs first. The pairs without a "
                          "profile are ticked; a profile is created for each tick, named like BI1NWO @ ON80ea, with "
                          "the callsign's zones and DXCC. Importing never moves a QSO to a profile: a record keeps "
                          "the station callsign and grid it came with."))
        intro.setWordWrap(True)
        intro.setObjectName("Hint")
        lay.addWidget(intro)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([tr("Callsign · grid"), tr("QSOs"), ""])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        have = existing_pairs(db)
        self.rows = grids.my_station_places(db)
        self.table.setRowCount(len(self.rows))
        for i, (call, place, n) in enumerate(self.rows):
            has = (call, place) in have
            it = QTableWidgetItem(f"{call}  ·  {place_text(place) or '—'}")
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked if has else Qt.CheckState.Checked)
            it.setData(Qt.ItemDataRole.UserRole, (call, place))
            self.table.setItem(i, 0, it)
            count = QTableWidgetItem(f"{n:,}")
            count.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
            self.table.setItem(i, 1, count)
            self.table.setItem(i, 2, QTableWidgetItem(tr("has a profile") if has else ""))
        self.table.itemChanged.connect(lambda *_: self._update_button())
        lay.addWidget(self.table, 1)
        self.empty = QLabel(tr("No QSO in the log carries a station callsign yet."))
        self.empty.setProperty("muted", True)
        self.empty.setVisible(not self.rows)
        lay.addWidget(self.empty)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.create_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        self.create_btn.setProperty("primary", True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._update_button()

    def ticked(self) -> list[tuple[str, str]]:
        return [self.table.item(i, 0).data(Qt.ItemDataRole.UserRole) for i in range(self.table.rowCount())
                if self.table.item(i, 0).checkState() == Qt.CheckState.Checked]

    def _update_button(self) -> None:
        n = len(self.ticked())
        self.create_btn.setText(tr("Create") + f" {n} " + (tr("profile") if n == 1 else tr("profiles")))
        self.create_btn.setEnabled(n > 0)

    def accept(self) -> None:  # noqa: N802 - Qt API
        for call, place in self.ticked():
            self.created.append(self.db.save_profile(profile_for(call, place)))
        super().accept()
