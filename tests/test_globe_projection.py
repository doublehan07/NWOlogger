"""Unit-sphere projection, horizon clipping and geodesic overlays."""

from __future__ import annotations

import math

import pytest

from nwologger.core import geo, globe


@pytest.mark.parametrize(("lon", "expected"), [
    (0, 0), (180, -180), (-180, -180), (540, -180), (-541, 179), (361.25, 1.25),
])
def test_longitude_wrap(lon, expected):
    assert globe.normalize_lon(lon) == expected


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_nonfinite_coordinates(bad):
    with pytest.raises(ValueError):
        globe.normalize_lon(bad)
    with pytest.raises(ValueError):
        globe.Orthographic(0, bad)
    with pytest.raises(ValueError):
        globe.Orthographic(bad, 0)
    with pytest.raises(ValueError):
        globe.sphere_point(0, bad)
    assert globe.Orthographic(0, 0).unproject(bad, 0) is None
    assert globe.Orthographic(0, 0).unproject(0, bad) is None


@pytest.mark.parametrize("lat", [-90.01, 90.01])
def test_invalid_latitude(lat):
    with pytest.raises(ValueError):
        globe.Orthographic(0, lat)
    with pytest.raises(ValueError):
        globe.sphere_point(0, lat)


@pytest.mark.parametrize(("lon", "lat", "expected"), [
    (0, 0, (1, 0, 0)), (90, 0, (0, 1, 0)), (0, 90, (0, 0, 1)),
    (180, 0, (-1, 0, 0)), (0, -90, (0, 0, -1)),
])
def test_world_cartesian_axes(lon, lat, expected):
    assert globe.sphere_point(lon, lat) == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize("center", [(0, 0), (117, 40.5), (179, -35), (-170, 80), (45, 90), (-30, -90)])
def test_front_hemisphere_round_trip(center):
    projection = globe.Orthographic(*center)
    assert projection.project(*center) == pytest.approx((0, 0, 1), abs=1e-12)
    assert projection.unproject(0, 0) == center
    for lon in range(-180, 180, 13):
        for lat in range(-85, 90, 17):
            x, y, depth = projection.project(lon, lat)
            assert x * x + y * y + depth * depth == pytest.approx(1, abs=1e-12)
            if depth < 1e-7:
                continue
            restored = projection.unproject(x, y)
            assert restored is not None
            assert globe.normalize_lon(restored[0] - lon) == pytest.approx(0, abs=1e-8)
            assert restored[1] == pytest.approx(lat, abs=1e-8)


def test_dateline_poles_limb_and_hidden_side():
    projection = globe.Orthographic(179, 0)
    x, y, depth = projection.project(-179, 0)
    assert x > 0 and y == 0 and depth > 0
    assert projection.unproject(x, y) == pytest.approx((-179, 0))
    assert projection.project(-1, 0)[2] == pytest.approx(-1)
    for point in ((0, 90), (0, -90)):
        x, y, _depth = projection.project(*point)
        restored = projection.unproject(x, y)
        assert restored is not None and restored[1] == pytest.approx(point[1])
    equatorial = globe.Orthographic(0, 0)
    assert equatorial.unproject(1, 0) == pytest.approx((90, 0))
    assert equatorial.unproject(-1, 0) == pytest.approx((-90, 0))
    assert equatorial.unproject(1.000001, 0) is None
    assert equatorial.unproject(1, 1) is None
    assert equatorial.unproject(1e200, 1e200) is None
    assert equatorial.unproject(1 + 1e-15, 0) == pytest.approx((90, 0))


@pytest.mark.parametrize("origin", [(117, 40.5), (179.9, 0), (-179.9, -82), (0, 90), (45, -90)])
@pytest.mark.parametrize("bearing", [0, 45, 90, 180, 270, 359])
def test_destination_keeps_geodesic_radius(origin, bearing):
    lon, lat = globe.destination(*origin, bearing, 200)
    assert -180 <= lon < 180 and -90 <= lat <= 90
    assert geo.distance_km((origin[1], origin[0]), (lat, lon)) == pytest.approx(200, abs=1e-8)
    if abs(origin[1]) < 89:
        actual_bearing = geo.bearing((origin[1], origin[0]), (lat, lon))
        assert globe.normalize_lon(actual_bearing - bearing) == pytest.approx(0, abs=1e-8)


def test_destination_special_distances_and_validation():
    assert globe.destination(181, 25, 45, 0) == pytest.approx((-179, 25))
    assert globe.destination(0, 0, 90, geo.EARTH_KM * math.pi) == pytest.approx((-180, 0))
    for bearing, distance in ((math.nan, 200), (0, math.inf), (0, -1)):
        with pytest.raises(ValueError):
            globe.destination(0, 0, bearing, distance)


def _vertex(projection, lon, lat, u, v):
    return (*projection.project(lon, lat), u, v)


def test_clip_preserves_front_and_removes_back_polygons():
    projection = globe.Orthographic(0, 0)
    front = [_vertex(projection, lon, lat, u, v) for lon, lat, u, v in
             [(-10, -10, 0, 0), (10, -10, 1, 0), (10, 10, 1, 1), (-10, 10, 0, 1)]]
    back = [_vertex(projection, lon + 180, lat, u, v) for lon, lat, u, v in
            [(-10, -10, 0, 0), (10, -10, 1, 0), (10, 10, 1, 1), (-10, 10, 0, 1)]]
    assert globe.clip_polygon(front) == front
    assert globe.clip_polygon(back) == []
    assert globe.clip_polygon([]) == []


def test_clip_crossing_polygon_is_on_globe_with_interpolated_texture():
    projection = globe.Orthographic(0, 0)
    polygon = [_vertex(projection, lon, lat, u, v) for lon, lat, u, v in
               [(80, -10, 0, 0), (100, -10, 1, 0), (100, 10, 1, 1), (80, 10, 0, 1)]]
    clipped = globe.clip_polygon(polygon)
    assert len(clipped) == 4
    horizon = [vertex for vertex in clipped if vertex[2] == 0]
    assert len(horizon) == 2
    for x, y, depth, u, v in clipped:
        assert depth >= 0
        assert x * x + y * y + depth * depth == pytest.approx(1, abs=1e-12)
        assert 0 <= u <= 1 and 0 <= v <= 1
    texture_coordinates = sorted((u, v) for _x, _y, _depth, u, v in horizon)
    assert texture_coordinates[0] == pytest.approx((0.5, 0))
    assert texture_coordinates[1] == pytest.approx((0.5, 1))


def test_clip_horizon_vertex_and_antipodal_edge():
    polygon = [(0.0, 1.0, 0.0, 0.5, 1.0), (0.6, 0.0, 0.8, 0.0, 0.0),
               (0.6, 0.0, -0.8, 1.0, 0.0)]
    clipped = globe.clip_polygon(polygon)
    assert len(clipped) == 3
    assert clipped[0] == polygon[0]
    assert clipped[-1] == (1, 0, 0, 0.5, 0)
    with pytest.raises(ValueError, match="antipodal"):
        globe.clip_polygon([(0, 0, 1, 0, 0), (0, 0, -1, 1, 0), (1, 0, 0, 0, 1)])
