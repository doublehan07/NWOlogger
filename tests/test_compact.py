"""File → Compact log file: the file shrinks after deleting QSOs, their old data leaves it, and a backup comes first."""

from __future__ import annotations

import pytest

from nwologger.core import db as dbm
from nwologger.core import qso as qsomod

SECRET = "ZZ-OLD-COMMENT-" * 12


def _fill(db, n=2000):
    return [db.insert(qsomod.normalize({"call": f"BA{i % 10}TS{chr(65 + i % 26)}", "band": "20m", "mode": "FT8",
                                        "time_on": f"2026-01-{1 + i % 28:02d}T{i % 24:02d}:{i % 60:02d}:00Z",
                                        "comment": SECRET})) for i in range(n)]


def test_compacting_shrinks_the_file_and_drops_deleted_data(tmp_path):
    path = tmp_path / "log.sqlite"
    db = dbm.LogDB(path, backup_on_open=False)
    ids = _fill(db)
    kept = db.insert(qsomod.normalize({"call": "BG2TSK", "time_on": "2026-02-01T00:00:00Z", "band": "2m",
                                       "mode": "FM", "comment": "still here"}))
    db.delete(ids, backup=False)
    db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert SECRET.encode() in path.read_bytes()  # SQLite keeps deleted rows' bytes in its free pages
    size = db.file_size()
    before, after, backup = db.compact()
    assert before == size and after < before / 5
    assert SECRET.encode() not in path.read_bytes()
    assert backup is not None and backup.name.endswith("-compact.sqlite") and backup.exists()
    assert db.count() == 1 and db.get(kept)["comment"] == "still here"
    db.insert(qsomod.normalize({"call": "BH4TSL", "time_on": "2026-02-02T00:00:00Z", "band": "2m", "mode": "FM"}))
    assert db.count() == 2
    db.close()
    reopened = dbm.LogDB(path, backup_on_open=False)
    assert reopened.count() == 2
    reopened.close()


def test_compacting_is_refused_in_the_middle_of_a_change(tmp_path):
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    with pytest.raises(RuntimeError), db.batch("test"):
        db.compact()
    db.close()
