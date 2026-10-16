"""Core logic tests. All data here is synthetic."""

from __future__ import annotations

import sqlite3

import pytest

from nwologger.core import adif, bands, callsign, dupes, dxcc, importer, mldx, modes, qso, regions, search
from nwologger.core import db as dbm
from nwologger.core.modes import Relation


@pytest.fixture
def db(tmp_path):
    d = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    yield d
    d.close()


def make(call="BA1AA", t="2026-10-10T15:19:00Z", mode="FM", band="2m", **kw):
    q = {"call": call, "time_on": t, "mode": mode, "band": band}
    q.update(kw)
    return q


# ------------------------------------------------------------------ modes

@pytest.mark.parametrize("a,b,rel", [
    ("USB", "USB", Relation.SAME),
    ("USB", "LSB", Relation.SIBLING),
    ("SSB", "USB", Relation.COULD_BE),
    ("DIGI", "FT8", Relation.COULD_BE),
    ("FT8", "FT4", Relation.SIBLING),
    ("MFSK", "FT4", Relation.COULD_BE),
    ("FM", "SSB", Relation.SIBLING),
    ("CW", "USB", Relation.UNRELATED),
    ("FT8", "CW", Relation.UNRELATED),
])
def test_mode_relation(a, b, rel):
    assert modes.relation(modes.normalize(a), modes.normalize(b)) == rel


def test_mode_aliases():
    assert modes.normalize("usb") == ("SSB", "USB")
    assert modes.normalize("FT4") == ("MFSK", "FT4")
    assert modes.normalize("digi") == ("DATA", None)
    assert modes.normalize("C4FM") == ("DIGITALVOICE", "C4FM")
    assert modes.group("SSB", "USB") == "PHONE"
    assert modes.group("MFSK", "FT4") == "DATA"


def test_bands():
    assert bands.band_for_freq(145.92) == "2m"
    assert bands.band_for_freq(436.95) == "70cm"
    assert bands.band_for_freq(14.074) == "20m"
    assert bands.band_for_freq(99.0) is None


# ------------------------------------------------------------------ ADIF

def test_adif_parse_char_and_byte_lengths():
    text = "hdr <ADIF_VER:5>3.1.8 <EOH>\n<CALL:5>BA1AA <NAME:2>张三 <QSO_DATE:8>20261010 <TIME_ON:4>1519 <EOR>\n"
    _, recs = adif.parse(text)
    assert recs[0]["NAME"] == "张三"
    # Same record with UTF-8 byte length (6 bytes for two CJK characters).
    text_b = "<CALL:5>BA1AA <NAME:6>张三 <QSO_DATE:8>20261010 <TIME_ON:4>1519 <EOR>"
    _, recs = adif.parse(text_b.encode("utf-8"))
    assert recs[0]["NAME"] == "张三"
    assert recs[0]["QSO_DATE"] == "20261010"


def test_adif_roundtrip_keeps_unknown_fields():
    rec = {"CALL": "BA1AA", "QSO_DATE": "20261010", "TIME_ON": "151900", "MODE": "SSB", "SUBMODE": "USB",
           "FREQ": "14.2", "NAME": "Wu Jian Qi", "APP_FOO_BAR": "x", "MY_ODD_FIELD": "keep"}
    q = qso.from_adif(rec)
    assert (q["first_name"], q["last_name"]) == ("Wu Jian", "Qi")
    assert q["band"] == "20m"
    out = qso.to_adif(q)
    assert out["NAME"] == "Wu Jian Qi"
    assert out["APP_FOO_BAR"] == "x" and out["MY_ODD_FIELD"] == "keep"
    assert out["SUBMODE"] == "USB"


def test_mldx_adif_qsl_sent_is_not_paper():
    q = qso.from_adif({"CALL": "BA1AA", "QSO_DATE": "20261010", "TIME_ON": "1519", "QSL_SENT": "Y"},
                      program_id="MacLoggerDX")
    assert q.get("qsl_sent") is None


def test_operator_fills_station_callsign():
    q = qso.from_adif({"CALL": "BA1AA", "QSO_DATE": "20261010", "TIME_ON": "1519", "OPERATOR": "bi1zzz"})
    assert q["station_callsign"] == "BI1ZZZ"


