"""The regular backup plan: every N days a scheduled copy into backups/ when the QSOs changed, in a pool of its own,
and a reminder for a copy on another disk, at most once a day."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from PySide6.QtCore import QSettings

from nwologger.core import backup_plan as plan
from nwologger.core import qso as qsomod
from nwologger.core.db import LogDB
from nwologger.ui.backup_reminder import BackupReminder
from nwologger.ui.prefs import Prefs

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def _qso(db, i: int) -> None:
    db.insert(qsomod.normalize({"call": f"BA1A{i}", "time_on": f"2026-01-{i + 1:02d}T10:00:00Z", "mode": "FM",
                                "band": "2m"}))


def scheduled(db) -> list:
    return [p for p in db.backups() if p.stem.endswith("-scheduled")]


def test_the_clock_starts_at_the_first_check_and_a_copy_comes_when_the_qsos_changed(tmp_path):
    db = LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    try:
        _qso(db, 0)
        assert plan.run_scheduled(db, 7, NOW) is None and db.meta(plan.SCHEDULED_AT).startswith("2026-10-18")
        assert plan.run_scheduled(db, 7, NOW + 6 * DAY) is None and scheduled(db) == []  # not yet
        _qso(db, 1)
        dest = plan.run_scheduled(db, 7, NOW + 7 * DAY)
        assert dest is not None and dest.stem.endswith("-scheduled")
        assert db.meta(plan.SCHEDULED_AT).startswith("2026-10-25")
        before = db.backups()  # due again but nothing logged, edited or deleted: no new file, the time moves
        assert plan.run_scheduled(db, 7, NOW + 14 * DAY) is None and db.backups() == before
        assert db.meta(plan.SCHEDULED_AT).startswith("2026-11-01")
        db.delete([2])  # a deleted QSO counts as a change too
        assert plan.run_scheduled(db, 7, NOW + 21 * DAY) is not None and len(scheduled(db)) == 2
        assert plan.run_scheduled(db, 0, NOW + 60 * DAY) is None  # the plan off
    finally:
        db.close()


def test_scheduled_copies_have_a_pool_of_their_own(tmp_path):
    db = LogDB(tmp_path / "log.sqlite", backup_on_open=False, backup_keep=1, scheduled_keep=2)
    try:
        when = NOW
        plan.run_scheduled(db, 7, when)  # the clock
        for i in range(4):
            _qso(db, i)
            when += 7 * DAY
            assert plan.run_scheduled(db, 7, when) is not None
        assert len(scheduled(db)) == 2  # the newest two
        db.backup("manual")
        db.backup("manual")
        assert len(scheduled(db)) == 2 and len([p for p in db.backups() if p.stem.endswith("-manual")]) == 1
    finally:
        db.close()


def test_the_reminder_is_due_once_a_day_and_the_outside_copy_resets_it(tmp_path):
    db = LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    try:
        _qso(db, 0)
        assert not plan.external_due(db, 7, NOW)  # the clock starts
        assert plan.external_due(db, 7, NOW + 8 * DAY)
        plan.asked(db, NOW + 8 * DAY)
        assert not plan.external_due(db, 7, NOW + 8 * DAY)  # Later: not again today
        assert plan.external_due(db, 7, NOW + 9 * DAY)
        folder = tmp_path / "usb" / "NWOlogger"
        dest = plan.copy_outside(db, folder, NOW + 9 * DAY)
        assert dest.parent == folder and dest.name.endswith("-external.sqlite") and dest.stat().st_size > 0
        copy = LogDB(dest, backup_on_open=False)
        try:
            assert copy.count() == 1
        finally:
            copy.close()
        assert not plan.external_due(db, 7, NOW + 10 * DAY)
        assert plan.external_due(db, 7, NOW + 16 * DAY)
        assert not plan.external_due(db, 0, NOW + 60 * DAY)
    finally:
        db.close()


def test_the_reminder_window_copies_or_waits(qtbot, tmp_path):
    db = LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    try:
        _qso(db, 0)
        bare = BackupReminder(db, prefs)
        qtbot.addWidget(bare)
        assert bare.to_btn.text() == "Choose a folder…" and not bare.other_btn.isVisibleTo(bare)
        bare.later()
        assert db.meta(plan.ASKED_ON) == datetime.now(UTC).date().isoformat()
        folder = tmp_path / "cloud"
        prefs.set("external_backup_dir", str(folder))
        dlg = BackupReminder(db, prefs)
        qtbot.addWidget(dlg)
        assert dlg.to_btn.text() == f"Back up to {folder}" and dlg.other_btn.isVisibleTo(dlg)
        with qtbot.waitSignal(dlg.copied, timeout=2000) as got:
            dlg.to_btn.click()
        assert got.args[0].parent == folder and db.meta(plan.EXTERNAL_AT) is not None
    finally:
        db.close()
