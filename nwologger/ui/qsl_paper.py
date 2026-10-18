"""Paper QSL cards: a queue of cards to send, and marking sent / received with date and route."""

from __future__ import annotations

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from ..core.fields import VIA_LABELS
from . import theme
from .log.log_model import LogModel
from .log.log_view import LogView

tr = i18n.tr

PAPER_COLUMNS = ["call", "time_on", "band", "mode", "sat_name", "country", "qsl_via", "qsl_sent", "qslsdate",
                 "qsl_sent_via", "qsl_rcvd", "qslrdate", "qsl_rcvd_via", "qslmsg"]
VIEWS = [
    ("paper_to_send", "To send"),
    ("paper_awaiting", "Sent, awaiting reply"),
    ("paper_unanswered", "Received, not answered"),
    ("any_paper", "All paper activity"),
]


class PaperDialog(QDialog):
    """Mark paper cards sent or received for the given QSOs."""

    def __init__(self, kind: str, count: int, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle(tr("Paper QSL sent") if kind == "sent" else tr("Paper QSL received"))
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"{count} " + tr("QSOs")))
        form = QFormLayout()
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.via = QComboBox()
        for k in ("B", "D", "M", "E"):
            self.via.addItem(tr(VIA_LABELS[k]), k)
        self.msg = QLineEdit()
        form.addRow(tr("Date"), self.date)
        form.addRow(tr("Via"), self.via)
        if kind == "sent":
            form.addRow(tr("Card message"), self.msg)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setProperty("primary", True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def changes(self) -> dict:
        d = self.date.date().toString("yyyy-MM-dd")
        if self.kind == "sent":
            ch = {"qsl_sent": "Y", "qslsdate": d, "qsl_sent_via": self.via.currentData()}
            if self.msg.text().strip():
                ch["qslmsg"] = self.msg.text().strip()
            return ch
        return {"qsl_rcvd": "Y", "qslrdate": d, "qsl_rcvd_via": self.via.currentData()}


def mark_paper(parent, db, ids: list[int], kind: str) -> int:
    if not ids:
        return 0
    if kind == "queue":
        return db.update_many(ids, {"qsl_sent": "Q"}, label=tr("Queue paper QSL"))
    if kind == "clear":
        return db.update_many(ids, {"qsl_sent": None, "qslsdate": None, "qsl_sent_via": None, "qsl_rcvd": None,
                                    "qslrdate": None, "qsl_rcvd_via": None}, label=tr("Clear paper QSL"))
    if kind == "not_sent":  # one direction emptied, the way Clear empties both; a queued card leaves the queue
        return db.update_many(ids, {"qsl_sent": None, "qslsdate": None, "qsl_sent_via": None},
                              label=tr("Paper QSL not sent"))
    if kind == "not_rcvd":
        return db.update_many(ids, {"qsl_rcvd": None, "qslrdate": None, "qsl_rcvd_via": None},
                              label=tr("Paper QSL not received"))
    dlg = PaperDialog(kind, len(ids), parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return 0
    return db.update_many(ids, dlg.changes(), label=tr("Paper QSL sent") if kind == "sent" else
                          tr("Paper QSL received"))


class PaperQslTab(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        bar = QHBoxLayout()
        self.view_combo = QComboBox()
        for key, label in VIEWS:
            self.view_combo.addItem(tr(label), key)
        self.view_combo.currentIndexChanged.connect(self.refresh)
        bar.addWidget(QLabel(tr("Show:")))
        bar.addWidget(self.view_combo)
        bar.addSpacing(12)
        for text, kind, icon in ((tr("Mark sent…"), "sent", "upload"), (tr("Mark received…"), "rcvd", "download"),
                                 (tr("Queue to send"), "queue", "mail"), (tr("Clear"), "clear", "x")):
            b = QPushButton(text)
            b.setIcon(theme.icon(icon))
            b.clicked.connect(lambda _=False, k=kind: self._mark(k))
            bar.addWidget(b)
        self.replies_btn = QPushButton(tr("Queue replies to received cards…"))
        self.replies_btn.setIcon(theme.icon("mail"))
        self.replies_btn.setToolTip(tr("Put every QSO with a card received and none sent back into the queue to send"))
        self.replies_btn.clicked.connect(self.queue_replies)
        bar.addWidget(self.replies_btn)
        bar.addStretch(1)
        self.count = QLabel()
        self.count.setProperty("muted", True)
        bar.addWidget(self.count)
        lay.addLayout(bar)
        self.model = LogModel(db, PAPER_COLUMNS, self)
        self.view = LogView(self.model, self)
        lay.addWidget(self.view, 1)
        hint = QLabel(tr("Paper cards only. LoTW, eQSL, QRZ and HRDLog are tracked in their own columns."))
        hint.setObjectName("Hint")
        lay.addWidget(hint)
        self.model.counts_changed.connect(lambda shown, total: self.count.setText(f"{shown} " + tr("QSOs")))
        self.refresh()

    def refresh(self) -> None:
        key = self.view_combo.currentData()
        if key == "any_paper":
            self.model.extra_where = ("(qsl_sent IS NOT NULL OR qsl_rcvd IS NOT NULL)", [])
            self.model.spec = {}
        else:
            self.model.extra_where = ("", [])
            self.model.spec = {"quick": {"preset": key}}
        self.model.refresh()

    def _mark(self, kind: str) -> None:
        mark_paper(self, self.db, self.view.selected_ids(), kind)

    UNANSWERED = "qsl_rcvd = 'Y' AND (qsl_sent IS NULL OR qsl_sent NOT IN ('Y', 'Q', 'I'))"

    def unanswered_ids(self) -> list[int]:
        """QSOs with a card received and no card sent back, queued or ignored."""
        return [q["id"] for q in self.db.iter_all(self.UNANSWERED)]

    def queue_replies(self) -> int:
        """Ask, then put every unanswered card in the queue to send."""
        ids = self.unanswered_ids()
        if not ids:
            QMessageBox.information(self, tr("Queue replies"), tr("No received card is waiting for a reply."))
            return 0
        if QMessageBox.question(
                self, tr("Queue replies"), f"{len(ids)} " + tr("cards received without a card sent back.") + "\n\n" +
                tr("Put these QSOs in the queue to send? A card you don't want to answer stays in this count until "
                   "you answer it or mark it ignored (I).")) != QMessageBox.StandardButton.Yes:
            return 0
        return self.db.update_many(ids, {"qsl_sent": "Q"}, label=tr("Queue paper QSL"))
