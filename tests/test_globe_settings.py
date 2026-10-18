"""Globe orientation settings migrate independently of the old planar map state."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLabel

from nwologger import i18n
from nwologger.core import db as dbm
from nwologger.core import grids
from nwologger.ui.prefs import Prefs
from nwologger.ui.stats.gridmap import GridMapView
from nwologger.ui.stats.statistics_tab import StatisticsTab


@pytest.fixture
def make_tab(qtbot, tmp_path, monkeypatch):
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    db.save_profile({"name": "Test station", "station_callsign": "BI1ZZZ", "my_gridsquare": "ON80",
                     "is_default": True})
    tabs = []
    fits = []
    fit = GridMapView.fit

    def record_fit(self, box):
        fits.append((self, box))
        return fit(self, box)

    monkeypatch.setattr(GridMapView, "fit", record_fit)

    def make(values):
        prefs = Prefs(QSettings(str(tmp_path / f"settings-{len(tabs)}.ini"), QSettings.Format.IniFormat))
        prefs.set("map_provider", "none")
        for key, value in values.items():
            prefs.set_value(key, value)
        tab = StatisticsTab(db, prefs)
        tab.resize(1000, 650)
        qtbot.addWidget(tab)
        tabs.append(tab)
        qtbot.wait(1)  # the initial region fit is scheduled after construction
        return tab

    yield make, fits
    for tab in tabs:
        tab.shutdown()
    db.close()


def test_globe_view_takes_precedence_and_saves_without_overwriting_planar_view(make_tab):
    make, fits = make_tab
    legacy = "4,200,100"
    tab = make({"map/globe_view": "5.25,-73.5,42.25", "map/view": legacy})
    assert tab.map.view_state() == pytest.approx((5.25, -73.5, 42.25))
    assert not fits
    tab.map.set_view_state((6.125, 117, 40.5))
    tab.save_settings()
    assert tab.prefs.value("map/globe_view") == "6.125000,117.000000,40.500000"
    assert tab.prefs.value("map/view") == legacy
    restored = make({"map/globe_view": tab.prefs.value("map/globe_view")})
    assert restored.map.view_state() == pytest.approx((6.125, 117, 40.5))


def test_planar_view_migrates_mercator_pixels_to_degrees(make_tab):
    make, fits = make_tab
    x, y = grids.to_world(117, 40.5, 0)
    legacy = f"4.25,{x:.12f},{y:.12f}"
    tab = make({"map/view": legacy})
    assert tab.map.view_state() == pytest.approx((4.25, 117, 40.5))
    assert not fits
    tab.save_settings()
    assert tab.prefs.value("map/globe_view") == "4.250000,117.000000,40.500000"
    assert tab.prefs.value("map/view") == legacy


@pytest.mark.parametrize("state", [
    "", "3,0", "3,0,0,0", "bad", "nan,0,0", "3,inf,0", "3,0,-inf",
    "0.9,0,0", "11.1,0,0", "3,181,0", "3,-181,0", "3,0,91", "3,0,-91",
])
def test_invalid_globe_view_fits_region_instead_of_reusing_stale_planar_view(make_tab, state):
    make, fits = make_tab
    tab = make({"map/globe_view": state, "map/view": "4,200,100", "map/region": "japan"})
    assert fits == [(tab.map, grids.region_box("japan", "ON80"))]


@pytest.mark.parametrize("state", [None, "", "3,0", "3,nan,128", "3,128,inf", "3,-1,128", "3,128,257"])
def test_missing_or_invalid_legacy_view_fits_selected_region(make_tab, state):
    make, fits = make_tab
    values = {"map/region": "europe"}
    if state is not None:
        values["map/view"] = state
    tab = make(values)
    assert fits == [(tab.map, grids.region_box("europe", "ON80"))]


@pytest.mark.parametrize("lang,gesture", [
    ("en", "Rotate: right-drag (Ctrl + drag on a trackpad) or arrow keys · Zoom: scroll, double-click, +/−"),
    ("zh_CN", "旋转：右键拖动（触控板上 Ctrl + 拖动）或方向键 · 缩放：滚轮、双击或 +/−")])
def test_globe_interaction_hint_is_translated_and_wraps(make_tab, lang, gesture):
    make, _fits = make_tab
    previous = i18n.lang()
    try:
        i18n.load(lang)
        tab = make({"map/globe_view": "3,110,35"})
        hint = tab.findChild(QLabel, "Hint")
        assert hint.wordWrap()
        assert hint.text().startswith(gesture)
        assert "drag to move" not in hint.text()
        if lang == "zh_CN":
            assert "方向键" in hint.text()
    finally:
        i18n.load(previous)
