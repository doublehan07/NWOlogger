"""Side-by-side view of a QSO and the earlier QSO it may duplicate, with actions to resolve it."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import i18n
from ..core import dupes, importer, modes, propagation, timeutil
from ..core.fields import BY_COL
from ..core.modes import Relation
from . import theme
from .widgets import Chip, fmt, mode_text

tr = i18n.tr

ROWS = ["call", "time_on", "time_off", "band", "freq", "freq_rx", "mode", "sat_name", "prop_mode", "rst_sent",
        "rst_rcvd", "tx_pwr", "station_callsign", "operator", "my_gridsquare", "gridsquare", "first_name",
        "last_name", "qth", "state", "comment", "contest_id", "lotw_qslsdate", "lotw_qslrdate", "eqsl_qslsdate",
        "eqsl_qslrdate", "qrzcom_qso_upload_date", "qrz_qslrdate", "qslsdate", "qslrdate"]
KEY_ROWS = {"call", "band", "mode", "sat_name", "prop_mode", "time_on"}
RELATION_TEXT = {
    Relation.SAME: "same mode and submode",
    Relation.COULD_BE: "one mode is a more general name for the other",
    Relation.SIBLING: "related modes in the same group",
}


def _val(q: dict, col: str) -> str:
    if col == "mode":
        return mode_text(q)
    if col == "prop_mode" and q.get("prop_mode"):
        return f"{q['prop_mode']} · {propagation.describe(q['prop_mode'])}"
    return fmt(col, q.get(col))


class DupeCompareDialog(QDialog):
    edit_requested = Signal(int)

    def __init__(self, db, prefs, *, queue: list[int] | None = None, draft: dict | None = None,
                 other_id: int | None = None, parent=None):
        super().__init__(parent)
        self.db, self.prefs = db, prefs
        self.queue = list(queue or [])
        self.pos = 0
        self.draft = draft
        self.draft_other = other_id
        self.setWindowTitle(tr("Compare possible duplicate"))
        self.resize(820, 720)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.chip = Chip("", "warn")
        self.reason = QLabel("")
        self.reason.setWordWrap(True)
        self.counter = QLabel("")
        self.counter.setProperty("muted", True)
        top.addWidget(self.chip)
        top.addWidget(self.reason, 1)
        top.addWidget(self.counter)
        lay.addLayout(top)
        self.table = QTableWidget(len(ROWS), 2)
        self.table.setVerticalHeaderLabels([tr(BY_COL[c].label) for c in ROWS])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(24)
        lay.addWidget(self.table, 1)
        legend = QLabel(tr("Highlighted rows differ. Bold rows decide whether it is a dupe: call, band, mode, "
                           "satellite and time."))
        legend.setObjectName("Hint")
        legend.setWordWrap(True)
        lay.addWidget(legend)
        bar = QHBoxLayout()
        self.prev_btn = QPushButton("◀ " + tr("Previous"))
        self.next_btn = QPushButton(tr("Next") + " ▶")
        self.prev_btn.clicked.connect(lambda: self._go(-1))
        self.next_btn.clicked.connect(lambda: self._go(1))
        self.ok_btn = QPushButton(tr("Not a dupe"))
        self.ok_btn.setIcon(theme.icon("check"))
        self.ok_btn.clicked.connect(self._not_dupe)
        self.merge_btn = QPushButton(tr("Merge into original"))
        self.merge_btn.setToolTip(tr("Copy fields the original is missing (and confirmations), then delete this QSO"))
        self.merge_btn.clicked.connect(self._merge)
        self.del_btn = QPushButton(tr("Delete this QSO"))
        self.del_btn.setProperty("danger", True)
        self.del_btn.clicked.connect(self._delete_suspect)
        self.del_orig_btn = QPushButton(tr("Delete original"))
        self.del_orig_btn.setProperty("danger", True)
        self.del_orig_btn.clicked.connect(self._delete_original)
        self.edit_btn = QPushButton(tr("Edit…"))
        self.edit_btn.clicked.connect(self._edit)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.accept)
        for b in (self.prev_btn, self.next_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        for b in (self.ok_btn, self.merge_btn, self.del_btn, self.del_orig_btn, self.edit_btn, close):
            bar.addWidget(b)
        lay.addLayout(bar)
        self._show()

    # ------------------------------------------------------------------ data
    def current(self) -> tuple[dict | None, dict | None]:
        """(suspect, original)."""
        if self.draft is not None:
            return self.draft, self.db.get(self.draft_other) if self.draft_other else None
        while self.queue and 0 <= self.pos < len(self.queue):
            q = self.db.get(self.queue[self.pos])
            if q and q.get("dupe_of"):
                return q, self.db.get(q["dupe_of"])
            if q and q.get("dupe_flag") in ("DUPE", "SUSPECT"):
                hit = dupes.check(self.db, q, block_s=self.prefs.get("block_seconds"),
                                  warn_s=self.prefs.get("warn_hours") * 3600, exclude_id=q["id"])
                if hit:
                    return q, hit.other
            self.queue.pop(self.pos)
            self.pos = min(self.pos, max(0, len(self.queue) - 1))
        return None, None

    def _show(self) -> None:
        q, o = self.current()
        draft = self.draft is not None
        for b in (self.ok_btn, self.merge_btn, self.del_btn, self.del_orig_btn, self.edit_btn):
            b.setVisible(not draft)
        self.prev_btn.setVisible(not draft and len(self.queue) > 1)
        self.next_btn.setVisible(not draft and len(self.queue) > 1)
        if not q or not o:
            self.chip.setText(tr("Done"))
            self.chip.set_kind("ok")
            self.reason.setText(tr("No more marked QSOs to review."))
            self.counter.setText("")
            self.table.setRowCount(0)
            for b in (self.ok_btn, self.merge_btn, self.del_btn, self.del_orig_btn, self.edit_btn, self.prev_btn,
                      self.next_btn):
                b.setEnabled(False)
            return
        self.table.setRowCount(len(ROWS))
        self.table.setHorizontalHeaderLabels([tr("Original (logged first)"),
                                              tr("Not logged yet") if draft else tr("Possible duplicate")])
        res = dupes.classify(q, o, block_s=self.prefs.get("block_seconds"), warn_s=self.prefs.get("warn_hours") * 3600)
        level = res[0] if res else (q.get("dupe_flag") or "SUSPECT")
        rel = res[1] if res else modes.relation((q.get("mode"), q.get("submode")), (o.get("mode"), o.get("submode")))
        secs = res[2] if res else timeutil.seconds_between(q.get("time_on") or "", o.get("time_on") or "")
        self.chip.setText(tr("DUPE") if level == "DUPE" else tr("Possible dupe"))
        self.chip.set_kind("bad" if level == "DUPE" else "warn")
        apart = f"{secs:.0f} s" if secs < 120 else f"{secs / 60:.0f} min" if secs < 7200 else f"{secs / 3600:.1f} h"
        what = tr("Logging is blocked.") if level == "DUPE" else tr("Warning only.")
        self.reason.setText(f"{tr('Same call and band')}, {tr(RELATION_TEXT.get(rel, 'related modes'))}, "
                            f"{tr('{0} apart').format(apart)}. {what}")
        self.counter.setText(f"{self.pos + 1} / {len(self.queue)}" if self.queue else "")
        t = theme.tokens()
        for i, col in enumerate(ROWS):
            a, b = _val(o, col), _val(q, col)
            for j, v in enumerate((a, b)):
                it = QTableWidgetItem(v)
                if col in KEY_ROWS:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                if a != b:
                    it.setBackground(QBrush(QColor(t.warn_soft)))
                self.table.setItem(i, j, it)
        for b in (self.ok_btn, self.merge_btn, self.del_btn, self.del_orig_btn, self.edit_btn):
            b.setEnabled(True)
        self.prev_btn.setEnabled(self.pos > 0)
        self.next_btn.setEnabled(self.pos < len(self.queue) - 1)

    def _go(self, step: int) -> None:
        self.pos = max(0, min(len(self.queue) - 1, self.pos + step))
        self._show()

    def _resolved(self) -> None:
        """The current item is done: drop it from the queue and show the next one."""
        if self.queue:
            self.queue.pop(self.pos)
            self.pos = min(self.pos, max(0, len(self.queue) - 1))
        self._show()

    # ------------------------------------------------------------------ actions
    def _not_dupe(self) -> None:
        q, _o = self.current()
        if q:
            self.db.update_many([q["id"]], {"dupe_flag": "OK", "dupe_of": None}, label=tr("Not a dupe"),
                                track_service=False)
            self._resolved()

    def _merge(self) -> None:
        q, o = self.current()
        if not q or not o:
            return
        ch = importer.merge_changes(o, q)
        with self.db.batch(tr("Merge into original")):
            if ch:
                self.db.update(o["id"], ch, track_service=False)
            self.db.delete([q["id"]])
        self._resolved()

    def _delete_suspect(self) -> None:
        q, _o = self.current()
        if q and QMessageBox.question(self, tr("Delete"), tr("Delete this QSO?")) == QMessageBox.StandardButton.Yes:
            self.db.delete([q["id"]], label=tr("Delete QSO"))
            self._resolved()

    def _delete_original(self) -> None:
        q, o = self.current()
        if o and QMessageBox.question(self, tr("Delete"), tr("Delete the original QSO? The other one is kept.")
                                      ) == QMessageBox.StandardButton.Yes:
            with self.db.batch(tr("Delete QSO")):
                self.db.delete([o["id"]])
                if q:
                    self.db.update(q["id"], {"dupe_flag": None, "dupe_of": None}, track_service=False,
                                   normalized=True)
            self._resolved()

    def _edit(self) -> None:
        q, _o = self.current()
        if q:
            self.edit_requested.emit(q["id"])
            self.accept()


def flagged_ids(db, ids: list[int] | None = None) -> list[int]:
    if ids:
        rows = db.get_many(ids)
        return [i for i in ids if rows.get(i, {}).get("dupe_flag") in ("DUPE", "SUSPECT")]
    return db.select_ids("dupe_flag IN ('DUPE', 'SUSPECT')", [], [("time_on", False)])


def show_review(parent, db, prefs, ids: list[int] | None = None, on_edit=None) -> None:
    queue = flagged_ids(db, ids)
    if not queue:
        QMessageBox.information(parent, tr("Compare possible duplicate"), tr("No marked QSOs to review."))
        return
    dlg = DupeCompareDialog(db, prefs, queue=queue, parent=parent)
    if on_edit:
        dlg.edit_requested.connect(on_edit)
    dlg.exec()

