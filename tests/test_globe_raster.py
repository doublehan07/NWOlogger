"""Low-resolution imagery keeps its geographic alignment on the globe."""

from __future__ import annotations

import gc
import math

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage, QPainter

from nwologger.core import globe, grids
from nwologger.ui.stats import globeraster
from nwologger.ui.stats.globeraster import CellTable, GlobeRaster


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
@pytest.mark.parametrize("scale", [0.5, 1.0, 2.0])
def test_image_has_the_requested_scale_without_stretching_viewport(size, scale):
    image = GlobeRaster().image(_atlas(256, 256), size, min(size.width(), size.height()) * .46,
                                globe.Orthographic(0, 0), level=0, tile_left=0, tile_top=0, scale=scale)
    assert not image.isNull()
    assert (image.width(), image.height()) == (math.ceil(size.width() * scale), math.ceil(size.height() * scale))


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
    coarse = raster.image(atlas, size, radius, camera, level=0, tile_left=0, tile_top=0, scale=120 / 900)
    assert max(coarse.width(), coarse.height()) <= 120
    assert coarse.width() < full.width() and coarse.height() < full.height()
    assert _sample(coarse, size, radius, camera, 20, 20) == QColor("green")


def _pixel(image, size, radius, camera, lon, lat, scale=1.0):
    x, y, depth = camera.project(lon, lat)
    assert depth >= 0
    return image.pixelColor(math.floor((size.width() / 2 + radius * x) * scale),
                            math.floor((size.height() / 2 - radius * y) * scale))


@pytest.mark.parametrize(("center", "square", "inside"), [
    ((116, 35), "OM85", (117, 35.5)),
    ((179, 0), "AJ00", (-179, 0.5)),
    ((-179, 0), "RJ90", (179, 0.5)),
    ((3, 89.4), "JR19", (3, 89.5)),
    ((3, -89.4), "JA10", (3, -89.5)),
])
def test_squares_are_filled_where_they_are_across_the_dateline_and_at_the_poles(center, square, inside):
    # Next to a pole a 2° square is narrow, so look closer there.
    size, radius = QSize(900, 640), 2000 if abs(inside[1]) < 89 else 40000
    camera = globe.Orthographic(*center)
    image = GlobeRaster().cells(CellTable({square: QColor(0, 200, 0, 255)}), size, radius, camera)
    color = _pixel(image, size, radius, camera, *inside)
    assert color.alpha() == 255 and color.green() == 200
    west, south, east, north = grids.bounds(square)
    beyond = north + 0.4 if south <= -90 else south - 0.4  # the square next to it in latitude
    for lon, lat in ((west - 0.5, (south + north) / 2), ((west + east) / 2, beyond)):
        x, y, depth = camera.project(lon, lat)
        if depth > 0 and abs(radius * x) < size.width() / 2 - 1 and abs(radius * y) < size.height() / 2 - 1:
            assert _pixel(image, size, radius, camera, lon, lat).alpha() == 0


def test_square_edges_blend_over_about_one_pixel():
    size, radius, camera = QSize(900, 640), 3000, globe.Orthographic(1, 0.5)
    image = GlobeRaster().cells(CellTable({"JJ00": QColor(0, 200, 0, 255)}), size, radius, camera)
    x, y, _depth = camera.project(0, 0.5)  # the square's west edge
    column, row = size.width() / 2 + radius * x, math.floor(size.height() / 2 - radius * y)
    alphas = [image.pixelColor(math.floor(column) + dx, row).alpha() for dx in range(-3, 4)]
    assert alphas == sorted(alphas)
    assert alphas[0] == 0 and alphas[-1] == 255
    partial = [a for a in alphas if 0 < a < 255]
    assert 1 <= len(partial) <= 2
    # The edge pixel's coverage matches how far the edge runs through it.
    left = math.floor(column)
    expected = 255 * min(1, max(0, left + 1 - column))
    assert image.pixelColor(left, row).alpha() == pytest.approx(expected, abs=40)


