"""Current-time solar shading stays aligned with the globe, exports and cache."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage

from nwologger.core import globe, grids
from nwologger.ui.stats import globeraster, gridmap


@pytest.fixture
def clock(monkeypatch):
    current = [datetime(2026, 3, 20, 12, 0, tzinfo=UTC)]
    monkeypatch.setattr(gridmap, "_utc_now", lambda: current[0])
    return current


@pytest.fixture
def view(qtbot, clock):
    widget = gridmap.GridMapView()
    widget.resize(900, 640)
    widget.center_lon, widget.center_lat = 0, 0
    widget.fit(grids.REGIONS["world"])
    qtbot.addWidget(widget)
    yield widget
    widget.shutdown()


def _solar_dot(lon, lat, sun):
    point = globe.sphere_point(lon, lat)
    sun_point = globe.sphere_point(*sun)
    return sum(a * b for a, b in zip(point, sun_point, strict=True))


def _night_alpha(view, image, lon, lat):
    pos = view.to_screen(lon, lat)
    return image.pixelColor(min(image.width() - 1, int(pos.x() * image.width() / view.width())),
                            min(image.height() - 1, int(pos.y() * image.height() / view.height()))).alpha()


@pytest.mark.parametrize(("center", "sun"), [
    ((0, 0), (0, 0)),
    ((0, 0), (180, 0)),
    ((117, 40.5), (10, 23.44)),
    ((179, 25), (-179, -23.4)),
    ((0, 90), (179.9, 0)),
    ((-90, -90), (45, -23)),
    ((179.9, 89), (-179.9, 23)),
    ((0, 0), (90, 0)),
])
def test_night_shade_follows_solar_altitude_on_visible_hemisphere(view, center, sun):
    view.center_lon, view.center_lat = center
    view._sun_position = sun
    image = view._night_image()
    full = gridmap.NIGHT_SHADE.alpha()
    checked = 0
    for lon in range(-177, 180, 13):
        for lat in range(-83, 90, 11):
            if view._camera().project(lon, lat)[2] < .2:
                continue
            altitude = math.degrees(math.asin(_solar_dot(lon, lat, sun)))
            alpha = _night_alpha(view, image, lon, lat)
            if altitude > globeraster.DAY_ALTITUDE + 2:
                assert alpha == 0, (center, sun, lon, lat, altitude)
            elif altitude < globeraster.NIGHT_ALTITUDE - 2:
                assert alpha == full, (center, sun, lon, lat, altitude)
            checked += 1
    assert checked > 100


@pytest.mark.parametrize(("center", "sun"), [((0, 0), (90, 0)), ((117, 40.5), (10, 23.44))])
def test_twilight_fades_from_day_to_night_without_a_line(view, center, sun):
    view.center_lon, view.center_lat = center
    view._sun_position = sun
    image = view._night_image()
    # Along the great circle from the subsolar point to its antipode, the shade only grows, through many steps.
    sun_lon, sun_lat = sun
    between = set()
    for bearing in (90, 270):
        alphas = []
        for step in range(0, 181):
            lon, lat = globe.destination(sun_lon, sun_lat, bearing, step * globe.EARTH_KM * math.pi / 180)
            if view._camera().project(lon, lat)[2] > .2:
                alphas.append(_night_alpha(view, image, lon, lat))
        assert alphas == sorted(alphas)
        between |= {a for a in alphas if 0 < a < gridmap.NIGHT_SHADE.alpha()}
    assert len(between) >= 3
    # No boundary line is drawn on top: the scene's pixels near the terminator are not brighter than the day.
    assert not hasattr(gridmap, "TERMINATOR_LINE")


@pytest.mark.parametrize("center", [(0, 0), (117, 40.5), (179, -60), (30, 90)])
def test_view_facing_sun_or_antisun_has_whole_day_or_whole_night(view, center):
    view.center_lon, view.center_lat = center
    view._sun_position = center
    image = view._night_image()
    middle = QPointF(view.width() / 2, view.height() / 2)
    for offset in (0, .9):
        pos = middle + QPointF(view._radius() * offset, 0)
        lonlat = view.to_lonlat(pos)
        assert _night_alpha(view, image, *lonlat) == 0
    view._sun_position = (globe.normalize_lon(center[0] + 180), -center[1])
    image = view._night_image()
    for offset in (0, .9):
        lonlat = view.to_lonlat(middle + QPointF(view._radius() * offset, 0))
        assert _night_alpha(view, image, *lonlat) == gridmap.NIGHT_SHADE.alpha()


def test_default_overlay_toggle_changes_night_pixels_and_png_export(view, tmp_path):
    assert view.terminator_enabled
    view.center_lon = 90
    night_pos, day_pos = view.to_screen(155, 17).toPoint(), view.to_screen(25, 17).toPoint()
    enabled = view.grab().toImage()
    path = tmp_path / "current-day-night.png"
    assert enabled.save(str(path), "PNG")
    exported = QImage(str(path))
    assert not exported.isNull()
    assert exported.pixelColor(night_pos) == enabled.pixelColor(night_pos)
    view.set_terminator_enabled(False)
    disabled = view.grab().toImage()
    on, off = enabled.pixelColor(night_pos), disabled.pixelColor(night_pos)
    assert sum(on.getRgb()[:3]) < sum(off.getRgb()[:3])
    assert enabled.pixelColor(day_pos) == disabled.pixelColor(day_pos)
    assert enabled != disabled


def _watch_scene(view, monkeypatch):
    scenes = []
    paint = view._paint_scene

    def record(painter):
        scenes.append(view._solar_time)
        paint(painter)

    monkeypatch.setattr(view, "_paint_scene", record)
    return scenes


def test_clock_refreshes_frame_after_one_minute_only_when_overlay_enabled(view, clock, monkeypatch):
    scenes = _watch_scene(view, monkeypatch)
    initial = clock[0]
    view.grab()
    assert view._solar_time == initial and len(scenes) == 1
    clock[0] = initial + timedelta(seconds=59)
    view.grab()
    assert view._solar_time == initial and len(scenes) == 1
    clock[0] = initial + timedelta(seconds=60)
    view.grab()
    assert view._solar_time == clock[0] and len(scenes) == 2
    view.set_terminator_enabled(False)
    view.grab()
    assert len(scenes) == 3
    last_time = view._solar_time
    clock[0] += timedelta(minutes=10)
    view.grab()
    assert len(scenes) == 3 and view._solar_time == last_time


def test_clock_moving_backwards_refreshes_sun_instead_of_using_future_cache(view, clock):
    view.grab()
    clock[0] -= timedelta(hours=1)
    view.grab()
    assert view._solar_time == clock[0]


def test_solar_timer_follows_visibility_toggle_and_shutdown(view, clock):
    timer = view._solar_timer
    assert timer.interval() == 60_000
    assert timer.timerType() == Qt.TimerType.PreciseTimer
    assert not timer.isActive()
    view.show()
    assert timer.isActive() and view._solar_time == clock[0]
    view.hide()
    assert not timer.isActive()
    view.show()
    assert timer.isActive()
    view.set_terminator_enabled(False)
    assert not timer.isActive()
    view.set_terminator_enabled(True)
    assert timer.isActive()
    clock[0] += timedelta(seconds=30)
    timer.timeout.emit()
    assert view._solar_time == clock[0]
    view.shutdown()
    assert not timer.isActive()
    clock[0] += timedelta(seconds=30)
    timer.timeout.emit()
    assert view._solar_time != clock[0]


@pytest.mark.parametrize("action", ["show", "PNG"])
def test_hidden_clock_is_refreshed_before_show_or_png_export(view, clock, tmp_path, action):
    view.show()
    view.hide()
    initial = view._solar_time
    assert not view._solar_timer.isActive()
    clock[0] += timedelta(minutes=20)
    assert view._solar_time == initial
    if action == "show":
        view.show()
        assert view._solar_timer.isActive()
    else:
        path = tmp_path / "hidden-current-day-night.png"
        assert view.grab().save(str(path), "PNG")
        assert not QImage(str(path)).isNull()
        assert not view._solar_timer.isActive()
    assert view._solar_time == clock[0]
