"""Bulk edit: change many selected QSOs at once, with a preview and one undo step."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
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
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from ..core import dxcc, regions
from ..core import qso as qsomod
from ..core.fields import BY_COL, GROUP_ORDER, bulk_fields
from . import theme
from .widgets import fmt

tr = i18n.tr

CTY_COLS = {"country", "dxcc", "cqz", "ituz", "cont", "state"}
OPS = [("set", "Set to"), ("clear", "Clear"), ("replace", "Replace text"), ("cty", "Fill from country file")]


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

    def apply(self, q: dict, cty) -> Any:
        """New value for this field on QSO q (or the old value if unchanged)."""
        op = self.op.currentData()
        old = q.get(self.col)
        if op == "set":
            return self.value.text().strip() or None
        if op == "clear":
            return None
        if op == "replace":
            find = self.value.text()
            if not find or old is None:
                return old
            return str(old).replace(find, self.with_.text()) or None
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
        self.find.addAction(theme.icon("search", theme.tokens().muted), QLineEdit.ActionPosition.LeadingPosition)
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
        self.preview = QTableWidget(0, 5)
        self.preview.setHorizontalHeaderLabels([tr("Call"), tr("Time On"), tr("Field"), tr("Old"), tr("New")])
        self.preview.verticalHeader().setVisible(False)
        self.preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.preview.horizontalHeader().setStretchLastSection(True)
        rl.addWidget(self.preview, 1)
        hint = QLabel(tr("Changes are applied in one step and can be undone with Edit → Undo. "
                         "QSOs already uploaded to QRZ / HRDLog are marked for re-upload."))
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
        for fr in self.fields:
            if fr.active():
                new = fr.apply(q, self.cty)
                if (new if new is not None else None) != q.get(fr.col):
                    ch[fr.col] = new
        return ch

    def _update_preview(self) -> None:
        rows = []
        n = 0
        for qid in self.ids:
            q = self.rows_data.get(qid)
            if not q:
                continue
            ch = self.changes_for(q)
            norm = {}
            if ch:
                merged = qsomod.normalize({**q, **ch})
                norm = {k: merged.get(k) for k in ch}
            real = {k: v for k, v in norm.items() if v != q.get(k)}
            if real:
                n += 1
                for k, v in real.items():
                    if len(rows) < 300:
                        rows.append((q.get("call") or "", fmt("time_on", q.get("time_on")), tr(BY_COL[k].label),
                                     fmt(k, q.get(k)), fmt(k, v)))
        self.changed_count = n
        self.count.setText(f"<b>{n}</b> " + tr("of") + f" {len(self.ids)} " + tr("QSOs will change"))
        self.preview.setRowCount(len(rows))
        t = theme.tokens()
        for i, r in enumerate(rows):
            for j, v in enumerate(r):
                it = QTableWidgetItem(v)
                if j == 3:
                    it.setForeground(Qt.GlobalColor.gray)
                if j == 4:
                    it.setForeground(Qt.GlobalColor.darkGreen if t.name == "light" else Qt.GlobalColor.green)
                self.preview.setItem(i, j, it)
        self.apply_btn.setEnabled(n > 0)
        if self.only_active.isChecked():
            self._filter()

    def _apply(self) -> None:
        if not self.changed_count:
            return
        self.db.backup("bulk-edit")
        self.db.update_many(self.ids, self.changes_for, label=tr("Bulk edit") + f" ({self.changed_count})")
        self.accept()
