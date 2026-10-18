"""Save as PNG: the whole globe, named after the operator, once the imagery is in; and the map on the whole screen."""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QBuffer, QIODevice, QPointF, QSettings, Qt
from PySide6.QtGui import QImage

from nwologger.core import db as dbm
from nwologger.core import grids, maptiles
from nwologger.core import qso as qsomod
from nwologger.ui.prefs import Prefs
from nwologger.ui.stats import gridmap, statistics_tab
from nwologger.ui.stats.gridmap import GlobeSnapshot, GridMapView
from nwologger.ui.stats.statistics_tab import StatisticsTab


@pytest.fixture
def tab(qtbot, tmp_path):
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    db.insert(qsomod.normalize({
        "call": "BI1ZZZ", "time_on": "2026-10-18T10:00:00Z", "mode": "FM", "band": "2m",
        "prop_mode": "SAT", "sat_name": "SO-50", "gridsquare": "ON80", "lotw_qsl_rcvd": "Y",
    }))
    db.save_profile({"name": "Home", "station_callsign": "BY1ZZ", "operator": "BI1ZZZ", "is_default": True})
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    prefs.set("map_provider", "none")
    prefs.set_value("map/globe_view", "6,116,40")  # zoomed in on one region
    widget = StatisticsTab(db, prefs)
    widget.resize(1000, 650)
    qtbot.addWidget(widget)
    widget.show()
    yield widget
    widget.shutdown()
    db.close()


@pytest.mark.parametrize(("operator", "station", "name"), [
    ("BI1ZZZ", "BY1ZZ", "BI1ZZZ-grid-map-NWOlogger.png"),
    ("", "BY1ZZ", "BY1ZZ-grid-map-NWOlogger.png"),
    ("bi1zzz/p", "", "BI1ZZZ-P-grid-map-NWOlogger.png"),
    ("", "", "grid-map-NWOlogger.png"),
])
def test_png_is_named_after_the_operator(tab, operator, station, name):
    profile = tab.db.default_profile()
    tab.db.save_profile({**profile, "operator": operator, "station_callsign": station})
    assert tab.png_name() == name


def test_save_as_png_draws_the_whole_globe_however_far_the_map_is_zoomed(tab, monkeypatch, tmp_path):
    asked = []

    def save_dialog(_parent, _title, suggested, _filter):
        asked.append(suggested)
        return str(tmp_path / "mine"), ""

    monkeypatch.setattr(statistics_tab.QFileDialog, "getSaveFileName", save_dialog)
    zoom_before = tab.map.zoom
    tab.save_png()
    assert asked and asked[0].endswith("BI1ZZZ-grid-map-NWOlogger.png")
    image = QImage(str(tmp_path / "mine.png"))
    assert (image.width(), image.height()) == (gridmap.EXPORT_PX, gridmap.EXPORT_PX)
    middle, radius = gridmap.EXPORT_PX / 2, gridmap.EXPORT_PX * gridmap.EXPORT_RADIUS
    for angle in range(0, 360, 30):  # the atmosphere's glow all round: the whole globe is in the image
        dx, dy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        glow = image.pixelColor(round(middle + dx * (radius + 4)), round(middle + dy * (radius + 4)))
        space = image.pixelColor(round(middle + dx * (radius + 70)), round(middle + dy * (radius + 70)))
        assert glow.blue() > space.blue() + 30, angle
    assert tab.map.zoom == zoom_before and tab._snapshot is None


def test_snapshot_waits_for_the_tiles_of_the_whole_globe(qtbot, monkeypatch):
    source = GridMapView()
    source.resize(600, 400)
    source.set_provider(maptiles.NASA)
    view = source.export_view(400)
    wanted = []
    monkeypatch.setattr(view.fetcher, "want", lambda _provider, z, x, y, _key=None: wanted.append((z, x, y)))
    monkeypatch.setattr(view.fetcher, "pending", lambda: len(set(wanted)) - len(view._pix))  # none answered yet
    snapshot = GlobeSnapshot(view, deadline=30)
    images = []
    snapshot.finished.connect(images.append)
    snapshot.start()
    qtbot.wait(500)
    assert wanted and not images  # it asked for the tiles and waits for them
    tile = QImage(256, 256, QImage.Format.Format_RGB32)
    tile.fill(Qt.GlobalColor.darkGreen)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    tile.save(buffer, "PNG")
    for z, x, y in set(wanted):
        view._store_tile(maptiles.NASA.key, z, x, y, bytes(buffer.data()))
    qtbot.waitUntil(lambda: bool(images), timeout=3000)
    assert images[0].width() == 400
    centre = images[0].pixelColor(200, 200)
    assert centre.green() > centre.red() and centre.green() > centre.blue()
    source.shutdown()


