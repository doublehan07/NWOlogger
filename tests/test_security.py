"""Secrets never reach dialogs, the log file, backups or logs. All secrets here are fake."""

from __future__ import annotations

import html
import logging
from urllib.parse import quote, quote_plus

import httpx
import pytest
from PySide6.QtCore import QSettings

from nwologger.core import db as dbm
from nwologger.core import importer, net, sats, secrets
from nwologger.core import qso as qsomod
from nwologger.core.lookup import clients
from nwologger.core.sync import apply, services

SECRET = "FAKE&pw%+ x/Ü9"  # characters that change under URL and HTML encoding
KEY = "FAKE-QRZ-API-KEY-1234"
SESSION = "FAKESESSIONKEY0123456789"


def forms(s: str) -> list[str]:
    return [s, quote(s, safe=""), quote_plus(s), html.escape(s)]


def assert_clean(text: str, *values: str) -> None:
    for v in values:
        for f in forms(v):
            assert f.lower() not in (text or "").lower(), f"secret leaked: {text!r}"


# ------------------------------------------------------------------ redact

def test_redact_urls_values_and_pairs():
    text = ("Client error '403 Forbidden' for url 'https://lotw.arrl.org/lotwuser/lotwreport.adi?login=u&password="
            + quote_plus(SECRET) + "' and then " + SECRET + " / " + html.escape(SECRET) + " key=" + KEY.lower())
    out = net.redact(text, [SECRET, KEY])
    assert_clean(out, SECRET, KEY)
    assert "https://lotw.arrl.org/lotwuser/lotwreport.adi?•••" in out
    assert net.redact("EQSL_PSWD=abcdef Password=xyz12 is wrong") == "EQSL_PSWD=••• Password=••• is wrong"
    # Plain messages pass unchanged; empty secrets are ignored.
    assert net.redact("12 confirmations updated", [""]) == "12 confirmations updated"


@pytest.mark.parametrize("echo", ["FAKE&#39;PASS9", "FAKE&apos;PASS9", "FAKE&#x27;PASS9", "FAKE%27PASS9",
                                  "FAKE&amp;#39;PASS9", "FAKE%2527PASS9", "fake'pass9"])
def test_redact_finds_encoded_echoes(echo):
    out = net.redact(f"Error: {echo} is wrong", ["FAKE'PASS9"])
    assert out == "Error: ••• is wrong"


def test_short_secrets_masked_as_whole_tokens():
    assert net.redact("RST 559, code 59 sent", ["59"]) == "RST 559, code ••• sent"
    assert net.redact("RST 559 sent", ["59"]) == "RST 559 sent"


@pytest.mark.parametrize("page", [
    lambda: services.lotw_download("u", "FAKE'PASS9"),
    lambda: services.eqsl_download("u", "FAKE'PASS9"),
])
def test_encoded_echo_in_download_pages(mock_net, page):
    mock_net(lambda req: httpx.Response(200, text="<html>Error: no such user FAKE&#39;PASS9</html>"))
    with pytest.raises(services.ServiceError) as ei:
        page()
    assert "no such user" in str(ei.value) and "PASS9" not in str(ei.value)


def test_encoded_echo_in_hrdlog_fallback(mock_net):
    mock_net(lambda req: httpx.Response(200, text="<html>refused FAKE&#39;CODE9</html>"))
    ok, msg = services.hrdlog_upload("BI1ZZZ", "FAKE'CODE9", {"CALL": "BA1AA"})
    assert not ok and "refused" in msg and "CODE9" not in msg


# ------------------------------------------------------------------ HTTP errors carry no secret

def _qrz_session(req):
    if "username" in req.url.params:
        return httpx.Response(200, text=f"<QRZDatabase><Session><Key>{SESSION}</Key></Session></QRZDatabase>")
    return httpx.Response(503)


CALLS = {
    "lotw": lambda: services.lotw_download("u", SECRET),
    "eqsl_down": lambda: services.eqsl_download("u", SECRET),
    "eqsl_up": lambda: services.eqsl_upload("u", SECRET, [{"CALL": "BA1AA"}]),
    "qrz_logbook": lambda: services.qrz_status(KEY),
    "hrdlog": lambda: services.hrdlog_upload("BI1ZZZ", SECRET, {"CALL": "BA1AA"}),
    "qrz_xml": lambda: clients.QrzXml("u", SECRET).lookup("BA1AA"),
    "hamqth": lambda: clients.HamQth("u", SECRET).lookup("BA1AA"),
}


