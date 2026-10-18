"""Marks made under an earlier dupe window (24 hours before 0.1.9) stay in the log although the review can show no
pair for them: the review, Find duplicates and a change of the windows in Preferences rescan the marks first and
say what they cleared."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox, QWidget

from nwologger.core import dupes
from nwologger.core import qso as qsomod
from nwologger.core.db import LogDB
from nwologger.ui import dupe_dialog
from nwologger.ui.prefs import Prefs


def _qso(db, call, when, mode="SSB"):
    return db.insert(qsomod.normalize({"call": call, "time_on": when, "mode": mode, "band": "15m"}))


def _stale_pair(db):
    """A pair 21 minutes apart, marked SUSPECT as 0.1.8's 24-hour window did."""
    a = _qso(db, "VK6KFK", "2024-01-11T10:37:14Z")
    b = _qso(db, "VK6KFK", "2024-01-11T10:58:19Z")
    db.update(b, {"dupe_flag": "SUSPECT", "dupe_of": a}, track_service=False, normalized=True)
    return a, b


@pytest.fixture
def log(tmp_path, qapp):
    db = LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    yield db, prefs
    db.close()


def test_the_rescan_clears_marks_the_window_no_longer_makes_and_counts_them(log):
    db, _prefs = log
    _a, b = _stale_pair(db)
    c = _qso(db, "OH6IH", "2023-12-31T09:38:29Z")
    d = _qso(db, "OH6IH", "2023-12-31T09:39:14Z")  # 45 s later: suspicious under the 1-minute window
    res = dupes.rescan(db, block_s=30, warn_s=60)
    assert res == (0, 1, 1, 1)  # dupes, suspects, cleared, added
    assert db.get(b)["dupe_flag"] is None and db.get(b)["dupe_of"] is None
    assert db.get(d)["dupe_flag"] == "SUSPECT" and db.get(d)["dupe_of"] == c
    assert dupes.rescan(db, block_s=30, warn_s=60) == (0, 1, 0, 0)  # settled


def test_the_review_says_what_it_cleared_when_nothing_is_left(log, monkeypatch):
    db, prefs = log
    _a, b = _stale_pair(db)
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: shown.append(args[2]))
    dupe_dialog.show_review(None, db, prefs)
    assert shown == ["1 mark made under an earlier rule was cleared: those QSOs are more than 1 min apart.\n\n"
                     "No marked QSOs to review."]
    assert db.get(b)["dupe_flag"] is None  # the log agrees with the review now


def test_the_review_opens_on_the_pairs_that_remain_and_flashes_the_rest(log, monkeypatch, qtbot):
    db, prefs = log
    _stale_pair(db)
    _qso(db, "BI1QGX", "2024-01-19T16:52:28Z")
    _qso(db, "BI1QGX", "2024-01-19T16:52:40Z")  # 12 s: a dupe under the 30 s block window
    opened, flashed = [], []
    monkeypatch.setattr(dupe_dialog.DupeCompareDialog, "exec", lambda self: opened.append(self.groups) or 0)

    class Parent(QWidget):  # the main window: the review's parent, with a status bar to flash
        def flash(self, text):
            flashed.append(text)

    parent = Parent()
    qtbot.addWidget(parent)
    dupe_dialog.show_review(parent, db, prefs)
    assert len(opened) == 1 and len(opened[0]) == 1 and len(opened[0][0]) == 2
    assert flashed == ["1 mark made under an earlier rule was cleared: those QSOs are more than 1 min apart."]
