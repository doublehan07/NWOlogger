"""The backup copy in ``Documents/NWOlogger``: one complete copy of the current log, written with SQLite's backup
API to a temporary name and renamed into place, so a cloud sync client only ever sees a whole file. Written on the
hourly check when the log changed since the last copy, once more at exit, and right after a migration or an added
log. It carries the write guard like every log of ours (a backup is not for editing), and a README says what it is.
Up to 0.1.9 the live log itself lived here.

The copy reads the log through a connection of its own, so the UI can run it on a worker thread; ``plan`` and
``mark`` touch the LogDB and belong to the GUI thread.
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .. import APP_NAME, __version__
from . import logcopy, station

log = logging.getLogger("nwologger.mirror")
README = "README.txt"
_last: dict[Path, int] = {}  # log path → change count at the last mirror, this process


@dataclass
class Plan:
    source: Path
    dest: Path
    readme: str
    count: int


def path_for(db, folder: Path | None = None) -> Path:
    return (folder or station.mirror_dir()) / db.path.name


def due(db) -> bool:
    """Whether the log changed since this process last mirrored it (or never did)."""
    return _last.get(db.path) != db.change_count


def plan(db, folder: Path | None = None) -> Plan:
    """What to copy where (GUI thread): folds the WAL first so the copy is one file's worth of pages."""
    try:
        db.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except Exception:  # noqa: BLE001
        pass
    return Plan(db.path, path_for(db, folder), readme_text(db.path.parent), db.change_count)


def write_file(p: Plan) -> Path:
    """Copy and write the README (any thread)."""
    p.dest.parent.mkdir(parents=True, exist_ok=True)
    logcopy.copy_database(p.source, p.dest)
    try:
        (p.dest.parent / README).write_text(p.readme, encoding="utf-8")
    except OSError:
        log.debug("Could not write the mirror README", exc_info=True)
    log.info("Backup copy written: %s", p.dest)
    return p.dest


def mark(db, count: int) -> None:
    _last[db.path] = count


def write(db, folder: Path | None = None) -> Path:
    """Copy now, on this thread, and remember it (migration, an added log, exit)."""
    p = plan(db, folder)
    dest = write_file(p)
    mark(db, p.count)
    return dest


def write_if_changed(db, folder: Path | None = None) -> Path | None:
    if not due(db):
        return None
    try:
        return write(db, folder)
    except Exception as e:  # noqa: BLE001 - a backup copy that fails must never stop the app
        log.warning("Backup copy failed: %s", e, exc_info=True)
        return None


def readme_text(live_folder: Path) -> str:
    return (
        f"This folder holds a backup copy of your {APP_NAME} log, made by {APP_NAME} {__version__} on "
        f"{socket.gethostname()} at {datetime.now(UTC).isoformat(timespec='seconds')} UTC.\n"
        f"\n"
        f"The live log is in {live_folder}\n"
        f"The copy is refreshed about once an hour while {APP_NAME} runs and when it quits, so it is at most an hour\n"
        f"behind.\n"
        f"\n"
        f"Do not edit this copy. It is a complete SQLite database that any program can read. Its tables carry a\n"
        f"write guard (triggers named nwo_guard_*) so that other programs cannot change it by accident; to edit it\n"
        f"elsewhere, drop them in any SQLite tool:\n"
        f"    SELECT 'DROP TRIGGER \"' || name || '\";' FROM sqlite_master WHERE type='trigger' AND name LIKE 'nwo_guard_%';\n"
        f"or use File → Save a copy… with \"for other programs\" in {APP_NAME}.\n"
        f"\n"
        f"To restore from this copy: File → Add log file… in {APP_NAME} and pick it; it is copied back into the live\n"
        f"folder and opened.\n"
        f"\n"
        f"If this folder is synced to the cloud and used from more than one computer, each computer writes its own\n"
        f"copy under the same name and the sync client may keep conflicting copies; the live logs on each computer\n"
        f"are not affected. Use one computer at a time, at your own risk otherwise.\n"
    )
