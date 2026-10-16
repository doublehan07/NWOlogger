"""Command palette (⌘K / Ctrl+K): type to find and run any action."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from .. import i18n

tr = i18n.tr


class CommandPalette(QDialog):
    def __init__(self, actions: list[QAction], parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("Palette")
        self.actions_ = [a for a in actions if a.text() and a.isVisible()]
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setObjectName("PaletteEdit")
        self.edit.setPlaceholderText(tr("Type a command…"))
        self.list = QListWidget()
        self.list.setObjectName("PaletteList")
        lay.addWidget(self.edit)
        lay.addWidget(self.list, 1)
        self.edit.textChanged.connect(self._filter)
        self.edit.returnPressed.connect(self._run)
        self.list.itemActivated.connect(lambda *_: self._run())
        self._filter("")

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            row = self.list.currentRow() + (1 if e.key() == Qt.Key.Key_Down else -1)
            self.list.setCurrentRow(max(0, min(self.list.count() - 1, row)))
            return
        if e.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(e)

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        self.list.clear()
        for a in self.actions_:
            name = a.text().replace("&", "")
            hay = (name + " " + (a.toolTip() or "")).lower()
            if all(w in hay for w in words):
                sc = a.shortcut().toString(a.shortcut().SequenceFormat.NativeText)
                it = QListWidgetItem(f"{name}" + (f"      {sc}" if sc else ""))
                it.setData(Qt.ItemDataRole.UserRole, a)
                if not a.isEnabled():
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _run(self) -> None:
        it = self.list.currentItem()
        if not it:
            return
        a: QAction = it.data(Qt.ItemDataRole.UserRole)
        self.accept()
        if a.isEnabled():
            a.trigger()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if self.parent():
            p = self.parent()
            geo = p.geometry()
            self.move(geo.center().x() - self.width() // 2, geo.top() + 90)
        self.edit.setFocus()