@pytest.mark.parametrize("status", [403, 500, 503])
@pytest.mark.parametrize("name", list(CALLS))
def test_status_errors_hide_credentials(mock_net, name, status):
    mock_net(lambda req: httpx.Response(status))
    with pytest.raises(net.NetError) as ei:
        CALLS[name]()
    msg = str(ei.value)
    assert f"HTTP {status}" in msg
    assert_clean(msg, SECRET, KEY)
    assert ei.value.__cause__ is None and ei.value.__context__ is None  # the URL-bearing error is not chained


def test_qrz_session_key_not_in_lookup_errors(mock_net):
    mock_net(_qrz_session)
    data, _src, errors = clients.lookup_chain("BA1AA", [clients.QrzXml("u", SECRET)])
    assert data is None and errors and "HTTP 503" in errors[0]
    assert_clean(errors[0], SECRET, SESSION)


def test_lookup_chain_redacts_service_messages(mock_net):
    mock_net(lambda req: httpx.Response(200, text=f"<HamQTH><session><error>bad {html.escape(SECRET)}</error>"
                                                   "</session></HamQTH>"))
    _d, _s, errors = clients.lookup_chain("BA1AA", [clients.HamQth("u", SECRET)])
    assert errors and "bad" in errors[0]
    assert_clean(errors[0], SECRET)


# ------------------------------------------------------------------ services echoing a secret

def test_echoed_secrets_are_removed_but_reasons_kept(mock_net):
    mock_net(lambda req: httpx.Response(200, text=f"RESULT=FAIL&REASON=invalid api key {KEY}&COUNT=0"))
    ok, _logid, reason = services.qrz_insert(KEY, {"CALL": "BA1AA"})
    assert not ok and "invalid api key" in reason
    assert_clean(reason, KEY)

    mock_net(lambda req: httpx.Response(200, text=f"<xml><error>Wrong code {SECRET}</error></xml>"))
    ok, msg = services.hrdlog_upload("BI1ZZZ", SECRET, {"CALL": "BA1AA"})
    assert not ok and "Wrong code" in msg
    assert_clean(msg, SECRET)

    mock_net(lambda req: httpx.Response(200, text=f"<html>Error: No such Username/Password {SECRET}</html>"))
    with pytest.raises(services.ServiceError) as ei:
        services.eqsl_upload("u", SECRET, [{"CALL": "BA1AA"}])
    assert "No such Username" in str(ei.value)
    assert_clean(str(ei.value), SECRET)

    mock_net(lambda req: httpx.Response(200, text=f"<html>Login {SECRET} failed</html>"))
    with pytest.raises(services.ServiceError) as ei:
        services.lotw_download("u", SECRET)
    assert_clean(str(ei.value), SECRET)


# ------------------------------------------------------------------ destinations

def test_redirect_to_http_or_other_host_is_refused_before_sending(mock_net):
    for location in (f"http://www.eqsl.cc/x?Password={quote_plus(SECRET)}", "https://untrusted.invalid/x",
                     "https://user:pw@www.eqsl.cc/x", "https://www.eqsl.cc:8443/x"):
        seen = mock_net(lambda req, loc=location: httpx.Response(302, headers={"Location": loc}))
        with pytest.raises(net.NetError) as ei:
            services.eqsl_download("u", SECRET)
        assert len(seen) == 1, location  # the second hop never reached the transport
        assert "refused" in str(ei.value)
        assert_clean(str(ei.value), SECRET)


def test_redirect_loop_is_capped(mock_net):
    seen = mock_net(lambda req: httpx.Response(302, headers={"Location": "https://www.eqsl.cc/again"}))
    with pytest.raises(net.NetError) as ei:
        services.eqsl_download("u", SECRET)
    assert "too many redirects" in str(ei.value) and len(seen) <= 6


