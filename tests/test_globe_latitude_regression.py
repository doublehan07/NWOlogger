"""Sampling the visible polar edge must never produce invalid globe coordinates."""

from __future__ import annotations

import math

import pytest

from nwologger.core import globe, grids
from nwologger.ui.stats.gridmap import GridMapView, _samples


@pytest.mark.parametrize(("start", "end", "step"), [
    (-76.83138876988907, 90.0, 0.9319666737511019),
    (37.12516705382234, -90.0, 3.512319207691088),
    (-20.2063475047524, 90.0, 2.260772147537493),
])
def test_polar_sampling_preserves_endpoints_and_coordinate_domain(start, end, step):
    values = _samples(start, end, step)
    assert values[0] == start
    assert values[-1] == end
    assert all(min(start, end) <= value <= max(start, end) for value in values)
    projection = globe.Orthographic(110, 45)
    for latitude in values:
        assert all(math.isfinite(value) for value in projection.project(110, latitude))


@pytest.mark.parametrize(("center_lon", "center_lat", "zoom"), [
    (110, 45, 3.9000000000000004),
    (-179.999, 45, 3.9000000000000004),
    (179.999, -45, 3.9000000000000004),
    (0, 90, 3.9000000000000004),
    (0, -90, 3.9000000000000004),
    (110, 35, 3),
])
def test_grid_paths_cover_polar_and_dateline_views_without_invalid_latitudes(
    qtbot, center_lon, center_lat, zoom,
):
    widget = GridMapView()
    qtbot.addWidget(widget)
    widget.resize(900, 640)
    widget.center_lon, widget.center_lat, widget.zoom = center_lon, center_lat, zoom
    try:
        for spacing in ((2, 1), (20, 10)):
            path = widget._grid_path(*spacing)
            assert not path.isEmpty()
            for index in range(path.elementCount()):
                point = path.elementAt(index)
                assert math.isfinite(point.x) and math.isfinite(point.y)
    finally:
        widget.shutdown()


@pytest.mark.parametrize("home", ["JR19", "JA10"])
def test_home_region_extending_past_a_pole_can_be_fitted(qtbot, home):
    widget = GridMapView()
    qtbot.addWidget(widget)
    widget.resize(900, 640)
    try:
        widget.fit(grids.region_box("home", home))
        assert all(math.isfinite(value) for value in widget.view_state())
        assert -90 <= widget.center_lat <= 90
        assert not widget.grab().isNull()
    finally:
        widget.shutdown()


def test_failed_frame_render_releases_painter_and_does_not_cache_partial_frame(qtbot, monkeypatch):
    widget = GridMapView()
    qtbot.addWidget(widget)
    widget.resize(900, 640)
    painters = []

    def fail_scene(painter):
        painters.append(painter)
        raise RuntimeError("synthetic rendering failure")

    monkeypatch.setattr(widget, "_paint_scene", fail_scene)
    try:
        with pytest.raises(RuntimeError, match="synthetic rendering failure"):
            widget.paintEvent(None)
        assert len(painters) == 1
        assert not painters[0].isActive()
        assert widget._frame_key is None
    finally:
        for painter in painters:
            if painter.isActive():
                painter.end()
        widget.shutdown()
