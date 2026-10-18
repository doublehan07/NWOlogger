"""Asking again while a download or upload runs: a warning, nothing started, and the hidden progress window back."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QWidget

from nwologger.core import db as dbm
from nwologger.core import qso as qsomod
from nwologger.core import timeutil
from nwologger.core.sync import services
from nwologger.ui import sync_controller
from nwologger.ui.prefs import Prefs


@pytest.fixture
def sync(qtbot, tmp_path, monkeypatch):
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    for service in ("lotw", "eqsl"):
        prefs.set(service + "_user", "fake-user")
        prefs.set_secret(service + "_password", "fake-password")
    prefs.set_qrz_api_key("BI1ZZZ", "fake-api-key")
    prefs.set("hrdlog_call", "BI1ZZZ")  # the upload code is kept under the call
    prefs.set_secret("hrdlog_code", "fake-code")
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    db.save_profile({"name": "Home", "station_callsign": "BI1ZZZ", "eqsl_qth_nickname": "Home",
                     "is_default": True})
    db.insert(qsomod.normalize({"call": "BA1AA", "time_on": "2026-10-10T15:19:00Z", "band": "2m", "mode": "FM",
                                "station_callsign": "BI1ZZZ"}))
    parent = QWidget()
    qtbot.addWidget(parent)
    ctrl = sync_controller.SyncController(db, prefs, parent)
    tasks, warnings, outcomes = [], [], []
    monkeypatch.setattr(sync_controller, "run_async",
                        lambda work, *, on_done, on_error: tasks.append((work, on_done, on_error)))
    monkeypatch.setattr(sync_controller.QMessageBox, "warning",
                        lambda _parent, _title, text: warnings.append(text))
    monkeypatch.setattr(ctrl, "report", lambda svc, kind, *_a, **_k: outcomes.append((svc, kind)))
    monkeypatch.setattr(timeutil, "now_utc", lambda: datetime(2026, 10, 18, 12, tzinfo=UTC))
    yield SimpleNamespace(ctrl=ctrl, tasks=tasks, warnings=warnings, outcomes=outcomes)
    for dlg in list(ctrl.progress.values()):
        dlg.close()
    db.close()


@pytest.mark.parametrize("service", ["lotw", "eqsl", "qrz"])
def test_download_again_warns_skips_and_shows_the_hidden_progress_window(sync, service):
    ctrl = sync.ctrl
    ctrl.download(service)
    dlg = ctrl.progress[service + "down"]
    dlg.cancel()  # Hide
    assert not dlg.isVisible() and len(sync.tasks) == 1
    ctrl.download(service)
    assert len(sync.tasks) == 1  # nothing new started
    assert len(sync.warnings) == 1 and "another download is still in progress" in sync.warnings[0]
    assert dlg.isVisible()


@pytest.mark.parametrize("service", ["lotw", "eqsl", "qrz", "hrdlog"])
def test_upload_again_warns_skips_and_shows_the_hidden_progress_window(sync, monkeypatch, service):
    ctrl = sync.ctrl
    if service == "lotw":
        monkeypatch.setattr(sync_controller.tqsl, "find_tqsl", lambda _path: "/fake/tqsl")
        monkeypatch.setattr(ctrl, "_upload_lotw", lambda qsos, quiet: (
            ctrl._start("lotw", "upload", "test"), ctrl._progress("lotwup", "Uploading")))
    qid = ctrl.db.conn.execute("SELECT id FROM qso").fetchone()[0]
    ctrl.upload(service, [qid])
    dlg = ctrl.progress[service + "up"]
    dlg.cancel()
    ctrl.upload(service, [qid])
    assert len(sync.warnings) == 1 and "another upload is still in progress" in sync.warnings[0]
    assert dlg.isVisible()
    ctrl.upload(service, [qid], quiet=True)  # upload on log still waits quietly for the running one
    assert len(sync.warnings) == 1 and ctrl.queued.get(service) == {qid}
    ctrl.stop_timers()


def test_sync_everything_warns_once_for_all_downloads_still_running(sync, monkeypatch):
    ctrl = sync.ctrl
    for service in ("lotw", "qrz"):
        ctrl.download(service)
        ctrl.progress[service + "down"].cancel()
    ctrl.downloads(["lotw", "eqsl", "qrz"])
    assert len(sync.tasks) == 3  # eQSL starts; LoTW and QRZ.com are still running
    assert len(sync.warnings) == 1 and sync.warnings[0].startswith("LoTW, QRZ.com: ")
    assert ctrl.progress["lotwdown"].isVisible() and ctrl.progress["qrzdown"].isVisible()


def test_a_finished_run_forgets_its_progress_window(sync, monkeypatch):
    ctrl = sync.ctrl
    monkeypatch.setattr(services, "eqsl_download", lambda *_a, **_k: ({}, []))
    ctrl.download("eqsl")
    work, done, _error = sync.tasks.pop()
    done(work())
    assert "eqsldown" not in ctrl.progress and not ctrl.busy
