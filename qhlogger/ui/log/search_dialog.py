"""Advanced search: rows of field · operator · value, combined with AND; named saved filters."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import i18n
from ...core import search
from ...core.fields import BY_COL, GROUP_ORDER, user_fields
from .. import theme

tr = i18n.tr


class ConditionRow(QWidget):
    def __init__(self, cond: dict | None = None, on_remove=None):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.field = QComboBox()
        for g in GROUP_ORDER:
            for f in user_fields():
                if f.group == g:
                    self.field.addItem(f"{tr(f.label)}  · {tr(g)}", f.col)
        self.op = QComboBox()
        self.v1 = QLineEdit()
        self.v2 = QLineEdit()
        self.and_lab = QLabel(tr("and"))
        rm = QPushButton()
        rm.setIcon(theme.icon("x"))
        rm.setProperty("flat", True)
        rm.setToolTip(tr("Remove this condition"))
        rm.clicked.connect(lambda: on_remove(self) if on_remove else None)
        for w in (self.field, self.op, self.v1, self.and_lab, self.v2, rm):
            lay.addWidget(w)
        self.field.setMinimumWidth(200)
        self.field.currentIndexChanged.connect(self._field_changed)
        self.op.currentIndexChanged.connect(self._op_changed)
        if cond:
            i = self.field.findData(cond.get("field"))
            self.field.setCurrentIndex(max(0, i))
        self._field_changed()
        if cond:
            i = self.op.findData(cond.get("op"))
            if i >= 0:
                self.op.setCurrentIndex(i)
            v = cond.get("value")
            if isinstance(v, (list, tuple)):
                self.v1.setText(str(v[0]) if v else "")
                self.v2.setText(str(v[1]) if len(v) > 1 else "")
            elif v is not None:
                self.v1.setText(str(v))
        self._op_changed()

    def _field_changed(self) -> None:
        col = self.field.currentData()
        self.op.blockSignals(True)
        self.op.clear()
        for op in search.ops_for(col):
            label = tr("matches (vague; see Exact)") if op == "mode" else tr(search.OP_LABELS.get(op, op))
            self.op.addItem(label, op)
        self.op.blockSignals(False)
        f = BY_COL.get(col)
        ph = {"time": "YYYY-MM-DD", "date": "YYYY-MM-DD"}.get(f.kind if f else "", "")
        if col == "call":
            ph = "BA1*, B?1PK"
        self.v1.setPlaceholderText(ph)
        self.v2.setPlaceholderText(ph)
        self._op_changed()

    def _op_changed(self) -> None:
        op = self.op.currentData()
        between = op == "between"
        novalue = op in ("empty", "not_empty", "yes", "no")
        self.v1.setVisible(not novalue)
        self.v2.setVisible(between)
        self.and_lab.setVisible(between)

    def cond(self) -> dict | None:
        op = self.op.currentData()
        col = self.field.currentData()
        if op in ("empty", "not_empty", "yes", "no"):
            return {"field": col, "op": op, "value": None}
        if op == "between":
            if not self.v1.text().strip() or not self.v2.text().strip():
                return None
            return {"field": col, "op": op, "value": [self.v1.text().strip(), self.v2.text().strip()]}
        if not self.v1.text().strip():
            return None
        return {"field": col, "op": op, "value": self.v1.text().strip()}


class SearchDialog(QDialog):
    def __init__(self, db, conditions: list[dict], parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle(tr("Search log"))
        self.resize(760, 420)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        hint = QLabel(tr("All conditions must match. Use * and ? as wildcards in text."))
        hint.setObjectName("Hint")
        top.addWidget(hint)
        top.addStretch(1)
        self.saved = QComboBox()
        self.saved.setMinimumWidth(180)
        self._reload_saved()
        self.saved.activated.connect(self._load_saved)
        save = QPushButton(tr("Save as…"))
        save.clicked.connect(self._save)
        delete = QPushButton(tr("Delete"))
        delete.setProperty("flat", True)
        delete.clicked.connect(self._delete_saved)
        top.addWidget(QLabel(tr("Saved:")))
        top.addWidget(self.saved)
        top.addWidget(save)
        top.addWidget(delete)
        lay.addLayout(top)
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(6)
        lay.addLayout(self.rows_box)
        add = QPushButton(tr("Add condition"))
        add.setIcon(theme.icon("plus"))
        add.clicked.connect(lambda: self._add_row())
        lay.addWidget(add, 0)
        lay.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel |
                              QDialogButtonBox.StandardButton.Reset)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Search"))
        bb.button(QDialogButtonBox.StandardButton.Ok).setProperty("primary", True)
        bb.button(QDialogButtonBox.StandardButton.Reset).setText(tr("Clear all"))
        bb.button(QDialogButtonBox.StandardButton.Reset).clicked.connect(self._clear)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.rows: list[ConditionRow] = []
        for c in conditions:
            self._add_row(c)
        if not self.rows:
            self._add_row()

    def _add_row(self, cond: dict | None = None) -> None:
        row = ConditionRow(cond, on_remove=self._remove_row)
        self.rows.append(row)
        self.rows_box.addWidget(row)

    def _remove_row(self, row: ConditionRow) -> None:
        self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()

    def _clear(self) -> None:
        for r in list(self.rows):
            self._remove_row(r)
        self._add_row()

    def conditions(self) -> list[dict]:
        return [c for c in (r.cond() for r in self.rows) if c]

    def _reload_saved(self) -> None:
        self.saved.clear()
        self.saved.addItem("", None)
        for name in self.db.saved_filters():
            self.saved.addItem(name, name)

    def _load_saved(self) -> None:
        name = self.saved.currentData()
        if not name:
            return
        spec = self.db.saved_filters().get(name, {})
        for r in list(self.rows):
            self._remove_row(r)
        for c in spec.get("conditions", []):
            self._add_row(c)
        if not self.rows:
            self._add_row()

    def _save(self) -> None:
        name, ok = QInputDialog.getText(self, tr("Save search"), tr("Name:"))
        if ok and name.strip():
            self.db.save_filter(name.strip(), {"conditions": self.conditions()})
            self._reload_saved()
            self.saved.setCurrentText(name.strip())

    def _delete_saved(self) -> None:
        name = self.saved.currentData()
        if name:
            self.db.delete_filter(name)
            self._reload_saved()
