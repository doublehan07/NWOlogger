"""Save as PNG: the map as you see it, named after the operator, once the imagery is in; and the map on the whole
screen, with Save as PNG there too."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QBuffer, QIODevice, QPointF, QSettings, Qt
from PySide6.QtGui import QImage

from nwologger import __version__
from nwologger.core import db as dbm
from nwologger.core import grids, maptiles
from nwologger.core import qso as qsomod
from nwologger.ui.prefs import Prefs
from nwologger.ui.stats import gridmap, statistics_tab
from nwologger.ui.stats.gridmap import GlobeSnapshot, GridMapView
from nwologger.ui.stats.statistics_tab import StatisticsTab, png_file_name


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


@pytest.mark.parametrize(("operator", "station", "home", "name"), [
    ("BI1ZZZ", "BY1ZZ", "ON80da", "BI1ZZZ at ON80 - NWOlogger {v}.png"),
    ("", "BY1ZZ", "ON80", "BY1ZZ at ON80 - NWOlogger {v}.png"),
    ("bi1zzz/p", "", "", "BI1ZZZ-P - NWOlogger {v}.png"),
    ("", "", "", "NWOlogger {v}.png"),
])
def test_png_is_named_after_the_operator_and_the_home_grid(tab, operator, station, home, name):
    profile = tab.db.default_profile()
    tab.db.save_profile({**profile, "operator": operator, "station_callsign": station})
    tab.home.setText(home)
    assert tab.png_name() == name.format(v=__version__)


def test_png_file_name_says_et_al_for_more_calls_or_grids():
    assert png_file_name(["BI1NWO"], ["ON80"], "0.1.9") == "BI1NWO at ON80 - NWOlogger 0.1.9.png"
    assert png_file_name(["BI1NWO", "AI6OT"], ["ON80", "CM87", "ON70"], "0.1.9") == \
        "BI1NWO et al at ON80 et al - NWOlogger 0.1.9.png"
    assert png_file_name(["VR2/BD1AJF"], [], "0.1.9") == "VR2-BD1AJF - NWOlogger 0.1.9.png"
    assert png_file_name([], ["ON80"], "0.1.9") == "ON80 - NWOlogger 0.1.9.png"
    assert png_file_name(["", None], [""], "0.1.9") == "NWOlogger 0.1.9.png"


def test_the_counts_badge_is_drawn_in_the_corner_and_exported(tab):
    assert tab.map.badge.startswith("1 confirmed (LoTW) · 0 worked only · VUCC")
    view = tab.map.export_view()
    try:
        assert view.badge == tab.map.badge and view.badge_runs == tab.map.badge_runs
        assert view.badge_scale == gridmap.EXPORT_BADGE_SCALE and tab.map.badge_scale == 1.0
        assert tab.map.badge_runs[0] == ("1", "ok") and ("0", "warn") in tab.map.badge_runs
        image = view.snapshot()
        # the badge sits in the bottom right corner, in the sky: dark box with light text
        corner = [image.pixelColor(image.width() - 30 - x, image.height() - 10 - y).lightness()
                  for x in range(0, 300, 10) for y in range(0, 100, 5)]  # the export's badge is drawn larger
        assert max(corner) > 150, "no light text in the corner"
        greens = [image.pixelColor(image.width() - 30 - x, image.height() - 10 - y)
                  for x in range(0, 1600, 2) for y in range(0, 100, 2)]  # the whole badge; the "1" is a thin glyph
        assert any(c.green() > 170 and c.green() > c.red() + 40 for c in greens), "the confirmed count is not green"
    finally:
        view.shutdown()
        view.deleteLater()


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
    assert asked[0][0] is tab and asked[0][1].endswith(f"BI1ZZZ - NWOlogger {__version__}.png")
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


def test_open_in_a_window_and_esc_brings_it_back(tab, qtbot):
    pane, m = tab.pane, tab.map
    tab.toggle_window()
    assert pane.isWindow() and pane.isVisible() and tab.in_window() and m.parent() is pane
    assert not tab._away_box.isHidden() and tab.window_btn.text() == "Bring it back"
    assert pane.windowTitle().endswith("Grid map") and not pane.isFullScreen()
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QLabel, QPushButton

    pane.resize(1200, 700)
    qtbot.waitUntil(lambda: pane.width() == 1200)
    first = next(w for w in pane.findChildren(QLabel) if w.text() == "Satellite")
    last = next(b for b in pane.findChildren(QPushButton) if b.text() == "Save as PNG")
    assert pane.layout().contentsMargins().left() == 8  # the window keeps the tab's margins
    assert first.mapTo(pane, QPoint(0, 0)).x() >= 8
    assert last.mapTo(pane, QPoint(last.width(), 0)).x() <= pane.width() - 8
    qtbot.keyClick(m, Qt.Key.Key_Escape)  # Esc in the window: back into the tab
    assert not pane.isWindow() and tab.layout().indexOf(pane) >= 0 and tab._away_box.isHidden()
    assert tab.window_btn.text() == "Open in a window" and m.isVisible()
    assert pane.layout().contentsMargins().left() == 0  # the tab's layout has them again


def test_the_window_remembers_its_geometry_and_its_close_button_brings_it_back(tab):
    tab.toggle_window()
    width = tab.pane.minimumSizeHint().width() + 100  # above the controls' minimum, whatever the font's width
    tab.pane.setGeometry(120, 90, width, 600)
    tab.pane.close()  # the window's own close button comes back too
    assert not tab.pane.isWindow() and tab.pane.isVisible()
    assert tab.prefs.value("map/window_geometry") == f"120,90,{width},600"
    tab.window_btn.click()
    assert tab.in_window() and (tab.pane.width(), tab.pane.height()) == (width, 600)
    tab.bring_back()


def test_full_screen_fills_the_window_and_esc_leaves_it_first(tab, qtbot):
    m = tab.map
    tab.toggle_full_screen()  # from the tab: the window opens and fills the screen
    assert tab.in_window() and tab.pane.isFullScreen() and tab.is_full_screen()
    assert m._leave.isVisible() and m._save.isVisible()
    assert m._save.geometry().right() < m._leave.geometry().left()  # side by side in the top right corner
    assert m._leave.geometry().right() <= m.width() - 10 and m._leave.geometry().top() <= 20
    qtbot.keyClick(m, Qt.Key.Key_Escape)
    assert tab.in_window() and not tab.pane.isFullScreen() and not m._leave.isVisible()
    qtbot.keyClick(m, Qt.Key.Key_Escape)
    assert not tab.in_window()


def test_clicking_a_square_in_the_window_shows_its_qsos_and_keeps_the_window(tab, qtbot):
    shown = []
    tab.show_square.connect(shown.append)
    tab.refresh()
    tab.toggle_window()
    m = tab.map
    lat, lon = grids.center("ON80")
    m.set_view_state((6, lon, lat))
    pos = m.to_screen(lon, lat).toPoint()
    qtbot.mouseClick(m, Qt.MouseButton.LeftButton, pos=pos)
    assert shown == ["ON80"] and tab.in_window()
    tab.bring_back()


def test_shutdown_in_a_window_puts_the_map_back(tab):
    tab.toggle_window()
    tab.shutdown()
    assert not tab.pane.isWindow()


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
    assert parents == [tab.pane] and tab.is_full_screen()  # it stays full screen, in the map's own window
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
    assert tab._snapshot.view.caption == tab.png_name()[:-4]  # the band above the map names the file
    qtbot.waitUntil(progress.isVisible, timeout=1500)  # within about half a second, not Qt's default four
    tab.save_png()  # asked again: no second file dialog, the same progress comes forward
    assert len(asked) == 1 and tab._progress is progress and progress.isVisible()
    tab.toggle_full_screen()
    assert progress.parentWidget() is tab.pane and progress.isVisible()  # on the full-screen window
    tab.toggle_full_screen()
    assert tab.in_window() and progress.parentWidget() is tab.pane and progress.isVisible()  # still its window
    tab.bring_back()
    assert progress.parentWidget() is tab and progress.isVisible()
    snapshot = tab._snapshot
    from PySide6.QtWidgets import QPushButton

    progress.findChild(QPushButton).click()  # Cancel
    assert snapshot.cancelled and tab._snapshot is None and tab._progress is None
