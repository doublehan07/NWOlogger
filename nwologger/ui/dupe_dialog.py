"""Compare a group of possible duplicates side by side: one column per QSO, editable, with actions to resolve.

A group is every QSO linked by possible-dupe relations (same call and band, related mode, within the warning
window), so three or more QSOs can be reviewed together. Click a column header to select a QSO.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
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
from ..core import dupes, importer, propagation, timeutil
from ..core.fields import BY_COL
from ..core.modes import Relation
from . import theme
from .widgets import Chip, fmt, mode_text

tr = i18n.tr

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
                 parent=None):
        super().__init__(parent)
        self.db, self.prefs = db, prefs
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
        self.table.horizontalHeader().sectionClicked.connect(self._select)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.itemChanged.connect(self._edited)
        lay.addWidget(self.table, 1)
        legend = QLabel(tr("Click a column header to select that QSO. Double-click a cell to edit it, then "
                           "Save edits. Highlighted rows differ; bold rows decide whether it is a dupe."))
        legend.setObjectName("Hint")
        legend.setWordWrap(True)
        lay.addWidget(legend)
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
        self.keep_btn = QPushButton(tr("Keep selected, merge others"))
        self.keep_btn.setToolTip(tr("Copy fields and confirmations the selected QSO is missing from the others, "
                                    "then delete the others"))
        self.keep_btn.clicked.connect(self._keep_merge)
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
        for b in (self.save_btn, self.ok_btn, self.keep_btn, self.del_btn, self.open_btn, close):
            bar.addWidget(b)
        lay.addLayout(bar)
        self._load()

    # ------------------------------------------------------------------ loading
    def _windows(self) -> dict:
        return {"block_s": self.prefs.get("block_seconds"), "warn_s": self.prefs.get("warn_hours") * 3600}

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
                first = next((i for i in self.groups[self.pos] if self.db.get(i)), None)
                g = dupes.group(self.db, first, **self._windows()) if first else []
                if len(g) >= 2:
                    self.groups[self.pos] = [q["id"] for q in g]
                    self.qsos = g
                    break
                self.groups.pop(self.pos)
                self.pos = min(self.pos, max(0, len(self.groups) - 1))
        self._render()

    def _render(self) -> None:
        self.table.blockSignals(True)
        n = len(self.qsos)
        draft = self.draft is not None
        self.prev_btn.setVisible(not draft and len(self.groups) > 1)
        self.next_btn.setVisible(not draft and len(self.groups) > 1)
        self.ok_btn.setVisible(not draft)
        self.keep_btn.setVisible(not draft)
        if n < 2:
            self.chip.setText(tr("Done"))
            self.chip.set_kind("ok")
            self.reason.setText(tr("No more marked QSOs to review."))
            self.counter.setText("")
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            for b in (self.ok_btn, self.keep_btn, self.del_btn, self.open_btn, self.save_btn, self.prev_btn,
                      self.next_btn):
                b.setEnabled(False)
            self.table.blockSignals(False)
            return
        self.table.setRowCount(len(ROWS))
        self.table.setColumnCount(n)
        self.table.setVerticalHeaderLabels([tr(BY_COL[c].label) for c in ROWS])
        heads = []
        for i, q in enumerate(self.qsos):
            if q.get("_draft"):
                heads.append(tr("Not logged yet"))
                continue
            flag = {"DUPE": tr("Dupe"), "SUSPECT": tr("Suspect"), "OK": tr("not a dupe")}.get(q.get("dupe_flag"), "")
            heads.append(f"#{i + 1}  {fmt('time_on', q.get('time_on'))[11:]}" + (f"  · {flag}" if flag else ""))
        self.table.setHorizontalHeaderLabels(heads)
        t = theme.tokens()
        for r, col in enumerate(ROWS):
            vals = [_val(q, col) for q in self.qsos]
            differ = len(set(vals)) > 1
            for c, (q, v) in enumerate(zip(self.qsos, vals, strict=True)):
                key = (q["id"], col)
                shown = fmt(col, self.edits[key]) if key in self.edits else v
                it = QTableWidgetItem(shown)
                flags = it.flags()
                if q.get("_draft") or col in NOT_EDITABLE:
                    flags &= ~Qt.ItemFlag.ItemIsEditable
                it.setFlags(flags)
                if col in KEY_ROWS:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                if key in self.edits:
                    it.setBackground(QBrush(QColor(t.accent_soft)))
                elif differ:
                    it.setBackground(QBrush(QColor(t.warn_soft)))
                self.table.setItem(r, c, it)
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
        self.chip.setText(tr("DUPE") if worst == "DUPE" else tr("Possible dupe"))
        self.chip.set_kind("bad" if worst == "DUPE" else "warn")
        head = f"{n} " + tr("QSOs with") + f" <b>{self.qsos[0].get('call')}</b> " + tr("on") + \
               f" {fmt('band', self.qsos[0].get('band'))}"
        self.reason.setText(head + ":<br>" + "<br>".join(lines))
        self.counter.setText(f"{tr('Group')} {self.pos + 1} / {len(self.groups)}" if self.groups else "")
        self.ok_btn.setEnabled(not draft)
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

    # ------------------------------------------------------------------ selection / editing
    def _select(self, col: int) -> None:
        if col < 0 or col >= len(self.qsos):
            return
        self.sel = col
        q = self.qsos[col]
        is_real = not q.get("_draft")
        self.del_btn.setEnabled(is_real)
        self.open_btn.setEnabled(is_real)
        self.keep_btn.setEnabled(is_real and self.draft is None)
        self.keep_btn.setText(tr("Keep") + f" #{col + 1}" + tr(", merge others"))
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

    def keep_and_merge(self, col: int, *, confirm: bool = True) -> None:
        keep = self.qsos[col]
        others = [q for q in self._real() if q["id"] != keep["id"]]
        if confirm and QMessageBox.question(
                self, tr("Merge"), tr("Keep") + f" #{col + 1} " + tr("and delete the other") + f" {len(others)} " +
                tr("QSOs? Missing fields and confirmations are copied first. You can undo this.")
        ) != QMessageBox.StandardButton.Yes:
            return
        with self.db.batch(tr("Merge duplicates")):
            merged = dict(keep)
            for o in others:
                ch = importer.merge_changes(merged, o)
                if ch:
                    self.db.update(keep["id"], ch, track_service=False)
                    merged.update(ch)
            self.db.delete([o["id"] for o in others])
            self.db.update(keep["id"], {"dupe_flag": None, "dupe_of": None}, track_service=False, normalized=True)
        self._next_group()

    def _keep_merge(self) -> None:
        if self.sel is not None and self._unsaved_ok():
            self.keep_and_merge(self.sel)

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
        with self.db.batch(tr("Delete QSO")):
            self.db.delete([q["id"]])
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


def flagged_ids(db, ids: list[int] | None = None) -> list[int]:
    if ids:
        rows = db.get_many(ids)
        return [i for i in ids if rows.get(i, {}).get("dupe_flag") in ("DUPE", "SUSPECT")]
    return db.select_ids("dupe_flag IN ('DUPE', 'SUSPECT')", [], [("time_on", False)])


def show_review(parent, db, prefs, ids: list[int] | None = None, on_edit=None) -> None:
    """Review marked QSOs (all, or the given ones), one dupe group at a time."""
    w = {"block_s": prefs.get("block_seconds"), "warn_s": prefs.get("warn_hours") * 3600}
    start = flagged_ids(db, ids) or (ids or [])
    groups = dupes.groups_for(db, start, **w)
    if not groups:
        QMessageBox.information(parent, tr("Compare possible duplicates"), tr("No marked QSOs to review."))
        return
    dlg = DupeCompareDialog(db, prefs, groups=groups, parent=parent)
    if on_edit:
        dlg.edit_requested.connect(on_edit)
    dlg.exec()