# ------------------------------------------------------------------ search

def test_wildcards(db):
    for c in ("BA1AA", "BA1BB", "BG8XX", "BA1AA/P"):
        db.insert(make(c))
    def n(spec):
        w, p = search.build_where(spec)
        return db.count(w, p)
    assert n({"quick": {"call": "BA1*"}}) == 3
    assert n({"quick": {"call": "B?1*"}}) == 3
    assert n({"quick": {"call": "*/P"}}) == 1
    assert n({"quick": {"call": "1A"}}) == 2  # no wildcard: contains


def test_vague_mode_search(db):
    for i, m in enumerate(["SSB", "USB", "LSB", "FT8", "FT4", "DIGI", "CW"]):
        db.insert(make(f"BA1A{chr(65 + i)}", mode=m))
    def n(mode, exact=False):
        w, p = search.build_where({"quick": {"mode": mode}, "mode_exact": exact})
        return db.count(w, p)
    assert n("SSB") == 3          # SSB, USB, LSB
    assert n("USB") == 2          # USB and SSB without submode
    assert n("DIGI") == 3         # FT8, FT4, DIGI
    assert n("FT8") == 2          # FT8 and DIGI
    assert n("USB", exact=True) == 1
    assert n("SSB", exact=True) == 1


# ------------------------------------------------------------------ dupes

def test_dupe_rules(db):
    db.insert(make("BA1AA", "2026-10-10T15:19:00Z", "USB", "20m"))
    def lvl(**kw):
        hit = dupes.check(db, qso.normalize(make(**kw)))
        return hit.level if hit else None
    assert lvl(call="BA1AA", t="2026-10-10T15:19:20Z", mode="USB", band="20m") == "DUPE"
    assert lvl(call="BA1AA", t="2026-10-10T15:20:00Z", mode="USB", band="20m") == "SUSPECT"  # > 30 s
    assert lvl(call="BA1AA", t="2026-10-10T15:19:10Z", mode="LSB", band="20m") == "SUSPECT"  # sibling
    assert lvl(call="BA1AA", t="2026-10-10T15:19:10Z", mode="SSB", band="20m") == "SUSPECT"  # could be
    assert lvl(call="BA1AA", t="2026-10-10T15:19:10Z", mode="CW", band="20m") is None
    assert lvl(call="BA1AA", t="2026-10-12T15:19:10Z", mode="USB", band="20m") is None  # > 24 h
    assert lvl(call="BA1AA", t="2026-10-10T15:19:10Z", mode="USB", band="40m") is None


def test_sat_dupes_need_same_satellite(db):
    db.insert(make("BA1AA", sat_name="SO-50"))
    hit = dupes.check(db, qso.normalize(make("BA1AA", "2026-10-10T15:19:10Z", sat_name="SO-50")))
    assert hit and hit.level == "DUPE"
    assert dupes.check(db, qso.normalize(make("BA1AA", "2026-10-10T15:19:10Z", sat_name="AO-91"))) is None
    assert dupes.check(db, qso.normalize(make("BA1AA", "2026-10-10T15:19:10Z"))) is None


def test_import_marks_but_keeps_everything(db):
    recs = [qso.normalize(make("BA1AA", "2026-10-10T15:19:00Z", "FT8", "20m")),
            qso.normalize(make("BA1AA", "2026-10-10T15:19:00Z", "FT8", "20m")),
            qso.normalize(make("BA1AA", "2026-10-10T15:25:00Z", "FT4", "20m"))]
    preview = importer.run(db, recs, dry_run=True)
    assert preview.added == 3 and db.count() == 0
    res = importer.run(db, recs)
    assert res.added == 3 and db.count() == 3
    assert (res.dupes, res.suspects) == (1, 1)
    flags = sorted(r[0] or "" for r in db.conn.execute("SELECT dupe_flag FROM qso"))
    assert flags == ["", "DUPE", "SUSPECT"]


