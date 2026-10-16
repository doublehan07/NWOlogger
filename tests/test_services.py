"""Service clients against canned responses (no network: real httpx requests over a mock transport)."""

from __future__ import annotations

import httpx
import pytest

from nwologger.core import db as dbm
from nwologger.core import qso, sats
from nwologger.core.lookup import clients
from nwologger.core.sync import apply, services


def test_qrz_xml_lookup(mock_net):
    login = ('<?xml version="1.0"?><QRZDatabase xmlns="http://xmldata.qrz.com"><Session><Key>abc</Key>'
             '</Session></QRZDatabase>')
    found = ('<?xml version="1.0"?><QRZDatabase xmlns="http://xmldata.qrz.com"><Callsign><call>BA1AA</call>'
             '<fname>Jian</fname><name>Wu</name><addr2>Beijing</addr2><country>China</country><dxcc>318</dxcc>'
             '<grid>ON80eb</grid><cqzone>24</cqzone><ituzone>44</ituzone></Callsign><Session><Key>abc</Key>'
             '</Session></QRZDatabase>')
    seen = mock_net(lambda req: httpx.Response(200, text=login if b"username=" in req.content else found))
    d = clients.QrzXml("u", "p").lookup("BA1AA")
    assert d["first_name"] == "Jian" and d["last_name"] == "Wu" and d["dxcc"] == 318 and d["gridsquare"] == "ON80eb"
    assert [(r.method, r.url.host) for r in seen] == [("POST", "xmldata.qrz.com")] * 2
    # Password and session key go in the body, never in the URL.
    assert not seen[0].url.query and b"password=p" in seen[0].content
    assert not seen[1].url.query and b"s=abc" in seen[1].content


def test_hamqth_lookup(mock_net):
    login = '<HamQTH><session><session_id>s1</session_id></session></HamQTH>'
    found = ('<HamQTH version="2.8" xmlns="https://www.hamqth.com"><search><callsign>BA1AA</callsign>'
             '<adr_name>Jian Wu</adr_name><qth>Beijing</qth><adif>318</adif><grid>ON80eb</grid><cq>24</cq>'
             '<itu>44</itu></search></HamQTH>')
    mock_net(lambda req: httpx.Response(200, text=login if "u" in req.url.params else found))
    d = clients.HamQth("u", "p").lookup("BA1AA")
    assert d["first_name"] == "Jian" and d["last_name"] == "Wu" and d["cqz"] == 24


def test_qrz_logbook_parse():
    text = "RESULT=OK&COUNT=1&ADIF=&lt;call:5&gt;BA1AA &lt;app_qrzlog_logid:3&gt;123 &lt;eor&gt;"
    res = services._qrz_parse(text)
    assert res["RESULT"] == "OK" and "<call:5>BA1AA" in res["ADIF"]


def test_eqsl_upload_result(mock_net):
    body = "<html>Result: 2 out of 3 records added<br>Warning: Y=2026 M=10 D=10 BA1AA 20M FM Bad record: Duplicate</html>"
    mock_net(lambda req: httpx.Response(200, text=body))
    added, total, lines = services.eqsl_upload("u", "p", [{"CALL": "BA1AA"}])
    assert (added, total) == (2, 3) and any("Duplicate" in ln for ln in lines)


def test_lotw_failed_login(mock_net):
    mock_net(lambda req: httpx.Response(200, text="<html>Username/password incorrect</html>"))
    with pytest.raises(services.ServiceError):
        services.lotw_download("u", "p")


def test_apply_lotw_and_eqsl_sat_names(tmp_path):
    d = dbm.LogDB(tmp_path / "l.sqlite", backup_on_open=False)
    d.insert(qso.normalize({"call": "BA1AA", "time_on": "2026-10-10T15:19:00Z", "mode": "FM", "band": "2m",
                            "sat_name": "ARISS"}))
    cat = sats.load()
    assert cat.to_service("eqsl", "ARISS") == "ISS" and cat.to_service("lotw", "ARISS") == "ARISS"
    lotw = [{"CALL": "BA1AA", "QSO_DATE": "20261010", "TIME_ON": "152100", "BAND": "2M", "MODE": "FM",
             "PROP_MODE": "SAT", "SAT_NAME": "ARISS", "QSL_RCVD": "Y", "QSLRDATE": "20261012"}]
    r = apply.apply_lotw_qsls(d, lotw, cat)
    assert r.matched == 1
    row = d.get(1)
    assert row["lotw_qsl_rcvd"] == "Y" and row["lotw_qslrdate"] == "2026-10-12" and row["qsl_rcvd"] is None
    eq = [{"CALL": "BA1AA", "QSO_DATE": "20261010", "TIME_ON": "1519", "BAND": "2M", "MODE": "FM",
           "PROP_MODE": "SAT", "SAT_NAME": "ISS", "QSL_SENT": "Y", "APP_EQSL_AG": "Y"}]
    r = apply.apply_eqsl(d, eq, cat)
    assert r.matched == 1 and d.get(1)["eqsl_qsl_rcvd"] == "Y" and d.get(1)["eqsl_ag"] == "Y"
    rec = apply.upload_record(d.get(1), "eqsl", cat)
    assert rec["SAT_NAME"] == "ISS"
    d.close()


