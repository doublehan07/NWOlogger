"""Save as PNG: the map as you see it, named after the operator, once the imagery is in; and the map on the whole
screen, with Save as PNG there too."""

from __future__ import annotations

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


def test_save_as_png_keeps_the_view_as_you_see_it_at_twice_its_size(tab, monkeypatch, tmp_path):
    asked = []

    def save_dialog(parent, _title, suggested, _filter):
        asked.append((parent, suggested))
        return str(tmp_path / "mine"), ""

    monkeypatch.setattr(statistics_tab.QFileDialog, "getSaveFileName", save_dialog)
    tab.refresh()
    m = tab.map
    m.set_terminator_enabled(False)  # the colours, not the time of day the test runs at
    state = m.view_state()
    on_screen = m.grab().toImage()
    tab.save_png()
    assert asked[0][0] is tab and asked[0][1].endswith("BI1ZZZ-grid-map-NWOlogger.png")
    image = QImage(str(tmp_path / "mine.png"))
    scale, band = gridmap.EXPORT_SCALE, gridmap.CAPTION_PX
    assert (image.width(), image.height()) == (m.width() * scale, (m.height() + band) * scale)
    assert m.view_state() == state and tab._snapshot is None
    # The same view: the confirmed square ON80 is where it is on screen, below the caption's band (sampled in
    # its corner, away from its name and call).
    pos = m.to_screen(116.25, 40.12)
    shown = on_screen.pixelColor(pos.toPoint())
    saved = image.pixelColor(round(pos.x() * scale), round((pos.y() + band) * scale))
    assert shown.green() > shown.red() + 40 and saved.green() > saved.red() + 40
    assert abs(saved.green() - shown.green()) < 25


def test_snapshot_waits_for_the_tiles_of_the_view(qtbot, monkeypatch):
    source = GridMapView()
    source.resize(600, 400)
    source.set_provider(maptiles.NASA)
    view = source.export_view()
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
    assert images[0].width() == 600 * gridmap.EXPORT_SCALE
    centre = images[0].pixelColor(600, round((200 + gridmap.CAPTION_PX) * gridmap.EXPORT_SCALE))
    assert centre.green() > centre.red() and centre.green() > centre.blue()
    source.shutdown()


def test_snapshot_draws_without_tiles_that_failed(qtbot, monkeypatch):
    source = GridMapView()
    source.resize(300, 360)
    source.set_provider(maptiles.NASA)
    view = source.export_view()
    monkeypatch.setattr(view.fetcher, "want", lambda *_args: None)  # asked, answered at once, nothing came
    snapshot = GlobeSnapshot(view, deadline=5)
    with qtbot.waitSignal(snapshot.finished, timeout=3000) as signal:
        snapshot.start()
    assert signal.args[0].width() == 300 * gridmap.EXPORT_SCALE
    source.shutdown()


def test_full_screen_shows_the_map_alone_and_esc_brings_it_back(tab, qtbot):
    m = tab.map
    tab.toggle_full_screen()
    assert m.isWindow() and m.isFullScreen()
    assert m._leave.isVisible() and m._save.isVisible() and tab.away.isVisible()
    assert m._save.geometry().right() < m._leave.geometry().left()  # side by side in the top right corner
    assert m._leave.geometry().right() <= m.width() - 10 and m._leave.geometry().top() <= 20
    qtbot.keyClick(m, Qt.Key.Key_Escape)
    assert not m.isWindow() and m.parent() is tab and m.isVisible()
    assert not m._leave.isVisible() and not m._save.isVisible() and not tab.away.isVisible()
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
    view = source.export_view()
    monkeypatch.setattr(view.fetcher, "want", lambda *_args: None)
    monkeypatch.setattr(view.fetcher, "pending", lambda: 3)  # a provider that never answers
    snapshot = GlobeSnapshot(view, deadline=0.5)
    with qtbot.waitSignal(snapshot.finished, timeout=3000):
        snapshot.start()
    source.shutdown()


def test_save_as_png_from_the_full_screen_map_opens_on_its_window(tab, monkeypatch, tmp_path):
    parents = []

    def save_dialog(parent, _title, _suggested, _filter):
        parents.append(parent)
        return str(tmp_path / "full.png"), ""

    monkeypatch.setattr(statistics_tab.QFileDialog, "getSaveFileName", save_dialog)
    tab.toggle_full_screen()
    tab.map._save.click()
    assert parents == [tab.map] and tab.map.isWindow()  # it stays full screen
    image = QImage(str(tmp_path / "full.png"))
    assert image.width() == tab.map.width() * gridmap.EXPORT_SCALE


def test_export_names_nwologger_and_its_version_in_a_band_of_its_own(qtbot):
    from nwologger import __version__

    source = GridMapView()
    source.resize(500, 400)
    source.set_terminator_enabled(False)
    view = source.export_view()
    assert view.caption == f"NWOlogger {__version__}" and view.view_state() == source.view_state()
    image = view.snapshot()
    scale, band = gridmap.EXPORT_SCALE, gridmap.CAPTION_PX
    assert image.height() == (400 + band) * scale
    rows = range(round(band * .3 * scale), round(band * .7 * scale))
    text = [image.pixelColor(x, y) for y in rows for x in range(image.width())]
    assert max(c.red() + c.green() + c.blue() for c in text) > 500  # white text in the middle of the band
    edges = [image.pixelColor(x, y) for y in rows for x in (*range(60), *range(image.width() - 60, image.width()))]
    assert all(c.red() + c.green() + c.blue() < 120 for c in edges)  # dark band either side
    # Below the band the map is drawn as on screen: the globe's centre there, as in the view itself.
    centre = image.pixelColor(round(250 * scale), round((200 + band) * scale))
    shown = source.grab().toImage().pixelColor(250, 200)
    assert all(abs(a - b) < 20 for a, b in zip(centre.getRgb()[:3], shown.getRgb()[:3], strict=True))
    assert source.caption is None  # only the export carries it
    view.shutdown()
    source.shutdown()


def test_a_slow_export_shows_its_progress_soon_and_keeps_it_with_the_map(tab, qtbot, monkeypatch, tmp_path):
    asked = []

    def save_dialog(_parent, _title, _suggested, _filter):
        asked.append(True)
        return str(tmp_path / "slow.png"), ""

    class Waiting:  # an export whose imagery never arrives
        def __init__(self, view, parent=None):
            self.view, self.cancelled = view, False
            self.finished = type("Signal", (), {"connect": lambda *_a: None})()

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True
            self.view.shutdown()

    monkeypatch.setattr(statistics_tab.QFileDialog, "getSaveFileName", save_dialog)
    monkeypatch.setattr(statistics_tab, "GlobeSnapshot", Waiting)
    tab.save_png()
    progress = tab._progress
    qtbot.waitUntil(progress.isVisible, timeout=1500)  # within about half a second, not Qt's default four
    tab.save_png()  # asked again: no second file dialog, the same progress comes forward
    assert len(asked) == 1 and tab._progress is progress and progress.isVisible()
    tab.toggle_full_screen()
    assert progress.parentWidget() is tab.map and progress.isVisible()  # on the full-screen window
    tab.toggle_full_screen()
    assert progress.parentWidget() is tab and progress.isVisible()
    snapshot = tab._snapshot
    from PySide6.QtWidgets import QPushButton

    progress.findChild(QPushButton).click()  # Cancel
    assert snapshot.cancelled and tab._snapshot is None and tab._progress is None
