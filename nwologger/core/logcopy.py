"""Copying a log file the right way: through SQLite's online backup, never a plain file copy.

A plain copy of a SQLite database loses whatever still sits in its ``-wal`` file (a log in use elsewhere, or left
by a crash) and can catch a half-written page. The backup API reads the source through SQLite, replays the WAL and
writes a complete, self-contained file, in steps whose progress it reports. The copy goes to a temporary name and
takes its real name only after ``quick_check`` passes; a failure or a cancel removes the partial file, so the
folder never holds a half-copied log under a real name. There is no resume: a restarted copy starts over.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("nwologger.copy")
PAGES = 200  # pages per step: with 4 KB pages about 0.8 MB, so a 200 MB log reports some 250 steps
Progress = Callable[[int, int], None]  # (pages copied so far, pages in all)


class CopyCancelled(Exception):
    pass


class CopyFailed(Exception):
    pass


def partial_name(dest: Path) -> Path:
    return dest.with_name(f"{dest.name}.part-{os.getpid()}")


def open_source(src: Path) -> sqlite3.Connection:
    """Read-only; ``immutable`` when the folder refuses the ``-shm`` a WAL reader needs (a read-only stick)."""
    uri = f"file:{src.resolve()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return con
    except sqlite3.Error:
        log.info("Reading %s as immutable: its folder refuses a read-only WAL open", src.name)
        return sqlite3.connect(uri + "&immutable=1", uri=True)


def enough_space(src_size: int, folder: Path) -> bool:
    try:
        return shutil.disk_usage(folder).free > src_size + 50 * 1024 * 1024
    except OSError:
        return True


def copy_database(src: Path | sqlite3.Connection, dest: Path, *, progress: Progress | None = None,
                  cancel: Callable[[], bool] | None = None, drop_guard: bool = False, pages: int = PAGES) -> Path:
    """Copy ``src`` (a path, or an open connection) to ``dest`` and return ``dest``. ``progress`` gets (done, total)
    pages; ``cancel`` is asked between steps. ``drop_guard`` leaves the write guard (core/db.py) out of the copy, for
    a copy meant for other programs."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = partial_name(dest)
    tmp.unlink(missing_ok=True)
    own_source = not isinstance(src, sqlite3.Connection)
    source = open_source(Path(src)) if own_source else src
    out = sqlite3.connect(str(tmp))
    try:
        def step(status: int, remaining: int, total: int) -> None:
            if progress is not None:
                progress(total - remaining, total)
            if cancel is not None and cancel():
                raise CopyCancelled()

        source.backup(out, pages=pages, progress=step)
        if drop_guard:
            for (name,) in out.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'nwo_guard_%'"
                                       ).fetchall():
                out.execute(f'DROP TRIGGER IF EXISTS "{name}"')
            out.commit()
        verdict = out.execute("PRAGMA quick_check").fetchone()[0]
        if verdict != "ok":
            raise CopyFailed(f"the copy failed its integrity check: {verdict}")
        out.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except BaseException:
        out.close()
        if own_source:
            source.close()
        for p in (tmp, tmp.with_name(tmp.name + "-wal"), tmp.with_name(tmp.name + "-shm")):
            p.unlink(missing_ok=True)
        raise
    out.close()
    if own_source:
        source.close()
    for p in (tmp.with_name(tmp.name + "-wal"), tmp.with_name(tmp.name + "-shm")):
        p.unlink(missing_ok=True)
    os.replace(tmp, dest)
    log.info("Log copied to %s", dest)
    return dest


def suggested_name(src: Path) -> str:
    """A file name for a log added to the station folder: its default station profile's callsign, else its own
    name."""
    try:
        con = open_source(Path(src))
        try:
            row = con.execute("SELECT name, data FROM station_profile ORDER BY is_default DESC, id LIMIT 1").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        row = None
    if row:
        import json  # noqa: PLC0415

        try:
            call = json.loads(row[1]).get("station_callsign") or row[0]
        except (ValueError, AttributeError):
            call = row[0]
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in str(call)).strip("-")
        if safe:
            return safe + ".sqlite"
    return Path(src).name


def unused_name(folder: Path, name: str) -> str:
    stem, suffix = Path(name).stem, Path(name).suffix or ".sqlite"
    candidate = stem + suffix
    n = 2
    while (folder / candidate).exists():
        candidate = f"{stem}-{n}{suffix}"
        n += 1
    return candidate


def sweep_partials(folder: Path) -> list[Path]:
    """Remove ``*.part-<pid>`` files left by a copy that died, when that pid is gone."""
    removed = []
    for p in folder.glob("*.part-*"):
        try:
            pid = int(p.name.rsplit("-", 1)[1])
        except ValueError:
            continue
        if pid == os.getpid() or _alive(pid):
            continue
        try:
            p.unlink()
            removed.append(p)
            log.info("Removed the partial copy %s left by a run that ended", p.name)
        except OSError:
            pass
    return removed


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
