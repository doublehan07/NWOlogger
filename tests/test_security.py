"""Secrets never reach dialogs, the log file, backups or logs. All secrets here are fake."""

from __future__ import annotations

import html
import logging
from urllib.parse import quote, quote_plus

import httpx
import pytest
from PySide6.QtCore import QSettings

from qhlogger.core import db as dbm
from qhlogger.core import importer, net, sats, secrets
from qhlogger.core import qso as qsomod
from qhlogger.core.lookup import clients
from qhlogger.core.sync import apply, services

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
    # Short or empty secrets don't blank out ordinary text; plain messages pass unchanged.
    assert net.redact("12 confirmations updated", ["12", ""]) == "12 confirmations updated"


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

    from qhlogger.ui.prefs import Prefs
    from qhlogger.ui.sync_controller import SyncController

    prefs = Prefs(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    for user_key, value in (("lotw_user", "u"), ("eqsl_user", "u"), ("hrdlog_call", "BI1ZZZ")):
        prefs.set(user_key, value)
    for key in ("lotw_password", "eqsl_password", "hrdlog_code"):
        prefs.set_secret(key, SECRET)
    prefs.set_qrz_api_key("BI1ZZZ", KEY)
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    db.insert(qsomod.normalize({"call": "BA1AA", "time_on": "2026-10-10T15:19:00Z", "mode": "FM", "band": "2m",
                                "station_callsign": "BI1ZZZ"}))
    parent = QWidget()
    qtbot.addWidget(parent)
    shown: list[str] = []
    for name in ("warning", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda _p, _t, text, *a, **k: shown.append(text)))
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

    from qhlogger.core.sync import tqsl

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


def test_old_passwords_cleaned_once_and_backups_listed(tmp_path):
    path = tmp_path / "log.sqlite"
    d = dbm.LogDB(path, backup_on_open=False)
    old = (f"Client error '503 Service Unavailable' for url 'https://lotw.arrl.org/lotwuser/lotwreport.adi?"
           f"login=u&password={quote_plus(SECRET)}&qso_query=1'")
    d.conn.execute("INSERT INTO sync_log(service, direction, started_at, errors, message) VALUES "
                   "('LoTW', 'download', '2026-10-10T00:00:00Z', 1, ?)", (old,))
    d.conn.execute("INSERT INTO sync_log(service, direction, started_at, message) VALUES "
                   "('eQSL', 'download', '2026-10-10T00:00:00Z', '3 updated')")
    d.conn.execute("DELETE FROM meta WHERE key='sync_log_scrubbed'")  # as written by 0.1.0
    leaked_backup = d.backup("manual")
    d.close()
    assert quote_plus(SECRET).encode() in leaked_backup.read_bytes()

    d = dbm.LogDB(path, backup_on_open=True)  # the open backup is taken after cleaning
    msgs = [r["message"] for r in d.sync_history()]
    assert "3 updated" in msgs and all(quote_plus(SECRET) not in m for m in msgs)
    assert d.meta("sync_log_scrubbed") == "1"
    assert quote_plus(SECRET).encode() not in _file_bytes(path)
    found = d.backups_with_secrets()
    assert [p for p, _n in found] == [leaked_backup]
    d.close()
    # Runs once: a later open does not rescan.
    d = dbm.LogDB(path, backup_on_open=False)
    assert d.meta("sync_log_scrubbed") == "1"
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
        assert "FAKE_IMPORTED_SECRET" not in "".join(rec.values()) and rec.get("APP_OTHER_TAG") == "ok"


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
    from qhlogger.ui.prefs import Prefs

    p = Prefs(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    secrets.set("lotw", "olduser", "pw-old-1")
    assert p.account_renamed("lotw_password", "olduser", "newuser")
    assert secrets.get("lotw", "olduser") is None and secrets.get("lotw", "newuser") is None
    secrets.set("eqsl", "MyName", "pw-eqsl-1")
    assert p.account_renamed("eqsl_password", "MyName", "myname")  # same account, other case: moved
    assert secrets.get("eqsl", "MyName") is None and secrets.get("eqsl", "myname") == "pw-eqsl-1"


def test_prefs_dialog_masks_keys_and_forgets(qtbot, tmp_path, keyring_store):
    from PySide6.QtWidgets import QLineEdit

    from qhlogger.ui import prefs_dialog
    from qhlogger.ui.prefs import Prefs

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
    from qhlogger.ui.prefs import Prefs
    from qhlogger.ui.prefs_dialog import ProfilesPage

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


def test_tests_use_the_fake_keychain():
    assert type(secrets._keyring()).__name__ == "FakeKeyring"