def test_import_merge_updates_confirmation(db):
    db.insert(make("BA1AA", "2026-10-10T15:19:00Z", "USB", "20m"))
    conf = qso.normalize(make("BA1AA", "2026-10-10T15:21:00Z", "SSB", "20m", lotw_qsl_rcvd="Y",
                              lotw_qslrdate="2026-10-11"))
    res = importer.run(db, [conf], merge=True)
    assert res.merged == 1 and db.count() == 1
    assert db.get(1)["lotw_qsl_rcvd"] == "Y"


# ------------------------------------------------------------------ db

def test_undo_and_service_modified(db):
    qid = db.insert(make("BA1AA", qrzcom_qso_upload_status="Y"))
    db.update_many([qid], {"rst_sent": "55"}, label="Bulk edit")
    row = db.get(qid)
    assert row["rst_sent"] == "55" and row["qrzcom_qso_upload_status"] == "M"
    assert db.undo_last() == "Bulk edit"
    assert db.get(qid)["rst_sent"] is None and db.get(qid)["qrzcom_qso_upload_status"] == "Y"
    db.delete([qid])
    assert db.count() == 0
    db.undo_last()
    assert db.count() == 1


def test_partial_update_recomputes_band_and_mode(db):
    qid = db.insert(make("BA1AA", mode="USB", band="20m", freq=14.2))
    db.update(qid, {"freq": 7.1, "mode": "CW"})
    row = db.get(qid)
    assert (row["band"], row["mode"], row["submode"], row["mode_group"]) == ("40m", "CW", None, "CW")


# ------------------------------------------------------------------ callsigns / DXCC / provinces

def test_callsigns_and_dxcc():
    cty = dxcc.default()
    assert cty.resolve("BA1AA").dxcc == 318
    assert cty.resolve("VE3/BA1AA").dxcc == 1
    assert cty.resolve("BA1AA/P").dxcc == 318
    assert cty.resolve("DL1ABC/MM") is None
    assert callsign.wpx_prefix("BA1AA") == "BA1"
    assert callsign.wpx_prefix("LX/BA1AA") == "LX0"
    assert callsign.wpx_prefix("N8BJQ/KH6") == "KH6"


def test_china_provinces():
    assert regions.normalize_cn("新疆") == "XJ"
    assert regions.normalize_cn("Nei Mongol Zizhiqu") == "NM"
    assert regions.normalize_cn("江苏省") == "JS"
    assert regions.normalize_cn("在线") is None
    assert regions.guess_cn_province("BG8LAA") == "CQ"
    assert regions.guess_cn_province("BD6HAA") == "AH"
    assert regions.guess_cn_province("BH7ZAA") is None


# ------------------------------------------------------------------ MacLoggerDX

