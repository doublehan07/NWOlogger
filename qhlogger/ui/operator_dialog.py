"""Ask who is operating a club station."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from .. import i18n
from ..core import callsign
from ..core.operators import GAP_HOURS, OperatorSession, is_club

tr = i18n.tr


class OperatorDialog(QDialog):
    def __init__(self, station: str, recent: list[str], current: str | None, reason: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Operator"))
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{tr('Who is operating')} {station}?</b>")
        lay.addWidget(head)
        why = QLabel(reason)
        why.setObjectName("Hint")
        why.setWordWrap(True)
        lay.addWidget(why)
        self.op = QComboBox()
        self.op.setEditable(True)
        self.op.addItems(recent)
        self.op.setCurrentText(current or (recent[0] if recent else ""))
        self.op.lineEdit().setPlaceholderText(tr("Operator callsign"))
        self.op.lineEdit().selectAll()
        lay.addWidget(self.op)
        hint = QLabel(tr("Saved while QHlogger runs. You will be asked again on a new UTC day or after a break of")
                      + f" {GAP_HOURS} " + tr("hours."))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setProperty("primary", True)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.value = ""

    def _ok(self) -> None:
        v = callsign.clean(self.op.currentText())
        if v:
            self.value = v
            self.accept()


def recent_operators(db, station: str) -> list[str]:
    rows = db.conn.execute(
        "SELECT operator FROM qso WHERE station_callsign = ? AND operator IS NOT NULL AND operator != '' "
        "GROUP BY operator ORDER BY MAX(time_on) DESC LIMIT 20", (station,)).fetchall()
    return [r[0] for r in rows]


def ensure_operator(parent, ops: OperatorSession, db, profile: dict | None, *, force: bool = False) -> str | None:
    """The operator for the next QSO. Asks for club stations when needed; None means the user cancelled."""
    if not profile:
        return None
    if not is_club(profile):
        return ops.personal_operator(profile)
    if not force and not ops.needs_confirmation(profile):
        return ops.current(profile)
    station = (profile.get("station_callsign") or "").upper()
    cur = ops.current(profile)
    if force:
        reason = tr("Change the operator for the next QSOs.")
    elif cur is None:
        reason = tr("This is a club station: enter the operator for this session.")
    else:
        reason = tr("Please check the operator: a new UTC day started, or nothing was logged for a while.")
    dlg = OperatorDialog(station, recent_operators(db, station), cur, reason, parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    ops.set(profile, dlg.value)
    return dlg.value
