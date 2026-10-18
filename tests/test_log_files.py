"""File → Import and File → Open log file tell a log of ours, another program's database and a file without ADIF
records apart, instead of showing 0 QSOs or writing our tables into a foreign file."""

from __future__ import annotations

import sqlite3

import pytest
from PySide6.QtCore import QSettings

from nwologger.core import db as dbm
from nwologger.ui.import_dialog import ImportDialog
from nwologger.ui.prefs import Prefs


@pytest.fixture
def log(tmp_path, qapp):  # the import dialog is a widget: a QApplication must exist
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    yield db, prefs
    db.close()


def _foreign(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE TABLE_HRD_CONTACTS_V07 (COL_PRIMARY_KEY INTEGER PRIMARY KEY, COL_CALL TEXT)")
    con.execute("INSERT INTO TABLE_HRD_CONTACTS_V07 (COL_CALL) VALUES ('BA1AA')")
    con.commit()
    con.close()
    return path.read_bytes()


def test_import_points_a_log_of_ours_to_open_log_file(log, tmp_path):
    db, prefs = log
    other = tmp_path / "friend.sqlite"
    dbm.LogDB(other, backup_on_open=False).close()
    with pytest.raises(ValueError, match="Open log file"):
        ImportDialog(db, prefs, str(other))


def test_import_refuses_another_programs_database_whatever_its_extension(log, tmp_path):
    db, prefs = log
    for name in ("logbook.hrdsql", "logbook.sqlite", "logbook.dat"):
        path = tmp_path / name
        before = _foreign(path)
        with pytest.raises(ValueError, match="cannot import"):
            ImportDialog(db, prefs, str(path))
        assert path.read_bytes() == before


def test_import_of_a_file_without_adif_records_says_so(log, tmp_path):
    db, prefs = log
    junk = tmp_path / "junk.adi"
    junk.write_bytes(bytes(range(256)) * 20)
    with pytest.raises(ValueError, match="No ADIF records"):
        ImportDialog(db, prefs, str(junk))
    empty = tmp_path / "empty.adi"
    empty.write_text("Just a header\n<eoh>\n", encoding="utf-8")
    with pytest.raises(ValueError, match="No ADIF records"):
        ImportDialog(db, prefs, str(empty))


def test_an_older_log_of_ours_still_opens_and_migrates(tmp_path):
    path = tmp_path / "old.sqlite"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES ('schema_version', '1'), ('created_at', '2025-06-01T00:00:00Z');
        CREATE TABLE qso (id INTEGER PRIMARY KEY, uuid TEXT, created_at TEXT, updated_at TEXT, call TEXT,
                          time_on TEXT, band TEXT, mode TEXT);
        INSERT INTO qso (uuid, created_at, updated_at, call, time_on, band, mode)
            VALUES ('u1', '2025-06-01T00:00:00Z', '2025-06-01T00:00:00Z', 'BA1AA', '2025-06-01T00:00:00Z',
                    '2m', 'FM');
    """)
    con.commit()
    con.close()
    assert dbm.sqlite_kind(path) == "nwologger"
    db = dbm.LogDB(path, backup_on_open=False)
    try:
        assert db.count() == 1 and db.get(1)["call"] == "BA1AA" and db.get(1)["gridsquare"] is None  # column added
        assert db.meta("created_at") == "2025-06-01T00:00:00Z" and db.meta("app_name") == "NWOlogger"
    finally:
        db.close()