def test_eqsl_adif_link_rules(mock_net):
    adi = "<EOH><CALL:5>BA1AA<QSO_DATE:8>20261010<TIME_ON:4>1519<BAND:2>2M<MODE:2>FM<EOR>"

    def page(link):
        def h(req):
            if req.url.path.endswith(".adi"):
                return httpx.Response(200, text=adi)
            return httpx.Response(200, text=f'<a href="{link}">your log</a>')
        return h

    seen = mock_net(page("../downloadedfiles/x.adi"))
    _h, recs = services.eqsl_download("u", SECRET)
    assert recs and str(seen[1].url) == "https://www.eqsl.cc/downloadedfiles/x.adi"
    seen = mock_net(page("http://www.eqsl.cc/downloadedfiles/y.adi"))
    services.eqsl_download("u", SECRET)
    assert seen[1].url.scheme == "https"  # same host: upgraded to HTTPS
    seen = mock_net(page("http://untrusted.invalid/log.adi"))
    with pytest.raises(net.NetError):
        services.eqsl_download("u", SECRET)
    assert len(seen) == 1


# ------------------------------------------------------------------ logging

def test_httpx_info_logs_carry_no_secret(mock_net, caplog):
    mock_net(lambda req: httpx.Response(200, text="<EOH>"))
    caplog.set_level(logging.INFO, logger="httpx")
    services.lotw_download("u", SECRET)
    assert "HTTP Request" in caplog.text
    assert_clean(caplog.text, SECRET)


# ------------------------------------------------------------------ sync controller, log file and backups

def _file_bytes(path) -> bytes:
    out = b""
    for p in (path, path.with_name(path.name + "-wal")):
        if p.exists():
            out += p.read_bytes()
    return out


@pytest.fixture
def ctrl(qtbot, tmp_path, monkeypatch, keyring_store):
    from PySide6.QtWidgets import QMessageBox, QWidget

    from nwologger.ui.prefs import Prefs
    from nwologger.ui.sync_controller import SyncController, SyncResults

    prefs = Prefs(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    for user_key, value in (("lotw_user", "u"), ("eqsl_user", "u"), ("hrdlog_call", "BI1ZZZ")):
        prefs.set(user_key, value)
    for key in ("lotw_password", "eqsl_password", "hrdlog_code"):
        prefs.set_secret(key, SECRET)
    prefs.set_qrz_api_key("BI1ZZZ", KEY)
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    db.save_profile({"name": "BI1ZZZ", "station_callsign": "BI1ZZZ", "eqsl_qth_nickname": "Home",
                     "is_default": True})
    db.insert(qsomod.normalize({"call": "BA1AA", "time_on": "2026-10-10T15:19:00Z", "mode": "FM", "band": "2m",
                                "station_callsign": "BI1ZZZ"}))
    parent = QWidget()
    qtbot.addWidget(parent)
    shown: list[str] = []
    for name in ("warning", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda _p, _t, text, *a, **k: shown.append(text)))
    add = SyncResults.add  # what the Sync results window shows, after redaction
    monkeypatch.setattr(SyncResults, "add", lambda self, svc, kind, text, **k: shown.append(text) or
                        add(self, svc, kind, text, **k))
    c = SyncController(db, prefs, parent)
    c.shown = shown
    yield c
    db.close()


def test_failed_downloads_store_and_show_no_password(ctrl, qtbot, mock_net, tmp_path):
    mock_net(lambda req: httpx.Response(503))
    for svc in ("lotw", "eqsl"):
        ctrl.download(svc)
        qtbot.waitUntil(lambda s=svc: s + "down" not in ctrl.busy)
    rows = ctrl.db.sync_history()
    assert len(rows) == 2 and all("HTTP 503" in r["message"] for r in rows)
    ctrl.download("lotw")  # a failed run no longer leaves the service marked busy
    assert "lotwdown" in ctrl.busy
    qtbot.waitUntil(lambda: "lotwdown" not in ctrl.busy)
    rows = ctrl.db.sync_history()
    assert len(rows) == 3
    backup = ctrl.db.backup("test")
    for text in [*ctrl.shown, *(r["message"] for r in rows)]:
        assert_clean(text, SECRET)
    for blob in (_file_bytes(tmp_path / "log.sqlite"), backup.read_bytes()):
        for f in forms(SECRET):
            assert f.encode() not in blob


