"""Only fully applied downloads start the persistent service guard."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QWidget

from nwologger.core import db as dbm
from nwologger.core import qso as qsomod
from nwologger.core import timeutil
from nwologger.core.sync import apply, services
from nwologger.ui import sync_controller
from nwologger.ui.prefs import Prefs

SERVICES = ("lotw", "eqsl", "qrz")
DOWNLOAD_FN = {"lotw": "lotw_download", "eqsl": "eqsl_download", "qrz": "qrz_fetch"}
APPLY_FN = {"lotw": "apply_lotw_qsls", "eqsl": "apply_eqsl", "qrz": "apply_qrz"}
STAMP = "2026-10-18T12:00:00Z"
CURSOR = "2026-10-17 12:00:00"
RECORD = {"CALL": "BA1AA", "QSO_DATE": "20261010", "TIME_ON": "151900", "BAND": "2M",
          "MODE": "FM", "QSL_RCVD": "Y", "APP_QRZLOG_STATUS": "C"}


def response(service, records=None, kwargs=None):
    """What the mocked service function answers; QRZ.com's pages reach the controller through ``on_page``."""
    records = records or []
    if service == "lotw":
        return {"APP_LOTW_LASTQSL": CURSOR, "APP_LOTW_LASTQSORX": CURSOR}, records
    if service == "eqsl":
        return {}, records
    on_page = (kwargs or {}).get("on_page")
    if on_page is not None:
        on_page(1, records, "")
    return records


def fail(*_args, **_kwargs):
    raise services.ServiceError("HTTP 503")