def _fake_mldx(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE qso_table_v008 (pk INTEGER PRIMARY KEY, my_grid TEXT, my_call TEXT, my_rig TEXT, "
                "call TEXT, first_name TEXT, last_name TEXT, street TEXT, city TEXT, county TEXT, state TEXT, "
                "postal_country TEXT, zip TEXT, grid TEXT, dxcc_country TEXT, iota TEXT, sota TEXT, cq_zone TEXT, "
                "itu TEXT, ten_ten TEXT, email TEXT, url TEXT, mode TEXT, band_rx TEXT, band_tx TEXT, "
                "rst_sent TEXT, rst_received TEXT, qsl_via TEXT, qsl_sent TEXT, qsl_received TEXT, srx TEXT, "
                "stx TEXT, comments TEXT, satellite TEXT, qso_start REAL, qso_done REAL, latitude REAL, "
                "longitude REAL, tx_frequency REAL, rx_frequency REAL, azimuth REAL, elevation REAL, power REAL, "
                "srx_numeric INTEGER, stx_numeric INTEGER, dxcc_id INTEGER, contest_id TEXT, my_sota TEXT, "
                "skcc TEXT, pota TEXT, my_pota TEXT, wwff TEXT, my_wwff TEXT, sig TEXT, my_sig TEXT, "
                "sig_info TEXT, my_sig_info TEXT, ext_val_1 TEXT, ext_val_2 TEXT, ext_val_3 TEXT, "
                "ext_val_4 TEXT, distance REAL)")
    con.execute(
        "INSERT INTO qso_table_v008 (pk, my_grid, my_call, call, first_name, last_name, mode, band_tx, band_rx, "
        "tx_frequency, rx_frequency, satellite, qso_start, qso_done, dxcc_id, state, ext_val_3, qsl_sent, "
        "qsl_received) VALUES (7, 'ON80da', 'BI1ZZZ', 'BA1AA', 'Wu', 'Jian', 'FM', '2M', '70CM', 145.92, "
        "436.95, 'RS95S', 1791645540, 1791645600, 318, '', '新疆', "
        "'Uploaded to eQSL 2026-10-10 14:52, Uploaded to LoTW 2026-10-11 10:00, Mail Card 2026-10-12, Y', "
        "'LoTW:20261013, QRZLogbook:20261014, eQSL.cc DownloadInBox:Y, Mai Card 2026-10-15')")
    con.commit()
    con.close()


def test_mldx_import(tmp_path, db):
    p = tmp_path / "MacLoggerDX.sql"
    _fake_mldx(p)
    assert mldx.is_mldx_db(p)
    qsos, rep = mldx.read(p)
    q = qsos[0]
    assert q["time_on"] == "2026-10-10T15:19:00Z"
    assert q["station_callsign"] == "BI1ZZZ"
    assert q["state"] == "XJ"
    assert (q["eqsl_qsl_sent"], q["eqsl_qslsdate"]) == ("Y", "2026-10-10")
    assert (q["lotw_qsl_sent"], q["lotw_qslsdate"]) == ("Y", "2026-10-11")
    assert (q["qsl_sent"], q["qslsdate"]) == ("Y", "2026-10-12")
    assert (q["lotw_qsl_rcvd"], q["lotw_qslrdate"]) == ("Y", "2026-10-13")
    assert (q["qrz_qsl_rcvd"], q["qrz_qslrdate"]) == ("Y", "2026-10-14")
    assert q["eqsl_qsl_rcvd"] == "Y"
    assert (q["qsl_rcvd"], q["qslrdate"]) == ("Y", "2026-10-15")
    assert q["sat_name"] == "RS95S" and q["prop_mode"] == "SAT"
    assert rep.tokens["bare Y (ignored)"] == 1 and not rep.unknown_tokens
    importer.run(db, qsos, key_tag=mldx.KEY_TAG)
    importer.run(db, qsos, key_tag=mldx.KEY_TAG)
    assert db.count() == 1


def test_sample_fixture_imports(db):
    from pathlib import Path

    data = (Path(__file__).parent / "fixtures" / "sample.adi").read_bytes()
    _h, qsos = importer.read_adif(data)
    res = importer.run(db, qsos)
    assert res.added == 17 and res.dupes == 1 and res.suspects >= 1
    assert db.count("sat_name = 'ARISS'") == 1


def test_similar_calls_ranking():
    from nwologger.core import similar

    idx = similar.CallIndex({"BA1PK": "2026-10-10T15:19:00Z", "BA1PKA": "2026-10-01T00:00:00Z",
                             "BA1PK/P": "2026-10-12T00:00:00Z"}, {"BA1PX": "QRZ.com"}, ["BA1PQ", "BA1PK", "XBA1P"])
    # Worked (most recent first), then known on QRZ.com / HamQTH, then MASTER.SCP; better matches first.
    assert [m.call for m in idx.find("BA1P")] == ["BA1PK/P", "BA1PK", "BA1PKA", "BA1PX", "BA1PQ", "XBA1P"]
    # A typo: one character changed.
    assert [m.call for m in idx.find("BA1PJ")] == ["BA1PK/P", "BA1PK", "BA1PX", "BA1PQ"]
    # A portable call matches its base call exactly.
    top = idx.find("BA1PK/P")[:2]
    assert {m.call for m in top} == {"BA1PK/P", "BA1PK"} and all(m.kind == similar.EXACT for m in top)
    assert idx.find("B") == []
    idx.set_scp(["BA1PQ", "BA1PX", "BA1PWX", "BA1PZ"])
    assert idx.online_candidates("BA1PW") == ["BA1PQ", "BA1PZ", "BA1PWX"]  # not BA1PX: already known
    assert idx.online_candidates("BA1PW", skip={"BA1PQ"}, limit=1) == ["BA1PZ"]
