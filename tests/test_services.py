"""Service clients against canned responses (no network: real httpx requests over a mock transport)."""

from __future__ import annotations

import httpx
import pytest

from qhlogger.core import db as dbm
from qhlogger.core import qso, sats
from qhlogger.core.lookup import clients
from qhlogger.core.sync import apply, services


def test_qrz_xml_lookup(mock_net):
    login = ('<?xml version="1.0"?><QRZDatabase xmlns="http://xmldata.qrz.com"><Session><Key>abc</Key>'
             '</Session></QRZDatabase>')
    found = ('<?xml version="1.0"?><QRZDatabase xmlns="http://xmldata.qrz.com"><Callsign><call>BA1AA</call>'
             '<fname>Jian</fname><name>Wu</name><addr2>Beijing</addr2><country>China</country><dxcc>318</dxcc>'
             '<grid>ON80eb</grid><cqzone>24</cqzone><ituzone>44</ituzone></Callsign><Session><Key>abc</Key>'
             '</Session></QRZDatabase>')
    seen = mock_net(lambda req: httpx.Response(200, text=login if "username" in req.url.params else found))
    d = clients.QrzXml("u", "p").lookup("BA1AA")
    assert d["first_name"] == "Jian" and d["last_name"] == "Wu" and d["dxcc"] == 318 and d["gridsquare"] == "ON80eb"
    assert [r.url.host for r in seen] == ["xmldata.qrz.com"] * 2 and seen[1].url.params["s"] == "abc"


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
