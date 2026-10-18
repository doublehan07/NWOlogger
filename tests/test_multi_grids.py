"""One Grid field for a station on a grid line: "OM88, OM89" is read and shown by the entry form, the log, the
compare window, Worked before and the station profile, with the first grid in GRIDSQUARE and all of them in
VUCC_GRIDS (MY_GRIDSQUARE and MY_VUCC_GRIDS for your own)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings

from nwologger.core import importer
from nwologger.core import qso as qsomod
from nwologger.core.db import LogDB
from nwologger.ui import dupe_dialog, prefs_dialog
from nwologger.ui.prefs import Prefs


def test_grid_field_helpers():
    assert qsomod.split_grids("om88, OM89aa;OM98/ON88 OM88") == ["OM88", "OM89aa", "OM98", "ON88"]
    assert qsomod.grid_columns("OM88da, OM89") == {"gridsquare": "OM88da", "vucc_grids": "OM88,OM89"}
    assert qsomod.grid_columns("OM88") == {"gridsquare": "OM88", "vucc_grids": None}
    assert qsomod.grid_columns("") == {"gridsquare": None, "vucc_grids": None}
    assert qsomod.grid_columns("OM88, hello") == {"gridsquare": "OM88", "vucc_grids": None}  # junk is no grid
    assert qsomod.grid_columns("OM32,OM33", "my_gridsquare") == {"my_gridsquare": "OM32",
                                                                 "my_vucc_grids": "OM32,OM33"}
    assert qsomod.grids_text({"gridsquare": "OM88da", "vucc_grids": "OM88,OM89"}) == "OM88da, OM89"
    assert qsomod.grids_text({"gridsquare": "OM88", "vucc_grids": None}) == "OM88"
    assert qsomod.grids_text({"gridsquare": None, "vucc_grids": "ON31,ON32,ON41,ON42"}) == "ON31, ON32, ON41, ON42"
    assert qsomod.grids_text({"my_gridsquare": "ON80ea", "my_vucc_grids": ""}, "my_gridsquare") == "ON80ea"


def test_normalize_splits_several_grids_typed_into_one_field():
    q = qsomod.normalize({"call": "BA1AA", "gridsquare": "OM88;OM89"})
    assert (q["gridsquare"], q["vucc_grids"]) == ("OM88", "OM88,OM89")
    q = qsomod.normalize({"call": "BA1AA", "gridsquare": "om88da", "vucc_grids": " om88, om89aa "})
    assert (q["gridsquare"], q["vucc_grids"]) == ("OM88da", "OM88,OM89")
    q = qsomod.normalize({"call": "BA1AA", "my_gridsquare": "OM32/OM33"})
    assert (q["my_gridsquare"], q["my_vucc_grids"]) == ("OM32", "OM32,OM33")
    assert qsomod.normalize({"call": "BA1AA", "vucc_grids": "nonsense"})["vucc_grids"] is None


def test_the_import_profile_brings_its_vucc_grids():
    q = importer.apply_profile({"call": "BA1AA"}, {"id": 1, "station_callsign": "BI1NWO", "my_gridsquare": "OM32",
                                                   "my_vucc_grids": "OM32,OM33"})
    assert (q["my_gridsquare"], q["my_vucc_grids"]) == ("OM32", "OM32,OM33")


@pytest.fixture
def log(tmp_path):
    db = LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    yield db
    db.close()


def _qso(call="BA1AA", when="2026-01-01T10:00:00Z", **more) -> dict:
    return qsomod.normalize({"call": call, "time_on": when, "mode": "FM", "band": "2m", **more})


def test_updating_the_grid_with_several_grids_fills_the_vucc_grids(log):
    qid = log.insert(_qso(gridsquare="OM88"))
    assert log.update(qid, {"gridsquare": "OM88, OM89"})
    q = log.get(qid)
    assert (q["gridsquare"], q["vucc_grids"]) == ("OM88", "OM88,OM89")
    assert log.update(qid, {"gridsquare": "OM88da"})  # one grid alone says nothing about the VUCC list
    q = log.get(qid)
    assert (q["gridsquare"], q["vucc_grids"]) == ("OM88da", "OM88,OM89")


def test_the_compare_window_shows_and_edits_all_grids(log, qtbot, tmp_path):
    a = log.insert(_qso(gridsquare="OM88da", vucc_grids="OM88,OM89", my_gridsquare="ON80ea"))
    b = log.insert(_qso(when="2026-01-01T10:00:20Z", gridsquare="OM88"))
    assert dupe_dialog._val(log.get(a), "gridsquare") == "OM88da, OM89"
    assert dupe_dialog._val(log.get(a), "my_gridsquare") == "ON80ea"
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    dlg = dupe_dialog.DupeCompareDialog(log, prefs, groups=[[a, b]], fixed=True)
    qtbot.addWidget(dlg)
    dlg.edits[(a, "gridsquare")] = "OM88"  # the second grid removed
    dlg.edits[(b, "gridsquare")] = "OM87, OM88"
    assert dlg._save()
    assert (log.get(a)["gridsquare"], log.get(a)["vucc_grids"]) == ("OM88", None)
    assert (log.get(b)["gridsquare"], log.get(b)["vucc_grids"]) == ("OM87", "OM87,OM88")


@pytest.fixture
def panel(qtbot, log, tmp_path):
    from nwologger.ui.entry.dx_panel import DXPanel

    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    prefs.set("auto_lookup", False)
    prefs.set("allow_dupes", True)
    prefs.set("rx_sync", False)
    p = DXPanel(log, prefs)
    qtbot.addWidget(p)
    yield p
    for timer in (p._clock, p._check_timer, p._lookup_timer, p._index_timer):
        timer.stop()
    log.remove_listener(p._on_db_change)


def test_the_entry_form_reads_and_shows_several_grids(panel, log):
    panel.call.setText("BA1AA")
    panel.w["gridsquare"].setText("om88da; om89")
    q = panel.values()
    assert (q["gridsquare"], q["vucc_grids"]) == ("OM88da", "OM88,OM89")
    qid = log.insert(_qso(**{k: v for k, v in q.items() if k in ("gridsquare", "vucc_grids")}))
    panel.load_qso(qid)
    assert panel.w["gridsquare"].text() == "OM88da, OM89"
    panel.w["gridsquare"].setText("OM88")
    assert panel.values()["vucc_grids"] is None  # one grid again: the VUCC list goes
    panel.clear_form(full=True)
    panel._refresh_history("BA1AA", None)  # Worked before shows every grid too
    assert [panel.history.horizontalHeaderItem(i).text() for i in range(6)][4:] == ["Grid", "Confirmed"]
    assert panel.history.item(0, 4).text() == "OM88da, OM89"


def test_the_station_profile_grid_field_takes_several_grids(qtbot, log, monkeypatch):
    monkeypatch.setattr(prefs_dialog.tqsl, "station_locations", lambda: [])
    page = prefs_dialog.ProfilesPage(log)
    qtbot.addWidget(page)
    assert "my_vucc_grids" not in page.edits  # the one My grid field carries them
    page.edits["name"].setText("Line")
    page.edits["station_callsign"].setText("BI1NWO")
    page.edits["my_gridsquare"].setText("OM32, OM33")
    assert page._save()
    p = log.default_profile()
    assert (p["my_gridsquare"], p["my_vucc_grids"]) == ("OM32", "OM32,OM33")
    assert page.edits["my_gridsquare"].text() == "OM32, OM33"
    page.edits["my_gridsquare"].setText("ON80ea")
    assert page._save() and log.default_profile()["my_vucc_grids"] is None
