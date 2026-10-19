"""The log file says which NWOlogger made it and which last wrote it, so a log and every backup of it say where
they came from; another program's SQLite file is never turned into a log."""

from __future__ import annotations

import sqlite3

import pytest

from nwologger import __version__
from nwologger.core import db as dbm
from nwologger.core import qso as qsomod
from nwologger.core import timeutil


def test_a_new_log_says_who_made_it(tmp_path):
    db = dbm.LogDB(tmp_path / "a.sqlite", backup_on_open=False)
    try:
        made = db.provenance()
        assert made["app_name"] == "NWOlogger" and made["created_by_version"] == __version__
        assert made["last_written_by_version"] == __version__ and made["last_written_at"] and made["created_at"]
        assert db.conn.execute("PRAGMA application_id").fetchone()[0] == dbm.APPLICATION_ID
        assert db.conn.execute("PRAGMA user_version").fetchone()[0] == dbm.SCHEMA_VERSION
    finally:
        db.close()


def test_a_change_stamps_the_log_but_an_open_does_not(tmp_path, monkeypatch):
    path = tmp_path / "a.sqlite"
    db = dbm.LogDB(path, backup_on_open=False)
    first = db.meta("last_written_at")
    db.close()
    monkeypatch.setattr(timeutil, "now_iso", lambda: "2026-11-01T10:00:00Z")
    db = dbm.LogDB(path, backup_on_open=False)
    try:
        assert db.meta("last_written_at") == first  # opening is not writing
        db.insert(qsomod.normalize({"call": "BA1AA", "time_on": "2026-10-10T10:00:00Z", "mode": "FM", "band": "2m"}))
        assert db.meta("last_written_at") == "2026-11-01T10:00:00Z"
        monkeypatch.setattr(timeutil, "now_iso", lambda: "2026-11-01T10:00:30Z")
        with db.batch("more"):
            db.insert(qsomod.normalize({"call": "BA1BB", "time_on": "2026-10-10T10:01:00Z", "mode": "FM",
                                        "band": "2m"}))
        assert db.meta("last_written_at") == "2026-11-01T10:00:00Z"  # at most once a minute
    finally:
        db.close()


def test_an_old_log_gains_the_keys_without_changing_created_at(tmp_path):
    path = tmp_path / "old.sqlite"
    db = dbm.LogDB(path, backup_on_open=False)
    db.conn.execute("DELETE FROM meta WHERE key IN ('app_name', 'created_by_version', 'last_written_by_version', "
                    "'last_written_at')")
    db.conn.execute("UPDATE meta SET value='2025-01-02T03:04:05Z' WHERE key='created_at'")
    db.conn.execute("PRAGMA application_id = 0")
    db.conn.execute("PRAGMA user_version = 0")
    db.close()
    assert dbm.sqlite_kind(path) == "nwologger"  # recognised by its tables, before the application id
    db = dbm.LogDB(path, backup_on_open=False)
    try:
        made = db.provenance()
        assert made["created_at"] == "2025-01-02T03:04:05Z" and made["created_by_version"] == "0.1.8 or earlier"
        assert made["app_name"] == "NWOlogger" and made["last_written_by_version"] == __version__
        assert db.conn.execute("PRAGMA application_id").fetchone()[0] == dbm.APPLICATION_ID
    finally:
        db.close()


def test_a_backup_carries_the_keys(tmp_path):
    db = dbm.LogDB(tmp_path / "a.sqlite", backup_on_open=False)
    try:
        copy = db.backup("manual")
    finally:
        db.close()
    con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    try:
        meta = dict(con.execute("SELECT key, value FROM meta"))
        assert meta["app_name"] == "NWOlogger" and meta["created_by_version"] == __version__
        assert con.execute("PRAGMA application_id").fetchone()[0] == dbm.APPLICATION_ID
    finally:
        con.close()
    assert dbm.sqlite_kind(copy) == "nwologger"


def test_a_log_written_by_a_newer_nwologger_is_refused_and_left_alone(tmp_path):
    """The version gate (0.2.0): a lower version never edits a higher version's log; it is not even opened."""
    path = tmp_path / "new.sqlite"
    db = dbm.LogDB(path, backup_on_open=False)
    db.set_meta("schema_version", str(dbm.SCHEMA_VERSION + 1))
    db.set_meta("last_written_by_version", "0.9.0")
    db.close()
    before = path.read_bytes()
    with pytest.raises(dbm.NewerLog) as info:
        dbm.LogDB(path, backup_on_open=True)
    assert info.value.version == "0.9.0" and info.value.schema == dbm.SCHEMA_VERSION + 1
    assert "Update NWOlogger" in str(info.value)
    assert path.read_bytes() == before, "nothing written, not even provenance"
    assert not (tmp_path / "backups").exists(), "no launch backup of a log we refuse"
    assert not (tmp_path / "nwologger.lock").exists() or dbm.lease.held(tmp_path) is None
    # The same with only the header's user_version raised, as a future version would do.
    other = tmp_path / "other.sqlite"
    dbm.LogDB(other, backup_on_open=False).close()
    con = sqlite3.connect(other)
    con.execute(f"PRAGMA user_version = {dbm.SCHEMA_VERSION + 1}")
    con.close()
    with pytest.raises(dbm.NewerLog):
        dbm.LogDB(other, backup_on_open=False)


def _foreign(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE TABLE_HRD_CONTACTS_V07 (COL_PRIMARY_KEY INTEGER PRIMARY KEY, COL_CALL TEXT)")
    con.execute("INSERT INTO TABLE_HRD_CONTACTS_V07 (COL_CALL) VALUES ('BA1AA')")
    con.commit()
    con.close()
    return path.read_bytes()


def test_what_a_file_is(tmp_path):
    log = tmp_path / "log.sqlite"
    dbm.LogDB(log, backup_on_open=False).close()
    assert dbm.sqlite_kind(log) == "nwologger" and dbm.check_log_file(log) == "nwologger"
    assert dbm.sqlite_kind(tmp_path / "none.sqlite") == "missing" and dbm.check_log_file(tmp_path / "none.sqlite")
    (tmp_path / "zero.sqlite").write_bytes(b"")
    assert dbm.sqlite_kind(tmp_path / "zero.sqlite") == "missing"
    (tmp_path / "junk.sqlite").write_bytes(b"not a database at all" * 10)
    assert dbm.sqlite_kind(tmp_path / "junk.sqlite") is None
    with pytest.raises(dbm.NotALogFile, match="not a SQLite file"):
        dbm.check_log_file(tmp_path / "junk.sqlite")
    sqlite3.connect(tmp_path / "empty.sqlite").execute("PRAGMA user_version = 0")
    assert dbm.sqlite_kind(tmp_path / "empty.sqlite") in ("empty", "missing")
    mldx = tmp_path / "mldx.sql"
    con = sqlite3.connect(mldx)
    con.execute("CREATE TABLE qso_table_v008 (pk INTEGER PRIMARY KEY, call TEXT)")
    con.commit()
    con.close()
    assert dbm.sqlite_kind(mldx) == "mldx"
    with pytest.raises(dbm.NotALogFile, match="MacLoggerDX"):
        dbm.check_log_file(mldx)
    foreign = tmp_path / "hrd.sqlite"
    before = _foreign(foreign)
    assert dbm.sqlite_kind(foreign) == "foreign"
    with pytest.raises(dbm.NotALogFile, match="another program"):
        dbm.LogDB(foreign, backup_on_open=False)
    assert foreign.read_bytes() == before  # not a byte of it changed, and no tables of ours were added
    assert not (tmp_path / "backups").exists()