def test_snapshot_draws_without_tiles_that_failed(qtbot, monkeypatch):
    source = GridMapView()
    source.set_provider(maptiles.NASA)
    view = source.export_view(300)
    monkeypatch.setattr(view.fetcher, "want", lambda *_args: None)  # asked, answered at once, nothing came
    snapshot = GlobeSnapshot(view, deadline=5)
    with qtbot.waitSignal(snapshot.finished, timeout=3000) as signal:
        snapshot.start()
    assert signal.args[0].width() == 300
    source.shutdown()


def test_full_screen_shows_the_map_alone_and_esc_brings_it_back(tab, qtbot):
    m = tab.map
    tab.toggle_full_screen()
    assert m.isWindow() and m.isFullScreen()
    assert m._leave.isVisible() and tab.away.isVisible()
    qtbot.keyClick(m, Qt.Key.Key_Escape)
    assert not m.isWindow() and m.parent() is tab and m.isVisible()
    assert not m._leave.isVisible() and not tab.away.isVisible()
    assert tab.layout().indexOf(m) >= 0


def test_full_screen_button_and_closing_the_window(tab):
    tab.full_btn.click()
    assert tab.map.isWindow()
    tab.map.close()  # the full-screen window's own close comes back too
    assert not tab.map.isWindow() and tab.map.isVisible()


def test_clicking_a_square_in_full_screen_comes_back_to_show_its_qsos(tab, qtbot):
    shown = []
    tab.show_square.connect(shown.append)
    tab.refresh()
    tab.toggle_full_screen()
    m = tab.map
    lat, lon = grids.center("ON80")
    m.set_view_state((6, lon, lat))
    pos = m.to_screen(lon, lat).toPoint()
    qtbot.mouseClick(m, Qt.MouseButton.LeftButton, pos=pos)
    assert shown == ["ON80"] and not m.isWindow()


def test_shutdown_in_full_screen_puts_the_map_back(tab):
    tab.toggle_full_screen()
    tab.shutdown()
    assert not tab.map.isWindow()


def test_halo_and_stars_frame_the_globe(qtbot):
    view = GridMapView()
    view.resize(800, 600)
    view.set_terminator_enabled(False)
    view.fit(grids.REGIONS["world"])
    qtbot.addWidget(view)
    image = view.grab().toImage()
    middle, radius = QPointF(400, 300), view._radius()
    just_outside = image.pixelColor(round(middle.x() + radius + 3), 300)
    far = image.pixelColor(round(middle.x() + radius + 80), 300)
    assert just_outside.blue() > far.blue()  # the atmosphere's glow
    brightest = max(max(image.pixelColor(x, y).getRgb()[:3]) for x in range(0, 800, 2) for y in range(0, 60, 2))
    assert brightest > 120  # stars in the sky above the globe
    view.shutdown()


def test_snapshot_stops_waiting_at_its_deadline(qtbot, monkeypatch):
    source = GridMapView()
    source.set_provider(maptiles.NASA)
    view = source.export_view(300)
    monkeypatch.setattr(view.fetcher, "want", lambda *_args: None)
    monkeypatch.setattr(view.fetcher, "pending", lambda: 3)  # a provider that never answers
    snapshot = GlobeSnapshot(view, deadline=0.5)
    with qtbot.waitSignal(snapshot.finished, timeout=3000):
        snapshot.start()
    source.shutdown()


def test_export_names_nwologger_and_its_version_above_the_globe():
    from nwologger import __version__

    source = GridMapView()
    view = source.export_view(1000)
    assert view.caption == f"NWOlogger {__version__}"
    rect, radius = view.caption_rect(), view._radius()
    glow = max(6.0, min(40.0, radius * 0.045))
    assert rect.bottom() < view.height() / 2 - radius - glow  # clear of the globe and its glow
    image = view.snapshot()
    band = [image.pixelColor(x, round(rect.center().y())) for x in range(400, 600)]
    assert max(c.red() + c.green() + c.blue() for c in band) > 500  # the text is there, white
    assert source.caption is None  # only the export carries it
    view.shutdown()
    source.shutdown()