def test_per_record_upload_errors_are_redacted(ctrl, qtbot, mock_net):
    mock_net(lambda req: httpx.Response(200, text=f"RESULT=FAIL&REASON=bad key {KEY}" if req.url.host ==
                                        "logbook.qrz.com" else f"<error>bad code {SECRET}</error>"))
    for svc in ("qrz", "hrdlog"):
        ctrl.upload(svc, [1], quiet=True)
        qtbot.waitUntil(lambda s=svc: s + "up" not in ctrl.busy)
    rows = ctrl.db.sync_history()
    assert len(rows) == 2 and any("bad key" in r["message"] for r in rows)
    for text in [*ctrl.shown, *(r["message"] for r in rows)]:
        assert_clean(text, SECRET, KEY)


def test_lotw_upload_removes_its_temp_files(ctrl, qtbot, monkeypatch, tmp_path):
    import tempfile

    from nwologger.core.sync import tqsl

    made = []
    real = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **kw: made.append(real(**kw)) or made[-1])
    monkeypatch.setattr(tqsl, "find_tqsl", lambda *_a: "/fake/tqsl")
    monkeypatch.setattr(tqsl, "sign_and_upload", lambda *_a, **_k: (0, "ok"))
    ctrl.db.save_profile({"name": "BI1ZZZ", "station_callsign": "BI1ZZZ", "lotw_location": "Home",
                          "is_default": True})
    ctrl.upload("lotw", [1], quiet=True)
    qtbot.waitUntil(lambda: "lotwup" not in ctrl.busy)
    import os

    assert made and not os.path.exists(made[0])


def test_sync_log_add_strips_urls(tmp_path):
    d = dbm.LogDB(tmp_path / "l.sqlite", backup_on_open=False)
    d.sync_log_add("LoTW", "download", "2026-10-16T00:00:00Z",
                   message=f"for url 'https://lotw.arrl.org/x?login=u&password={quote_plus(SECRET)}'")
    assert_clean(d.sync_history()[0]["message"], SECRET)
    d.close()




# ------------------------------------------------------------------ ADIF records with credential tags

def test_credential_tags_never_imported_or_sent(tmp_path):
    data = ("<EQSL_USER:1>u<EQSL_PSWD:9>headerpw1<EOH>"
            "<CALL:5>BA1AA<QSO_DATE:8>20261010<TIME_ON:4>1519<BAND:2>2M<MODE:2>FM"
            "<EQSL_PSWD:20>FAKE_IMPORTED_SECRET<APP_X_PASSWORD:3>abc<APP_OTHER_TAG:2>ok<EOR>")
    dropped: list[str] = []
    _h, qsos = importer.read_adif(data, dropped=dropped)
    assert sorted(dropped) == ["APP_X_PASSWORD", "EQSL_PSWD"]
    extra = qsomod.load_extra(qsos[0])
    assert "EQSL_PSWD" not in extra and extra.get("APP_OTHER_TAG") == "ok"
    # A QSO stored by 0.1.0 with the tag in extra: still never written out.
    q = dict(qsos[0], extra=qsomod.dump_extra({"EQSL_PSWD": "FAKE_IMPORTED_SECRET", "APP_OTHER_TAG": "ok"}))
    assert "EQSL_PSWD" not in qsomod.to_adif(q)
    cat = sats.load()
    for svc in ("lotw", "eqsl", "qrz", "hrdlog"):
        rec = apply.upload_record(q, svc, cat)
        assert "FAKE_IMPORTED_SECRET" not in "".join(rec.values()) and "APP_OTHER_TAG" not in rec


# ------------------------------------------------------------------ keychain

def test_keyring_backend_must_be_a_system_keychain():
    def backend(module):
        return type("Keyring", (), {"__module__": module})()

    assert secrets._secure(backend("keyring.backends.macOS"))
    assert secrets._secure(backend("keyring.backends.Windows"))
    for bad in ("keyrings.alt.file", "keyring.backends.fail", "keyring.backends.null", "my.plaintext"):
        assert not secrets._secure(backend(bad))
    chain = type("ChainerBackend", (), {"__module__": "keyring.backends.chainer"})()
    chain.backends = [backend("keyring.backends.macOS")]
    assert secrets._secure(chain)
    chain.backends.append(backend("keyrings.alt.file"))
    assert not secrets._secure(chain)
    chain.backends = []
    assert not secrets._secure(chain)


def test_delete_reports_failure(keyring_store):
    assert secrets.set("lotw", "u", "pw-1234")
    keyring_store.refuse_delete = True
    assert not secrets.delete("lotw", "u")  # still stored: not a success
    keyring_store.refuse_delete = False
    assert secrets.delete("lotw", "u") and secrets.get("lotw", "u") is None
    assert secrets.delete("lotw", "u")  # nothing stored is fine


