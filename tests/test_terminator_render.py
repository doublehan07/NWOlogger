"""Current-time solar shading stays aligned with the globe, exports and cache."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage

from nwologger.core import globe, grids
from nwologger.ui.stats import gridmap


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
def test_night_polygon_matches_solar_dot_on_visible_hemisphere(view, center, sun):
    view.center_lon, view.center_lat = center
    view._sun_position = sun
    night, _boundary = view._terminator_paths()
    checked = 0
    for lon in range(-177, 180, 13):
        for lat in range(-83, 90, 11):
            if view._camera().project(lon, lat)[2] < .05:
                continue
            dot = _solar_dot(lon, lat, sun)
            if abs(dot) < .005:
                continue
            assert night.contains(view.to_screen(lon, lat)) == (dot < 0), (center, sun, lon, lat, dot)
            checked += 1
    assert checked > 100


@pytest.mark.parametrize(("center", "sun"), [
    ((0, 0), (90, 0)),
    ((117, 40.5), (10, 23.44)),
    ((179, 25), (-179, -23.4)),
    ((0, 90), (179.9, 0)),
    ((-90, -90), (45, -23)),
])
def test_terminator_boundary_has_zero_geometric_solar_altitude(view, center, sun):
    view.center_lon, view.center_lat = center
    view._sun_position = sun
    _night, boundary = view._terminator_paths()
    assert not boundary.isEmpty()
    sun_x, sun_y, sun_z = view._camera().project(*sun)
    radius = view._radius()
    for index in range(boundary.elementCount()):
        point = boundary.elementAt(index)
        x, y = (point.x - view.width() / 2) / radius, (view.height() / 2 - point.y) / radius
        assert x * x + y * y <= 1 + 1e-12
        depth = math.sqrt(max(0, 1 - x * x - y * y))
        assert abs(x * sun_x + y * sun_y + depth * sun_z) <= 1e-7


@pytest.mark.parametrize("center", [(0, 0), (117, 40.5), (179, -60), (30, 90)])
def test_view_facing_sun_or_antisun_has_whole_day_or_whole_night(view, center):
    view.center_lon, view.center_lat = center
    view._sun_position = center
    night, boundary = view._terminator_paths()
    assert night.isEmpty() and boundary.isEmpty()
    view._sun_position = (globe.normalize_lon(center[0] + 180), -center[1])
    night, boundary = view._terminator_paths()
    assert boundary.isEmpty() and not night.isEmpty()
    middle = QPointF(view.width() / 2, view.height() / 2)
    assert night.contains(middle)
    assert night.contains(middle + QPointF(view._radius() * .95, 0))
    assert not night.contains(middle + QPointF(view._radius() * 1.05, 0))


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
