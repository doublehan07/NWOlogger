"""Preferences → Station profiles → Add from my log…: a profile per station callsign + grid pair your QSOs carry,
the ones you tick; and the import preview's line on which records the chosen profile fills."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings, Qt

from nwologger.core import grids
from nwologger.core import qso as qsomod
from nwologger.core.db import LogDB
from nwologger.ui import prefs_dialog
from nwologger.ui.import_dialog import ImportDialog
from nwologger.ui.prefs import Prefs
from nwologger.ui.profiles_from_log import ProfilesFromLogDialog, profile_for

QSOS = [  # call, station call, my grid, my VUCC grids
    ("BA1AA", "BI1NWO", "ON80ea", None), ("BA1BB", "bi1nwo", "ON80EA", None), ("BA1CC", "BI1NWO", "OM32", "OM32,OM33"),
    ("BA1DD", "AI6OT", "CM87", None), ("BA1EE", None, None, None), ("BA1FF", "BI1NWO", None, None),
]


@pytest.fixture
def log(tmp_path):
    db = LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    for i, (call, mine, grid, vucc) in enumerate(QSOS):
        db.insert(qsomod.normalize({"call": call, "time_on": f"2026-01-0{i + 1}T10:00:00Z", "mode": "FM", "band": "2m",
                                    "station_callsign": mine, "my_gridsquare": grid, "my_vucc_grids": vucc}))
    yield db
    db.close()


def test_station_places_are_counted_case_folded(log):
    assert grids.my_station_places(log) == [("BI1NWO", "ON80EA", 2), ("AI6OT", "CM87", 1), ("BI1NWO", "", 1),
                                            ("BI1NWO", "OM32,OM33", 1)]


def test_profile_for_names_the_place_and_fills_the_zones():
    p = profile_for("BI1NWO", "OM32,OM33")
    assert p["name"] == "BI1NWO @ OM32, OM33" and p["station_callsign"] == p["operator"] == "BI1NWO"
    assert (p["my_gridsquare"], p["my_vucc_grids"]) == ("OM32", "OM32,OM33")
    assert p["my_dxcc"] == 318 and isinstance(p["my_cq_zone"], int) and isinstance(p["my_itu_zone"], int)
    bare = profile_for("AI6OT", "")
    assert bare["name"] == "AI6OT" and bare["my_gridsquare"] is None and bare["my_vucc_grids"] is None


def test_the_dialog_ticks_the_pairs_without_a_profile_and_creates_them(qtbot, log):
    log.save_profile({"name": "Home", "station_callsign": "BI1NWO", "my_gridsquare": "ON80ea", "is_default": True})
    dlg = ProfilesFromLogDialog(log)
    qtbot.addWidget(dlg)
    rows = [(dlg.table.item(i, 0).text(), dlg.table.item(i, 0).checkState() == Qt.CheckState.Checked,
             dlg.table.item(i, 1).text(), dlg.table.item(i, 2).text()) for i in range(dlg.table.rowCount())]
    assert rows == [("BI1NWO  ·  ON80ea", False, "2", "has a profile"), ("AI6OT  ·  CM87", True, "1", ""),
                    ("BI1NWO  ·  —", True, "1", ""), ("BI1NWO  ·  OM32, OM33", True, "1", "")]
    assert dlg.create_btn.text() == "Create 3 profiles" and dlg.create_btn.isEnabled()
    dlg.table.item(2, 0).setCheckState(Qt.CheckState.Unchecked)  # not the one without a grid
    assert dlg.create_btn.text() == "Create 2 profiles"
    dlg.accept()
    assert sorted(p["name"] for p in log.profiles()) == ["AI6OT @ CM87", "BI1NWO @ OM32, OM33", "Home"]
    assert len(dlg.created) == 2 and log.default_profile()["name"] == "Home"
    again = ProfilesFromLogDialog(log)
    qtbot.addWidget(again)
    assert again.ticked() == [("BI1NWO", "")]
    for i in range(again.table.rowCount()):
        again.table.item(i, 0).setCheckState(Qt.CheckState.Unchecked)
    assert not again.create_btn.isEnabled() and again.create_btn.text() == "Create 0 profiles"


def test_an_empty_log_says_so(qtbot, tmp_path):
    db = LogDB(tmp_path / "empty.sqlite", backup_on_open=False)
    try:
        dlg = ProfilesFromLogDialog(db)
        qtbot.addWidget(dlg)
        assert dlg.table.rowCount() == 0 and dlg.empty.isVisibleTo(dlg) and not dlg.create_btn.isEnabled()
    finally:
        db.close()


def test_the_profiles_page_adds_them_from_its_button(qtbot, log, monkeypatch):
    monkeypatch.setattr(prefs_dialog.tqsl, "station_locations", lambda: [])
    page = prefs_dialog.ProfilesPage(log)
    qtbot.addWidget(page)
    assert page.from_log_btn.text() == "Add from my log…"
    monkeypatch.setattr(ProfilesFromLogDialog, "exec", lambda self: (self.accept(), 1)[1])
    page.from_log_btn.click()
    assert {p["station_callsign"] for p in log.profiles()} == {"BI1NWO", "AI6OT"} and len(log.profiles()) == 4
    assert page.list.count() == 4 and page.current_id == log.profiles()[-1]["id"] or page.current_id is not None


ADIF = ("<ADIF_VER:5>3.1.4<EOH>\n"
        "<CALL:5>BA1AA<QSO_DATE:8>20260101<TIME_ON:4>1000<BAND:2>2m<MODE:2>FM<STATION_CALLSIGN:5>AI6OT<EOR>\n"
        "<CALL:5>BA1BB<QSO_DATE:8>20260102<TIME_ON:4>1000<BAND:2>2m<MODE:2>FM<EOR>\n")


def test_the_import_preview_says_which_records_the_profile_fills(qtbot, log, tmp_path):
    log.save_profile({"name": "Home", "station_callsign": "BI1NWO", "my_gridsquare": "ON80ea", "is_default": True})
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    path = tmp_path / "in.adi"
    path.write_text(ADIF, encoding="utf-8")
    dlg = ImportDialog(log, prefs, str(path))
    qtbot.addWidget(dlg)
    assert dlg.profile.currentText() == "Home"
    assert dlg._profile_note() == ("1 record has no station callsign; it gets BI1NWO @ ON80ea. Every other record "
                                   "keeps the station callsign and grid it came with, whatever profile is chosen.")
    dlg.profile.setCurrentIndex(0)  # (none)
    assert dlg._profile_note().startswith("1 record has no station callsign and no profile is chosen")
    path.write_text(ADIF.replace("<MODE:2>FM<EOR>", "<MODE:2>FM<STATION_CALLSIGN:6>BI1NWO<EOR>"), encoding="utf-8")
    full = ImportDialog(log, prefs, str(path))
    qtbot.addWidget(full)
    assert full._profile_note().startswith("Every record carries its own station callsign")
