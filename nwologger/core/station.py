"""The station folder: one folder per computer that holds everything NWOlogger has.

Logs (``*.sqlite``), ``backups/``, ``downloads/<log name>/``, ``settings.ini``, ``logs/`` (the diagnostic log and
crash file) and ``nwologger.lock`` all live here. It sits in the operating system's application-data location,
which cloud sync clients (iCloud Desktop & Documents, OneDrive folder backup, Dropbox) never touch, so the live
database is never copied half-written. ``Documents/NWOlogger`` keeps one complete copy of the current log as a
backup (core/mirror.py); up to 0.1.9 the log itself lived there.

Nothing is stored outside the folder: no plist, registry key or pointer. The folder is a constant, so none is
needed. ``--db <file>`` (tests, developers) makes that file's folder the station folder for the run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .. import APP_NAME

LOG_NAME = "nwolog.sqlite"
SETTINGS_NAME = "settings.ini"
LOCK_NAME = "nwologger.lock"
DOCUMENTS_FOLDER = Path.home() / "Documents" / APP_NAME  # the mirror's place, and the log's place up to 0.1.9

_folder: Path | None = None


def default_folder() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME.lower()


def configure(db_path: str | os.PathLike | None = None) -> Path:
    """Decide the station folder for this process: the given log file's folder, or the default."""
    global _folder
    _folder = Path(db_path).expanduser().resolve().parent if db_path else default_folder()
    return _folder


def folder() -> Path:
    return _folder if _folder is not None else default_folder()


def configured() -> bool:
    return _folder is not None


def settings_path() -> Path:
    return folder() / SETTINGS_NAME


def logs_dir() -> Path:
    return folder() / "logs"


def lock_path(where: str | os.PathLike | None = None) -> Path:
    return (Path(where) if where is not None else folder()) / LOCK_NAME


def mirror_dir() -> Path:
    """Where the backup copy goes: Documents/NWOlogger. NWOLOGGER_MIRROR_DIR overrides (tests must never write into
    the real Documents folder)."""
    env = os.environ.get("NWOLOGGER_MIRROR_DIR")
    return Path(env) if env else DOCUMENTS_FOLDER
