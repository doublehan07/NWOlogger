"""0.2.0: one station folder, one writing NWOlogger per folder (the lease), the write guard in the log file, the
version gate, copies through SQLite's backup API, and the backup copy in Documents (core/station.py, lease.py,
logcopy.py, mirror.py, db.py). The decisions behind them are in docs/data-safety.md."""

from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nwologger import applog
from nwologger.core import db as dbm
from nwologger.core import lease, logcopy, mirror, station

ROOT = Path(__file__).resolve().parent.parent


def _log(path: Path, calls=("BA1AA", "BA1BB")) -> Path:
    db = dbm.LogDB(path, backup_on_open=False)
    db.save_profile({"name": "BI1ZZZ", "station_callsign": "BI1ZZZ", "operator": "BI1ZZZ", "my_gridsquare": "ON80",
                     "is_default": True})
    for i, c in enumerate(calls):
        db.insert({"call": c, "time_on": f"2026-10-18T10:0{i}:00Z", "band": "20m", "mode": "SSB", "freq": 14.2})
    db.close()
    return path


# ------------------------------------------------------------------ the station folder
def test_the_station_folder_layout(tmp_path, monkeypatch):
    folder = station.configure(tmp_path / "station" / "nwolog.sqlite")
    assert folder == (tmp_path / "station").resolve()
    assert station.settings_path() == folder / "settings.ini"
    assert station.logs_dir() == folder / "logs"
    assert station.lock_path() == folder / "nwologger.lock"
    monkeypatch.delenv("NWOLOGGER_LOG_DIR", raising=False)
    assert applog.log_dir() == folder / "logs", "the diagnostic log lives in the station folder"
    assert station.mirror_dir() == Path.home() / "Documents" / "NWOlogger"
    default = station.default_folder()
    assert "Documents" not in default.parts, "the live log is never under the OS's cloud-synced folders"


def test_preferences_live_in_settings_ini_and_move_in_from_the_old_store(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from nwologger.ui.prefs import Prefs

    station.configure(tmp_path / "nwolog.sqlite")
    monkeypatch.delenv("NWOLOGGER_SETTINGS", raising=False)
    old = QSettings(str(tmp_path / "old-native.ini"), QSettings.Format.IniFormat)
    old.setValue("language", "zh_CN")
    old.setValue("db_path", "/somewhere/else.sqlite")
    old.setValue("rigs", json.dumps(["IC-705"]))
    old.sync()
    monkeypatch.setattr(Prefs, "native_store", staticmethod(lambda: QSettings(str(tmp_path / "old-native.ini"),
                                                                              QSettings.Format.IniFormat)))
    moved = Prefs.migrate_native()
    assert set(moved) == {"language", "db_path", "rigs"}
    prefs = Prefs()
    assert Path(prefs.s.fileName()) == tmp_path / "settings.ini"  # QSettings reports forward slashes on Windows
    assert prefs.get("language") == "zh_CN" and prefs.get("rigs") == ["IC-705"]
    assert prefs.get("current_log") == "nwolog.sqlite"
    again = QSettings(str(tmp_path / "old-native.ini"), QSettings.Format.IniFormat)
    assert again.allKeys() == [], "the old store is cleared: nothing of ours stays outside the folder"
    assert Prefs.migrate_native() == []


# ------------------------------------------------------------------ the lease
def test_the_lease_is_shared_within_a_process_and_released_with_the_last_log(tmp_path):
    a = dbm.LogDB(tmp_path / "a.sqlite", backup_on_open=False)
    b = dbm.LogDB(tmp_path / "b.sqlite", backup_on_open=False)
    assert a.lease is b.lease and a.lease.count == 2
    holder = lease.read_holder(tmp_path / "nwologger.lock")
    assert holder["pid"] == os.getpid() and holder["host"] == socket.gethostname()
    a.close()
    assert lease.held(tmp_path) is not None
    b.close()
    assert lease.held(tmp_path) is None
    assert (tmp_path / "nwologger.lock").exists(), "the lock file stays in place; only the OS lock is released"


HOLDER = textwrap.dedent("""
    import os, sys, time
    from nwologger.core import lease
    lease.acquire(sys.argv[1])
    print("held", os.getpid(), flush=True)
    time.sleep(float(sys.argv[2]))
""")


def test_another_process_cannot_open_the_folder_while_it_is_held(tmp_path):
    _log(tmp_path / "nwolog.sqlite")
    proc = subprocess.Popen([sys.executable, "-c", HOLDER, str(tmp_path), "30"], cwd=ROOT, stdout=subprocess.PIPE,
                            text=True)
    holder_pid = proc.pid
    try:
        word, holder_pid = proc.stdout.readline().split()  # the venv's python.exe on Windows is a launcher: its
        holder_pid = int(holder_pid)  # child is the interpreter that holds the lock, so take the pid it prints
        assert word == "held"
        with pytest.raises(dbm.LeaseHeld) as info:
            dbm.LogDB(tmp_path / "nwolog.sqlite", backup_on_open=False)
        assert info.value.holder.get("pid") == holder_pid
        assert "pid" in str(info.value) and "in use by another NWOlogger" in str(info.value)
        # Reading is never blocked: the lease is about writers.
        con = sqlite3.connect(f"file:{tmp_path / 'nwolog.sqlite'}?mode=ro", uri=True)
        assert con.execute("SELECT COUNT(*) FROM qso").fetchone()[0] == 2
        con.close()
    finally:
        proc.kill()
        proc.wait(timeout=30)
        try:
            os.kill(holder_pid, signal.SIGTERM)  # the interpreter behind a launcher stub, if it outlived it
        except (OSError, ProcessLookupError):
            pass
    # The holder died (a crash, a kill): the OS released the lock, the next open simply takes it.
    deadline = time.monotonic() + 15
    while True:
        try:
            db = dbm.LogDB(tmp_path / "nwolog.sqlite", backup_on_open=False)
            break
        except dbm.LeaseHeld:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)
    assert db.lease is not None
    db.close()


