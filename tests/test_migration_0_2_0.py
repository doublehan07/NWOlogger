"""The one-time move into the station folder (ui/migrate.py) and the add-by-copy dialog (ui/add_log.py)."""

from __future__ import annotations

import json
import sqlite3

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QInputDialog, QMessageBox

from nwologger.core import db as dbm
from nwologger.core import station
from nwologger.ui import add_log, migrate
from nwologger.ui.prefs import Prefs


def _old_log(path, calls=("BA1AA", "BA1BB")):
    db = dbm.LogDB(path, backup_on_open=False, hold_lease=False)
    db.save_profile({"name": "BI1ZZZ", "station_callsign": "BI1ZZZ", "operator": "BI1ZZZ", "my_gridsquare": "ON80",
                     "is_default": True})
    for i, c in enumerate(calls):
        db.insert({"call": c, "time_on": f"2026-10-18T10:0{i}:00Z", "band": "20m", "mode": "SSB", "freq": 14.2})
    db.close()
    con = sqlite3.connect(path)  # make it look like a 0.1.x log: no guard
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
        con.execute(f'DROP TRIGGER "{name}"')
    con.commit()
    con.close()
    return path


@pytest.fixture
def old_world(tmp_path, monkeypatch):
    """A 0.1.x installation: the log in Documents/NWOlogger with backups and downloads, preferences in the system."""
    documents = tmp_path / "Documents" / "NWOlogger"
    documents.mkdir(parents=True)
    old = _old_log(documents / "nwolog.sqlite")
    (documents / "backups").mkdir()
    (documents / "backups" / "nwolog-20261001-120000-000000-open.sqlite").write_bytes(old.read_bytes())
    (documents / "downloads").mkdir()
    (documents / "downloads" / "lotw-20261018-112513-2023.adi").write_text("<eor>")
    native = QSettings(str(tmp_path / "native.ini"), QSettings.Format.IniFormat)
    native.setValue("language", "en")
    native.setValue("db_path", str(old))
    native.setValue("sticky", json.dumps(["freq"]))
    native.sync()
    monkeypatch.setattr(Prefs, "native_store", staticmethod(lambda: QSettings(str(tmp_path / "native.ini"),
                                                                              QSettings.Format.IniFormat)))
    monkeypatch.setattr(dbm, "DEFAULT_PATH", old)
    monkeypatch.setattr(station, "DOCUMENTS_FOLDER", documents)
    monkeypatch.delenv("NWOLOGGER_SETTINGS", raising=False)
    station_folder = tmp_path / "AppData" / "NWOlogger"
    monkeypatch.setattr(station, "_folder", station_folder)
    return old, documents, station_folder


def _click(monkeypatch, which: int):
    """Make every QMessageBox pick its ``which``-th added button."""
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: self.buttons()[which])


def test_the_migration_copies_the_log_moves_the_companions_and_the_preferences(qapp, old_world, monkeypatch):
    old, documents, folder = old_world
    before = old.read_bytes()
    assert migrate.needed() == old
    _click(monkeypatch, 0)  # Migrate
    new = migrate.run(None, old)
    assert new == folder / "nwolog.sqlite" and new.exists()
    assert old.read_bytes() == before, "the original is untouched until the first mirror replaces it"
    assert (folder / "backups" / "nwolog-20261001-120000-000000-open.sqlite").exists()
    assert (folder / "downloads" / "nwolog" / "lotw-20261018-112513-2023.adi").exists()
    assert not (documents / "backups").exists() and not (documents / "downloads").exists()
    s = QSettings(str(folder / "settings.ini"), QSettings.Format.IniFormat)
    assert s.value("current_log") == "nwolog.sqlite" and s.value("language") == "en"
    assert s.value("db_path") is None, "the pointer is gone: nothing outside the folder decides the log"
    assert Prefs.native_store().allKeys() == []
    con = sqlite3.connect(new)
    assert con.execute("SELECT COUNT(*) FROM qso").fetchone()[0] == 2
    assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    con.close()
    assert migrate.needed() is None, "settings.ini exists now: no second migration"
    # The new log opens with the guard and the lease; 0.1.x-style writes to it fail.
    db = dbm.LogDB(new, backup_on_open=False)
    assert db.lease is not None
    plain = sqlite3.connect(new)
    with pytest.raises(sqlite3.OperationalError, match="no such function"):
        plain.execute("INSERT INTO meta(key, value) VALUES('last_written_by_version', '0.1.9')")
    plain.close()
    db.close()


def test_declining_the_migration_changes_nothing(qapp, old_world, monkeypatch):
    old, documents, folder = old_world
    before = old.read_bytes()
    _click(monkeypatch, 1)  # Quit
    assert migrate.run(None, old) is None
    assert old.read_bytes() == before and not folder.exists()
    assert (documents / "backups").exists() and Prefs.native_store().value("db_path") == str(old)


def test_adding_a_log_copies_it_under_a_confirmed_name(qapp, tmp_path, monkeypatch):
    friend = _old_log(tmp_path / "elsewhere" / "nwolog.sqlite", calls=("BA1PK",))
    folder = tmp_path / "station"
    folder.mkdir()
    asked = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (asked.append(k.get("text")) or "BA1PK.sqlite", True))
    dest = add_log.add_log(None, friend, folder)
    assert asked == ["BI1ZZZ.sqlite"], "the name suggested comes from the log's station profile"
    assert dest == folder / "BA1PK.sqlite" and dest.exists()
    con = sqlite3.connect(dest)
    assert con.execute("SELECT call FROM qso").fetchall() == [("BA1PK",)]
    con.close()
    assert not list(folder.glob("*.part-*"))
    # A second add under the same name is refused before anything is copied.
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(a[2]))
    assert add_log.add_log(None, friend, folder) is None and shown


def test_adding_refuses_a_foreign_database_and_leaves_it_as_it_was(qapp, tmp_path, monkeypatch):
    foreign = tmp_path / "logbook.sqlite"
    con = sqlite3.connect(foreign)
    con.execute("CREATE TABLE TABLE_HRD_CONTACTS_V07 (COL_PRIMARY_KEY INTEGER PRIMARY KEY, COL_CALL TEXT)")
    con.commit()
    con.close()
    before = foreign.read_bytes()
    shown = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a: shown.append(a[2]))
    assert add_log.add_log(None, foreign, tmp_path / "station") is None
    assert shown and "another program" in shown[0] and foreign.read_bytes() == before