def test_border_edges_every_filled_square_even_next_to_the_same_colour():
    size, radius, camera = QSize(900, 640), 3000, globe.Orthographic(2, 0.5)
    table = CellTable({"JJ00": QColor(0, 200, 0, 255), "JJ10": QColor(0, 200, 0, 255)})
    raster = GlobeRaster()
    plain = raster.cells(table, size, radius, camera)
    edged = raster.cells(table, size, radius, camera, border=QColor(255, 255, 255, 255))
    x, y, _depth = camera.project(2, 0.5)  # between the two squares
    column, row = round(size.width() / 2 + radius * x), math.floor(size.height() / 2 - radius * y)
    assert plain.pixelColor(column, row) == plain.pixelColor(column - 20, row)
    assert edged.pixelColor(column, row).red() > 100 or edged.pixelColor(column - 1, row).red() > 100
    middle = edged.pixelColor(column - 20, row)
    assert (middle.red(), middle.green()) == (0, 200)


def test_deep_imagery_samples_the_right_tile_pixel():
    """Precision at the deepest level: one-pixel stripes far from the origin are still told apart. On the equator
    with one tile pixel per screen pixel and the centre on a pixel boundary, each screen pixel's centre is the
    centre of one tile pixel."""
    level, tile_left, tile_top = 12, 3000, 2047
    atlas = QImage(512, 512, QImage.Format.Format_RGB32)
    painter = QPainter(atlas)
    for column in range(512):
        painter.fillRect(column, 0, 1, 512, QColor("red") if column % 2 == 0 else QColor("blue"))
    painter.end()
    world = 256 * 2 ** level
    center_x = (tile_left + 1) * 256
    size, radius = QSize(600, 400), world / math.tau
    camera = globe.Orthographic(center_x / world * 360 - 180, 0)
    image = GlobeRaster().image(atlas, size, radius, camera, level=level, tile_left=tile_left, tile_top=tile_top)
    for column in range(size.width() // 2 - 40, size.width() // 2 + 40):
        texel = center_x - size.width() // 2 + column - tile_left * 256
        expected = QColor("red") if texel % 2 == 0 else QColor("blue")
        assert image.pixelColor(column, size.height() // 2) == expected, column


def test_night_follows_solar_altitude_and_repeats_the_limb_outside():
    size, radius, camera = QSize(900, 640), 290, globe.Orthographic(0, 0)
    sun = (60, 0)
    shade = QColor(0, 0, 0, 160)
    image = GlobeRaster().night(size, radius, camera, sun, shade, max_pixels=900)
    sun_point = globe.sphere_point(*sun)
    for lon in range(-85, 86, 5):
        x, y, depth = camera.project(lon, 0)
        altitude = math.degrees(math.asin(sum(a * b for a, b in zip(globe.sphere_point(lon, 0), sun_point,
                                                                      strict=True))))
        alpha = image.pixelColor(math.floor(size.width() / 2 + radius * x), math.floor(size.height() / 2)).alpha()
        if altitude > globeraster.DAY_ALTITUDE + 1:
            assert alpha == 0, lon
        elif altitude < globeraster.NIGHT_ALTITUDE - 1:
            assert alpha == 160, lon
        else:
            assert 0 <= alpha <= 160
    # A clear edge, yet antialiased: no jump of more than a third of the shade between neighbouring pixels along
    # the equator, and only a few pixels between day and night.
    row = [image.pixelColor(column, size.height() // 2).alpha() for column in range(image.width())]
    assert max(abs(a - b) for a, b in zip(row, row[1:], strict=False)) <= 56
    assert 4 <= len(set(row)) <= 20
    # Beyond the limb (night side) the shade continues, so scaling up leaves no light seam.
    assert image.pixelColor(2, size.height() // 2).alpha() == 160
