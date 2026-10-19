"""The one-time move to the station folder (0.2.0). Up to 0.1.9 the log lived in Documents/NWOlogger (or wherever
Open log file had pointed) and the preferences in the system's store. The first start of 0.2.0 asks, then copies
the log into the station folder with the backup API (progress bar), moves backups/ and downloads/ along, moves the
preferences into settings.ini, and writes the first backup copy back into Documents. The old file is replaced by
that copy and not touched by one byte before; older NWOlogger versions cannot edit the new log (the guard)."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox, QPushButton, QWidget

from .. import APP_NAME, i18n
from ..core import db as dbm
from ..core import station
from . import add_log
from .prefs import Prefs

tr = i18n.tr
log = logging.getLogger("nwologger.migrate")


def old_log() -> Path | None:
    """The 0.1.x log to bring along: the one the old preferences point at, else Documents/NWOlogger/nwolog.sqlite,
    when it exists outside the station folder."""
    old = Prefs.native_store()
    candidates = []
    pointed = old.value("db_path")
    if pointed:
        candidates.append(Path(str(pointed)).expanduser())
    candidates.append(dbm.DEFAULT_PATH)
    folder = station.folder().resolve()
    for p in candidates:
        try:
            if p.is_file() and p.stat().st_size > 0 and p.resolve().parent != folder:
                return p
        except OSError:
            continue
    return None


def needed() -> Path | None:
    """A migration is due when the station folder has no settings.ini yet and an old log exists elsewhere."""
    if station.settings_path().exists():
        return None
    return old_log()


def _move_tree(src: Path, dest: Path) -> None:
    if not src.is_dir():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dest)  # instant on the same volume
    except OSError:
        shutil.copytree(src, dest, dirs_exist_ok=True)
        shutil.rmtree(src, ignore_errors=True)


def run(parent: QWidget | None, source: Path) -> Path | None:
    """Ask, copy, move the companions and the preferences. Returns the new log's path, or None when the user chose
    to quit or the copy did not complete (then nothing has changed)."""
    folder = station.folder()
    box = QMessageBox(parent)
    box.setWindowTitle(APP_NAME)
    box.setIcon(QMessageBox.Icon.Information)
    box.setText(tr("NWOlogger 0.2.0 keeps your log in the application-data folder and leaves a backup copy in "
                   "Documents/NWOlogger."))
    box.setInformativeText(tr("Your log") + f"\n{source}\n" +
                           tr("will be copied into") + f"\n{folder}\n\n" +
                           tr("The original file is not changed. Older NWOlogger versions (0.1.x) will not be able to "
                              "edit the new log. Migrate now?"))
    go = box.addButton(tr("Migrate"), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(tr("Quit"), QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is not go:
        log.info("Migration declined; quitting without changes")
        return None
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / source.name
    if dest.exists():
        dest = folder / (source.stem + "-migrated.sqlite")
    copied = add_log.copy_with_progress(parent, source, dest, title=tr("Migrating the log"))
    if copied is None:
        return None
    old_folder = source.parent
    _move_tree(old_folder / "backups", folder / "backups")
    # downloads were flat beside the log; since 0.2.0 each log has a folder of its own
    _move_tree(old_folder / "downloads", folder / "downloads" / copied.stem)
    for extra in ("MASTER.SCP", "lotw-user-activity.csv", "eqsl-ag.txt"):
        p = old_folder / extra
        if p.is_file() and not (folder / extra).exists():
            try:
                shutil.copy2(p, folder / extra)
            except OSError:
                pass
    moved = Prefs.migrate_native(QSettings(str(station.settings_path()), QSettings.Format.IniFormat))
    s = QSettings(str(station.settings_path()), QSettings.Format.IniFormat)
    s.setValue("current_log", copied.name)
    s.remove("db_path")
    s.sync()
    log.info("Migrated %s to %s; %d preference key(s) moved into settings.ini", source, copied, len(moved))
    return copied


def tell(parent: QWidget | None, live: Path, mirror_path: Path | None) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(APP_NAME)
    box.setIcon(QMessageBox.Icon.Information)
    box.setText(tr("Done. The live log is now at") + f"\n{live.parent}")
    text = tr("The file in Documents/NWOlogger is a backup copy, refreshed about once an hour and when NWOlogger "
              "quits, so it is at most an hour behind.") + "\n\n" + \
        tr("Older NWOlogger versions (0.1.x) can no longer edit it. Please use 0.2.0 or newer only.")
    if mirror_path is None:
        text += "\n\n" + tr("The first backup copy could not be written yet; it will be tried again within the hour.")
    box.setInformativeText(text)
    box.addButton(QPushButton(tr("OK")), QMessageBox.ButtonRole.AcceptRole)
    box.exec()
