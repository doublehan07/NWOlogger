"""The regular backup plan's reminder: time to copy the log to another disk or a cloud folder."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from .. import i18n
from ..core import backup_plan

tr = i18n.tr


class BackupReminder(QDialog):
    copied = Signal(object)  # the copy's path
    prefs_requested = Signal()

    def __init__(self, db, prefs, parent=None):
        super().__init__(parent)
        self.db, self.prefs = db, prefs
        self._done = False  # Later, a copy or Preferences happened: closing afterwards records nothing
        self.setWindowTitle(tr("Regular backup"))
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        lay = QVBoxLayout(self)
        every = int(prefs.get("backup_every_days") or 7)
        head = QLabel(tr("Time for your weekly backup") if every == 7 else tr("Time for your regular backup"))
        head.setStyleSheet("font-weight: 600; font-size: 15px")
        lay.addWidget(head)
        folder = (prefs.get("external_backup_dir") or "").strip()
        last = db.meta(backup_plan.EXTERNAL_AT)
        if last:
            text = tr("Last copy to another disk:") + f" {last[:10]}" + (f", {folder}" if folder else "") + "."
        else:
            text = tr("The log has not been copied to another disk yet.")
        body = QLabel(text)
        body.setWordWrap(True)
        lay.addWidget(body)
        try:
            size = backup_plan.size_text(db.path.stat().st_size)
        except OSError:
            size = ""
        info = QLabel(f"{db.path.name} · {db.count():,} " + tr("QSOs") + (f" · {size}" if size else ""))
        info.setProperty("muted", True)
        lay.addWidget(info)
        row = QHBoxLayout()
        self.to_btn = QPushButton((tr("Back up to") + f" {folder}") if folder else tr("Choose a folder…"))
        self.to_btn.setProperty("primary", True)
        self.to_btn.clicked.connect(lambda: self.back_up(folder or None))
        row.addWidget(self.to_btn)
        self.other_btn = QPushButton(tr("Another folder…"))
        self.other_btn.clicked.connect(lambda: self.back_up(None))
        self.other_btn.setVisible(bool(folder))
        row.addWidget(self.other_btn)
        row.addStretch(1)
        self.later_btn = QPushButton(tr("Later"))
        self.later_btn.clicked.connect(self.later)
        row.addWidget(self.later_btn)
        self.prefs_btn = QPushButton(tr("Preferences…"))
        self.prefs_btn.clicked.connect(self._prefs)
        row.addWidget(self.prefs_btn)
        lay.addLayout(row)

    def back_up(self, folder: str | None) -> None:
        """Copy the log into the folder (asked for when none is set) and remember it."""
        if not folder:
            folder = QFileDialog.getExistingDirectory(self, tr("Folder for copies of the log"),
                                                      self.prefs.get("external_backup_dir") or str(Path.home()))
            if not folder:
                return
        try:
            dest = backup_plan.copy_outside(self.db, folder)
        except OSError as e:
            QMessageBox.warning(self, tr("Regular backup"), tr("Could not copy the log:") + f"\n{e}")
            return
        self.prefs.set("external_backup_dir", folder)
        self._done = True
        self.copied.emit(dest)
        self.accept()

    def _asked(self) -> None:
        if self._done:
            return
        self._done = True
        try:
            backup_plan.asked(self.db)
        except sqlite3.ProgrammingError:  # the log was closed meanwhile (a restart): nothing to record
            pass

    def later(self) -> None:
        """Not again today."""
        self._asked()
        self.reject()

    def _prefs(self) -> None:
        self._asked()
        self.prefs_requested.emit()
        self.reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._asked()  # closing the window counts as Later
        super().closeEvent(event)
