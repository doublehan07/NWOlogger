"""The diagnostic log file, readable service errors, and syncs that can't hang NWOlogger. All secrets are fake."""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

import httpx
import pytest
from PySide6.QtCore import QSettings

from nwologger import applog
from nwologger.core import db as dbm
from nwologger.core import net
from nwologger.core import qso as qsomod
from nwologger.core.sync import services, tqsl

SECRET = "FAKE&pw%+ x/Ü9"
KEY = "FAKE-QRZ-API-KEY-1234"

EQSL_ERROR_PAGE = (
    "<html><head><title>Download InBox - eQSL.cc</title><script>//***** This function pops up a window and loads "
    "it with URL, l=left, t=top\nfunction popupPrintPage(URL, WindowName, l, t, w, h) { var windowprops = "
    "'location=no'; if (error) {} }</script></head><body><table><tr><td>Menu</td></tr></table>"
    "<center><b>Error: You must specify a QTHNickname</b></center></body></html>")


@pytest.fixture
def logfile(tmp_path):
    path = applog.setup(directory=tmp_path / "logs")
    yield path
    applog.shutdown()


def _text(path) -> str:
    for h in logging.getLogger().handlers:
        h.flush()
    return path.read_text(encoding="utf-8")


# ------------------------------------------------------------------ log file

def test_log_file_never_holds_a_secret(logfile):
    log = logging.getLogger("nwologger.test")
    applog.remember_secrets([SECRET, KEY, None])
    log.info("login with %s and key=%s", SECRET, KEY)
    log.warning("for url 'https://lotw.arrl.org/lotwuser/lotwreport.adi?login=u&password=%s'", "other-pw-77")
    try:
        raise ValueError(f"server echoed {SECRET}")
    except ValueError:
        log.exception("failed")
    text = _text(logfile)
    assert "login with" in text and "Traceback" in text and "ValueError" in text
    for v in (SECRET, KEY, "other-pw-77"):
        assert v not in text
    assert "lotwreport.adi?•••" in text


def test_requests_are_logged_by_host_and_path_only(logfile, mock_net):
    mock_net(lambda req: httpx.Response(503))
    with pytest.raises(net.NetError):
        services.lotw_download("u", SECRET)
    text = _text(logfile)
    assert "LoTW GET lotw.arrl.org/lotwuser/lotwreport.adi failed" in text and "HTTP 503" in text
    assert "password" not in text.lower() and SECRET not in text


