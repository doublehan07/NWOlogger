"""The grid map's filters: a satellite or an orbit, your callsigns and the places you operated from, as Gridmaster
lists them; the counts, the 200 km helper, the saved choice and the PNG name that follows them."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings

from nwologger import __version__
from nwologger.core import db as dbm
from nwologger.core import grids, sats
from nwologger.core import qso as qsomod
from nwologger.ui.prefs import Prefs
from nwologger.ui.stats.statistics_tab import StatisticsTab
from nwologger.ui.widgets import CheckListButton

QSOS = [
    # call, time, grid, my call, my grid, my vucc grids, sat, confirmed
    ("BA1AA", "2026-01-01T10:00:00Z", "OM89", "BI1NWO", "ON80DA", None, "SO-50", "Y"),
    ("BA1BB", "2026-01-02T10:00:00Z", "ON70", "BI1NWO", "ON80EA", None, "QO-100", "Y"),
    ("BA1CC", "2026-01-03T10:00:00Z", "PM95", "AI6OT", "CM87", None, "SO-50", None),
    ("BA1DD", "2026-01-04T10:00:00Z", "OL48", "BI1NWO", "OM32", "OM32,OM33", "IO-117", "Y"),
    ("BA1EE", "2026-01-05T10:00:00Z", "PM06", None, None, None, "ARISS", "Y"),  # no station call or grid
    ("BA1FF", "2026-01-06T10:00:00Z", "ON60", "BI1NWO", "ON70", None, "JAMX", None),  # a name only the log knows
]


@pytest.fixture
def log(tmp_path):
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    for call, t, grid, mine, my_grid, vucc, sat, cfm in QSOS:
        db.insert(qsomod.normalize({"call": call, "time_on": t, "mode": "FM", "band": "2m", "prop_mode": "SAT",
                                    "sat_name": sat, "gridsquare": grid, "station_callsign": mine,
                                    "my_gridsquare": my_grid, "my_vucc_grids": vucc, "lotw_qsl_rcvd": cfm}))
    yield db
    db.close()


def test_orbits_come_from_the_satellite_list():
    cat = sats.default()
    assert cat.orbit("QO-100") == "GEO" and cat.orbit("IO-117") == "MEO" and cat.orbit("AO-40") == "HEO"
    assert cat.orbit("SO-50") == "LEO" and cat.orbit("NOT-A-SAT") == "LEO"
    assert cat.names_in("GEO") == {"QO-100"} and cat.names_in("MEO") == {"IO-117"}
    assert "QO-100" not in cat.names_in(sats.NON_GEO) and {"SO-50", "IO-117", "AO-13"} <= cat.names_in(sats.NON_GEO)
    assert set(cat.orbits) == {"LEO", "MEO", "HEO", "GEO"}
    extra = sats.load(extra=[{"name": "MY-SAT", "orbit": "heo"}, {"name": "OTHER"}])
    assert extra.orbit("MY-SAT") == "HEO" and extra.orbit("OTHER") == "LEO"


def test_stations_and_places_come_from_the_log(log):
    assert grids.my_stations(log) == [("BI1NWO", 4), ("AI6OT", 1)]
    # most QSOs first; equal counts in name order
    assert grids.my_locations(log) == [("CM87", 1), ("OM32,OM33", 1), ("ON70", 1), ("ON80DA", 1), ("ON80EA", 1)]
    assert grids.my_locations(log, ["AI6OT"]) == [("CM87", 1)]
    assert grids.location_key({"my_gridsquare": "on80da", "my_vucc_grids": " OM32, OM33 "}) == "OM32,OM33"
    assert grids.location_key({"my_gridsquare": None}) == ""
    assert grids.distance_from("ON80", "ON80DA") == 0 and 150 < grids.distance_from("ON80", "ON70") < 200
    assert grids.distance_from("ON80DA", "ON80EA") == 0  # one square is one place
    assert grids.distance_from("ON80", "CM87") > 9000 and grids.distance_from(None, "ON70") is None
    assert grids.within(["ON80DA", "ON70", "CM87", "OM32,OM33"], "ON80") == ["ON80DA", "ON70"]


def test_collect_filters_by_satellite_callsign_and_place(log):
    cat = sats.default()
    all_sat = grids.collect(log, radius_km=None)
    assert set(all_sat) == {"OM89", "ON70", "PM95", "OL48", "PM06", "ON60"}
    geo = grids.collect(log, radius_km=None, sat_names=cat.names_in("GEO"))
    assert set(geo) == {"ON70"}
    one = grids.collect(log, radius_km=None, sat_names={"SO-50"})
    assert set(one) == {"OM89", "PM95"}
    assert grids.collect(log, radius_km=None, sat_names=set()) == {}
    ai6ot = grids.collect(log, radius_km=None, station_calls=["AI6OT"])
    assert set(ai6ot) == {"PM95", "PM06"}  # the QSO without a station call counts for any
    near = grids.collect(log, radius_km=None, my_grids=["ON80DA", "ON70"])
    assert set(near) == {"OM89", "ON60", "PM06"}  # the QSO without a grid counts for any place
    both = grids.collect(log, radius_km=None, station_calls=["BI1NWO"], my_grids=["OM32,OM33"])
    assert set(both) == {"OL48", "PM06"}


def test_check_list_button_names_the_ticked_entries(qtbot):
    b = CheckListButton("Grids")
    qtbot.addWidget(b)
    assert b.text() == "Grids: none in the log"
    b.set_items([("ON80DA", "ON80DA · 1", True), ("ON70", "ON70 · 1", True), ("CM87", "CM87 · 1", False)])
    assert b.checked() == ["ON80DA", "ON70"] and b.keys() == ["ON80DA", "ON70", "CM87"]
    assert b.text() == "Grids: ON80DA, ON70"
    changes = []
    b.changed.connect(lambda: changes.append(b.checked()))
    b.set_checked(["cm87"])
    assert changes == [["CM87"]] and b.text() == "Grids: CM87"
    b.set_checked(b.keys())
    assert b.text() == "Grids: all (3)"
    b.set_checked([])
    assert b.text() == "Grids: none"


@pytest.fixture
def tab(qtbot, log, tmp_path):
    log.save_profile({"name": "Home", "station_callsign": "BI1NWO", "operator": "BI1NWO", "my_gridsquare": "ON80DA",
                      "is_default": True})
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    prefs.set("map_provider", "none")
    prefs.set_value("map/globe_view", "3,110,35")
    widget = StatisticsTab(log, prefs)
    widget.resize(1000, 650)
    qtbot.addWidget(widget)
    widget.show()
    yield widget
    widget.shutdown()


def test_the_tab_lists_satellites_callsigns_and_places_with_counts(tab):
    texts = [tab.sat.itemText(i) for i in range(tab.sat.count())]
    assert texts[0] == "All satellites (6)" and texts[1].startswith("LEO") and "(4)" in texts[1]
    assert "GEO" in texts[4] and "(1)" in texts[4] and texts[5].startswith("Non-geostationary") and "(5)" in texts[5]
    assert "SO-50 (2)" in texts and "JAMX (1)" in texts and "QO-100 (1)" in texts and "FO-29" in texts  # worked or not
    assert tab.sat.isEnabled()
    assert tab.calls.keys() == ["BI1NWO", "AI6OT"] and tab.calls.checked() == ["BI1NWO", "AI6OT"]
    assert tab.calls.text() == "Callsigns: all (2)"
    assert tab.places.keys() == ["CM87", "OM32,OM33", "ON70", "ON80DA", "ON80EA"]
    assert tab.places.checked() == ["ON70", "ON80DA", "ON80EA"]  # within 200 km of the own grid by default
    assert tab.near_btn.text() == "Within 200 km of ON80"
    assert "km" in tab.places.list.item(0).text() and "1 QSOs" in tab.places.list.item(0).text()


def test_filters_change_the_squares_and_are_remembered(tab, tmp_path, log):
    tab.refresh()
    assert set(tab.map.cells) == {"OM89", "ON70", "ON60", "PM06"}  # near places, every call, every satellite
    tab.sat.setCurrentIndex(tab.sat.findData("GEO"))
    assert set(tab.map.cells) == {"ON70"}
    tab.sat.setCurrentIndex(tab.sat.findData("sat:SO-50"))
    assert set(tab.map.cells) == {"OM89"}
    tab.sat.setCurrentIndex(0)
    tab.places.set_checked(tab.places.keys())
    tab.calls.set_checked(["AI6OT"])
    assert set(tab.map.cells) == {"PM95", "PM06"}
    assert tab.prefs.value("map/calls") == "AI6OT" and tab.prefs.value("map/sat") == ""
    tab.places.set_checked(["CM87"])
    assert tab.prefs.value("map/grids") == "CM87"
    tab.band.setCurrentIndex(tab.band.findData(""))
    assert not tab.sat.isEnabled()
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    again = StatisticsTab(log, prefs)
    try:
        assert again.calls.checked() == ["AI6OT"] and again.places.checked() == ["CM87"]
        assert again.band.currentData() == ""
    finally:
        again.shutdown()


def test_the_png_is_named_after_the_ticked_calls_and_places(tab):
    assert tab.png_name() == f"BI1NWO (et al) at ON80 - NWOlogger {__version__}.png"  # the centre grid alone
    tab.calls.set_checked(["AI6OT"])
    tab.places.set_checked(["CM87"])
    assert tab.png_name() == f"AI6OT at CM87 - NWOlogger {__version__}.png"
    tab.calls.set_checked([])
    tab.places.set_checked([])
    assert tab.png_name() == f"BI1NWO at ON80 - NWOlogger {__version__}.png"  # the profile and the own grid


def test_within_200_km_and_the_own_grid(tab):
    tab.places.set_checked(tab.places.keys())
    tab.tick_near()
    assert tab.places.checked() == ["ON70", "ON80DA", "ON80EA"]
    tab.home.setText("cm87")
    tab.home.editingFinished.emit()
    assert tab.home.text() == "CM87" and tab.near_btn.text() == "Within 200 km of CM87"
    tab.tick_near()
    assert tab.places.checked() == ["CM87"]
    assert "0 km" in tab.places.list.item(0).text()


def test_qsos_logged_while_the_tab_is_hidden_reach_its_lists_when_it_is_shown(qtbot, log, tmp_path):
    """A new station profile's call, used for a QSO from the Log tab: on show, Callsigns and Grids hold it. A list
    that had everything ticked ticks the new call too; Grids on its 200 km default applies that rule."""
    from PySide6.QtWidgets import QStackedWidget, QWidget

    log.save_profile({"name": "Home", "station_callsign": "BI1NWO", "operator": "BI1NWO", "my_gridsquare": "ON80DA",
                      "is_default": True})
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    prefs.set("map_provider", "none")
    stack = QStackedWidget()
    stack.addWidget(QWidget())  # the Log tab, in front
    tab = StatisticsTab(log, prefs)
    stack.addWidget(tab)
    stack.resize(1000, 650)
    qtbot.addWidget(stack)
    stack.show()
    try:
        assert tab.calls.keys() == ["BI1NWO", "AI6OT"] and not tab.isVisible()
        log.insert(qsomod.normalize({"call": "BA1ZZ", "time_on": "2026-02-01T10:00:00Z", "mode": "FM", "band": "2m",
                                     "prop_mode": "SAT", "sat_name": "SO-50", "gridsquare": "PM95",
                                     "station_callsign": "BD1AJF", "my_gridsquare": "ON80EB"}))  # near the own grid
        log.insert(qsomod.normalize({"call": "BA1YY", "time_on": "2026-02-02T10:00:00Z", "mode": "FM", "band": "2m",
                                     "prop_mode": "SAT", "sat_name": "SO-50", "gridsquare": "FN31",
                                     "station_callsign": "BD1AJF", "my_gridsquare": "FN42"}))  # far away
        qtbot.wait(1000)
        assert "BD1AJF" not in tab.calls.keys()  # hidden: nothing rebuilt yet, nothing to see
        stack.setCurrentWidget(tab)
        qtbot.waitUntil(tab.isVisible)
        assert tab.calls.keys() == ["BI1NWO", "BD1AJF", "AI6OT"] and tab.calls.checked() == tab.calls.keys()
        assert "ON80EB" in tab.places.keys() and "FN42" in tab.places.keys()
        assert "ON80EB" in tab.places.checked() and "FN42" not in tab.places.checked()  # the 200 km default
        assert "PM95" in tab.map.cells and "FN31" not in tab.map.cells
        # An explicit choice stays as it was: a further new call is listed but not ticked.
        stack.setCurrentIndex(0)
        tab.calls.set_checked(["BI1NWO"])
        log.insert(qsomod.normalize({"call": "BA1XX", "time_on": "2026-02-03T10:00:00Z", "mode": "FM", "band": "2m",
                                     "prop_mode": "SAT", "sat_name": "SO-50", "gridsquare": "OM77",
                                     "station_callsign": "VR2XMT", "my_gridsquare": "OL72"}))
        stack.setCurrentWidget(tab)
        qtbot.waitUntil(tab.isVisible)
        assert "VR2XMT" in tab.calls.keys() and tab.calls.checked() == ["BI1NWO"] and "OM77" not in tab.map.cells
    finally:
        tab.shutdown()


def test_picking_the_current_region_again_fits_it_again(tab, monkeypatch):
    """Around my grid, a drag away, then Around my grid picked again: the globe comes back (a combo box emits no
    change for the item that is already current, so the fit listens to every pick)."""
    fits = []
    monkeypatch.setattr(tab.map, "fit", lambda box: fits.append(box))
    tab.region.setCurrentIndex(tab.region.findData("home"))
    fits.clear()
    tab.map.set_view_state((4.0, 10.0, 50.0))  # dragged far away
    tab.region.activated.emit(tab.region.currentIndex())  # the user picks Around my grid once more
    assert fits == [grids.region_box("home", "ON80DA")]