def test_account_rename_deletes_or_moves_the_old_secret(tmp_path, keyring_store):
    from nwologger.ui.prefs import Prefs

    p = Prefs(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    secrets.set("lotw", "olduser", "pw-old-1")
    assert p.account_renamed("lotw_password", "olduser", "newuser")
    assert secrets.get("lotw", "olduser") is None and secrets.get("lotw", "newuser") is None
    secrets.set("eqsl", "MyName", "pw-eqsl-1")
    assert p.account_renamed("eqsl_password", "MyName", "myname")  # same account, other case: moved
    assert secrets.get("eqsl", "MyName") is None and secrets.get("eqsl", "myname") == "pw-eqsl-1"


def test_prefs_dialog_masks_keys_and_forgets(qtbot, tmp_path, keyring_store):
    from PySide6.QtWidgets import QLineEdit

    from nwologger.ui import prefs_dialog
    from nwologger.ui.prefs import Prefs

    p = Prefs(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    p.set("lotw_user", "u")
    p.set_secret("lotw_password", "pw-lotw-1")
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    db.save_profile({"name": "A", "station_callsign": "BI1ZZZ", "is_default": True})
    db.save_profile({"name": "B", "station_callsign": "BI1YYY"})
    p.set_qrz_api_key("BI1YYY", "OLD-KEY-0001")
    dlg = prefs_dialog.PrefsDialog(db, p)
    qtbot.addWidget(dlg)
    # The saved password is never put in the field; the placeholder says it is saved.
    assert dlg.lotw_pass.edit.text() == "" and "saved" in dlg.lotw_pass.edit.placeholderText()
    rows = {dlg.qrz_keys.item(r, 0).text(): r for r in range(dlg.qrz_keys.rowCount())}
    item = dlg.qrz_keys.item(rows["BI1ZZZ"], 1)
    delegate = dlg.qrz_keys.itemDelegateForColumn(1)
    editor = delegate.createEditor(dlg.qrz_keys, None, dlg.qrz_keys.model().index(rows["BI1ZZZ"], 1))
    assert editor.echoMode() == QLineEdit.EchoMode.Password
    editor.setText("NEW-KEY-0002")
    delegate.setModelData(editor, dlg.qrz_keys.model(), dlg.qrz_keys.model().index(rows["BI1ZZZ"], 1))
    assert item.text() == prefs_dialog.MASKED  # never shown, not even the new key
    dlg.qrz_keys.cellWidget(rows["BI1YYY"], 2).click()  # Forget
    dlg.lotw_pass.forget_btn.click()
    dlg._save()
    assert p.qrz_api_key("BI1ZZZ") == "NEW-KEY-0002" and p.qrz_api_key("BI1YYY") is None
    assert p.secret("lotw_password") is None
    db.close()


def test_deleting_the_last_profile_with_a_call_removes_its_key(qtbot, tmp_path, keyring_store):
    from nwologger.ui.prefs import Prefs
    from nwologger.ui.prefs_dialog import ProfilesPage

    p = Prefs(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    a = db.save_profile({"name": "Home", "station_callsign": "BI1ZZZ", "is_default": True})
    b = db.save_profile({"name": "Portable", "station_callsign": "BI1ZZZ"})
    p.set_qrz_api_key("BI1ZZZ", "KEY-0001")
    page = ProfilesPage(db, p)
    qtbot.addWidget(page)
    page.current_id = b
    page._delete()
    assert p.qrz_api_key("BI1ZZZ") == "KEY-0001"  # Home still uses the call
    page.current_id = a
    page._delete()
    assert p.qrz_api_key("BI1ZZZ") is None
    db.close()


def test_a_changed_profile_call_moves_or_removes_its_qrz_key(qtbot, tmp_path, keyring_store, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from nwologger.ui.prefs import Prefs
    from nwologger.ui.prefs_dialog import PrefsDialog

    p = Prefs(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    db.save_profile({"name": "Home", "station_callsign": "BI1ZZQ", "is_default": True})
    p.set_qrz_api_key("BI1ZZQ", "KEY-0001")
    dlg = PrefsDialog(db, p)
    qtbot.addWidget(dlg)
    answers = [QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No]
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *_a, **_k: answers.pop(0)))
    page = dlg.profiles_page
    page.edits["station_callsign"].setText("BI1ZZZ")  # a corrected call: the key moves
    page._save()
    assert p.qrz_api_key("BI1ZZZ") == "KEY-0001" and p.qrz_api_key("BI1ZZQ") is None
    assert dlg.qrz_keys.item(0, 0).text() == "BI1ZZZ" and dlg.qrz_keys.item(0, 1).text() == "••••••••"
    page.edits["station_callsign"].setText("BA1XYZ")  # another call with its own logbook: the old key goes
    page._save()
    assert p.qrz_api_key("BI1ZZZ") is None and p.qrz_api_key("BA1XYZ") is None and not answers
    assert dlg.qrz_keys.item(0, 0).text() == "BA1XYZ"
    db.close()


def test_tests_use_the_fake_keychain():
    assert type(secrets._keyring()).__name__ == "FakeKeyring"


# ------------------------------------------------------------------ round 2: logging

def test_http_loggers_stay_quiet_and_mask_headers(caplog):
    trace = ("receive_response_headers.complete return_value=(b'HTTP/1.1', 302, b'Found', [(b'Location', "
             f"b'https://www.eqsl.cc/x?Password=FAKEPW1'), (b'Set-Cookie', b'sid=FAKESID1'), (b'X-Echo', "
             f"b'{quote(SECRET, safe='')}')])")
    lg = logging.getLogger("httpcore.http11")
    caplog.set_level(logging.DEBUG)  # an app-wide DEBUG setup: httpcore still prints nothing
    lg.debug(trace)
    assert "receive_response_headers" not in caplog.text
    caplog.set_level(logging.DEBUG, logger="httpcore.http11")  # deliberately turned on
    with net._in_flight([SECRET]):
        lg.debug(trace)
    assert "receive_response_headers" in caplog.text
    assert "FAKEPW1" not in caplog.text and "FAKESID1" not in caplog.text
    assert_clean(caplog.text, SECRET)
    assert not net.active_secrets()


def test_httpx_info_masks_secrets_echoed_into_paths(mock_net, caplog):
    echo = f"https://www.eqsl.cc/{quote(SECRET, safe='')}/inbox"
    mock_net(lambda req: httpx.Response(302, headers={"Location": echo}) if "UserName" in req.url.params
             else httpx.Response(200, text="nothing new"))
    caplog.set_level(logging.INFO, logger="httpx")
    services.eqsl_download("u", SECRET)
    assert caplog.text.count("HTTP Request") == 2
    assert_clean(caplog.text, SECRET)


# ------------------------------------------------------------------ round 2: history, backups, old imports











# ------------------------------------------------------------------ round 2: what uploads send

def test_uploads_send_standard_fields_only():
    from nwologger.core.fields import ADIF_STANDARD

    q = qsomod.normalize({"call": "BA1AA", "time_on": "2026-10-10T15:19:00Z", "mode": "FM", "band": "2m",
                          "gridsquare": "ON80", "comment": "FAKE_SAVED_KEY_1"})
    q["extra"] = qsomod.dump_extra({"APP_HRDLOG_CODE": "x1", "APP_QRZ_SESSION_KEY": "x2", "APP_OTHER_TAG": "ok",
                                    "USERDEF1": "u", "MY_CITY": "Beijing"})
    cat = sats.load()
    for svc in ("lotw", "eqsl", "qrz", "hrdlog"):
        prof = {"eqsl_qth_nickname": "Home"} if svc == "eqsl" else None
        rec = apply.upload_record(q, svc, cat, prof)
        assert set(rec) <= ADIF_STANDARD | {"APP_EQSL_QTH_NICKNAME"}, svc
        assert rec["MY_CITY"] == "Beijing" and rec["GRIDSQUARE"] == "ON80"
        assert ("APP_EQSL_QTH_NICKNAME" in rec) == (svc == "eqsl")
    out = qsomod.to_adif(q)  # an ADIF file is your own: other programs' fields stay, credential names don't
    assert out["APP_OTHER_TAG"] == "ok" and out["USERDEF1"] == "u" and out["COMMENT"] == "FAKE_SAVED_KEY_1"
    assert "APP_HRDLOG_CODE" not in out and "APP_QRZ_SESSION_KEY" not in out