# ------------------------------------------------------------------ found with real accounts (2026-10-16)

def test_upload_rule_is_one_rule(tmp_path):
    from nwologger.core import search, uploads

    d = dbm.LogDB(tmp_path / "l.sqlite", backup_on_open=False)
    statuses = [None, "Y", "N", "M", "I", "R", "Q"]
    for i, st in enumerate(statuses):
        for prop in (None, "RPT"):
            d.insert(qso.normalize({"call": f"BA{i}AA", "time_on": f"2026-10-1{i}T10:00:00Z", "mode": "FM",
                                    "band": "2m", "prop_mode": prop, "lotw_qsl_sent": st, "eqsl_qsl_sent": st,
                                    "qrzcom_qso_upload_status": st, "hrdlog_qso_upload_status": st}))
    rows = list(d.iter_all())
    for svc in uploads.SERVICES:
        by_rule = {q["id"] for q in rows if uploads.needs_upload(q, svc)}
        assert {q["id"] for q in apply.pending(d, svc)} == by_rule, svc
        assert set(d.select_ids(*search.preset(f"not_{svc}"), [("id", False)])) == by_rule, svc
    assert not any(uploads.needs_upload(q, "lotw") for q in rows if q["prop_mode"] == "RPT")
    assert {q["id"] for q in rows if uploads.needs_upload(q, "lotw") and q["lotw_qsl_sent"] in ("Y", "I")} == set()
    anywhere = {q["id"] for q in rows if uploads.missing(q)}
    assert set(d.select_ids(*search.preset("not_all"), [("id", False)])) == anywhere
    d.close()


def test_qrz_fetch_is_always_a_full_fetch(mock_net):
    logbook = [1, 2, 3, 4]  # log ids; AFTERLOGID:n returns ids from n on, MAX at a time

    def h(req):
        opts = dict(o.split(":", 1) for o in dict(httpx.QueryParams(req.content.decode()))["OPTION"].split(","))
        ids = [i for i in logbook if i >= int(opts["AFTERLOGID"])][:int(opts["MAX"])]
        if not ids:
            return httpx.Response(200, text="RESULT=FAIL&COUNT=0")  # past the end, as QRZ.com answers
        adi = "".join(f"<CALL:5>BA1AA<APP_QRZLOG_LOGID:{len(str(i))}>{i}<EOR>" for i in ids)
        return httpx.Response(200, text=f"RESULT=OK&COUNT={len(ids)}&ADIF={adi}")

    seen = mock_net(h)
    recs = services.qrz_fetch("FAKE-KEY", page=2)
    assert len(recs) == 4 and len(seen) == 3
    assert all(b"MODSINCE" not in r.content for r in seen)


def test_eqsl_cursor_starts_a_day_early(qtbot, tmp_path, mock_net, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QMessageBox, QWidget

    from nwologger.core import timeutil
    from nwologger.ui.prefs import Prefs
    from nwologger.ui.sync_controller import SyncController

    prefs = Prefs(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    prefs.set("eqsl_user", "u")
    prefs.set_secret("eqsl_password", "pw-eqsl-1")
    d = dbm.LogDB(tmp_path / "l.sqlite", backup_on_open=False)
    d.save_profile({"name": "BI1ZZZ", "station_callsign": "BI1ZZZ", "eqsl_qth_nickname": "Home"})
    parent = QWidget()
    qtbot.addWidget(parent)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    mock_net(lambda req: httpx.Response(200, text="<html>Your inbox has no new cards</html>"))
    c = SyncController(d, prefs, parent)
    c.download("eqsl")
    qtbot.waitUntil(lambda: "eqsldown" not in c.busy)
    yesterday = (timeutil.now_utc() - __import__("datetime").timedelta(days=1)).strftime("%Y%m%d")
    assert d.sync_get("eqsl", "rcvd_since") == yesterday
    d.close()
