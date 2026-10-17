"""Bulk edit: change many selected QSOs at once, with a preview; a backup is saved first."""

from __future__ import annotations

import re
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from ..core import dxcc, regions, timeutil
from ..core import qso as qsomod
from ..core.fields import BY_COL, GROUP_ORDER, bulk_fields
from . import theme
from .widgets import fmt

tr = i18n.tr

CTY_COLS = {"country", "dxcc", "cqz", "ituz", "cont", "state"}
TIME_COLS = ("time_on", "time_off")
OPS = [("set", "Set to"), ("clear", "Clear"), ("replace", "Replace text"), ("cty", "Fill from country file")]
# Fields cleaned together by qso.normalize: a change to one may change the other.
COMPANIONS = {"mode": ("submode",), "submode": ("mode",), "band": ("freq",), "freq": ("band",),
              "band_rx": ("freq_rx",), "freq_rx": ("band_rx",), "sat_name": ("prop_mode",),
              "prop_mode": ("sat_name",), "time_on": ("time_off",), "time_off": ("time_on",)}
PREVIEW_ROWS = 300


class FieldRow:
    def __init__(self, grid: QGridLayout, r: int, col: str, label: str, on_change):
        self.col = col
        self.on = QCheckBox(label)
        self.op = QComboBox()
        for key, text in OPS:
            if key == "cty" and col not in CTY_COLS:
                continue
            self.op.addItem(tr(text), key)
        self.value = QLineEdit()
        self.with_ = QLineEdit()
        self.with_.setPlaceholderText(tr("with"))
        self.value.setPlaceholderText(tr("new value"))
        grid.addWidget(self.on, r, 0)
        grid.addWidget(self.op, r, 1)
        grid.addWidget(self.value, r, 2)
        grid.addWidget(self.with_, r, 3)
        self.widgets = [self.on, self.op, self.value, self.with_]
        self.on.toggled.connect(on_change)
        self.op.currentIndexChanged.connect(self._op)
        self.op.currentIndexChanged.connect(on_change)
        self.value.textChanged.connect(lambda *_: (self.on.setChecked(True), on_change()))
        self.with_.textChanged.connect(on_change)
        self._op()

    def _op(self) -> None:
        op = self.op.currentData()
        self.value.setEnabled(op in ("set", "replace"))
        self.value.setPlaceholderText(tr("find") if op == "replace" else tr("new value"))
        self.with_.setVisible(op == "replace")

    def set_visible(self, v: bool) -> None:
        for w in self.widgets:
            w.setVisible(v and (w is not self.with_ or self.op.currentData() == "replace"))

    def active(self) -> bool:
        return self.on.isChecked()

    def edit(self) -> Edit:
        """What this row does, read from its widgets once (not once per QSO)."""
        return Edit(self.col, self.op.currentData(), self.value.text(), self.with_.text())


class Edit:
    """One field's change, independent of the widgets: computing it for thousands of QSOs stays fast."""

    __slots__ = ("col", "op", "text", "with_")

    def __init__(self, col: str, op: str, text: str, with_: str):
        self.col, self.op, self.text, self.with_ = col, op, text, with_

    def apply(self, q: dict, cty) -> Any:
        """New value for this field on QSO q (or the old value if unchanged)."""
        op = self.op
        old = q.get(self.col)
        if op == "set":
            return self.text.strip() or None
        if op == "clear":
            return None
        if op == "replace":
            find = self.text
            if not find or old is None:
                return old
            return str(old).replace(find, self.with_) or None
        if op == "cty":
            r = cty.resolve(q.get("call") or "")
            if self.col == "state":
                if (r and r.dxcc == regions.CHINA_DXCC):
                    if old:
                        return regions.normalize_cn(str(old)) or old
                    return regions.guess_cn_province(q.get("call") or "") or old
                return old
            if not r:
                return old
            return {"country": r.country, "dxcc": r.dxcc, "cqz": r.cqz, "ituz": r.ituz, "cont": r.cont}[self.col]
        return old


