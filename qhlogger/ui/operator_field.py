"""The operator field of the entry forms.

Personal stations show their own call. Club stations must enter the operator: an empty field turns red and
the QSO is not logged. On a new UTC day or after a break the field turns amber once ("check the operator"), and
a second Enter logs.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLineEdit

from .. import i18n
from ..core import callsign
from ..core.operators import OperatorSession, is_club
from . import theme

tr = i18n.tr


class OperatorField(QLineEdit):
    message = Signal(str)

    def __init__(self, ops: OperatorSession, parent=None):
        super().__init__(parent)
        self.ops = ops
        self.setProperty("mono", True)
        self._pending = False
        self.textEdited.connect(self._typed)

    def _state(self, state: str) -> None:
        if self.property("opstate") != state:
            self.setProperty("opstate", state)
            theme.repolish(self)

    def _typed(self, text: str) -> None:
        up = callsign.clean(text)
        if up != text:
            pos = self.cursorPosition()
            self.setText(up)
            self.setCursorPosition(pos)
        self._pending = False
        self._state("")

    def show_for(self, profile: dict | None) -> None:
        """Show the current operator for this station (keeps what you typed for club stations)."""
        self._pending = False
        self._state("")
        if is_club(profile):
            cur = self.ops.current(profile)
            if cur:
                self.setText(cur)
            self.setPlaceholderText(tr("operator callsign (required)"))
            self.setToolTip(tr("Club station: who is operating. Kept while QHlogger runs; checked again on a new "
                               "UTC day or after a 3-hour break."))
        else:
            self.setText(self.ops.personal_operator(profile) if profile else "")
            self.setPlaceholderText(tr("your callsign"))
            self.setToolTip(tr("Personal station: your callsign, from the station profile. Change it for a guest "
                               "operator."))

    def resolve(self, profile: dict | None) -> str | None:
        """The operator to log, or None when the QSO must not be logged yet."""
        op = callsign.clean(self.text())
        if not is_club(profile):
            return op or (self.ops.personal_operator(profile) if profile else None)
        if not op:
            self._state("bad")
            self.setFocus()
            self.message.emit(tr("Enter the operator: this is a club station."))
            return None
        if self.ops.needs_confirmation(profile) and self.ops.current(profile) == op and not self._pending:
            self._pending = True
            self._state("check")
            self.setFocus()
            self.selectAll()
            self.message.emit(tr("Check the operator (new UTC day or after a break), then press Enter again to log."))
            return None
        self.ops.set(profile, op)
        self._pending = False
        self._state("")
        return op