def test_failed_syncs_leave_no_secret_in_the_log(logfile, qtbot, tmp_path, mock_net, keyring_store):
    from PySide6.QtWidgets import QWidget

    from nwologger.ui.prefs import Prefs
    from nwologger.ui.sync_controller import SyncController

    prefs = Prefs(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    for user_key, value in (("lotw_user", "u"), ("eqsl_user", "u"), ("hrdlog_call", "BI1ZZZ")):
        prefs.set(user_key, value)
    for key in ("lotw_password", "eqsl_password", "hrdlog_code"):
        prefs.set_secret(key, SECRET)
    prefs.set_qrz_api_key("BI1ZZZ", KEY)
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    db.save_profile({"name": "BI1ZZZ", "station_callsign": "BI1ZZZ", "eqsl_qth_nickname": "Home", "is_default": True})
    db.insert(qsomod.normalize({"call": "BA1AA", "time_on": "2026-10-10T15:19:00Z", "mode": "FM", "band": "2m",
                                "station_callsign": "BI1ZZZ"}))
    parent = QWidget()
    qtbot.addWidget(parent)
    c = SyncController(db, prefs, parent)

    def echo(req):  # every service echoes the secrets back in its error
        if req.url.host == "logbook.qrz.com":
            return httpx.Response(200, text=f"RESULT=FAIL&REASON=bad key {KEY}")
        if req.url.host == "robot.hrdlog.net":
            return httpx.Response(200, text=f"<error>bad code {SECRET}</error>")
        return httpx.Response(200, text=f"<html>Error: no such user, password {SECRET}</html>")

    mock_net(echo)
    for svc in ("lotw", "eqsl", "qrz"):
        c.download(svc)
        qtbot.waitUntil(lambda s=svc: s + "down" not in c.busy)
    for svc in ("qrz", "hrdlog", "eqsl"):
        c.upload(svc, [1], quiet=True)
        qtbot.waitUntil(lambda s=svc: s + "up" not in c.busy)
    text = _text(logfile)
    assert "(ref " in text and "failed" in text
    for v in (SECRET, KEY):
        assert v not in text and v.lower() not in text.lower()
    assert "BI1ZZZ" not in text  # the HRDLog account name is part of the credentials
    db.close()


def test_watchdog_writes_thread_stacks_when_the_window_hangs(logfile, qtbot, monkeypatch):
    dog = applog.Watchdog(after=5.0)
    try:
        dog.beat = time.monotonic() - 30  # the GUI thread did not tick for 30 s
        assert dog.check() and not dog.check()  # written once per hang
        text = _text(logfile)
        assert "has not responded for 30 s" in text and "--- thread MainThread" in text
        dog._beat()
        assert "responds again" in _text(logfile)
        # Idle in the event loop (e.g. App Nap held the timers back): a note, not a hang report.
        monkeypatch.setattr(applog.Watchdog, "_main_thread_idle", staticmethod(lambda: True))
        dog.beat = time.monotonic() - 30
        assert not dog.check()
        assert "Timers ran 30 s late" in _text(logfile)
    finally:
        dog.stop()


def test_a_shorter_freeze_is_logged_with_the_gui_threads_stack(logfile, monkeypatch):
    dog = applog.Watchdog(after=30.0, blocked=2.0)
    try:
        dog.beat = time.monotonic() - 3.0  # 2.5 s past the next heartbeat
        assert not dog.check()  # too short for the all-thread dump, but the GUI thread's place is noted
        dog._beat()
        text = _text(logfile)
        assert "The window was blocked for 2." in text and "test_diagnostics.py" in text
        assert str(Path.home()) + os.sep not in text  # the home folder shows as ~
        # Idle in the event loop (App Nap): nothing to report.
        monkeypatch.setattr(applog.Watchdog, "_main_thread_idle", staticmethod(lambda: True))
        dog.beat = time.monotonic() - 3.0
        dog.check()
        dog._beat()
        assert _text(logfile).count("was blocked") == 1
    finally:
        dog.stop()


def test_timed_steps_are_logged_when_slow(logfile):
    with applog.timed("Quick"):
        pass
    with applog.timed("Something", at_least=0) as t:
        t.step("first")
        t.step("second")
    text = _text(logfile)
    assert "Quick took" not in text
    assert "Something took 0.00 s (first 0.00, second 0.00)" in text


def test_preferences_save_is_logged_by_setting_name(logfile, qtbot, tmp_path, keyring_store):
    from nwologger.ui.prefs import Prefs
    from nwologger.ui.prefs_dialog import PrefsDialog

    prefs = Prefs(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    dlg = PrefsDialog(db, prefs)
    qtbot.addWidget(dlg)
    dlg.theme.setCurrentIndex(dlg.theme.findData("dark"))
    dlg.lotw_user.setText("FAKEUSER9")
    dlg.lotw_pass.edit.setText(SECRET)
    dlg.devices_page._add_item(dlg.devices_page.rigs, "IC-705")
    dlg._save()
    line = next(ln for ln in _text(logfile).splitlines() if "Preferences saved" in ln)
    assert "theme system→dark" in line and "rigs 0→1" in line and "lotw_user" in line
    assert "lotw_password changed" in line and "FAKEUSER9" not in line and SECRET not in line
    db.close()


def test_sync_summaries_are_logged_in_english(logfile, ctrl):
    from nwologger import i18n

    i18n.load("zh_CN")
    try:
        ctrl.report("lotw", "ok", lambda t: t("No new confirmations"), log_detail="download 2.0 s")
    finally:
        i18n.load("en")
    assert "No new confirmations; download 2.0 s" in _text(logfile)
    assert ctrl.results.texts()[-1] != "No new confirmations"  # the window shows it translated


def test_tidy_path_hides_the_home_folder():
    home = os.path.expanduser("~")
    assert applog.tidy_path(os.path.join(home, "Documents", "x.sqlite")) == os.path.join("~", "Documents",
                                                                                         "x.sqlite")
    assert applog.tidy_path("/elsewhere/x") == "/elsewhere/x"


# ------------------------------------------------------------------ readable service errors

def test_eqsl_error_page_shows_the_error_not_javascript(mock_net):
    mock_net(lambda req: httpx.Response(200, text=EQSL_ERROR_PAGE))
    with pytest.raises(services.ServiceError) as ei:
        services.eqsl_download("u", SECRET)
    msg = str(ei.value)
    assert msg.startswith("eQSL: Error: You must specify a QTHNickname")
    assert "function" not in msg and "popupPrintPage" not in msg and "QTH nickname in the station profile" in msg


def test_page_text_drops_scripts_styles_and_head():
    t = services.page_text("<head><style>b{}</style><title>T</title></head><script>var error=1</script>"
                           "<p>Line one</p><div>Line&nbsp;two</div>")
    assert t == "Line one\nLine two"
    assert services.error_line(t) is None
    assert services.error_line("ok\nError: bad\nmore") == "Error: bad"


# ------------------------------------------------------------------ TQSL

def test_station_locations_are_read_from_tqsl(tmp_path):
    (tmp_path / "station_data").write_text(
        '<?xml version="1.0"?><StationDataFile><StationData name="Home"><CALL>BI1ZZZ</CALL></StationData>'
        '<StationData name="Portable ON80"><CALL>BI1ZZZ</CALL></StationData></StationDataFile>', encoding="utf-8")
    assert tqsl.station_locations(tmp_path) == ["Home", "Portable ON80"]
    assert tqsl.station_locations(tmp_path / "missing") is None


@pytest.fixture
def stuck_tqsl(tmp_path):
    """Stands in for TQSL waiting on its own window (e.g. a certificate passphrase)."""
    if sys.platform == "win32":
        pytest.skip("POSIX shell script")
    f = tmp_path / "tqsl"
    f.write_text("#!/bin/sh\necho 'Signing' >&2\nsleep 30\n")
    f.chmod(0o755)
    return str(f)


def test_tqsl_can_be_stopped_and_times_out(stuck_tqsl):
    import threading

    job = tqsl.Job()
    threading.Timer(0.5, job.stop).start()
    t0 = time.monotonic()
    code, _out = tqsl.sign_and_upload(stuck_tqsl, "x.adi", "Home", job=job)
    assert code == tqsl.CANCELLED, code
    assert time.monotonic() - t0 < 15
    t0 = time.monotonic()
    code, out = tqsl.sign_and_upload(stuck_tqsl, "x.adi", "Home", timeout=3.0)
    assert code == tqsl.TIMED_OUT, (code, out)
    assert "Signing" in out, out  # what TQSL wrote before it was stopped
    assert time.monotonic() - t0 < 15


# ------------------------------------------------------------------ sync controller

@pytest.fixture
def ctrl(qtbot, tmp_path, monkeypatch, keyring_store):
    from PySide6.QtWidgets import QMessageBox, QWidget

    from nwologger.ui.prefs import Prefs
    from nwologger.ui.sync_controller import SyncController

    for name in ("warning", "information", "critical"):  # results never come as message boxes
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: pytest.fail("message box shown")))
    prefs = Prefs(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    prefs.set("lotw_user", "u")
    prefs.set("eqsl_user", "u")
    prefs.set_secret("lotw_password", "pw-lotw-1")
    prefs.set_secret("eqsl_password", "pw-eqsl-1")
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    db.insert(qsomod.normalize({"call": "BA1AA", "time_on": "2026-10-10T15:19:00Z", "mode": "FM", "band": "2m",
                                "station_callsign": "BI1ZZZ"}))
    parent = QWidget()
    qtbot.addWidget(parent)
    c = SyncController(db, prefs, parent)
    yield c
    db.close()


def test_missing_eqsl_nickname_is_skipped_before_any_request(ctrl):
    ctrl.db.save_profile({"name": "BI1ZZZ", "station_callsign": "BI1ZZZ", "is_default": True})
    ctrl.download("eqsl")  # no request: the no_network fixture would fail the test
    ctrl.upload("eqsl", [1])
    texts = ctrl.results.texts()
    assert len(texts) == 2 and all("QTH nickname" in t for t in texts)
    assert ctrl.results.fixes == ["Station profiles", "Station profiles"]
    assert not ctrl.busy


def test_sync_everything_failures_are_rows_not_stacked_boxes(ctrl, qtbot, mock_net):
    ctrl.db.save_profile({"name": "BI1ZZZ", "station_callsign": "BI1ZZZ", "eqsl_qth_nickname": "Home",
                          "is_default": True})
    mock_net(lambda req: httpx.Response(200, text=EQSL_ERROR_PAGE if "eqsl" in req.url.host else "<html>bad</html>"))
    for svc in ("lotw", "eqsl", "qrz"):  # what Sync everything does
        ctrl.download(svc)
    qtbot.waitUntil(lambda: not ctrl.busy)
    texts = ctrl.results.texts()
    assert len(texts) == 3
    assert any("QTHNickname" in t for t in texts) and any("QRZ Logbook API key" in t for t in texts)
    assert ctrl.results.isVisible()


def test_unknown_tqsl_location_is_skipped(ctrl, tmp_path, monkeypatch):
    (tmp_path / "tq").mkdir()
    (tmp_path / "tq" / "station_data").write_text(
        '<StationDataFile><StationData name="Home"/></StationDataFile>', encoding="utf-8")
    monkeypatch.setenv("TQSLDIR", str(tmp_path / "tq"))
    monkeypatch.setattr(tqsl, "find_tqsl", lambda *_a: "/fake/tqsl")
    monkeypatch.setattr(tqsl, "sign_and_upload", lambda *_a, **_k: pytest.fail("TQSL ran"))
    ctrl.db.save_profile({"name": "BI1ZZZ", "station_callsign": "BI1ZZZ", "lotw_location": "Hmoe",
                          "is_default": True})
    ctrl.upload("lotw", [1])
    (text,) = ctrl.results.texts()
    assert "Hmoe" in text and "TQSL has: Home" in text and not ctrl.busy


def test_cancel_stops_tqsl_and_leaves_the_qso_pending(ctrl, qtbot, stuck_tqsl, monkeypatch):
    ctrl.prefs.set("tqsl_path", stuck_tqsl)
    ctrl.db.save_profile({"name": "BI1ZZZ", "station_callsign": "BI1ZZZ", "lotw_location": "Home",
                          "is_default": True})
    ctrl.upload("lotw", [1])
    assert "lotwup" in ctrl.busy and ctrl.running() == ["LoTW upload"]
    qtbot.wait(300)
    from PySide6.QtWidgets import QProgressDialog

    (dlg,) = ctrl.win.findChildren(QProgressDialog)
    dlg.close()  # closing the dialog (as quitting does, before you answer) does not stop TQSL
    assert not ctrl.jobs["lotwup"].stopped.is_set()
    ctrl.cancel_all()
    qtbot.waitUntil(lambda: not ctrl.busy, timeout=10000)
    assert ctrl.db.get(1).get("lotw_qsl_sent") is None
    assert "Stopped in NWOlogger" in ctrl.results.texts()[0]


def test_a_second_download_inside_the_gap_is_refused_and_says_when(ctrl, qtbot, mock_net):
    from nwologger.core import timeutil
    from nwologger.ui.sync_controller import SyncController

    seen = mock_net(lambda req: httpx.Response(200, text="<EOH>"))
    ctrl.download("lotw")
    qtbot.waitUntil(lambda: "lotwdown" not in ctrl.busy)
    sent = len(seen)
    ctrl.download("lotw")
    assert len(seen) == sent and "Too soon after the last download" in ctrl.results.texts()[0]
    assert "UTC" in ctrl.results.texts()[0] and ctrl.results.fixes[0] == "Services"
    again = SyncController(ctrl.db, ctrl.prefs, ctrl.parent())  # a restart doesn't shorten the wait
    assert again.wait_left("lotw", "download") > 0 and again.wait_left("lotw", "upload") == 0
    ctrl.prefs.set("sync_gap_minutes", 0)  # never less than a minute
    assert 0 < ctrl.wait_left("lotw", "download") <= 60
    ctrl.db.sync_set("lotw", "last_download", timeutil.shift_iso(timeutil.now_iso(), -61))
    assert ctrl.wait_left("lotw", "download") == 0
    ctrl.download("eqsl")  # skipped before any request (no QTH nickname): no wait starts
    assert ctrl.db.sync_get("eqsl", "last_download") is None


def test_uploads_when_logged_wait_and_go_together(ctrl, monkeypatch):
    from nwologger.core import timeutil

    ctrl.db.insert(qsomod.normalize({"call": "BA1BB", "time_on": "2026-10-10T15:20:00Z", "mode": "FM", "band": "2m",
                                     "station_callsign": "BI1ZZZ"}))
    runs = []
    monkeypatch.setattr(ctrl, "_upload_qrz", lambda qsos, quiet: runs.append([q["id"] for q in qsos]))
    ctrl.db.sync_set("qrz", "last_upload", timeutil.now_iso())
    ctrl.upload("qrz", [1], quiet=True)
    ctrl.upload("qrz", [2], quiet=True)
    assert runs == [] and ctrl.queued["qrz"] == {1, 2} and ctrl._queue_timers["qrz"].isActive()
    assert ctrl.waiting() == ["QRZ.com upload (2 QSOs waiting)"]
    ctrl.db.sync_set("qrz", "last_upload", None)  # the wait is over
    ctrl._flush("qrz")
    assert runs == [[1, 2]] and not ctrl.queued


def test_download_everything_again_resets_where_downloads_resume(ctrl, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    asked, started = [], []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda _p, _t, text, *a, **k: asked.append(text) or
                                                              QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(ctrl, "download", started.append)
    ctrl.db.sync_set("lotw", "last_qsl", "2026-10-14 23:07:03")
    ctrl.db.sync_set("lotw", "last_qsorx", "2026-10-16 14:37:32")
    ctrl.db.sync_set("lotw", "full_seconds", "233")
    ctrl.download_all_again("lotw")
    assert ctrl.db.sync_get("lotw", "last_qsl") is None and ctrl.db.sync_get("lotw", "last_qsorx") is None
    assert started == ["lotw"] and "3 min 53 sec" in asked[0]


def test_background_tasks_never_hold_up_quitting():
    from nwologger.ui.widgets import Task

    assert Task(lambda: None).thread.daemon
