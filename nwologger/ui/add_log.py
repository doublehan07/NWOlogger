"""Adding a log by copy (File → Add log file…) and saving a copy (File → Save a copy…), with a progress window.

A log from elsewhere is never opened in place: it is copied into the station folder with SQLite's backup API
(core/logcopy.py), which replays its WAL and reports progress, and the original is not touched. The copy runs on a
worker thread; Cancel removes the partial file; the real name appears only after the integrity check.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QMessageBox, QProgressDialog, QWidget

from .. import i18n
from ..core import db as dbm
from ..core import logcopy, station
from .widgets import run_async

tr = i18n.tr
log = logging.getLogger("nwologger.ui.addlog")


class _Cancel:
    def __init__(self):
        self.flag = False

    def __call__(self) -> bool:
        return self.flag


def copy_with_progress(parent: QWidget | None, source: Path, dest: Path, *, title: str,
                       drop_guard: bool = False) -> Path | None:
    """Copy ``source`` to ``dest`` behind a progress window. Returns ``dest``, or None after Cancel or a failure
    (reported in a message box; the partial file is gone either way)."""
    dlg = QProgressDialog(tr("Copying the log…"), tr("Cancel"), 0, 100, parent)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    dlg.setValue(0)
    cancel = _Cancel()
    dlg.canceled.connect(lambda: setattr(cancel, "flag", True))
    result: dict = {}

    def progress(done: int, total: int, event=None) -> None:
        if event is not None:
            event((done, total))

    def work(event=None):
        return logcopy.copy_database(source, dest, progress=lambda d, t: progress(d, t, event), cancel=cancel,
                                     drop_guard=drop_guard)

    def on_event(payload) -> None:
        done, total = payload
        if total:
            dlg.setMaximum(total)
            dlg.setValue(done)
            dlg.setLabelText(tr("Copying the log…") + f"  {done * 100 // total} %")

    def on_done(path) -> None:
        result["path"] = path
        dlg.accept()

    def on_error(msg: str) -> None:
        result["error"] = msg
        dlg.reject()

    run_async(work, on_done=on_done, on_error=on_error, on_event=on_event)
    dlg.exec()
    if "path" in result:
        return Path(result["path"])
    err = result.get("error", "")
    if cancel.flag or "CopyCancelled" in err:
        log.info("Copy of %s cancelled", source.name)
        return None
    log.warning("Copy of %s failed: %s", source.name, err)
    QMessageBox.critical(parent, title, tr("The copy failed and was removed.") + f"\n\n{err}")
    return None


def add_log(parent: QWidget | None, source: Path, folder: Path | None = None) -> Path | None:
    """Copy ``source`` into the station folder under a name the user confirms. Returns the new log's path, or
    None when nothing was added (the source stays as it was in every case)."""
    folder = folder or station.folder()
    source = Path(source)
    try:
        dbm.check_log_file(source)
    except dbm.NotALogFile as e:
        QMessageBox.critical(parent, tr("Add log file"), str(e))
        return None
    suggested = logcopy.unused_name(folder, logcopy.suggested_name(source))
    name, ok = QInputDialog.getText(parent, tr("Add log file"),
                                    tr("Name for the copy in your station folder:"), text=suggested)
    if not ok or not name.strip():
        return None
    name = name.strip()
    if not name.lower().endswith(".sqlite"):
        name += ".sqlite"
    dest = folder / name
    if dest.exists():
        QMessageBox.warning(parent, tr("Add log file"), tr("A log with that name is already in the folder."))
        return None
    size = source.stat().st_size if source.exists() else 0
    if not logcopy.enough_space(size, folder):
        free = shutil.disk_usage(folder).free
        QMessageBox.critical(parent, tr("Add log file"),
                             tr("Not enough free space for the copy.") + f"\n{size / 1e6:.0f} MB → {free / 1e6:.0f} MB")
        return None
    log.info("Adding the log %s as %s", source, name)
    return copy_with_progress(parent, source, dest, title=tr("Add log file"))


def save_copy(parent: QWidget | None, db, dest: Path, *, for_other_programs: bool) -> Path | None:
    """A complete copy of the open log at ``dest``; without the write guard when it is for other programs."""
    try:
        db.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except Exception:  # noqa: BLE001
        pass
    return copy_with_progress(parent, db.path, Path(dest), title=tr("Save a copy"), drop_guard=for_other_programs)
