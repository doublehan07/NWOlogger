"""Compare a group of possible duplicates side by side: one column per QSO, editable, with actions to resolve.

A group is every QSO linked by possible-dupe relations (same call and band, related mode, within the warning
window), so three or more QSOs can be reviewed together. Click a column header to select a QSO.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
from ..core import dupes, propagation, timeutil
from ..core import merge as mergemod
from ..core import qso as qsomod
from ..core.fields import BY_COL
from ..core.modes import Relation
from . import theme
from .widgets import Chip, fmt, mode_text

tr = i18n.tr
log = logging.getLogger("nwologger.dupes")

ROWS = ["call", "time_on", "time_off", "band", "freq", "freq_rx", "mode", "prop_mode", "sat_name", "rst_sent",
        "rst_rcvd", "tx_pwr", "station_callsign", "operator", "my_gridsquare", "gridsquare", "first_name",
        "last_name", "qth", "state", "comment", "notes", "contest_id", "dupe_flag", "lotw_qslsdate", "lotw_qslrdate",
        "eqsl_qslsdate", "eqsl_qslrdate", "qrzcom_qso_upload_date", "qrz_qslrdate", "qslsdate", "qslrdate"]
KEY_ROWS = {"call", "band", "mode", "sat_name", "prop_mode", "time_on"}
NOT_EDITABLE = {"band", "dupe_flag"}
RELATION_TEXT = {
    Relation.SAME: "same mode and submode",
    Relation.COULD_BE: "one mode is a more general name for the other",
    Relation.SIBLING: "related modes in the same group",
}


def _val(q: dict, col: str) -> str:
    if col == "mode":
        return mode_text(q)
    if col == "prop_mode" and q.get("prop_mode"):
        return f"{q['prop_mode']} · {tr(propagation.describe(q['prop_mode']))}"
    if col in qsomod.GRID_PAIRS:
        return qsomod.grids_text(q, col)
    return fmt(col, q.get(col))


def _parse(col: str, text: str):
    """Cell text back to a column value."""
    t = text.strip()
    if not t:
        return None
    f = BY_COL[col]
    if f.kind == "time":
        dt = timeutil.parse_iso(t.replace("T", " ").replace("Z", ""))
        return timeutil.to_iso(dt) if dt else None
    if col == "prop_mode":
        return t.split("·")[0].strip().upper()
    return t


def _apart(secs: float) -> str:
    return f"{secs:.0f} s" if secs < 120 else f"{secs / 60:.0f} min" if secs < 7200 else f"{secs / 3600:.1f} h"


class DupeCompareDialog(QDialog):
    edit_requested = Signal(int)

    def __init__(self, db, prefs, *, groups: list[list[int]] | None = None, draft: dict | None = None,
                 fixed: bool = False, start_merge: bool = False, parent=None):
        super().__init__(parent)
        self.db, self.prefs = db, prefs
        self.fixed = fixed  # the groups are QSOs you selected (Merge selected…), not dupe groups to recompute
        self.merging = False
        self.keep = 0  # merge mode: the column of the QSO that stays
        self.picks: dict[str, int | str] = {}  # merge mode: column name → the QSO whose value to use, or "both"
        self.groups = [list(g) for g in (groups or [])]
        self.pos = 0
        self.draft = draft
        self.qsos: list[dict] = []
        self.edits: dict[tuple[int, str], object] = {}
        self.sel: int | None = None
        self.setWindowTitle(tr("Compare possible duplicates"))
        self.resize(980, 760)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.chip = Chip("", "warn")
        self.counter = QLabel("")
        self.counter.setProperty("muted", True)
        top.addWidget(self.chip)
        top.addStretch(1)
        top.addWidget(self.counter)
        lay.addLayout(top)
        self.reason = QLabel("")
        self.reason.setWordWrap(True)
        self.reason.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.reason)
        self.table = QTableWidget(0, 0)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                                   QAbstractItemView.EditTrigger.EditKeyPressed |
                                   QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setMinimumSectionSize(170)
        self.table.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.itemChanged.connect(self._edited)
        self.table.cellClicked.connect(self._cell_clicked)
        lay.addWidget(self.table, 1)
        self.merge_note = QLabel()
        self.merge_note.setWordWrap(True)
        self.merge_note.setTextFormat(Qt.TextFormat.RichText)
        self.merge_note.setVisible(False)
        lay.addWidget(self.merge_note)
        self.legend = QLabel()
        self.legend.setObjectName("Hint")
        self.legend.setWordWrap(True)
        lay.addWidget(self.legend)
        bar = QHBoxLayout()
        self.prev_btn = QPushButton("◀ " + tr("Previous"))
        self.next_btn = QPushButton(tr("Next") + " ▶")
        self.prev_btn.clicked.connect(lambda: self._go(-1))
        self.next_btn.clicked.connect(lambda: self._go(1))
        self.save_btn = QPushButton(tr("Save edits"))
        self.save_btn.clicked.connect(self._save)
        self.ok_btn = QPushButton(tr("Not dupes"))
        self.ok_btn.setIcon(theme.icon("check"))
        self.ok_btn.setToolTip(tr("They are different QSOs: clear the marks"))
        self.ok_btn.clicked.connect(self._not_dupes)
        self.ok_all_btn = QPushButton(tr("Not dupes: all…"))
        self.ok_all_btn.setToolTip(tr("Mark every marked QSO in the log as not a dupe (asks twice)"))
        self.ok_all_btn.clicked.connect(self._ignore_all)
        self.keep_btn = QPushButton(tr("Merge…"))
        self.keep_btn.setToolTip(tr("Pick, row by row, the value the merged QSO keeps; the others are then deleted"))
        self.keep_btn.clicked.connect(self._start_merge)
        self.back_btn = QPushButton(tr("Back to compare"))
        self.back_btn.clicked.connect(self._back)
        self.merge_btn = QPushButton()
        self.merge_btn.setProperty("primary", True)
        self.merge_btn.clicked.connect(lambda: self._do_merge())
        self.del_btn = QPushButton(tr("Delete selected"))
        self.del_btn.setProperty("danger", True)
        self.del_btn.clicked.connect(self._delete_selected)
        self.open_btn = QPushButton(tr("Open in entry form"))
        self.open_btn.clicked.connect(self._open)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self._close)
        for b in (self.prev_btn, self.next_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        for b in (self.save_btn, self.ok_btn, self.ok_all_btn, self.keep_btn, self.del_btn, self.open_btn,
                  self.back_btn, self.merge_btn, close):
            bar.addWidget(b)
        lay.addLayout(bar)
        self._load()
        if start_merge:
            self._start_merge()

    # ------------------------------------------------------------------ loading
    def _windows(self) -> dict:
        return {"block_s": self.prefs.get("block_seconds"), "warn_s": self.prefs.warn_seconds()}

    def _load(self) -> None:
        self.edits.clear()
        self.sel = None
        if self.draft is not None:
            hs = dupes.hits(self.db, self.draft, exclude_id=self.draft.get("id"), **self._windows())
            others = sorted({h.other_id for h in hs})
            self.qsos = [q for q in (self.db.get(i) for i in others) if q]
            self.qsos.sort(key=lambda q: (q.get("time_on") or "", q["id"]))
            self.qsos.append(dict(self.draft, id=self.draft.get("id") or -1, _draft=True))
        else:
            self.qsos = []
            while self.groups and 0 <= self.pos < len(self.groups):
                if self.fixed:
                    g = sorted((q for q in (self.db.get(i) for i in self.groups[self.pos]) if q),
                               key=lambda q: (q.get("time_on") or "", q["id"]))
                else:
                    first = next((i for i in self.groups[self.pos] if self.db.get(i)), None)
                    g = dupes.group(self.db, first, **self._windows()) if first else []
                if len(g) >= 2:
                    self.groups[self.pos] = [q["id"] for q in g]
                    self.qsos = g
                    break
                self.groups.pop(self.pos)
                self.pos = min(self.pos, max(0, len(self.groups) - 1))
        self._render()

    def _source(self, col: str) -> int | str:
        """Merge mode: which QSO the result takes this field from (a column), or "both"."""
        pick = self.picks.get(col)
        if pick is not None:
            return pick
        if self.qsos[self.keep].get(col) not in (None, ""):
            return self.keep
        return next((i for i, q in enumerate(self.qsos) if q.get(col) not in (None, "")), self.keep)

    def _render(self) -> None:
        self.table.blockSignals(True)
        n = len(self.qsos)
        draft = self.draft is not None
        merging = self.merging and n >= 2
        for b, show in ((self.prev_btn, not draft and not merging and len(self.groups) > 1),
                        (self.next_btn, not draft and not merging and len(self.groups) > 1),
                        (self.save_btn, not merging), (self.ok_btn, not draft and not merging and not self.fixed),
                        (self.ok_all_btn, not draft and not merging and not self.fixed),
                        (self.keep_btn, not draft and not merging), (self.del_btn, not merging),
                        (self.open_btn, not merging), (self.back_btn, merging), (self.merge_btn, merging)):
            b.setVisible(show)
        self.merge_note.setVisible(merging)
        self.legend.setText(tr("Click a value to use it in the merged QSO; click a column header to choose the QSO "
                               "that stays. Confirmations and upload states are combined.") if merging else
                            tr("Click a column header to select that QSO. Double-click a cell to edit it, then "
                               "Save edits. Highlighted rows differ; bold rows decide whether it is a dupe."))
        if n < 2:
            self.merging = False
            self.chip.setText(tr("Done"))
            self.chip.set_kind("ok")
            self.reason.setText(tr("No more marked QSOs to review."))
            self.counter.setText("")
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            for b in (self.ok_btn, self.ok_all_btn, self.keep_btn, self.del_btn, self.open_btn, self.save_btn,
                      self.prev_btn, self.next_btn):
                b.setEnabled(False)
            self.table.blockSignals(False)
            return
        self.table.setRowCount(len(ROWS))
        self.table.setColumnCount(n + (1 if merging else 0))
        self.table.setVerticalHeaderLabels([tr(BY_COL[c].label) for c in ROWS])
        heads = []
        for i, q in enumerate(self.qsos):
            if q.get("_draft"):
                heads.append(tr("Not logged yet"))
                continue
            flag = {"DUPE": tr("Dupe"), "SUSPECT": tr("Suspect"), "OK": tr("not a dupe")}.get(q.get("dupe_flag"), "")
            keep = merging and i == self.keep
            heads.append(f"#{i + 1}  {fmt('time_on', q.get('time_on'))[11:]}" +
                         (f"  · {tr('stays')}" if keep else f"  · {tr('deleted')}" if merging else
                          f"  · {flag}" if flag else ""))
        self.table.setHorizontalHeaderLabels(heads + ([tr("Result")] if merging else []))
        t = theme.tokens()
        result = mergemod.merged(self.qsos, self.keep, self.picks) if merging else {}
        differing = taken = 0
        for r, col in enumerate(ROWS):
            vals = [_val(q, col) for q in self.qsos]
            differ = len(set(vals)) > 1
            locked = col in mergemod.STATE_COLS or col in NOT_EDITABLE
            src = self._source(col) if merging and differ and not locked else None
            if src is not None:
                differing += 1
                taken += src != self.keep
            for c, (q, v) in enumerate(zip(self.qsos, vals, strict=True)):
                key = (q["id"], col)
                shown = fmt(col, self.edits[key]) if key in self.edits else v
                it = QTableWidgetItem(shown)
                flags = it.flags()
                if merging or q.get("_draft") or col in NOT_EDITABLE:
                    flags &= ~Qt.ItemFlag.ItemIsEditable
                it.setFlags(flags)
                f = it.font()
                f.setBold(col in KEY_ROWS or (src is not None and (src == c or (src == mergemod.BOTH and v))))
                it.setFont(f)
                if merging and locked:
                    it.setBackground(QBrush(QColor(t.surface_alt)))
                elif src is not None and (src == c or (src == mergemod.BOTH and v)):
                    it.setBackground(QBrush(QColor(t.accent_soft)))
                    it.setToolTip(tr("Used in the merged QSO"))
                elif key in self.edits:
                    it.setBackground(QBrush(QColor(t.accent_soft)))
                elif differ:
                    it.setBackground(QBrush(QColor(t.warn_soft)))
                    if merging and not locked:
                        it.setToolTip(tr("Click to use this value"))
                self.table.setItem(r, c, it)
            if merging:
                it = QTableWidgetItem(_val(result, col))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                f = it.font()
                f.setBold(True)
                it.setFont(f)
                it.setBackground(QBrush(QColor(t.surface_alt if locked else t.tint)))
                if locked:
                    it.setToolTip(tr("Combined: the best state of all of them (uploaded, confirmed), with its date"))
                elif col in mergemod.TEXT_BOTH and differ:
                    it.setToolTip(tr("Click to keep both texts") if src != mergemod.BOTH else
                                  tr("Both texts are kept; click to keep one"))
                self.table.setItem(r, n, it)
        self.table.blockSignals(False)
        # Why they were flagged: each QSO against the earlier ones.
        lines = []
        worst = "SUSPECT"
        for i in range(1, n):
            best = None
            for j in range(i):
                res = dupes.classify(self.qsos[i], self.qsos[j], **self._windows())
                if res and (best is None or (res[0] == "DUPE" and best[1][0] != "DUPE") or
                            (res[0] == best[1][0] and res[2] < best[1][2])):
                    best = (j, res)
            if best:
                j, (level, rel, secs) = best
                worst = "DUPE" if level == "DUPE" else worst
                what = tr("dupe of") if level == "DUPE" else tr("possible dupe of")
                name = tr("Not logged yet") if self.qsos[i].get("_draft") else f"#{i + 1}"
                lines.append(f"<b>{name}</b> {what} <b>#{j + 1}</b>: {tr(RELATION_TEXT.get(rel, 'related modes'))}, "
                             f"{tr('{0} apart').format(_apart(secs))}")
        if self.fixed:
            self.chip.setText(tr("Merge selected"))
            self.chip.set_kind("info")
        else:
            self.chip.setText(tr("DUPE") if worst == "DUPE" else tr("Possible dupe"))
            self.chip.set_kind("bad" if worst == "DUPE" else "warn")
        head = f"{n} " + tr("QSOs with") + f" <b>{self.qsos[0].get('call')}</b> " + tr("on") + \
               f" {fmt('band', self.qsos[0].get('band'))}"
        self.reason.setText(head + (":<br>" + "<br>".join(lines) if lines else ""))
        self.counter.setText(f"{tr('Group')} {self.pos + 1} / {len(self.groups)}" if len(self.groups) > 1 else "")
        if merging:
            self._merge_summary(result, differing, taken)
            self.merge_btn.setText(tr("Merge into") + f" #{self.keep + 1}, " + tr("delete the others"))
            for c in range(self.table.columnCount()):
                it = self.table.horizontalHeaderItem(c)
                if it:
                    f = it.font()
                    f.setBold(c in (self.keep, n))
                    f.setUnderline(c == self.keep)
                    it.setFont(f)
            return
        self.ok_btn.setEnabled(not draft)
        self.ok_all_btn.setEnabled(not draft)
        self.keep_btn.setEnabled(False)
        self.del_btn.setEnabled(False)
        self.open_btn.setEnabled(False)
        self.save_btn.setEnabled(bool(self.edits))
        self.save_btn.setProperty("primary", bool(self.edits))
        theme.repolish(self.save_btn)
        self.prev_btn.setEnabled(self.pos > 0)
        self.next_btn.setEnabled(self.pos < len(self.groups) - 1)
        if self.sel is not None and self.sel < n:
            self._select(self.sel)
        else:
            self._select(0)

    def _merge_summary(self, result: dict, differing: int, taken: int) -> None:
        t = theme.tokens()
        parts = [f"{differing} " + tr("rows differ") + f"; {taken} " + tr("taken from the QSOs that are deleted") + ". " +
                 tr("Confirmations and upload states are combined.")]
        for i, q in enumerate(self.qsos):
            if i != self.keep and mergemod.remote_copies(q):
                parts.append(f"#{i + 1} " + tr("is also stored at") + " " + ", ".join(mergemod.remote_copies(q)) +
                             ": " + tr("delete it there yourself after the merge."))
        probs = mergemod.problems(result)
        if probs:
            parts.append(f"<span style='color:{t.bad}'><b>" + " ".join(tr(p) for p in probs) + "</b></span>")
        self.merge_note.setText("<br>".join(parts))
        self.merge_btn.setEnabled(not probs)

    # ------------------------------------------------------------------ selection / editing
    def _header_clicked(self, col: int) -> None:
        if self.merging:
            if 0 <= col < len(self.qsos):
                self.keep = col
                self._render()
            return
        self._select(col)

    def _cell_clicked(self, row: int, col: int) -> None:
        """Merge mode: the clicked value goes into the merged QSO (the Result cell of Comment / Notes: keep both)."""
        if not self.merging or not 0 <= row < len(ROWS):
            return
        name = ROWS[row]
        if name in mergemod.STATE_COLS or name in NOT_EDITABLE:
            return
        if len({_val(q, name) for q in self.qsos}) < 2:
            return
        if col == len(self.qsos):
            if name in mergemod.TEXT_BOTH:
                if self.picks.get(name) == mergemod.BOTH:
                    self.picks.pop(name)
                else:
                    self.picks[name] = mergemod.BOTH
                self._render()
            return
        if 0 <= col < len(self.qsos):
            self.picks[name] = col
            self._render()

    def _select(self, col: int) -> None:
        if col < 0 or col >= len(self.qsos):
            return
        self.sel = col
        q = self.qsos[col]
        is_real = not q.get("_draft")
        self.del_btn.setEnabled(is_real)
        self.open_btn.setEnabled(is_real)
        self.keep_btn.setEnabled(is_real and self.draft is None)
        for c in range(self.table.columnCount()):
            it = self.table.horizontalHeaderItem(c)
            if it:
                f = it.font()
                f.setUnderline(c == col)
                f.setBold(c == col)
                it.setFont(f)

    def _edited(self, it: QTableWidgetItem) -> None:
        q = self.qsos[it.column()]
        col = ROWS[it.row()]
        if it.text().strip() == _val(q, col):
            self.edits.pop((q["id"], col), None)
        else:
            self.edits[(q["id"], col)] = _parse(col, it.text())
        sel = self.sel
        self._render()
        if sel is not None:
            self._select(sel)

    def _save(self) -> bool:
        if not self.edits:
            return True
        by_q: dict[int, dict] = {}
        for (qid, col), v in self.edits.items():
            if col in qsomod.GRID_PAIRS:  # "OM88, OM89" edits the grid and the VUCC grids together
                by_q.setdefault(qid, {}).update(qsomod.grid_columns(v, col))
            else:
                by_q.setdefault(qid, {})[col] = v
        for q in self.qsos:
            ch = by_q.get(q["id"])
            if not ch:
                continue
            on = ch.get("time_on", q.get("time_on")) or ""
            off = ch.get("time_off", q.get("time_off")) or ""
            if off and on and off < on:
                QMessageBox.warning(self, tr("Check the times"), tr("Time Off is earlier than Time On") + ".")
                return False
        sel = self.sel
        with self.db.batch(tr("Edit in compare window")):
            for qid, ch in by_q.items():
                self.db.update(qid, ch)
            dupes.mark(self.db, [q["id"] for q in self.qsos if not q.get("_draft")], **self._windows())
        self.edits.clear()
        self._load()
        if sel is not None:
            self._select(min(sel, len(self.qsos) - 1))
        return True

    def _unsaved_ok(self) -> bool:
        if not self.edits:
            return True
        r = QMessageBox.question(self, tr("Unsaved edits"), tr("Save the edits first?"),
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                 QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Save:
            return self._save()
        if r == QMessageBox.StandardButton.Discard:
            self.edits.clear()
            return True
        return False

    def _go(self, step: int) -> None:
        if not self._unsaved_ok():
            return
        self.pos = max(0, min(len(self.groups) - 1, self.pos + step))
        self._load()

    def _close(self) -> None:
        if self._unsaved_ok():
            self.accept()

    # ------------------------------------------------------------------ actions
    def _real(self) -> list[dict]:
        return [q for q in self.qsos if not q.get("_draft")]

    def _not_dupes(self) -> None:
        if not self._unsaved_ok():
            return
        ids = [q["id"] for q in self._real() if q.get("dupe_flag") in ("DUPE", "SUSPECT")]
        if ids:
            self.db.update_many(ids, {"dupe_flag": "OK", "dupe_of": None}, label=tr("Not a dupe"),
                                track_service=False)
        self._next_group()

    # ------------------------------------------------------------------ merge
    def _start_merge(self) -> None:
        """Merge mode: a Result column, and a click on a value uses it. The QSO selected (or the first) stays."""
        if len(self._real()) < 2 or self.draft is not None or not self._unsaved_ok():
            return
        self.merging = True
        self.keep = self.sel if self.sel is not None and self.sel < len(self.qsos) else 0
        self.picks = {}
        self._render()

    def _back(self) -> None:
        self.merging = False
        self.picks = {}
        self._render()

    def keep_and_merge(self, col: int, *, confirm: bool = True) -> None:
        """Merge with the defaults: QSO ``col`` stays, the others fill its empty fields and are deleted."""
        self.keep, self.picks = col, {}
        self._do_merge(confirm=confirm)

    def _do_merge(self, *, confirm: bool = True) -> bool:
        keep = self.qsos[self.keep]
        others = [q for q in self._real() if q["id"] != keep["id"]]
        result = mergemod.merged(self.qsos, self.keep, self.picks)
        probs = mergemod.problems(result)
        if probs:
            QMessageBox.warning(self, tr("Merge"), "\n".join(tr(p) for p in probs))
            return False
        remote = [f"#{self.qsos.index(o) + 1}: " + ", ".join(mergemod.remote_copies(o)) for o in others
                  if mergemod.remote_copies(o)]
        text = (tr("Merge into") + f" #{self.keep + 1} " + tr("and delete the other") + f" {len(others)} " +
                tr("QSOs?") + " " + tr("This can't be undone; a backup of the log is saved first."))
        if remote:
            text += "\n\n" + tr("Also stored at a service, so delete it there yourself:") + "\n" + "\n".join(remote)
        if confirm and QMessageBox.question(self, tr("Merge"), text) != QMessageBox.StandardButton.Yes:
            return False
        self.db.backup("merge")
        ch = mergemod.changes(self.qsos, self.keep, self.picks)
        with self.db.batch(tr("Merge duplicates")):
            if ch:
                self.db.update(keep["id"], ch)  # an edit like any other: uploaded copies are marked for re-upload
            self.db.delete([o["id"] for o in others], backup=False)
            self.db.update(keep["id"], {"dupe_flag": None, "dupe_of": None}, track_service=False, normalized=True)
        log.info("Merged %d QSOs into one (%d fields picked, %d changed)", len(others) + 1, len(self.picks), len(ch))
        self.merging = False
        self.picks = {}
        self._next_group()
        return True

    def _ignore_all(self) -> None:
        if self._unsaved_ok() and ignore_all(self, self.db):
            self.groups = []
            self._load()

    def _delete_selected(self) -> None:
        if self.sel is None:
            return
        q = self.qsos[self.sel]
        if q.get("_draft") or not self._unsaved_ok():
            return
        if QMessageBox.question(self, tr("Delete"), tr("Delete") + f" #{self.sel + 1}?") \
                != QMessageBox.StandardButton.Yes:
            return
        rest = [x["id"] for x in self._real() if x["id"] != q["id"]]
        self.db.backup("delete")
        with self.db.batch(tr("Delete QSO")):
            self.db.delete([q["id"]], backup=False)
            dupes.mark(self.db, rest, **self._windows())
        self._load()

    def _open(self) -> None:
        if self.sel is None or not self._unsaved_ok():
            return
        q = self.qsos[self.sel]
        if not q.get("_draft"):
            self.edit_requested.emit(q["id"])
            self.accept()

    def _next_group(self) -> None:
        if self.draft is not None:
            self._load()
            return
        if self.groups:
            self.groups.pop(self.pos)
            self.pos = min(self.pos, max(0, len(self.groups) - 1))
        self._load()


def ignore_all(parent, db) -> int:
    """Mark every DUPE / SUSPECT QSO in the log as not a dupe, after a warning and a second confirmation."""
    rows = db.get_many(flagged_ids(db))
    if not rows:
        QMessageBox.information(parent, tr("Ignore all marked dupes"), tr("No marked QSOs to review."))
        return 0
    strict = sum(1 for q in rows.values() if q.get("dupe_flag") == "DUPE")
    Btn = QMessageBox.StandardButton  # noqa: N806
    if QMessageBox.warning(
            parent, tr("Ignore all marked dupes"),
            f"{strict} " + tr("dupes") + f", {len(rows) - strict} " + tr("suspicious dupes") + ": " +
            tr("mark them all as not dupes? They are no longer shown as dupes, and later scans leave them alone. "
               "Check a few in the compare window first."), Btn.Yes | Btn.Cancel, Btn.Cancel) != Btn.Yes:
        return 0
    if QMessageBox.question(
            parent, tr("Ignore all marked dupes"),
            tr("Are you sure?") + f" {len(rows)} " + tr("QSOs") + ". " +
            tr("This can't be undone; a backup of the log is saved first."), Btn.Yes | Btn.No, Btn.No) != Btn.Yes:
        return 0
    db.backup("dupes")
    n = db.update_many(list(rows), {"dupe_flag": "OK", "dupe_of": None}, label="Ignore all marked dupes",
                       track_service=False)
    log.info("Ignored all marked dupes: %d QSOs", n)
    return n


def flagged_ids(db, ids: list[int] | None = None) -> list[int]:
    if ids:
        rows = db.get_many(ids)
        return [i for i in ids if rows.get(i, {}).get("dupe_flag") in ("DUPE", "SUSPECT")]
    return db.select_ids("dupe_flag IN ('DUPE', 'SUSPECT')", [], [("time_on", False)])


def window_text(prefs) -> str:
    """The dupe warning window as words: ``1 min``, ``90 s``."""
    secs = prefs.warn_seconds()
    return f"{secs / 60:g} min" if secs >= 60 else f"{secs:g} s"


def cleared_text(res, prefs) -> str:
    """What a rescan cleared, for the review and the status bar; "" when nothing was."""
    if not res.cleared:
        return ""
    head = (tr("mark made under an earlier rule was cleared: those QSOs are more than") if res.cleared == 1
            else tr("marks made under an earlier rule were cleared: those QSOs are more than"))
    return f"{res.cleared} {head} {window_text(prefs)} " + tr("apart.")


def show_review(parent, db, prefs, ids: list[int] | None = None, on_edit=None) -> None:
    """Review marked QSOs (all, or the given ones), one dupe group at a time. The marks are brought up to date
    with the current windows first: a mark made under an earlier, wider window (24 hours before 0.1.9) would
    otherwise stay in the log with no pair to show for it. The review says how many it cleared."""
    w = {"block_s": prefs.get("block_seconds"), "warn_s": prefs.warn_seconds()}
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        res = dupes.rescan(db, **w)
    finally:
        QApplication.restoreOverrideCursor()
    cleared = cleared_text(res, prefs)
    start = flagged_ids(db, ids) or (ids or [])
    groups = dupes.groups_for(db, start, **w)
    if not groups:
        QMessageBox.information(parent, tr("Compare possible duplicates"),
                                (cleared + "\n\n" if cleared else "") + tr("No marked QSOs to review."))
        return
    if cleared and hasattr(parent, "flash"):
        parent.flash(cleared)
    dlg = DupeCompareDialog(db, prefs, groups=groups, parent=parent)
    if on_edit:
        dlg.edit_requested.connect(on_edit)
    dlg.exec()
