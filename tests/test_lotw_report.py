"""A LoTW report saved as a file (lotwreport.adi): its QSL fields are LoTW's, and it is applied like a download."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QWidget

from nwologger.core import adif, importer, sats
from nwologger.core import db as dbm
from nwologger.core import qso as qsomod
from nwologger.core.sync import apply
from nwologger.ui import import_dialog, sync_controller
from nwologger.ui.prefs import Prefs

HEAD = """ARRL Logbook of the World Status Report
Generated at 2026-10-18 07:33:33
for bi1zzz
Query:
    QSL ONLY: NO
QSO RX SINCE: 1900-01-01 00:00:00 (user supplied value)

<PROGRAMID:4>LoTW
<APP_LoTW_LASTQSORX:19>2026-10-16 14:37:32

<APP_LoTW_NUMREC:1>4

<eoh>
"""


def _field(tag: str, value: str, comment: str = "") -> str:
    return f"<{tag}:{len(value)}>{value}" + (f" // {comment}" if comment else "") + "\n"


def record(call: str, date: str, time: str, *, confirmed: str | None = None, grid: str | None = None,
           sat: str | None = "RS-44", mode: str = "SSB") -> str:
    out = _field("APP_LoTW_OWNCALL", "BI1ZZZ") + _field("STATION_CALLSIGN", "BI1ZZZ")
    out += _field("MY_GRIDSQUARE", "ON80DA") + _field("MY_STATE", "BJ", "Beijing")
    out += _field("CALL", call) + _field("BAND", "2M") + _field("FREQ", "145.96100") + _field("MODE", mode)
    out += _field("QSO_DATE", date)
    out += _field("APP_LoTW_RXQSO", f"{date[:4]}-{date[4:6]}-{date[6:]} 14:48:07", "QSO record inserted/modified")
    out += _field("TIME_ON", time)
    if sat:
        out += _field("PROP_MODE", "SAT") + _field("SAT_NAME", sat)
    out += _field("QSL_RCVD", "Y" if confirmed else "N")
    if confirmed:
        out += _field("QSLRDATE", confirmed)
        out += _field("APP_LoTW_RXQSL", f"{confirmed[:4]}-{confirmed[4:6]}-{confirmed[6:]} 13:53:39", "QSL matched")
        out += _field("DXCC", "318") + _field("COUNTRY", "CHINA")
        if grid:
            out += _field("GRIDSQUARE", grid)
    return out + "<eor>\n\n"


# A and B are in the log, C and D are not; A and C are confirmed (three days after the QSO), B and D are not.
REPORT = HEAD + record("BA1TSA", "20261010", "094150", confirmed="20261013", grid="OM89AA") + \
    record("BG2TSB", "20261011", "101500") + record("BH4TSC", "20261012", "113000", confirmed="20261015") + \
    record("BD7TSD", "20261012", "120000", sat=None, mode="FT8")


def plain(call: str, time_on: str, **kw) -> dict:
    """A QSO as typed into the log: nothing from LoTW yet."""
    q = {"call": call, "time_on": time_on, "band": "2m", "mode": "SSB", "prop_mode": "SAT", "sat_name": "RS-44",
         "station_callsign": "BI1ZZZ"}
    q.update(kw)
    return qsomod.normalize(q)


@pytest.fixture
def log(tmp_path):
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    ids = {"A": db.insert(plain("BA1TSA", "2026-10-10T09:41:50Z")),
           "B": db.insert(plain("BG2TSB", "2026-10-11T10:15:00Z")),
           "other": db.insert(plain("BI8TSE", "2026-10-09T08:00:00Z", qsl_rcvd="Y", qslrdate="2026-10-12"))}
    yield db, ids
    db.close()


def _records(text: str = REPORT) -> list[dict[str, str]]:
    return adif.parse(text)[1]


# ------------------------------------------------------------------ recognising a report
def test_a_report_is_recognised_by_its_program_or_its_lotw_tags():
    header, records = adif.parse(REPORT)
    assert importer.is_lotw_report(header, records)
    no_header = REPORT.replace("<PROGRAMID:4>LoTW\n", "")
    assert importer.is_lotw_report(*adif.parse(no_header))  # every record has LoTW's received time
    ordinary = "<ADIF_VER:5>3.1.4\n<PROGRAMID:9>NWOlogger\n<EOH>\n" + \
        "<CALL:6>BA1TSA<QSO_DATE:8>20261010<TIME_ON:4>0941<BAND:2>2M<MODE:3>SSB<QSL_RCVD:1>Y<QSLRDATE:8>20261013<EOR>"
    assert not importer.is_lotw_report(*adif.parse(ordinary))
    assert not importer.is_lotw_report({}, [])
    # NWOlogger's own export of QSOs downloaded from LoTW carries LoTW's tags, with its own (paper) QSL fields.
    export = "<PROGRAMID:9>NWOlogger\n<EOH>\n<CALL:6>BA1TSA<QSO_DATE:8>20261010<TIME_ON:4>0941<BAND:2>2M<MODE:3>SSB" \
        "<QSL_RCVD:1>Y<QSLRDATE:8>20260901<LOTW_QSL_RCVD:1>Y<APP_LOTW_RXQSO:19>2026-10-10 14:48:07<EOR>"
    assert not importer.is_lotw_report(*adif.parse(export))
    assert not importer.is_lotw_report(*adif.parse(export.replace("<PROGRAMID:9>NWOlogger\n", "")))  # its own fields
    q = importer.read_adif(export)[1][0]
    assert (q["qsl_rcvd"], q["qslrdate"]) == ("Y", "2026-09-01")  # the paper card stays a paper card


def test_imported_as_qsos_its_qsl_fields_are_lotws_not_paper():
    _header, qsos = importer.read_adif(REPORT)
    a, b = qsos[0], qsos[1]
    assert (a["lotw_qsl_rcvd"], a["lotw_qslrdate"]) == ("Y", "2026-10-13")  # the confirmation date, not the QSO's
    assert (a["lotw_qsl_sent"], a["lotw_qslsdate"]) == ("Y", "2026-10-10")  # when LoTW received the upload
    assert (b["lotw_qsl_rcvd"], b["lotw_qsl_sent"]) == ("N", "Y")
    for q in qsos:
        assert q.get("qsl_rcvd") is None and q.get("qslrdate") is None
    # An ordinary ADIF file's QSL fields stay a paper card's.
    ordinary = "<PROGRAMID:9>NWOlogger\n<EOH>\n" \
        "<CALL:6>BA1TSA<QSO_DATE:8>20261010<TIME_ON:4>0941<BAND:2>2M<MODE:3>SSB<QSL_RCVD:1>Y<QSLRDATE:8>20261013<EOR>"
    q = importer.read_adif(ordinary)[1][0]
    assert (q["qsl_rcvd"], q["qslrdate"]) == ("Y", "2026-10-13") and q.get("lotw_qsl_rcvd") is None


# ------------------------------------------------------------------ applying it as a download
def test_applied_like_a_download(log):
    db, ids = log
    res = apply.apply_lotw_report(db, _records(), sats.load())
    a, b, other = db.get(ids["A"]), db.get(ids["B"]), db.get(ids["other"])
    assert (a["lotw_qsl_rcvd"], a["lotw_qslrdate"], a["lotw_qsl_sent"], a["lotw_qslsdate"]) == \
        ("Y", "2026-10-13", "Y", "2026-10-10")
    assert a["gridsquare"] == "OM89aa" and a.get("qsl_rcvd") is None  # details filled in; no paper card
    assert b["lotw_qsl_sent"] == "Y" and b.get("lotw_qsl_rcvd") in (None, "N")
    assert other.get("lotw_qsl_sent") is None and other["qsl_rcvd"] == "Y"  # untouched
    assert res.total == 4 and res.matched == 2 and res.confirmed == [ids["A"]]
    unmatched = {q["call"]: q for q in res.unmatched}
    assert set(unmatched) == {"BH4TSC", "BD7TSD"}  # confirmed or not, both offered to add
    assert (unmatched["BH4TSC"]["lotw_qsl_rcvd"], unmatched["BH4TSC"]["lotw_qslrdate"]) == ("Y", "2026-10-15")
    assert unmatched["BD7TSD"]["lotw_qsl_sent"] == "Y" and unmatched["BD7TSD"].get("lotw_qsl_rcvd") is None
    for key in ("last_qsl", "last_qsorx", "last_download"):
        assert db.sync_get("lotw", key) is None  # a report never moves where downloads resume
    again = apply.apply_lotw_report(db, _records(), sats.load())
    assert again.changed == 0 and again.confirmed == []


def test_the_preview_changes_nothing(log):
    db, ids = log
    before = [dict(db.get(i)) for i in ids.values()]
    notices = []
    db.add_listener(lambda kind, changed: notices.append(kind))
    preview = apply.apply_lotw_report(db, _records(), sats.load(), dry_run=True)
    assert [dict(db.get(i)) for i in ids.values()] == before and notices == []
    real = apply.apply_lotw_report(db, _records(), sats.load())
    assert (preview.matched, preview.changed, len(preview.unmatched)) == \
        (real.matched, real.changed, len(real.unmatched))
    assert preview.confirmed == real.confirmed


def test_paper_cards_are_never_touched(log):
    db, ids = log
    db.update(ids["A"], {"qsl_rcvd": "Y", "qslrdate": "2026-10-13"}, track_service=False)  # even on LoTW's date
    apply.apply_lotw_report(db, _records(), sats.load())
    a, other = db.get(ids["A"]), db.get(ids["other"])
    assert (a["qsl_rcvd"], a["qslrdate"]) == ("Y", "2026-10-13") and a["lotw_qsl_rcvd"] == "Y"
    assert (other["qsl_rcvd"], other["qslrdate"]) == ("Y", "2026-10-12")


def test_a_qso_uploaded_twice_matches_the_copy_with_the_same_satellite(tmp_path):
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    with_sat = db.insert(plain("BA1TSA", "2026-10-10T09:41:50Z"))
    without = db.insert(plain("BA1TSA", "2026-10-10T09:41:50Z", prop_mode=None, sat_name=None))
    assert importer.find_match(db, plain("BA1TSA", "2026-10-10T09:41:50Z")) == with_sat
    assert importer.find_match(db, plain("BA1TSA", "2026-10-10T09:41:50Z", prop_mode=None, sat_name=None)) == without
    db.close()


# ------------------------------------------------------------------ the import dialog
@pytest.fixture
def dialog(qtbot, tmp_path, log):
    db, ids = log
    path = tmp_path / "lotwreport.adi"
    path.write_text(REPORT, encoding="utf-8")
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))

    def make():
        dlg = import_dialog.ImportDialog(db, prefs, str(path))
        qtbot.addWidget(dlg)
        return dlg

    return make, db, ids


def _shown_numbers(dlg) -> list[str]:
    return [dlg.counts.itemAtPosition(0, c).widget().text() for c in range(dlg.counts.columnCount())
            if dlg.counts.itemAtPosition(0, c)]


def test_the_dialog_applies_a_report_as_a_download_by_default(dialog):
    make, db, ids = dialog
    dlg = make()
    assert dlg.is_lotw and dlg.applies_lotw()
    assert dlg.ok.text() == "Apply" and not dlg.opts.isEnabled()
    assert _shown_numbers(dlg) == ["4", "2", "2", "1", "2"]  # records, confirmed, in the log, new, not in the log
    dlg._run()
    assert dlg.lotw_result is not None and dlg.result is None
    assert dlg.lotw_result.confirmed == [ids["A"]] and db.get(ids["A"])["lotw_qslrdate"] == "2026-10-13"
    assert any("import" in p.name for p in db.backup_dir.iterdir())  # a backup first, as for every import


def test_the_dialog_can_still_import_every_record_as_a_qso(dialog):
    make, db, ids = dialog
    dlg = make()
    dlg.as_qsos.setChecked(True)
    assert not dlg.applies_lotw() and dlg.ok.text() == "Import" and dlg.opts.isEnabled()
    assert dlg.merge.isChecked()
    dlg._run()
    assert dlg.result is not None and dlg.lotw_result is None
    assert dlg.result.merged == 2 and dlg.result.added == 2
    calls = {q["call"]: q for q in db.iter_all()}
    assert calls["BH4TSC"]["lotw_qsl_rcvd"] == "Y" and calls["BH4TSC"]["lotw_qslrdate"] == "2026-10-15"
    assert calls["BA1TSA"]["lotw_qslrdate"] == "2026-10-13"
    assert all(q.get("qsl_rcvd") is None for c, q in calls.items() if c != "BI8TSE")  # no paper cards


def test_an_ordinary_adif_file_is_imported_as_before(qtbot, tmp_path, log):
    db, _ids = log
    path = tmp_path / "other.adi"
    path.write_text("<PROGRAMID:9>NWOlogger\n<EOH>\n<CALL:6>BA1TSZ<QSO_DATE:8>20261010<TIME_ON:4>0941<BAND:2>2M"
                    "<MODE:3>SSB<EOR>", encoding="utf-8")
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    dlg = import_dialog.ImportDialog(db, prefs, str(path))
    qtbot.addWidget(dlg)
    assert not dlg.is_lotw and not dlg.applies_lotw() and dlg.as_download is None
    assert dlg.ok.text() == "Import" and not dlg.merge.isChecked()


# ------------------------------------------------------------------ after applying
def test_an_applied_report_is_reported_selected_and_its_missing_qsos_offered(qtbot, log, monkeypatch, tmp_path):
    db, ids = log
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    parent = QWidget()
    qtbot.addWidget(parent)
    ctrl = sync_controller.SyncController(db, prefs, parent)
    reports, selected, offered = [], [], []
    monkeypatch.setattr(ctrl, "report", lambda svc, kind, text, **kw: reports.append((svc, kind, text(str), kw)))
    monkeypatch.setattr(ctrl, "_show_unmatched", lambda svc, res: offered.append((svc, len(res.unmatched))))
    ctrl.confirmed.connect(lambda svc, got: selected.append((svc, got)))
    res = apply.apply_lotw_report(db, _records(), sats.load())
    ctrl.lotw_report_applied("lotwreport.adi", res)
    svc, kind, text, kw = reports[0]
    assert (svc, kind) == ("lotw", "ok") and kw["ids"] == [ids["A"]]
    assert text.startswith("lotwreport.adi: 1 new confirmation") and text.endswith("2 not in your log")
    assert selected == [("lotw", [ids["A"]])] and offered == [("lotw", 2)]