def test_the_other_computer_hint(tmp_path):
    lock = tmp_path / "nwologger.lock"
    fresh = {"pid": 1, "host": "other-mac", "version": "0.2.0", "started": "2026-10-19T10:00:00+00:00",
             "beat": datetime.now(UTC).isoformat(timespec="seconds")}
    lock.write_text(json.dumps(fresh), encoding="utf-8")
    assert lease.other_computer(tmp_path)["host"] == "other-mac"
    stale = dict(fresh, beat=(datetime.now(UTC) - timedelta(hours=2)).isoformat(timespec="seconds"))
    lock.write_text(json.dumps(stale), encoding="utf-8")
    assert lease.other_computer(tmp_path) is None
    mine = dict(fresh, host=socket.gethostname())
    lock.write_text(json.dumps(mine), encoding="utf-8")
    assert lease.other_computer(tmp_path) is None
    lock.write_text("not json", encoding="utf-8")
    assert lease.other_computer(tmp_path) is None
    assert "other-mac" in lease.describe(fresh) and "since 2026-10-19 10:00" in lease.describe(fresh)


# ------------------------------------------------------------------ the write guard
def test_the_guard_stops_other_programs_writing_but_never_reading(tmp_path):
    path = _log(tmp_path / "nwolog.sqlite")
    con = sqlite3.connect(path)
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE "
                                        "'sqlite_%'")]
    triggers = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")]
    assert len(triggers) == 3 * len(tables) and all(t.startswith(dbm.GUARD_PREFIX) for t in triggers)
    assert con.execute("SELECT COUNT(*) FROM qso").fetchone()[0] == 2, "reading works as always"
    for sql in ("INSERT INTO meta(key, value) VALUES('x', 'y')", "UPDATE qso SET call='XX1XX'",
                "DELETE FROM qso", "UPDATE meta SET value='v' WHERE key='app_name'"):
        with pytest.raises(sqlite3.OperationalError, match="no such function"):
            con.execute(sql)
    assert con.execute("SELECT COUNT(*) FROM qso").fetchone()[0] == 2
    assert con.execute("SELECT call FROM qso ORDER BY id").fetchall() == [("BA1AA",), ("BA1BB",)]
    # Recovery: DDL fires no trigger, so any SQLite tool lifts the guard in one statement per trigger.
    for t in triggers:
        con.execute(f'DROP TRIGGER "{t}"')
    con.execute("DELETE FROM qso WHERE call='BA1BB'")
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM qso").fetchone()[0] == 1
    con.close()
    # And NWOlogger puts it back on the next open, writing as before.
    db = dbm.LogDB(path, backup_on_open=False)
    assert len(dbm.guard_statements(db.conn)) == 0
    db.insert({"call": "BA1CC", "time_on": "2026-10-18T11:00:00Z", "band": "20m", "mode": "SSB", "freq": 14.2})
    assert db.count() == 2
    db.close()


def test_the_guard_costs_about_a_page(tmp_path):
    path = tmp_path / "nwolog.sqlite"
    db = dbm.LogDB(path, backup_on_open=False)
    page = db.conn.execute("PRAGMA page_size").fetchone()[0]
    con = sqlite3.connect(path)
    sql = sum(len(r[0]) for r in con.execute("SELECT sql FROM sqlite_master WHERE type='trigger'"))
    con.close()
    db.close()
    assert sql < 2 * page, f"{sql} bytes of trigger SQL"


def test_a_backup_carries_the_guard_and_a_copy_for_other_programs_does_not(tmp_path):
    path = _log(tmp_path / "nwolog.sqlite")
    db = dbm.LogDB(path, backup_on_open=False)
    backup = db.backup("manual")
    db.close()
    con = sqlite3.connect(backup)
    assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0] > 0
    con.close()
    plain = logcopy.copy_database(path, tmp_path / "for-others.sqlite", drop_guard=True)
    con = sqlite3.connect(plain)
    assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0] == 0
    con.execute("UPDATE qso SET call='XX1XX'")
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM qso WHERE call='XX1XX'").fetchone()[0] == 2
    con.close()


