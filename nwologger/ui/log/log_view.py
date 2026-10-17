"""Log table view with a column chooser, selection by id, and state persistence."""

from __future__ import annotations

from PySide6.QtCore import QItemSelection, QItemSelectionModel, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QTableView

from ... import i18n
from ...core.fields import BY_COL, DEFAULT_LOG_COLUMNS, GROUP_ORDER, user_fields
from .log_model import LogModel

tr = i18n.tr


class LogView(QTableView):
    edit_requested = Signal(int)
    context_requested = Signal(object)  # QPoint (global)
    columns_changed = Signal(list)

    def __init__(self, model: LogModel, parent=None):
        super().__init__(parent)
        self.setModel(model)
        self.m = model
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSortingEnabled(True)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        vh = self.verticalHeader()
        vh.setVisible(False)
        vh.setDefaultSectionSize(26)
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        hh = self.horizontalHeader()
        hh.setSectionsMovable(True)
        hh.setHighlightSections(False)
        hh.setStretchLastSection(True)
        hh.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hh.customContextMenuRequested.connect(self._header_menu)
        hh.sectionMoved.connect(lambda *_: self.columns_changed.emit(self.visual_columns()))
        hh.setSortIndicator(self._col_index("time_on"), Qt.SortOrder.DescendingOrder)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda p: self.context_requested.emit(self.viewport().mapToGlobal(p)))
        self.doubleClicked.connect(self._double)
        self._saved_selection: list[int] = []
        model.modelAboutToBeReset.connect(self._remember)
        model.modelReset.connect(self._restore)
        self.apply_widths()

    def _col_index(self, col: str) -> int:
        return self.m.columns.index(col) if col in self.m.columns else -1

    def apply_widths(self) -> None:
        for i, c in enumerate(self.m.columns):
            self.setColumnWidth(i, BY_COL[c].width + 16)

    def visual_columns(self) -> list[str]:
        hh = self.horizontalHeader()
        return [self.m.columns[hh.logicalIndex(v)] for v in range(hh.count())]

    def set_columns(self, cols: list[str]) -> None:
        self.m.set_columns(cols)
        hh = self.horizontalHeader()
        # Reset any earlier moves so visual order = model order.
        for v in range(hh.count()):
            li = hh.logicalIndex(v)
            if li != v:
                hh.moveSection(hh.visualIndex(v), v)
        self.apply_widths()
        self.columns_changed.emit(self.visual_columns())

    def _header_menu(self, pos) -> None:
        menu = QMenu(self)
        current = set(self.m.columns)
        groups: dict[str, QMenu] = {}
        for g in GROUP_ORDER:
            fs = [f for f in user_fields() if f.group == g]
            if not fs:
                continue
            sub = menu.addMenu(tr(g))
            groups[g] = sub
            for f in fs:
                act = QAction(tr(f.label), sub)
                act.setCheckable(True)
                act.setChecked(f.col in current)
                act.toggled.connect(lambda on, col=f.col: self._toggle(col, on))
                sub.addAction(act)
        menu.addSeparator()
        reset = menu.addAction(tr("Reset columns"))
        reset.triggered.connect(lambda: self.set_columns(list(DEFAULT_LOG_COLUMNS)))
        menu.exec(self.horizontalHeader().mapToGlobal(pos))

    def _toggle(self, col: str, on: bool) -> None:
        cols = self.visual_columns()
        if on and col not in cols:
            cols.append(col)
        elif not on and col in cols and len(cols) > 1:
            cols.remove(col)
        self.set_columns(cols)

    def _double(self, index) -> None:
        qid = self.m.id_at(index.row())
        if qid is not None:
            self.edit_requested.emit(qid)

    # ------------------------------------------------------------------ selection by id
    def selected_ids(self) -> list[int]:
        rows = sorted({i.row() for i in self.selectionModel().selectedRows()})
        return [self.m.ids[r] for r in rows if r < len(self.m.ids)]

    def current_id(self) -> int | None:
        idx = self.currentIndex()
        return self.m.id_at(idx.row()) if idx.isValid() else None

    def select_ids(self, ids: list[int], scroll: bool = True) -> None:
        sel = QItemSelection()
        first = None
        for qid in ids:
            r = self.m.row_of.get(qid)
            if r is None:
                continue
            first = r if first is None else min(first, r)
            sel.select(self.m.index(r, 0), self.m.index(r, self.m.columnCount() - 1))
        sm = self.selectionModel()
        sm.select(sel, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        if first is not None:
            sm.setCurrentIndex(self.m.index(first, 0), QItemSelectionModel.SelectionFlag.NoUpdate)
            if scroll:
                self.scrollTo(self.m.index(first, 0))

    def _remember(self) -> None:
        self._saved_selection = self.selected_ids()
        idx = self.indexAt(self.viewport().rect().topLeft())
        self._top_id = self.m.id_at(idx.row()) if idx.isValid() else None

    def _restore(self) -> None:
        """After new data the same QSO stays at the top, so the table doesn't jump while you read. A new sort order
        (a header click) shows its first row; the selection stays highlighted wherever it is."""
        if self._saved_selection:
            self.select_ids(self._saved_selection, scroll=False)
        top = getattr(self, "_top_id", None)
        if self.m.resorted:
            self.scrollToTop()
        elif top is not None and top in self.m.row_of:
            self.scrollTo(self.m.index(self.m.row_of[top], 0), QAbstractItemView.ScrollHint.PositionAtTop)