@pytest.fixture
def download(qtbot, tmp_path, monkeypatch):
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    for service in ("lotw", "eqsl"):
        prefs.set(service + "_user", "fake-user")
        prefs.set_secret(service + "_password", "fake-password")
    prefs.set_qrz_api_key("BI1ZZZ", "fake-api-key")
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    db.save_profile({"name": "Home", "station_callsign": "BI1ZZZ", "eqsl_qth_nickname": "Home",
                     "is_default": True})
    parent = QWidget()
    qtbot.addWidget(parent)
    ctrl = sync_controller.SyncController(db, prefs, parent)
    tasks, outcomes = [], []
    monkeypatch.setattr(sync_controller, "run_async",
                        lambda work, *, on_done, on_error, on_event=None: tasks.append((work, on_done, on_error,
                                                                                        on_event)))
    monkeypatch.setattr(ctrl, "_progress", lambda *_a, **_k: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(ctrl, "report", lambda svc, kind, *_a, **_k: outcomes.append((svc, kind)))
    monkeypatch.setattr(ctrl, "_settle_unmatched", lambda *_a, **_k: ([], 0))
    monkeypatch.setattr(timeutil, "now_utc", lambda: datetime(2026, 10, 18, 12, tzinfo=UTC))
    for service in SERVICES:
        monkeypatch.setattr(services, DOWNLOAD_FN[service],
                            lambda *_a, _service=service, **k: response(_service, kwargs=k))

    def finish():
        """Run every queued task; events reach the controller at once, as they do in order on the GUI thread (a
        LoTW download in parts queues its closing request from its own done handler)."""
        while tasks:
            work, done, error, on_event = tasks.pop(0)
            try:
                result = work(event=on_event) if on_event is not None else work()
            except Exception as exc:
                error(str(exc))
            else:
                done(result)

    yield SimpleNamespace(ctrl=ctrl, tasks=tasks, outcomes=outcomes, finish=finish)
    db.close()


@pytest.mark.parametrize("service", SERVICES)
def test_failed_download_allows_immediate_retry(download, monkeypatch, service):
    ctrl = download.ctrl
    monkeypatch.setattr(services, DOWNLOAD_FN[service], fail)
    ctrl.download(service)
    assert ctrl.db.sync_get(service, "last_download") is None
    download.finish()
    assert not ctrl.busy
    assert ctrl.wait_left(service, "download") == 0
    assert ctrl.db.sync_get(service, "last_download") is None
    assert ctrl.db.sync_history()[0]["errors"] == 1
    assert download.outcomes == [(service, "failed")]

    monkeypatch.setattr(services, DOWNLOAD_FN[service], lambda *_a, **_k: response(service))
    ctrl.download(service)
    assert len(download.tasks) == 1
    download.finish()
    assert ctrl.db.sync_get(service, "last_download") == STAMP
    assert ctrl.wait_left(service, "download") > 0
    assert download.outcomes[-1] == (service, "ok")


@pytest.mark.parametrize("service", SERVICES)
def test_successful_download_starts_guard_before_idle_and_survives_restart(download, service):
    ctrl = download.ctrl
    idle_state = []
    ctrl.idle.connect(lambda: idle_state.append((ctrl.db.sync_get(service, "last_download"),
                                               {key: ctrl.db.sync_get(service, key)
                                                for key in ctrl.RESUME_KEYS.get(service, ())})))
    ctrl.download(service)
    assert ctrl.db.sync_get(service, "last_download") is None
    download.finish()
    expected_cursors = {"lotw": {"last_qsl": None, "last_qsorx": CURSOR},
                        "eqsl": {"rcvd_since": "20261017"}, "qrz": {}}
    assert idle_state == [(STAMP, expected_cursors[service])]
    ctrl.download(service)
    assert not download.tasks
    assert download.outcomes[-1] == (service, "skipped")
    again = sync_controller.SyncController(ctrl.db, ctrl.prefs, ctrl.win)
    assert again.wait_left(service, "download") > 0
    assert again.wait_left(service, "upload") == 0


@pytest.mark.parametrize("service", SERVICES)
def test_active_download_blocks_duplicates_without_timestamp(download, monkeypatch, service):
    ctrl = download.ctrl
    warnings = []
    monkeypatch.setattr(sync_controller.QMessageBox, "warning", lambda _parent, _title, text: warnings.append(text))
    ctrl.download(service)
    assert service + "down" in ctrl.busy
    assert ctrl.db.sync_get(service, "last_download") is None
    ctrl.download(service)
    assert len(download.tasks) == 1
    assert not download.outcomes
    assert len(warnings) == 1 and "another download is still in progress" in warnings[0]
    download.finish()


@pytest.mark.parametrize("service", SERVICES)
def test_failed_download_preserves_expired_success_timestamp(download, monkeypatch, service):
    ctrl = download.ctrl
    previous = timeutil.shift_iso(STAMP, -int(ctrl.gap_seconds()) - 1)
    ctrl.db.sync_set(service, "last_download", previous)
    monkeypatch.setattr(services, DOWNLOAD_FN[service], fail)
    ctrl.download(service)
    download.finish()
    assert ctrl.db.sync_get(service, "last_download") == previous
    assert ctrl.wait_left(service, "download") == 0
    ctrl.download(service)
    assert len(download.tasks) == 1
    download.finish()


@pytest.mark.parametrize("service", SERVICES)
def test_a_transfer_that_breaks_keeps_what_arrived_and_leaves_the_positions(download, monkeypatch, service):
    """An incremental LoTW download is two small requests and fails as a whole. eQSL (per QTH nickname) and
    QRZ.com (per page) apply what arrived, stop with a Stopped row and leave the positions, so the next download
    is allowed at once and fetches the rest."""
    ctrl = download.ctrl
    qid = ctrl.db.insert(qsomod.normalize({"call": "BA1AA", "time_on": "2026-10-10T15:19:00Z",
                                           "mode": "FM", "band": "2m", "station_callsign": "BI1ZZZ"}))
    before = ctrl.db.get(qid)
    cursors = {key: "2026-10-01" for key in ctrl.RESUME_KEYS.get(service, ())}
    for key, value in cursors.items():
        ctrl.db.sync_set(service, key, value)
    if service != "lotw":
        ctrl.db.save_profile({"name": "Portable", "station_callsign": "BI1YYY",
                             "eqsl_qth_nickname": "Portable"})
        ctrl.prefs.set_qrz_api_key("BI1YYY", "fake-second-key")
    calls = []

    def partial(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 2:
            fail()
        return response(service, [RECORD], kwargs)

    monkeypatch.setattr(services, DOWNLOAD_FN[service], partial)
    ctrl.download(service)
    download.finish()
    assert len(calls) == 2
    assert {key: ctrl.db.sync_get(service, key) for key in cursors} == cursors
    assert ctrl.db.sync_get(service, "last_download") is None
    assert ctrl.wait_left(service, "download") == 0
    assert not ctrl.busy
    assert ctrl.db.sync_history()[0]["errors"] == 1
    if service == "lotw":
        assert ctrl.db.get(qid) == before
        assert download.outcomes == [(service, "failed")]
    else:
        after = ctrl.db.get(qid)
        assert after[{"eqsl": "eqsl_qsl_rcvd", "qrz": "qrz_qsl_rcvd"}[service]] == "Y"  # the first part arrived
        assert download.outcomes == [(service, "cancelled")]
    if service == "qrz":
        # the logbooks go in call order: BI1YYY's page was applied and is not asked for again, BI1ZZZ starts over
        assert ctrl.db.sync_get("qrz", ctrl.QRZ_RESUME + "BI1YYY") == "1"
        assert ctrl.db.sync_get("qrz", ctrl.QRZ_RESUME + "BI1ZZZ") is None


@pytest.mark.parametrize("service", SERVICES)
def test_apply_failure_rolls_back_cursors_and_releases_download(download, monkeypatch, service):
    ctrl = download.ctrl
    cursors = {key: "2026-10-01" for key in ctrl.RESUME_KEYS.get(service, ())}
    for key, value in cursors.items():
        ctrl.db.sync_set(service, key, value)

    def broken_apply(*_args, **_kwargs):
        for key in cursors:
            ctrl.db.sync_set(service, key, "corrupt cursor")
        ctrl.db.insert(qsomod.normalize({"call": "BA1AA", "time_on": "2026-10-10T15:19:00Z",
                                        "mode": "FM", "band": "2m"}))
        raise RuntimeError("local apply failed")

    monkeypatch.setattr(apply, APPLY_FN[service], broken_apply)
    ctrl.download(service)
    download.finish()
    assert {key: ctrl.db.sync_get(service, key) for key in cursors} == cursors
    assert list(ctrl.db.iter_all()) == []
    assert ctrl.db.sync_get(service, "last_download") is None
    assert ctrl.wait_left(service, "download") == 0
    assert not ctrl.busy
    assert ctrl.db.sync_history()[0]["errors"] == 1
    assert download.outcomes == [(service, "failed")]