# ------------------------------------------------------------------ copies through the backup API
def test_a_copy_includes_what_still_sits_in_the_wal(tmp_path):
    """A plain file copy would lose the QSOs a running (or crashed) NWOlogger left in nwolog.sqlite-wal."""
    path = _log(tmp_path / "nwolog.sqlite")
    live = sqlite3.connect(path)
    live.execute("PRAGMA journal_mode=WAL")
    dbm.register_writer(live)
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.execute("INSERT INTO meta(key, value) VALUES('in-wal', 'yes')")
    live.commit()
    assert (tmp_path / "nwolog.sqlite-wal").stat().st_size > 0
    steps = []
    dest = logcopy.copy_database(path, tmp_path / "copy.sqlite", progress=lambda d, t: steps.append((d, t)), pages=1)
    live.close()
    assert steps and steps[-1][0] == steps[-1][1] > 1
    con = sqlite3.connect(dest)
    assert con.execute("SELECT value FROM meta WHERE key='in-wal'").fetchone()[0] == "yes"
    assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    con.close()
    assert not list(tmp_path.glob("*.part-*"))


def test_a_cancelled_or_failed_copy_leaves_nothing_behind(tmp_path):
    path = _log(tmp_path / "nwolog.sqlite")
    with pytest.raises(logcopy.CopyCancelled):
        logcopy.copy_database(path, tmp_path / "copy.sqlite", cancel=lambda: True, pages=1)
    assert not (tmp_path / "copy.sqlite").exists() and not list(tmp_path.glob("*.part-*"))
    with pytest.raises(sqlite3.Error):
        logcopy.copy_database(tmp_path / "missing.sqlite", tmp_path / "copy2.sqlite")
    assert not (tmp_path / "copy2.sqlite").exists() and not list(tmp_path.glob("*.part-*"))


def test_names_for_added_logs_and_the_sweep_of_dead_partials(tmp_path):
    path = _log(tmp_path / "friend.sqlite")
    assert logcopy.suggested_name(path) == "BI1ZZZ.sqlite"
    (tmp_path / "BI1ZZZ.sqlite").write_text("")
    assert logcopy.unused_name(tmp_path, "BI1ZZZ.sqlite") == "BI1ZZZ-2.sqlite"
    dead = tmp_path / "x.sqlite.part-999999"
    dead.write_text("half")
    mine = tmp_path / f"y.sqlite.part-{os.getpid()}"
    mine.write_text("mine")
    removed = logcopy.sweep_partials(tmp_path)
    assert removed == [dead] and mine.exists()


# ------------------------------------------------------------------ the backup copy in Documents
def test_the_mirror_is_a_complete_guarded_copy_written_only_when_the_log_changed(tmp_path):
    path = _log(tmp_path / "station" / "nwolog.sqlite")
    docs = tmp_path / "Documents" / "NWOlogger"
    db = dbm.LogDB(path, backup_on_open=False)
    assert mirror.due(db)
    dest = mirror.write(db, docs)
    assert dest == docs / "nwolog.sqlite" and (docs / mirror.README).exists()
    readme = (docs / mirror.README).read_text(encoding="utf-8")
    assert str(path.parent) in readme and "DROP TRIGGER" in readme and "own risk" in readme
    con = sqlite3.connect(dest)
    assert con.execute("SELECT COUNT(*) FROM qso").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0] > 0
    assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    con.close()
    assert not mirror.due(db)
    assert mirror.write_if_changed(db, docs) is None
    stamp = dest.stat().st_mtime_ns
    db.insert({"call": "BA1CC", "time_on": "2026-10-18T11:00:00Z", "band": "20m", "mode": "SSB", "freq": 14.2})
    assert mirror.due(db)
    time.sleep(0.01)
    assert mirror.write_if_changed(db, docs) == dest and dest.stat().st_mtime_ns != stamp
    assert [p.name for p in docs.glob("*.sqlite")] == ["nwolog.sqlite"], "exactly one .sqlite in Documents"
    db.close()


def test_downloads_have_a_folder_per_log(tmp_path):
    a = dbm.LogDB(tmp_path / "a.sqlite", backup_on_open=False)
    b = dbm.LogDB(tmp_path / "b.sqlite", backup_on_open=False)
    assert a.downloads_dir == tmp_path / "downloads" / "a" and b.downloads_dir == tmp_path / "downloads" / "b"
    a.close()
    b.close()


def test_the_idle_checkpoint_folds_the_wal_into_the_file(tmp_path):
    db = dbm.LogDB(tmp_path / "nwolog.sqlite", backup_on_open=False)
    db.insert({"call": "BA1AA", "time_on": "2026-10-18T10:00:00Z", "band": "20m", "mode": "SSB", "freq": 14.2})
    wal = tmp_path / "nwolog.sqlite-wal"
    assert wal.stat().st_size > 0
    db.idle_checkpoint()
    assert wal.stat().st_size == 0
    db.close()
