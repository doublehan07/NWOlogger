"""Low-resolution imagery keeps its geographic alignment on the globe."""

from __future__ import annotations

import gc
import math

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage, QPainter

from nwologger.core import globe, grids
from nwologger.ui.stats.globeraster import GlobeRaster


def _atlas(width, height, color="green"):
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor(color))
    return image


def _sample(image, size, radius, camera, lon, lat):
    x, y, depth = camera.project(lon, lat)
    assert depth >= 0
    column = math.floor((size.width() / 2 + radius * x) * image.width() / size.width())
    row = math.floor((size.height() / 2 - radius * y) * image.height() / size.height())
    assert 0 <= column < image.width() and 0 <= row < image.height()
    return image.pixelColor(column, row)


def test_inverse_projection_samples_matching_mercator_texture_quadrants():
    atlas = _atlas(256, 256)
    colors = {(0.25, 0.25): QColor("red"), (0.75, 0.25): QColor("green"),
              (0.25, 0.75): QColor("blue"), (0.75, 0.75): QColor("yellow")}
    painter = QPainter(atlas)
    for (u, v), color in colors.items():
        painter.fillRect(0 if u < .5 else 128, 0 if v < .5 else 128, 128, 128, color)
    painter.end()
    size, radius, camera = QSize(900, 640), 326, globe.Orthographic(20, 20)
    image = GlobeRaster().image(atlas, size, radius, camera, level=3, tile_left=4, tile_top=3)
    for (u, v), color in colors.items():
        lon, lat = grids.from_world((4 + u) * 256, (3 + v) * 256, 3)
        assert _sample(image, size, radius, camera, lon, lat) == color


@pytest.mark.parametrize(("center_lon", "tile_left"), [(179, 7), (-179, -1)])
def test_unwrapped_atlas_crosses_dateline_without_reversing_or_losing_imagery(center_lon, tile_left):
    atlas = _atlas(512, 512, "blue")
    painter = QPainter(atlas)
    painter.fillRect(0, 0, 256, 512, QColor("red"))
    painter.end()
    size, radius, camera = QSize(900, 640), 300, globe.Orthographic(center_lon, 10)
    image = GlobeRaster().image(atlas, size, radius, camera, level=3, tile_left=tile_left, tile_top=3)
    assert _sample(image, size, radius, camera, 170, 5) == QColor("red")
    assert _sample(image, size, radius, camera, -170, 5) == QColor("blue")


@pytest.mark.parametrize("pole", [-90, 90])
def test_polar_view_has_imagery_at_valid_latitudes_and_transparent_mercator_caps(pole):
    size, radius, camera = QSize(800, 600), 280, globe.Orthographic(40, pole)
    image = GlobeRaster().image(_atlas(256, 256), size, radius, camera,
                                level=0, tile_left=0, tile_top=0)
    assert _sample(image, size, radius, camera, 40, math.copysign(80, pole)) == QColor("green")
    assert _sample(image, size, radius, camera, 40, math.copysign(89, pole)).alpha() == 0


def test_pixels_outside_globe_and_outside_available_atlas_are_transparent():
    size, radius, camera = QSize(900, 640), 280, globe.Orthographic(20, 20)
    image = GlobeRaster().image(_atlas(256, 256), size, radius, camera,
                                level=3, tile_left=4, tile_top=3)
    for x, y in ((0, 0), (image.width() - 1, 0), (0, image.height() - 1)):
        assert image.pixelColor(x, y).alpha() == 0
    assert _sample(image, size, radius, camera, -10, 20).alpha() == 0
    assert _sample(image, size, radius, camera, 20, 20) == QColor("green")


@pytest.mark.parametrize("size", [QSize(2000, 1440), QSize(640, 900), QSize(320, 240)])
def test_imagery_resolution_is_bounded_without_stretching_viewport(size):
    image = GlobeRaster().image(_atlas(256, 256), size, min(size.width(), size.height()) * .46,
                                globe.Orthographic(0, 0), level=0, tile_left=0, tile_top=0)
    assert not image.isNull()
    assert max(image.width(), image.height()) <= 480
    assert image.width() <= size.width() and image.height() <= size.height()
    assert abs(image.width() * size.height() - image.height() * size.width()) <= max(
        size.width(), size.height(),
    )


def test_image_owns_its_pixels_across_new_frames_and_garbage_collection():
    raster = GlobeRaster()
    size, radius, camera = QSize(800, 600), 280, globe.Orthographic(20, 20)
    atlas = _atlas(256, 256, "red")
    first = raster.image(atlas, size, radius, camera, level=0, tile_left=0, tile_top=0)
    expected = first.copy()
    atlas.fill(QColor("blue"))
    second = raster.image(atlas, size, radius, camera, level=0, tile_left=0, tile_top=0)
    assert _sample(second, size, radius, camera, 20, 20) == QColor("blue")
    del raster, atlas, second
    gc.collect()
    assert first == expected
    assert _sample(first, size, radius, camera, 20, 20) == QColor("red")


def test_changing_resolution_budget_recomputes_projection_at_the_requested_size():
    raster = GlobeRaster()
    size, radius, camera = QSize(900, 640), 280, globe.Orthographic(20, 20)
    atlas = _atlas(256, 256)
    full = raster.image(atlas, size, radius, camera, level=0, tile_left=0, tile_top=0)
    coarse = raster.image(atlas, size, radius, camera, level=0, tile_left=0, tile_top=0, max_pixels=120)
    assert max(coarse.width(), coarse.height()) <= 120
    assert coarse.width() < full.width() and coarse.height() < full.height()
    assert _sample(coarse, size, radius, camera, 20, 20) == QColor("green")
