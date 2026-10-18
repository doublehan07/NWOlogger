"""Day/night display settings are independent of QSO statistics and survive restart."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QRect, QSettings, Qt

from nwologger import i18n
from nwologger.core import db as dbm
from nwologger.core import grids
from nwologger.core import qso as qsomod
from nwologger.ui.prefs import Prefs
from nwologger.ui.stats.statistics_tab import StatisticsTab
from nwologger.ui.widgets import ScrollArea


@pytest.fixture
def make_tab(qtbot, tmp_path):
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    db.insert(qsomod.normalize({
        "call": "BI1ZZZ", "time_on": "2026-10-18T10:00:00Z", "mode": "FM", "band": "2m",
        "prop_mode": "SAT", "sat_name": "SO-50", "gridsquare": "ON80", "lotw_qsl_rcvd": "Y",
    }))
    tabs = []

    def make(values=None, *, path=None):
        settings_path = path or tmp_path / f"settings-{len(tabs)}.ini"
        prefs = Prefs(QSettings(str(settings_path), QSettings.Format.IniFormat))
        prefs.set("map_provider", "none")
        prefs.set_value("map/globe_view", "3,110,35")
        for key, value in (values or {}).items():
            prefs.set_value(key, value)
        tab = StatisticsTab(db, prefs)
        tab.resize(1000, 650)
        qtbot.addWidget(tab)
        tabs.append(tab)
        return tab

    yield make
    for tab in tabs:
        tab.shutdown()
    db.close()


@pytest.mark.parametrize("lang,label,tooltip", [
    ("en", "Day/night", "current UTC time"),
    ("zh_CN", "晨昏线", "当前 UTC 时间"),
])
def test_button_defaults_on_and_explains_automatic_current_time_display(make_tab, lang, label, tooltip):
    previous = i18n.lang()
    try:
        i18n.load(lang)
        tab = make_tab()
        assert tab.terminator_btn.isCheckable()
        assert tab.terminator_btn.isChecked()
        assert tab.map.terminator_enabled
        assert tab.terminator_btn.text() == label
        assert tooltip in tab.terminator_btn.toolTip()
        assert ("自动更新" if lang == "zh_CN" else "updates automatically") in tab.terminator_btn.toolTip()
    finally:
        i18n.load(previous)


@pytest.mark.parametrize("saved,enabled", [
    (True, True), (False, False), (1, True), (0, False),
    ("true", True), ("false", False), ("1", True), ("0", False),
    (" TRUE ", True), (" FALSE ", False), ("on", True), ("off", False),
])
def test_native_and_serialized_boolean_settings_restore(make_tab, saved, enabled):
    tab = make_tab({"map/terminator": saved})
    assert tab.terminator_btn.isChecked() is enabled
    assert tab.map.terminator_enabled is enabled


@pytest.mark.parametrize("enabled", [False, True])
def test_toggle_persists_immediately_and_restores_from_disk(make_tab, qtbot, tmp_path, enabled):
    path = tmp_path / "restart.ini"
    tab = make_tab({"map/terminator": not enabled}, path=path)
    qtbot.mouseClick(tab.terminator_btn, Qt.MouseButton.LeftButton)
    assert tab.terminator_btn.isChecked() is enabled
    assert tab.map.terminator_enabled is enabled
    assert tab.prefs.value("map/terminator") is enabled
    tab.prefs.s.sync()
    restored = make_tab(path=path)
    assert restored.terminator_btn.isChecked() is enabled
    assert restored.map.terminator_enabled is enabled


def test_toggle_does_not_collect_filter_or_change_qso_counts(make_tab, monkeypatch, qtbot):
    tab = make_tab()
    tab.refresh()
    assert list(tab.map.cells) == ["ON80"]
    assert "1" in tab.count.text()
    original_cells = tab.map.cells
    original_count = tab.count.text()
    original_sources = tab.sources()
    original_state = (tab.band.currentData(), tab.mode.currentData(), tab.near.isChecked(), tab.radius.value(),
                      tab.home.text(), tab.region.currentData(), tab.map.view_state(), tab._dirty)

    def unexpected_collect(*_args, **_kwargs):
        pytest.fail("Toggling day/night must not reload QSO statistics")

    monkeypatch.setattr(grids, "collect", unexpected_collect)
    qtbot.mouseClick(tab.terminator_btn, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(tab.terminator_btn, Qt.MouseButton.LeftButton)
    assert tab.map.cells is original_cells
    assert tab.count.text() == original_count
    assert tab.sources() == original_sources
    assert (tab.band.currentData(), tab.mode.currentData(), tab.near.isChecked(), tab.radius.value(),
            tab.home.text(), tab.region.currentData(), tab.map.view_state(), tab._dirty) == original_state


@pytest.mark.parametrize("lang", ["en", "zh_CN"])
def test_button_remains_visible_in_scaled_screen_viewport(make_tab, qtbot, qapp, lang):
    previous = i18n.lang()
    try:
        i18n.load(lang)
        tab = make_tab({"map/region": "conus"})
        page = ScrollArea(tab)
        qtbot.addWidget(page)
        try:
            page.resize(780, 500)
            page.show()
            qapp.processEvents()
            assert page.width() == 780 and page.height() == 500
            assert page.horizontalScrollBar().value() == 0
            assert tab.terminator_btn.isVisibleTo(page)
            button_rect = QRect(tab.terminator_btn.mapTo(page.viewport(), QPoint()), tab.terminator_btn.size())
            assert page.viewport().rect().contains(button_rect)
            qtbot.mouseClick(tab.terminator_btn, Qt.MouseButton.LeftButton)
            assert not tab.terminator_btn.isChecked()
            assert not tab.map.terminator_enabled
        finally:
            page.takeWidget()  # the fixture owns shutdown and disposal of the tab
    finally:
        i18n.load(previous)


def test_button_looks_selected_while_the_night_side_is_shown(make_tab, qapp):
    from PySide6.QtGui import QColor

    from nwologger.ui import theme

    old = theme.mode()
    tokens = theme.apply(qapp, "light")
    try:
        tab = make_tab()
        button = tab.terminator_btn
        button.resize(button.sizeHint())
        assert button.isChecked()
        point = QPoint(6, button.height() // 2)
        on = button.grab().toImage().pixelColor(point)
        button.setChecked(False)
        off = button.grab().toImage().pixelColor(point)
        soft = QColor(tokens.accent_soft)
        assert on != off
        assert all(abs(a - b) <= 12 for a, b in zip(on.getRgb()[:3], soft.getRgb()[:3], strict=True))
    finally:
        theme.apply(qapp, old)
