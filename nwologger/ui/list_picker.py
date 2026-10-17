"""Pick one value from a long list (all modes, all satellites), optionally adding it to your short list."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from .. import i18n
from . import theme

tr = i18n.tr


class ListPicker(QDialog):
    def __init__(self, title: str, items: list[tuple[str, str]], parent=None, add_label: str | None = None):
        """``items``: (value, tooltip)."""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(360, 480)
        lay = QVBoxLayout(self)
        self.find = QLineEdit()
        self.find.setPlaceholderText(tr("Search…"))
        self.find.setClearButtonEnabled(True)
        self.find.addAction(theme.icon("search", "muted"), QLineEdit.ActionPosition.LeadingPosition)
        self.list = QListWidget()
        for value, tip in items:
            it = QListWidgetItem(value)
            it.setData(Qt.ItemDataRole.UserRole, value)
            if tip:
                it.setToolTip(tip)
            self.list.addItem(it)
        self.add = QCheckBox(add_label or tr("Also add it to my list"))
        self.add.setChecked(True)
        lay.addWidget(self.find)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.add)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setProperty("primary", True)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.find.textChanged.connect(self._filter)
        self.find.returnPressed.connect(self._ok)
        self.list.itemDoubleClicked.connect(lambda *_: self._ok())
        self.value: str | None = None
        self.find.setFocus()

    def _filter(self, text: str) -> None:
        t = text.strip().upper()
        first = None
        for i in range(self.list.count()):
            it = self.list.item(i)
            hit = not t or t in it.text().upper() or t in (it.toolTip() or "").upper()
            it.setHidden(not hit)
            if hit and first is None:
                first = it
        if first is not None:
            self.list.setCurrentItem(first)

    def _ok(self) -> None:
        it = self.list.currentItem()
        if it is not None and not it.isHidden():
            self.value = it.data(Qt.ItemDataRole.UserRole)
            self.accept()