class BulkEditDialog(QDialog):
    def __init__(self, db, ids: list[int], parent=None):
        super().__init__(parent)
        self.db = db
        self.ids = ids
        self.rows_data = db.get_many(ids)
        self.cty = dxcc.default()
        self.setWindowTitle(tr("Bulk edit") + f" — {len(ids)} " + tr("QSOs"))
        self.resize(1000, 640)
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        self.find = QLineEdit()
        self.find.setPlaceholderText(tr("Find a field…"))
        self.find.setClearButtonEnabled(True)
        self.find.addAction(theme.icon("search", "muted"), QLineEdit.ActionPosition.LeadingPosition)
        self.find.textChanged.connect(self._filter)
        self.only_active = QCheckBox(tr("Show only fields being changed"))
        self.only_active.toggled.connect(self._filter)
        head.addWidget(self.find, 1)
        head.addWidget(self.only_active)
        lay.addLayout(head)
        split = QSplitter(Qt.Orientation.Horizontal)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self.grid = QGridLayout(inner)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(4)
        self.fields: list[FieldRow] = []
        self.group_labels: list[tuple[QLabel, list[FieldRow]]] = []
        r = 0
        for g in GROUP_ORDER:
            fs = [f for f in bulk_fields() if f.group == g]
            if not fs:
                continue
            lab = QLabel(tr(g).upper())
            lab.setObjectName("SectionTitle")
            self.grid.addWidget(lab, r, 0, 1, 4)
            r += 1
            rows = []
            for f in fs:
                fr = FieldRow(self.grid, r, f.col, tr(f.label), self._schedule)
                self.fields.append(fr)
                rows.append(fr)
                r += 1
            self.group_labels.append((lab, rows))
        self.grid.setRowStretch(r, 1)
        self.grid.setColumnStretch(2, 1)
        scroll.setWidget(inner)
        split.addWidget(scroll)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.count = QLabel()
        rl.addWidget(self.count)
        self.problem = QLabel()
        self.problem.setWordWrap(True)
        self.problem.setVisible(False)
        rl.addWidget(self.problem)
        self.leave_out = QPushButton()
        self.leave_out.setVisible(False)
        self.leave_out.clicked.connect(self._leave_out)
        rl.addWidget(self.leave_out, 0, Qt.AlignmentFlag.AlignLeft)
        self.preview = QTableWidget(0, 6)
        self.preview.setHorizontalHeaderLabels([tr("Call"), tr("Time On"), tr("Field"), tr("Old"), tr("New"), ""])
        self.preview.verticalHeader().setVisible(False)
        self.preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.preview.horizontalHeader().setStretchLastSection(True)
        rl.addWidget(self.preview, 1)
        hint = QLabel(tr("Changes are applied in one step and can't be undone: a backup of the log is saved first "
                         "(Preferences → Database → Show backups folder). QSOs already uploaded to QRZ / HRDLog are "
                         "marked for re-upload."))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        rl.addWidget(hint)
        split.addWidget(right)
        split.setSizes([520, 480])
        lay.addWidget(split, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        self.apply_btn = bb.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_btn.setText(tr("Apply"))
        self.apply_btn.setProperty("primary", True)
        self.apply_btn.clicked.connect(self._apply)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._update_preview)
        self.changed_count = 0
        self.refused: list[int] = []  # QSOs that would end before they start
        self.unreadable: list[tuple[str, str, str]] = []  # (field label, text, kind) of times and dates not read
        self._edits: list[Edit] = []
        self._update_preview()

    def _schedule(self, *_a) -> None:
        self._timer.start()

    def _filter(self) -> None:
        text = self.find.text().strip().lower()
        only = self.only_active.isChecked()
        for lab, rows in self.group_labels:
            any_vis = False
            for fr in rows:
                vis = (not text or text in fr.on.text().lower() or text in fr.col) and (not only or fr.active())
                fr.set_visible(vis)
                any_vis = any_vis or vis
            lab.setVisible(any_vis)

    def changes_for(self, q: dict) -> dict:
        ch = {}
        for edit in self._edits:
            new = edit.apply(q, self.cty)
            if new != q.get(edit.col):
                ch[edit.col] = new
        return ch

    def _snapshot(self) -> None:
        self._edits = [fr.edit() for fr in self.fields if fr.active()]

    def _unreadable_fields(self) -> list[tuple[str, str, str]]:
        """Times and dates typed for "Set to" that can't be read: they are refused, never written as empty (a
        time) or as the text typed (a date)."""
        bad = []
        for fr in self.fields:
            kind = "time" if fr.col in TIME_COLS else "date" if BY_COL[fr.col].kind == "date" else None
            if kind is None:
                continue
            text = fr.value.text().strip()
            unreadable = fr.active() and fr.op.currentData() == "set" and bool(text) and (
                timeutil.parse_iso(text) is None if kind == "time" else timeutil.adif_date_to_iso(text) is None)
            if fr.value.property("opstate") != ("bad" if unreadable else ""):
                fr.value.setProperty("opstate", "bad" if unreadable else "")
                theme.repolish(fr.value)
            if unreadable:
                bad.append((tr(BY_COL[fr.col].label), text, kind))
        return bad

    @staticmethod
    def _normalized(q: dict, ch: dict, memo: dict) -> dict:
        """The changed fields as qso.normalize would store them (and both times, when one changes). Only the
        changed fields and the ones cleaned with them are normalised, and equal inputs once."""
        cols = set(ch)
        for col in ch:
            cols.update(COMPANIONS.get(col, ()))
        part = tuple(sorted((col, ch[col] if col in ch else q.get(col)) for col in cols))
        norm = memo.get(part)
        if norm is None:
            norm = memo[part] = qsomod.normalize(dict(part))
        return norm

    @staticmethod
    def _example(text: str, q: dict | None) -> str:
        """A time typed without a date (15:00) shown with the first QSO's date: 2026-10-10 15:00."""
        day = (q or {}).get("time_on", "")[:10] or timeutil.today_iso()
        return f"{day} {text}" if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", text) else f"{day} 15:00"

    def _update_preview(self) -> None:
        rows, refused_rows = [], []
        n = 0
        self.refused = []
        self.unreadable = self._unreadable_fields()
        self._snapshot()
        memo: dict = {}
        labels = {e.col: tr(BY_COL[e.col].label) for e in self._edits}
        for qid in self.ids:
            q = self.rows_data.get(qid)
            if not q:
                continue
            ch = self.changes_for(q)
            if not ch:
                continue
            norm = self._normalized(q, ch, memo)
            real = {k: norm.get(k) for k in ch if norm.get(k) != q.get(k)}
            if not real:
                continue
            times = any(k in TIME_COLS for k in ch)
            on, off = (norm.get("time_on"), norm.get("time_off")) if times else (q.get("time_on"), q.get("time_off"))
            why = ""
            if any(k in TIME_COLS and ch.get(k) not in (None, "") and norm.get(k) is None for k in real):
                why = tr("can't read the new time")
            elif on and off and off < on:
                why = tr("ends before it starts") + f" ({tr('Time Off')} {fmt('time_off', off)[11:16]})"
            if why:
                self.refused.append(qid)
            else:
                n += 1
            shown = refused_rows if why else rows
            for k, v in real.items():
                if len(shown) >= PREVIEW_ROWS:
                    break
                shown.append((q.get("call") or "", fmt("time_on", q.get("time_on")), labels[k],
                              fmt(k, q.get(k)), fmt(k, v), why))
        rows = (refused_rows + rows)[:PREVIEW_ROWS]
        self.changed_count = n
        self.count.setText(f"<b>{n}</b> " + tr("of") + f" {len(self.ids)} " + tr("QSOs will change"))
        t = theme.tokens()
        problems = []
        if self.unreadable:
            label, text, kind = self.unreadable[0]
            first = self.rows_data.get(self.ids[0]) if self.ids else None
            if kind == "time":
                problems.append(tr("Can't read") + f" “{text}” ({label}) " + tr("as a date and time. Type it as") +
                                f" {self._example(text, first)}.")
            else:
                problems.append(tr("Can't read") + f" “{text}” ({label}) " + tr("as a date. Type it as") +
                                f" {timeutil.today_iso()}.")
        if self.refused:
            problems.append(f"<b>{len(self.refused)} " + tr("of") + f" {len(self.ids)} " +
                            tr("QSOs would end before they start.") + "</b> " +
                            tr("Change Time On / Time Off, or leave those QSOs out of this bulk edit."))
        self.problem.setText("<br>".join(problems))
        self.problem.setStyleSheet(f"color: {t.bad};")
        self.problem.setVisible(bool(problems))
        self.leave_out.setText(tr("Leave those out") + f" ({len(self.refused)})")
        self.leave_out.setVisible(bool(self.refused) and not self.unreadable)
        self.preview.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, v in enumerate(r):
                it = QTableWidgetItem(v)
                if r[5]:
                    it.setBackground(QBrush(QColor(t.bad_soft)))
                    if j == 5:
                        it.setForeground(QBrush(QColor(t.bad)))
                elif j == 3:
                    it.setForeground(Qt.GlobalColor.gray)
                elif j == 4:
                    it.setForeground(Qt.GlobalColor.darkGreen if t.name == "light" else Qt.GlobalColor.green)
                self.preview.setItem(i, j, it)
        self.apply_btn.setEnabled(n > 0 and not self.refused and not self.unreadable)
        if self.only_active.isChecked():
            self._filter()

    def _leave_out(self) -> None:
        """Take the QSOs that would end before they start out of this bulk edit, so the rest can be applied."""
        out = set(self.refused)
        self.ids = [i for i in self.ids if i not in out]
        self.setWindowTitle(tr("Bulk edit") + f" — {len(self.ids)} " + tr("QSOs"))
        self._update_preview()

    def _apply(self) -> None:
        if not self.changed_count or self.refused or self.unreadable:
            return
        self._snapshot()
        self.db.backup("bulk-edit")
        self.db.update_many(self.ids, self.changes_for, label=tr("Bulk edit") + f" ({self.changed_count})")
        self.accept()
