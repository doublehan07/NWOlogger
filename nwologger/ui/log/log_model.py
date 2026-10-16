"""Table model over the qso table: ids are queried with SQL; rows are fetched a page at a time."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor

from ... import i18n
from ...core import search, uploads
from ...core.fields import BY_COL
from .. import theme
from ..widgets import fmt, mode_text, province_text

tr = i18n.tr

PAGE = 300
MONO_COLS = {"call", "time_on", "time_off", "freq", "freq_rx", "gridsquare", "my_gridsquare", "rst_sent",
             "rst_rcvd", "stx", "srx", "station_callsign"}
RIGHT_COLS = {"freq", "freq_rx", "tx_pwr", "dxcc", "cqz", "ituz", "stx", "srx"}

IdRole = Qt.ItemDataRole.UserRole + 1


def confirmed(q: dict) -> bool:
    """A green call: confirmed by LoTW (Y, or V for verified). eQSL, QRZ.com and paper cards don't count."""
    return q.get("lotw_qsl_rcvd") in ("Y", "V")


class LogModel(QAbstractTableModel):
    counts_changed = Signal(int, int)  # shown, total

    def __init__(self, db, columns: list[str], parent=None):
        super().__init__(parent)
        self.db = db
        self.columns = [c for c in columns if c in BY_COL]
        self.ids: list[int] = []
        self.row_of: dict[int, int] = {}
        self.cache: dict[int, dict] = {}
        self.order: list[tuple[str, bool]] = [("time_on", True)]
        self.spec: dict = {}
        self.extra_where: tuple[str, list] = ("", [])
        self.contest_session_id: int | None = None
        self._mono = theme.mono_font()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(250)
        self._refresh_timer.timeout.connect(self.refresh)
        db.add_listener(self._on_db_change)

    def detach(self) -> None:
        self._refresh_timer.stop()
        self.db.remove_listener(self._on_db_change)

    # ------------------------------------------------------------------ querying
    def where(self) -> tuple[str, list]:
        w, p = search.build_where(self.spec, contest_session_id=self.contest_session_id)
        ew, ep = self.extra_where
        parts = [x for x in (w, ew) if x]
        return " AND ".join(f"({x})" for x in parts), [*p, *ep]

    def refresh(self) -> None:
        w, p = self.where()
        self.beginResetModel()
        self.ids = self.db.select_ids(w, p, self.order)
        self.row_of = {i: r for r, i in enumerate(self.ids)}
        self.cache.clear()
        self.endResetModel()
        self.counts_changed.emit(len(self.ids), self.db.count())

    def set_spec(self, spec: dict) -> None:
        self.spec = spec
        self.refresh()

    def set_columns(self, columns: list[str]) -> None:
        self.beginResetModel()
        self.columns = [c for c in columns if c in BY_COL]
        self.endResetModel()

    def _on_db_change(self, kind: str, ids: list[int]) -> None:
        if kind in ("insert", "delete", "reset"):
            self._refresh_timer.start()
        elif kind == "update":
            for i in ids:
                self.cache.pop(i, None)
                r = self.row_of.get(i)
                if r is not None:
                    self.dataChanged.emit(self.index(r, 0), self.index(r, len(self.columns) - 1))
            self._refresh_timer.start()

    # ------------------------------------------------------------------ rows
    def row_dict(self, row: int) -> dict | None:
        if row < 0 or row >= len(self.ids):
            return None
        qid = self.ids[row]
        q = self.cache.get(qid)
        if q is None:
            lo = max(0, row - PAGE // 3)
            chunk = [i for i in self.ids[lo:lo + PAGE] if i not in self.cache]
            self.cache.update(self.db.get_many(chunk))
            if len(self.cache) > 20 * PAGE:
                keep = set(self.ids[lo:lo + PAGE])
                self.cache = {k: v for k, v in self.cache.items() if k in keep}
            q = self.cache.get(qid)
        return q

    def id_at(self, row: int) -> int | None:
        return self.ids[row] if 0 <= row < len(self.ids) else None

    # ------------------------------------------------------------------ Qt model API
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.ids)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.columns):
            col = self.columns[section]
            if role == Qt.ItemDataRole.DisplayRole:
                return tr(BY_COL[col].label)
            if role == Qt.ItemDataRole.ToolTipRole:
                f = BY_COL[col]
                return f"{tr(f.label)} ({f.adif or col})"
        return None

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        q = self.row_dict(index.row())
        if q is None:
            return None
        col = self.columns[index.column()]
        t = theme.tokens()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == "mode":
                return mode_text(q)
            return fmt(col, q.get(col))
        if role == IdRole:
            return q["id"]
        if role == Qt.ItemDataRole.FontRole and col in MONO_COLS:
            return self._mono
        if role == Qt.ItemDataRole.TextAlignmentRole and col in RIGHT_COLS:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole:
            flag = q.get("dupe_flag")
            if flag == "DUPE":
                return QBrush(QColor(t.bad))
            if col == "call" and confirmed(q):
                return QBrush(QColor(t.ok))
            if uploads.missing(q):  # grey until it is uploaded to all four services; Upload selected sends it
                return QBrush(QColor(t.muted))
            return None
        if role == Qt.ItemDataRole.BackgroundRole:
            flag = q.get("dupe_flag")
            if flag == "DUPE":
                return QBrush(QColor(t.bad_soft))
            if flag == "SUSPECT":
                return QBrush(QColor(t.warn_soft))
            return None
        if role == Qt.ItemDataRole.ToolTipRole:
            tips = []
            if col == "state" and province_text(q):
                tips.append(province_text(q))
            if q.get("dupe_flag") in ("DUPE", "SUSPECT"):
                other = self.db.get(q["dupe_of"]) if q.get("dupe_of") else None
                when = fmt("time_on", other.get("time_on")) if other else "?"
                kind = tr("Dupe") if q["dupe_flag"] == "DUPE" else tr("Suspicious dupe")
                tips.append(f"{kind}: {tr('collides with the QSO at')} {when} {mode_text(other) if other else ''}")
            todo = uploads.missing(q)
            if todo:
                tips.append(tr("Not uploaded to:") + " " + ", ".join(uploads.SERVICES[s] for s in todo))
            return "\n".join(tips) or None
        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        if 0 <= column < len(self.columns):
            self.order = [(self.columns[column], order == Qt.SortOrder.DescendingOrder)]
            self.refresh()
