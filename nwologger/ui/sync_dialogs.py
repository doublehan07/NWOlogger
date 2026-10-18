"""Windows of the sync: the near-duplicates a download holds back, and the date a first LoTW download starts from."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
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

from .. import i18n
from ..core import importer
from .widgets import fmt, mode_text

tr = i18n.tr

COMPARED = ("band", "mode", "sat_name")  # what made the record a near-duplicate instead of a match


def describe(q: dict) -> str:
    return " · ".join(x for x in (q.get("call") or "", fmt("time_on", q.get("time_on")), fmt("band", q.get("band")),
                                  mode_text(q), q.get("sat_name") or "") if x)


class NearDuplicatesDialog(QDialog):
    """Records a download could not match that lie within a minute of a QSO of the same call: the same contact
    logged with another band, mode or satellite, nearly always. They wait here; only the ticked ones are added.
    *Edit my QSO* opens the QSO to correct it, and *Check again* applies every record that then matches."""

    add_requested = Signal(list)  # the records to add as QSOs
    edit_requested = Signal(int)  # the id of the QSO to open
    message = Signal(str)  # for the status bar

    def __init__(self, db, service: str, pairs: list[tuple[dict, dict]], parent=None, added: int = 0):
        super().__init__(parent)
        self.db = db
        self.pairs = list(pairs)
        self.setWindowTitle(tr("Possible duplicates") + f" — {service}")
        self.resize(900, 420)
        lay = QVBoxLayout(self)
        lead = (f"{added} " + tr("records were added to your log.") + " " if added else "")
        lab = QLabel(lead + tr("These are within a minute of a QSO you already have but differ in a bold field. Tick "
                               "the ones to add anyway, or fix your QSO and the record is applied to it."))
        lab.setWordWrap(True)
        lay.addWidget(lab)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["", tr("Record from") + f" {service}", tr("Your QSO")])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self.table, 1)
        self._fill()
        bar = QHBoxLayout()
        self.add_btn = QPushButton(tr("Add ticked"))
        self.add_btn.setProperty("primary", True)
        self.add_btn.clicked.connect(self._add)
        self.edit_btn = QPushButton(tr("Edit my QSO…"))
        self.edit_btn.clicked.connect(self._edit)
        self.check_btn = QPushButton(tr("Check again"))
        self.check_btn.setToolTip(tr("Apply every record that now matches a QSO (after you edited it)"))
        self.check_btn.clicked.connect(self.check_again)
        bb = QDialogButtonBox()
        skip = bb.addButton(tr("Skip all"), QDialogButtonBox.ButtonRole.RejectRole)
        skip.setToolTip(tr("Nothing is lost by skipping: the next download shows them again."))
        bb.rejected.connect(self.reject)
        for w in (self.add_btn, self.edit_btn, self.check_btn):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(bb)
        lay.addLayout(bar)

    def _fill(self) -> None:
        self.table.setRowCount(len(self.pairs))
        for i, (rec, mine) in enumerate(self.pairs):
            box = QWidget()
            h = QHBoxLayout(box)
            h.setContentsMargins(6, 0, 6, 0)
            h.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb = QCheckBox()
            cb.setObjectName("add")
            h.addWidget(cb)
            self.table.setCellWidget(i, 0, box)
            differs = [c for c in COMPARED if (rec.get(c) or "").upper() != (mine.get(c) or "").upper()]
            for col, q in ((1, rec), (2, mine)):
                it = QTableWidgetItem(describe(q))
                it.setToolTip(tr("Differs in:") + " " + ", ".join(tr({"band": "Band", "mode": "Mode", "sat_name": "Sat"}[c])
                                                                     for c in differs) if differs else "")
                font = it.font()
                font.setBold(bool(differs))
                it.setFont(font)
                self.table.setItem(i, col, it)
        self.table.resizeRowsToContents()

    def ticked(self) -> list[int]:
        out = []
        for i in range(self.table.rowCount()):
            box = self.table.cellWidget(i, 0)
            cb = box.findChild(QCheckBox, "add") if box else None
            if cb is not None and cb.isChecked():
                out.append(i)
        return out

    def _add(self) -> None:
        rows = self.ticked()
        if not rows:
            QMessageBox.information(self, tr("Possible duplicates"), tr("Tick the records to add first."))
            return
        recs = [self.pairs[i][0] for i in rows]
        self.pairs = [p for i, p in enumerate(self.pairs) if i not in set(rows)]
        self.add_requested.emit(recs)
        self._fill()
        if not self.pairs:
            self.accept()

    def _edit(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, tr("Possible duplicates"), tr("Select a row first."))
            return
        self.edit_requested.emit(self.pairs[row][1]["id"])

    def check_again(self) -> int:
        """Apply every record that now matches a QSO of yours; returns how many were applied."""
        applied, keep = 0, []
        for rec, mine in self.pairs:
            if importer.find_match(self.db, rec) is not None:
                importer.run(self.db, [rec], merge=True, label=tr("Apply a record to its QSO"))
                applied += 1
            else:
                keep.append((rec, mine))
        self.pairs = keep
        self._fill()
        if applied:
            self.message.emit(f"{applied} " + tr("records applied to your QSOs"))
        if not self.pairs:
            self.accept()
        return applied


class FromDateDialog(QDialog):
    """QSL → LoTW → Download from a date…: the first day a first LoTW download asks for."""

    def __init__(self, parent=None, default: date | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("Download from a date") + " — LoTW")
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("QSOs made on or after")))
        self.edit = QDateEdit()
        self.edit.setCalendarPopup(True)
        self.edit.setDisplayFormat("yyyy-MM-dd")
        today = date.today()
        start = default or date(today.year - 1, 1, 1)
        self.edit.setDate(QDate(start.year, start.month, start.day))
        self.edit.setMaximumDate(QDate(today.year, today.month, today.day))
        row.addWidget(self.edit)
        row.addStretch(1)
        lay.addLayout(row)
        note = QLabel(tr("LoTW is asked only for QSOs made from this day on, in yearly parts from this year back to "
                         "it; earlier QSOs are not asked for (run this again with an earlier date to add them). "
                         "Where downloads resume is set at the end, as a full download does."))
        note.setWordWrap(True)
        note.setObjectName("Hint")
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Download"))
        bb.button(QDialogButtonBox.StandardButton.Ok).setProperty("primary", True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def date(self) -> date:
        d = self.edit.date()
        return date(d.year(), d.month(), d.day())
