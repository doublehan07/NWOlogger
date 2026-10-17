"""Cached cell boundaries remain aligned while the globe rotates and zooms."""

from __future__ import annotations

import math

import numpy as np
import pytest
from PySide6.QtCore import QPointF

from nwologger.core import globe, grids
from nwologger.ui.stats.globegeometry import CellGeometry


def _screen(camera, radius, width, height, lon, lat):
    x, y, _depth = camera.project(lon, lat)
    return QPointF(width / 2 + x * radius, height / 2 - y * radius)


def test_result_includes_empty_paths_for_hidden_and_offscreen_cells():
    camera = globe.Orthographic(1, .5)
    paths = CellGeometry().paths(["JJ00", "KK00", "RJ80"], camera, 5000, 900, 640)
    assert set(paths) == {"JJ00", "KK00", "RJ80"}
    assert paths["JJ00"].contains(_screen(camera, 5000, 900, 640, 1, .5))
    assert paths["KK00"].isEmpty()
    assert paths["RJ80"].isEmpty()


def test_horizon_crossing_cell_is_clipped_to_the_globe_limb():
    camera, radius, width, height = globe.Orthographic(1, 0), 300, 900, 640
    path = CellGeometry().paths(["NJ50"], camera, radius, width, height)["NJ50"]
    assert not path.isEmpty()
    assert path.contains(_screen(camera, radius, width, height, 90.1, .5))
    for index in range(path.elementCount()):
        point = path.elementAt(index)
        assert math.hypot(point.x - width / 2, point.y - height / 2) <= radius + 1e-7


@pytest.mark.parametrize(("square", "center"), [
    ("AJ00", (179, .5)), ("RJ90", (-179, .5)),
    ("AR09", (-179, 89.5)), ("AA00", (-179, -89.5)),
])
def test_cells_remain_aligned_across_dateline_and_at_both_poles(square, center):
    camera = globe.Orthographic(*center)
    radius, width, height = 600, 900, 640
    path = CellGeometry().paths([square], camera, radius, width, height)[square]
    lat, lon = grids.center(square)
    assert not path.isEmpty()
    assert path.contains(_screen(camera, radius, width, height, lon, lat))
    assert all(math.isfinite(value) for index in range(path.elementCount())
               for value in (path.elementAt(index).x, path.elementAt(index).y))


@pytest.mark.parametrize("radius", [326, 1303, 83443])
def test_projected_boundary_chord_error_stays_below_quarter_pixel(radius):
    camera, square, width, height = globe.Orthographic(111, 35.5), "OM55", 2000, 1440
    path = CellGeometry().paths([square], camera, radius, width, height)[square]
    assert not path.isEmpty()
    vertices = np.array([(path.elementAt(index).x, path.elementAt(index).y)
                         for index in range(path.elementCount())])
    west, south, east, north = grids.bounds(square)
    samples = ([(lon, north) for lon in np.linspace(west, east, 101)] +
               [(east, lat) for lat in np.linspace(north, south, 101)] +
               [(lon, south) for lon in np.linspace(east, west, 101)] +
               [(west, lat) for lat in np.linspace(south, north, 101)])
    points = np.array([(point.x(), point.y()) for lon, lat in samples
                       for point in [_screen(camera, radius, width, height, lon, lat)]])
    starts, vectors = vertices[:-1], np.diff(vertices, axis=0)
    squared_lengths = np.sum(vectors * vectors, axis=1)
    offsets = points[:, None, :] - starts[None, :, :]
    fractions = np.clip(np.sum(offsets * vectors[None, :, :], axis=2) / squared_lengths, 0, 1)
    distances = np.linalg.norm(offsets - fractions[:, :, None] * vectors[None, :, :], axis=2)
    assert np.max(np.min(distances, axis=1)) <= .251


def test_rotation_reuses_world_vertices_and_zoom_adapts_boundary_precision():
    geometry = CellGeometry()
    initial = geometry.paths(["OM55"], globe.Orthographic(111, 35.5), 326, 2000, 1440)["OM55"]
    batch = geometry._batch
    rotated = geometry.paths(["OM55"], globe.Orthographic(112, 35.5), 326, 2000, 1440)["OM55"]
    assert geometry._batch is batch
    assert rotated.boundingRect().center() != initial.boundingRect().center()
    zoomed = geometry.paths(["OM55"], globe.Orthographic(111, 35.5), 83443, 2000, 1440)["OM55"]
    assert geometry._batch is not batch
    assert zoomed.elementCount() > initial.elementCount()


def test_world_vertex_cache_is_bounded_when_cell_sets_change():
    geometry = CellGeometry(cache_limit=2)
    first = geometry.paths(["JJ00", "JJ10", "JJ20"], globe.Orthographic(3, .5), 326, 900, 640)
    assert all(not path.isEmpty() for path in first.values())
    assert len(geometry._vertices) <= 2
    second = geometry.paths(["JJ30", "JJ00"], globe.Orthographic(3, .5), 326, 900, 640)
    assert all(not path.isEmpty() for path in second.values())
    assert len(geometry._vertices) <= 2
