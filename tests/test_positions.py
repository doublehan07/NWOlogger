"""Where downloads continue: read, written, proposed by imports, and shown in Preferences → Sync positions."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from nwologger.core import db as dbm
from nwologger.core import qso as qsomod
from nwologger.core.sync import positions
from nwologger.ui.import_dialog import ImportDialog
from nwologger.ui.prefs import Prefs
from nwologger.ui.prefs_dialog import PositionsPage, PrefsDialog


@pytest.fixture
def log(tmp_path):
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    yield db
    db.close()


def test_values_are_normalised_and_checked():
    assert positions.normalize("stamp", "2026-10-14 23:07:03") == "2026-10-14 23:07:03"
    assert positions.normalize("stamp", "2026-10-14T23:07Z") == "2026-10-14 23:07:00"
    assert positions.normalize("stamp", "2026-10-14") == "2026-10-14 00:00:00"
    assert positions.normalize("day", "2026-10-17") == "20261017" and positions.normalize("day", "20261017") == "20261017"
    assert positions.normalize("stamp", "  ") is None and positions.normalize("day", None) is None
    for kind, bad in (("stamp", "yesterday"), ("day", "2026-13-01"), ("stamp", "2026-10-14 25:00:00")):
        with pytest.raises(ValueError):
            positions.normalize(kind, bad)


def test_an_imported_log_proposes_its_newest_dates_minus_a_day(log):
    qsos = [qsomod.normalize({"call": "BA1AA", "time_on": "2026-10-10T10:00:00Z", "mode": "FM", "band": "2m",
                              "lotw_qslrdate": "2026-10-17", "lotw_qslsdate": "2026-10-18", "eqsl_qslrdate": "2026-10-12",
                              "hrdlog_qso_upload_date": "2026-10-18"}),
            qsomod.normalize({"call": "BA1BB", "time_on": "2026-10-11T10:00:00Z", "mode": "FM", "band": "2m",
                              "lotw_qslrdate": "2026-10-01", "eqsl_qslrdate": "2099-01-01"})]  # the future is ignored
    dates = positions.service_dates(qsos)
    assert dates == {"lotw_qsl": "2026-10-17", "lotw_sent": "2026-10-18", "eqsl_qsl": "2026-10-12",
                     "hrdlog_sent": "2026-10-18"}
    proposed = positions.from_import(dates)
    assert proposed == {("lotw", "last_qsl"): "2026-10-16 00:00:00", ("lotw", "last_qsorx"): "2026-10-17 00:00:00",
                        ("eqsl", "rcvd_since"): "20261011"}
    assert positions.describe(proposed, str) == \
        "LoTW confirmations from 2026-10-16, LoTW uploads from 2026-10-17, eQSL cards from 2026-10-11"
    log.sync_set("lotw", "last_qsl", "2026-10-20 00:00:00")  # already there, later than the file: stays
    done = positions.set_if_unset(log, proposed, origin="import friend.adi")
    assert set(done) == {("lotw", "last_qsorx"), ("eqsl", "rcvd_since")}
    assert log.sync_get("lotw", "last_qsl") == "2026-10-20 00:00:00"
    assert log.sync_get("lotw", "last_qsorx") == "2026-10-17 00:00:00" and log.sync_get("eqsl", "rcvd_since") == "20261011"
    assert log.sync_get("eqsl", "rcvd_since:origin") == "import friend.adi"
    assert positions.set_if_unset(log, proposed, origin="again") == {}  # nothing moves a second time


def test_a_complete_report_carries_lotws_own_values():
    header = {"APP_LOTW_LASTQSORX": "2026-10-16 14:37:32"}
    records = [{"QSL_RCVD": "Y", "APP_LOTW_RXQSL": "2026-10-14 23:07:03"}, {"QSL_RCVD": "N"},
               {"QSL_RCVD": "Y", "APP_LOTW_RXQSL": "2026-10-10 01:00:00"}]
    info = {"generated": "2026-10-18 07:33:33", "QSL ONLY": "NO"}
    assert positions.from_report(header, records, info) == {("lotw", "last_qsorx"): "2026-10-16 14:37:32",
                                                            ("lotw", "last_qsl"): "2026-10-14 23:07:03"}
    assert positions.from_report({}, [{"QSL_RCVD": "N"}], info) == {("lotw", "last_qsorx"): "2026-10-18 07:33:33",
                                                                    ("lotw", "last_qsl"): "2026-10-18 07:33:33"}
    only = positions.from_report({"APP_LOTW_LASTQSL": "2026-10-14 23:07:03"}, records, {**info, "QSL ONLY": "YES"})
    assert only == {("lotw", "last_qsl"): "2026-10-14 23:07:03"}


def test_writing_checks_backs_up_and_logs(log, caplog):
    log.sync_set("lotw", "last_qsl", "2026-10-14 23:07:03")
    with pytest.raises(ValueError):
        positions.write(log, {("lotw", "last_qsl"): "soon"}, origin="edited")
    assert log.sync_get("lotw", "last_qsl") == "2026-10-14 23:07:03" and not list(log.backups())
    lines = positions.write(log, {("lotw", "last_qsl"): "2026-10-01", ("eqsl", "rcvd_since"): ""}, origin="edited")
    assert lines == ["lotw/last_qsl 2026-10-14 23:07:03 → 2026-10-01 00:00:00, edited"]
    assert log.sync_get("lotw", "last_qsl") == "2026-10-01 00:00:00" and log.sync_get("lotw", "last_qsl:origin") == "edited"
    assert len(list(log.backups())) == 1 and "positions" in list(log.backups())[0].name
    rows = {(r["service"], r["key"]): r for r in positions.read(log)}
    assert rows["lotw", "last_qsl"]["value"] == "2026-10-01 00:00:00" and rows["lotw", "last_qsl"]["origin"] == "edited"
    assert rows["eqsl", "rcvd_since"]["value"] == "" and rows["eqsl", "rcvd_since"]["editable"]
    log.sync_set("qrz", "after_logid:BI1ZZZ", "1250")
    log.sync_set("lotw", "run", "20261018-112513")
    log.sync_set("lotw", "parts_done", "2026,2025")
    log.sync_set("lotw", "parts_floor", "2019")
    rows = {(r["service"], r["key"]): r for r in positions.read(log)}
    assert rows["qrz", "after_logid:BI1ZZZ"]["value"] == "1250" and not rows["qrz", "after_logid:BI1ZZZ"]["editable"]
    assert rows["lotw", "parts"]["value"] == "2026,2025 done, back to 2019"


ADIF = ("<ADIF_VER:5>3.1.5 <PROGRAMID:14>HamRadioDeluxe <eoh>\n"
        "<CALL:5>BA1AA <QSO_DATE:8>20261010 <TIME_ON:6>100000 <BAND:2>2M <MODE:2>FM <LOTW_QSL_RCVD:1>Y "
        "<LOTW_QSLRDATE:8>20261017 <LOTW_QSL_SENT:1>Y <LOTW_QSLSDATE:8>20261018 <EQSL_QSL_RCVD:1>Y "
        "<EQSL_QSLRDATE:8>20261012 <HRDLOG_QSO_UPLOAD_STATUS:1>Y <HRDLOG_QSO_UPLOAD_DATE:8>20261018 <eor>\n"
        "<CALL:5>BA1BB <QSO_DATE:8>20261011 <TIME_ON:6>100000 <BAND:2>2M <MODE:2>FM <eor>\n")


def test_the_import_dialog_offers_the_positions_and_sets_only_the_empty_ones(qtbot, log, tmp_path):
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    path = tmp_path / "friend.adi"
    path.write_text(ADIF, encoding="utf-8")
    log.sync_set("eqsl", "rcvd_since", "20261001")
    dlg = ImportDialog(log, prefs, str(path))
    qtbot.addWidget(dlg)
    text = dlg.positions_box.text()
    assert dlg.positions_box.isChecked() and not dlg.positions_box.isHidden()
    assert "LoTW confirmations from 2026-10-16, LoTW uploads from 2026-10-17" in text
    assert "HRDLog: no download" in text and "already set" in text and "eQSL cards from 2026-10-11" in text
    dlg._run()
    assert dlg.result.added == 2
    assert log.sync_get("lotw", "last_qsl") == "2026-10-16 00:00:00"
    assert log.sync_get("lotw", "last_qsorx") == "2026-10-17 00:00:00"
    assert log.sync_get("eqsl", "rcvd_since") == "20261001"  # stayed
    assert set(dlg.positions_set) == {("lotw", "last_qsl"), ("lotw", "last_qsorx")}
    assert log.sync_get("lotw", "last_qsl:origin") == "import friend.adi"
    # a file without such dates offers nothing
    plain = tmp_path / "plain.adi"
    plain.write_text("<eoh>\n<CALL:5>BA1CC <QSO_DATE:8>20261011 <TIME_ON:6>100000 <BAND:2>2M <MODE:2>FM <eor>\n")
    dlg2 = ImportDialog(log, prefs, str(plain))
    qtbot.addWidget(dlg2)
    assert dlg2.positions_box.isHidden() and dlg2.proposed == {}


def test_the_preferences_tab_is_locked_until_unlocked_and_writes_on_lock(qtbot, log, monkeypatch):
    log.sync_set("lotw", "last_qsl", "2026-10-14 23:07:03")
    log.sync_set("lotw", "last_qsorx", "2026-10-16 14:37:32")
    page = PositionsPage(log)
    qtbot.addWidget(page)
    labels = [page.table.item(i, 1).text() for i in range(page.table.rowCount())]
    assert labels[:3] == ["Confirmations since", "Uploads since", "Cards since"]
    assert not (page.table.item(0, 2).flags() & Qt.ItemFlag.ItemIsEditable)
    assert page.table.item(2, 2).text() == "the start" and page.lock_btn.text() == "Unlock"
    assert page.danger.isHidden() and page.cancel_btn.isHidden()
    page.toggle()
    assert page.unlocked and page.lock_btn.text() == "Lock and save" and not page.danger.isHidden()
    assert page.table.item(0, 2).flags() & Qt.ItemFlag.ItemIsEditable and page.table.item(2, 2).text() == ""
    page.table.item(0, 2).setText("not a date")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    assert not page.lock_and_save() and page.unlocked and warnings and "Could not read a date" in warnings[0]
    page.table.item(0, 2).setText("2026-10-01")
    page.table.item(2, 2).setText("2026-10-11")
    assert page.lock_and_save() and not page.unlocked and page.lock_btn.text() == "Unlock"
    assert log.sync_get("lotw", "last_qsl") == "2026-10-01 00:00:00" and log.sync_get("eqsl", "rcvd_since") == "20261011"
    assert log.sync_get("lotw", "last_qsorx") == "2026-10-16 14:37:32"
    assert "edited in Preferences" in page.table.item(0, 3).text() and len(list(log.backups())) == 1
    page.toggle()
    page.table.item(1, 2).setText("")
    page.cancel_edit()
    assert log.sync_get("lotw", "last_qsorx") == "2026-10-16 14:37:32" and not page.unlocked


def test_the_dialog_saves_or_discards_an_unlocked_page(qtbot, log, tmp_path, monkeypatch):
    from nwologger.ui import prefs_dialog

    monkeypatch.setattr(prefs_dialog.tqsl, "station_locations", lambda: [])
    monkeypatch.setattr(prefs_dialog.tqsl, "find_tqsl", lambda *_a: None)
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    log.sync_set("lotw", "last_qsl", "2026-10-14 23:07:03")
    dlg = PrefsDialog(log, prefs, page="Sync positions")
    qtbot.addWidget(dlg)
    page = dlg.positions_page
    assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == "Sync positions"
    page.unlock()
    page.table.item(0, 2).setText("2026-10-02")
    dlg.reject()
    assert log.sync_get("lotw", "last_qsl") == "2026-10-14 23:07:03" and not page.unlocked
    page.unlock()
    page.table.item(0, 2).setText("2026-10-02")
    dlg._save()
    assert dlg.result() == QDialog.DialogCode.Accepted
    assert log.sync_get("lotw", "last_qsl") == "2026-10-02 00:00:00"
