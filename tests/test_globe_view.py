"""The globe widget projects imagery, picking and overlays onto the same sphere."""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QBuffer, QIODevice, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

from nwologger.core import globe, grids, maptiles
from nwologger.ui.stats import gridmap
from nwologger.ui.stats.gridmap import BG, GridMapView


@pytest.fixture
def view(qtbot):
    widget = GridMapView()
    widget.resize(900, 640)
    widget.center_lon, widget.center_lat = 0, 0
    qtbot.addWidget(widget)
    yield widget
    widget.shutdown()


def _image(view):
    image = QImage(view.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    return image


def _point(view, x, y):
    return QPointF(view.width() / 2 + x * view._radius(), view.height() / 2 - y * view._radius())


def _path_vertices(path):
    return [QPointF(path.elementAt(index).x, path.elementAt(index).y) for index in range(path.elementCount())]


def test_world_fit_draws_a_disk_with_empty_space_outside(view):
    view.center_lon, view.center_lat = 117, 40.5
    view.fit(grids.REGIONS["world"])
    assert (view.center_lon, view.center_lat) == (117, 40.5)
    assert view._radius() == pytest.approx(min(view.width(), view.height()) * 0.46)
    image = _image(view)
    view.render(image)
    for pos in (QPoint(5, 5), QPoint(view.width() - 5, 5), QPoint(5, view.height() - 5)):
        assert max(image.pixelColor(pos).getRgb()[:3]) < 40  # dark space (stars are sparse)
        assert view.to_lonlat(QPointF(pos)) is None
        assert view.square_at(QPointF(pos)) is None
    assert image.pixelColor(view.width() // 2, view.height() // 2) != BG


def test_front_square_is_selected_instead_of_overlapping_hidden_square(view, qtbot):
    front, hidden = "JJ12", "RJ82"
    view.cells = {front: grids.Square(front), hidden: grids.Square(hidden)}
    front_pos, hidden_pos = view.to_screen(3, 2.5), view.to_screen(177, 2.5)
    assert hidden_pos.x() == pytest.approx(front_pos.x())
    assert hidden_pos.y() == pytest.approx(front_pos.y())
    assert view.square_at(front_pos) == front
    assert not view._square_path(front).isEmpty()
    assert view._square_path(hidden).isEmpty()
    clicked = []
    view.square_clicked.connect(clicked.append)
    qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=front_pos.toPoint())
    assert clicked == [front]
    qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
    assert clicked == [front]


def test_spherical_grid_lines_curve_and_cell_edges_are_subdivided(view):
    view.center_lat = 45
    view.zoom = 5
    line = view._line_path((lon, 30) for lon in range(-60, 61, 2))
    points = _path_vertices(line)
    middle = view.to_screen(0, 30)
    assert len(points) >= 61
    assert abs(middle.y() - (points[0].y() + points[-1].y()) / 2) > 20
    view.zoom = 3
    polygon = view._geo_polygon(40, 20, 60, 30)
    assert polygon.elementCount() > 20
    assert not polygon.isEmpty()


def test_horizon_clips_cells_and_lines_to_unit_disk(view):
    view.zoom = 2
    polygon = view._geo_polygon(88, -5, 92, 5)
    assert not polygon.isEmpty()
    for pos in _path_vertices(polygon):
        distance = math.hypot(pos.x() - view.width() / 2, pos.y() - view.height() / 2)
        assert distance <= view._radius() + 1e-7
    assert view._geo_polygon(100, -5, 110, 5).isEmpty()
    path = view._line_path((lon, 20) for lon in range(80, 101))
    assert not path.isEmpty()
    last = _path_vertices(path)[-1]
    assert math.hypot(last.x() - view.width() / 2, last.y() - view.height() / 2) == pytest.approx(view._radius())


@pytest.mark.parametrize(("center", "target", "square"), [
    ((179, 0), (-179, 0.5), "AJ00"),
    ((-179, 0), (179, 0.5), "RJ90"),
    ((0, 90), (3, 89.5), "JR19"),
    ((0, -90), (3, -89.5), "JA10"),
])
def test_picking_and_cell_geometry_cross_dateline_and_reach_poles(view, center, target, square):
    view.center_lon, view.center_lat = center
    view.zoom = 7
    pos = view.to_screen(*target)
    assert view.to_lonlat(pos) == pytest.approx(target, abs=1e-8)
    assert view.square_at(pos) == square
    assert not view._square_path(square).isEmpty()


@pytest.mark.parametrize("box", [
    grids.REGIONS["china"], grids.REGIONS["conus"], grids.REGIONS["japan"],
    (170, -10, -170, 15), (-20, 75, 20, 89),
])
def test_regional_fit_keeps_region_inside_viewport(view, box):
    view.fit(box)
    west, south, east, north = box
    if east < west:
        east += 360
    for lon in (west, (west + east) / 2, east):
        for lat in (south, (south + north) / 2, north):
            assert view._camera().project(lon, lat)[2] >= 0
            pos = view.to_screen(lon, lat)
            assert view.width() * 0.049 <= pos.x() <= view.width() * 0.951
            assert view.height() * 0.049 <= pos.y() <= view.height() * 0.951


@pytest.mark.parametrize("center", [(0, 0), (179, 65), (-30, -75)])
def test_zoom_keeps_cursor_geographical_anchor(view, center):
    view.center_lon, view.center_lat = center
    view.zoom = 5
    pos = _point(view, 0.18, 0.12)
    anchor = view.to_lonlat(pos)
    assert anchor is not None
    view.zoom_at(pos, 1)
    new_pos = view.to_screen(*anchor)
    assert new_pos.x() == pytest.approx(pos.x(), abs=1e-4)
    assert new_pos.y() == pytest.approx(pos.y(), abs=1e-4)
    view.zoom_at(pos, -1)
    assert view.to_lonlat(pos) == pytest.approx(anchor, abs=1e-7)


def test_zoom_keeps_cursor_anchor_when_view_starts_at_north_pole(view):
    view.center_lon, view.center_lat = 110, 90
    view.zoom = 5
    pos = QPointF(850, 320)
    anchor = view.to_lonlat(pos)
    assert anchor is not None
    view.zoom_at(pos, 0.35)
    new_pos = view.to_screen(*anchor)
    assert new_pos.x() == pytest.approx(pos.x(), abs=1e-4)
    assert new_pos.y() == pytest.approx(pos.y(), abs=1e-4)


def test_drag_rotates_globe_while_preserving_grabbed_point(view, qtbot):
    view.zoom = 4
    view.center_lat = 25
    start = QPoint(view.width() // 2 + 30, view.height() // 2 - 20)
    end = start + QPoint(90, 35)
    anchor = view.to_lonlat(QPointF(start))
    qtbot.mousePress(view, Qt.MouseButton.RightButton, pos=start)
    qtbot.mouseMove(view, pos=end)
    qtbot.mouseRelease(view, Qt.MouseButton.RightButton, pos=end)
    assert view.center_lon != 0 and view.center_lat != 25
    new_pos = view.to_screen(*anchor)
    assert new_pos.x() == pytest.approx(end.x(), abs=1e-3)
    assert new_pos.y() == pytest.approx(end.y(), abs=1e-3)
    assert view._drag is None


def test_left_button_never_rotates_and_the_middle_button_does(view, qtbot):
    view.zoom = 4
    view.center_lat = 25
    before = view.view_state()
    start = QPoint(view.width() // 2 + 30, view.height() // 2 - 20)
    end = start + QPoint(90, 35)
    qtbot.mousePress(view, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(view, pos=end)
    qtbot.mouseRelease(view, Qt.MouseButton.LeftButton, pos=end)
    assert view.view_state() == before and view._drag is None and view._press is None
    qtbot.mousePress(view, Qt.MouseButton.MiddleButton, pos=start)
    qtbot.mouseMove(view, pos=end)
    qtbot.mouseRelease(view, Qt.MouseButton.MiddleButton, pos=end)
    assert view.view_state() != before and view._drag is None
    assert view.contextMenuPolicy() == Qt.ContextMenuPolicy.NoContextMenu  # the right button is for rotating


def test_keyboard_rotates_and_zooms_and_restored_state_is_validated(view, qtbot):
    assert view.set_view_state((4, 540, 25))
    assert view.view_state() == (4, -180, 25)
    before = view.view_state()
    for bad in (None, (4, 0), (4, 0, 91), (math.nan, 0, 0), (4, math.inf, 0)):
        assert not view.set_view_state(bad)
        assert view.view_state() == before
    qtbot.keyClick(view, Qt.Key.Key_Right)
    assert view.center_lon != before[1]
    qtbot.keyClick(view, Qt.Key.Key_Up)
    assert view.center_lat > before[2]
    center = view.center_lon, view.center_lat
    qtbot.keyClick(view, Qt.Key.Key_Plus)
    assert view.zoom == 4.5
    assert (view.center_lon, view.center_lat) == center
    qtbot.keyClick(view, Qt.Key.Key_Minus)
    assert view.zoom == 4


def test_keyboard_up_rotates_north_and_down_rotates_south(view, qtbot):
    view.zoom = 4
    view.center_lat = 35
    qtbot.keyClick(view, Qt.Key.Key_Up)
    north = view.center_lat
    assert north > 35
    qtbot.keyClick(view, Qt.Key.Key_Down)
    assert view.center_lat < north
    assert view.center_lat == pytest.approx(35)


def _quadrant_tile():
    image = QImage(256, 256, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    colors = {(.25, .25): QColor("red"), (.75, .25): QColor("green"),
              (.25, .75): QColor("blue"), (.75, .75): QColor("yellow")}
    for (u, v), color in colors.items():
        painter.fillRect(0 if u < .5 else 128, 0 if v < .5 else 128, 128, 128, color)
    painter.end()
    return QPixmap.fromImage(image), colors


def test_mercator_tile_texture_is_warped_to_matching_globe_coordinates(view, monkeypatch):
    view.zoom = 3
    view.center_lon, view.center_lat = 20, 20
    view.provider = maptiles.NASA
    pixmap, colors = _quadrant_tile()
    view._pix[(maptiles.NASA.key, 3, 4, 3)] = pixmap
    monkeypatch.setattr(view, "_imagery_level", lambda: 3)
    monkeypatch.setattr(view.fetcher, "want", lambda *_args: None)
    image = _image(view)
    painter = QPainter(image)
    view._draw_tiles(painter)
    painter.end()
    for (u, v), color in colors.items():
        lon, lat = grids.from_world((4 + u) * 256, (3 + v) * 256, 3)
        pos = view.to_screen(lon, lat).toPoint()
        assert image.pixelColor(pos) == color


def test_parent_tile_fallback_samples_the_correct_texture_region(view, monkeypatch):
    view.zoom = 4
    view.center_lon, view.center_lat = 20, 20
    view.provider = maptiles.NASA
    pixmap, colors = _quadrant_tile()
    view._pix[(maptiles.NASA.key, 3, 4, 3)] = pixmap
    monkeypatch.setattr(view, "_imagery_level", lambda: 4)
    wanted = []
    monkeypatch.setattr(view.fetcher, "want", lambda *args: wanted.append(args))
    image = _image(view)
    painter = QPainter(image)
    view._draw_tiles(painter)
    painter.end()
    assert wanted and all(args[1] == 4 for args in wanted)
    for (u, v), color in colors.items():
        lon, lat = grids.from_world((4 + u) * 256, (3 + v) * 256, 3)
        pos = view.to_screen(lon, lat).toPoint()
        assert image.pixelColor(pos) == color


@pytest.mark.parametrize("center", [(110, 35), (179.9, 55), (0, -60)])
def test_low_resolution_tiles_stay_aligned_and_picking_precise_at_high_zoom(view, monkeypatch, center):
    view.zoom = 11
    view.center_lon, view.center_lat = center
    view.provider = maptiles.NASA
    z = view._imagery_level()
    assert z == maptiles.NASA.max_zoom and view.zoom > z  # magnified: sampled smaller and scaled up smoothly
    tile = QImage(256, 256, QImage.Format.Format_RGB32)
    painter = QPainter(tile)
    for column in range(0, 256, 2):  # two-texel stripes: their centres stay pure after smooth scaling
        painter.fillRect(column, 0, 2, 256, QColor("red") if column % 4 == 0 else QColor("blue"))
    painter.end()
    pixmap = QPixmap.fromImage(tile)
    for tx, ty in view._tile_indices(z):
        view._pix[(maptiles.NASA.key, z, tx, ty)] = pixmap
    monkeypatch.setattr(view.fetcher, "want", lambda *_args: None)
    image = _image(view)
    painter = QPainter(image)
    view._draw_tiles(painter)
    painter.end()
    world_x, world_y = grids.to_world(*center, z)
    checked = 0
    for offset in range(-2, 3):
        column = (math.floor(world_x) // 2 + offset) * 2
        lon, lat = grids.from_world(column + 1, math.floor(world_y) + .5, z)
        pos = view.to_screen(lon, lat)
        if not view.rect().contains(pos.toPoint()):
            continue
        color = image.pixelColor(pos.toPoint())
        if column % 4 == 0:
            assert color.red() > color.blue() + 100, (column, color.getRgb())
        else:
            assert color.blue() > color.red() + 100, (column, color.getRgb())
        assert view.to_lonlat(pos) == pytest.approx((globe.normalize_lon(lon), lat), abs=1e-8)
        assert view.square_at(pos) == next(grids.squares_in(globe.normalize_lon(lon), lat,
                                                           globe.normalize_lon(lon), lat))
        checked += 1
    assert checked >= 3


@pytest.mark.parametrize("center", [(0, 0), (179.9, 0), (30, 80)])
def test_high_zoom_requests_only_small_visible_tile_set(view, monkeypatch, center):
    view.zoom = 11
    view.center_lon, view.center_lat = center
    view.provider = maptiles.TIANDITU
    wanted = []
    monkeypatch.setattr(view.fetcher, "want", lambda *args: wanted.append(args))
    image = _image(view)
    painter = QPainter(image)
    view._draw_tiles(painter)
    painter.end()
    assert 1 <= len(wanted) <= 36
    indices = {(args[1], args[2], args[3]) for args in wanted}
    assert len(indices) == len(wanted)
    for z, tx, ty in indices:
        assert 0 <= tx < 2 ** z and 0 <= ty < 2 ** z
        assert 8 <= z <= maptiles.TIANDITU.max_zoom  # detail follows the zoom instead of stopping at level 5
        assert (tx, ty) in set(view._tile_indices(z))


def _watch_scene(view, monkeypatch):
    scenes = []
    paint = view._paint_scene

    def record(painter):
        scenes.append(view.view_state())
        paint(painter)

    monkeypatch.setattr(view, "_paint_scene", record)
    return scenes


def test_unchanged_view_reuses_complete_frame_on_consecutive_grabs(view, monkeypatch):
    scenes = _watch_scene(view, monkeypatch)
    first = view.grab().toImage()
    second = view.grab().toImage()
    assert first == second
    assert len(scenes) == 1


def test_setting_changed_cells_refreshes_cached_frame_even_with_same_dictionary(view):
    view.center_lon, view.center_lat = 1, .5
    cell = grids.Square("JJ00")
    cells = {cell.square: cell}
    view.set_cells(cells)
    position = view.to_screen(1, .5).toPoint()
    worked = view.grab().toImage().pixelColor(position)
    cell.confirmed = {"call": "BA1AA"}
    cell.source = "lotw"
    view.set_cells(cells)
    confirmed = view.grab().toImage().pixelColor(position)
    assert confirmed != worked
    assert confirmed.green() > confirmed.red()
    assert worked.red() > worked.green()
    assert confirmed.red() < worked.red()


def _png_bytes(color):
    image = QImage(256, 256, QImage.Format.Format_RGB32)
    image.fill(color)
    buffer = QBuffer()
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(buffer.data())


def test_replacing_a_tile_refreshes_cached_frame_without_changing_cache_size(view, monkeypatch):
    view.center_lon, view.center_lat = 20, 20
    view.set_terminator_enabled(False)  # the colours, not the time of day
    view.provider = maptiles.NASA
    monkeypatch.setattr(view, "_imagery_level", lambda: 3)
    monkeypatch.setattr(view.fetcher, "want", lambda *_args: None)
    position = view.to_screen(17.5, 17.25).toPoint()
    view._store_tile(maptiles.NASA.key, 3, 4, 3, _png_bytes(QColor("red")))
    red = view.grab().toImage().pixelColor(position)
    assert len(view._pix) == 1
    view._store_tile(maptiles.NASA.key, 3, 4, 3, _png_bytes(QColor("blue")))
    blue = view.grab().toImage().pixelColor(position)
    assert len(view._pix) == 1
    assert red.red() > red.blue() + 100
    assert blue.blue() > blue.red() + 100


def test_changing_only_provider_api_key_invalidates_frame_and_retries_tiles(view, monkeypatch):
    wanted = []
    monkeypatch.setattr(view.fetcher, "want", lambda *args: wanted.append(args))
    view.set_provider(maptiles.TIANDITU)
    view.grab()
    assert wanted and all(args[-1] is None for args in wanted)
    wanted.clear()
    view.set_provider(maptiles.TIANDITU, "NEW-TEST-KEY")
    view.grab()
    assert wanted and all(args[-1] == "NEW-TEST-KEY" for args in wanted)


def test_rotation_and_resize_invalidate_cached_frames(view, monkeypatch):
    scenes = _watch_scene(view, monkeypatch)
    view.grab()
    assert len(scenes) == 1
    view._rotate(35, 20)
    view.grab()
    assert len(scenes) == 2
    assert scenes[0] != scenes[1]
    view.resize(view.width() + 30, view.height() + 20)
    resized = view.grab()
    assert len(scenes) == 3
    assert resized.deviceIndependentSize() == view.size()


@pytest.mark.parametrize(("ratio", "frame"), [(1.0, 1.0), (1.5, 1.5), (2.0, 2.0), (2.5, 2.0)])
def test_scaled_display_renders_at_its_own_resolution_up_to_twice(view, monkeypatch, qtbot, ratio, frame):
    monkeypatch.setattr(view, "devicePixelRatioF", lambda: ratio)
    updates = []
    update = view.update

    def record_update():
        updates.append(True)
        update()

    monkeypatch.setattr(view, "update", record_update)
    view.grab()
    assert view._frame.devicePixelRatio() == frame
    assert view._frame.width() == math.ceil(view.width() * frame)
    assert view._raster_scale(frame) == frame
    start = QPoint(view.width() // 2, view.height() // 2)
    end = start + QPoint(60, 25)
    qtbot.mousePress(view, Qt.MouseButton.RightButton, pos=start)
    qtbot.mouseMove(view, pos=end)
    view.grab()
    # Lines and text stay sharp while dragging; imagery and squares are computed at one pixel per point.
    assert view._frame.devicePixelRatio() == frame
    assert view._raster_scale(frame, moving=True) == 1.0
    before_release = len(updates)
    qtbot.mouseRelease(view, Qt.MouseButton.RightButton, pos=end)
    assert len(updates) > before_release
    view.grab()
    assert view._frame.devicePixelRatio() == frame


def test_per_pixel_layers_stay_within_their_pixel_budget_on_a_huge_view(view, monkeypatch):
    monkeypatch.setattr(view, "devicePixelRatioF", lambda: 2.0)
    view.resize(3000, 2000)
    scale = view._raster_scale(view._frame_dpr())
    assert scale < 2.0
    assert (view.width() * scale) * (view.height() * scale) <= gridmap.RASTER_PIXELS * 1.001


def test_squares_are_filled_per_pixel(view):
    view.set_terminator_enabled(False)
    view.zoom, view.center_lon, view.center_lat = 6, 1, .5
    cell = grids.Square("JJ00")
    cell.confirmed, cell.source = {"call": "N0CALL"}, "lotw"
    view.set_cells({cell.square: cell})
    image = view.grab().toImage()
    inside = image.pixelColor(view.to_screen(0.5, 0.75).toPoint())
    outside = image.pixelColor(view.to_screen(-0.5, 0.75).toPoint())
    assert inside.green() > inside.red() + 60 and outside.green() < outside.red() + 60


def test_tiles_dropped_from_memory_load_again_when_the_view_comes_back(view, monkeypatch):
    """Zooming in and out and dragging fills the tile memory; a view seen before used to stay without imagery,
    because its dropped tiles were never asked for again."""
    monkeypatch.setattr(gridmap, "PIX_CACHE", 40)
    view.set_terminator_enabled(False)
    view.set_provider(maptiles.NASA)
    jobs = []
    view.fetcher._threads = [None] * 6  # no workers: the test answers every request itself
    monkeypatch.setattr(view.fetcher._queue, "put", jobs.append)
    tile = _png_bytes(QColor("green"))

    def settle():
        for _ in range(3):
            view.grab()
            while jobs:
                provider, z, x, y, _key = jobs.pop()
                view.fetcher._pending -= 1
                view._store_tile(provider.key, z, x, y, tile)
        image = view.grab().toImage()
        return image.pixelColor(view.width() // 2, view.height() // 2)

    view.center_lon, view.center_lat, view.zoom = 110, 35, 6.5
    assert settle().green() > 100
    for lon in range(-170, 60, 12):  # far away, zoomed in: the first view's tiles are dropped
        view.center_lon, view.center_lat, view.zoom = lon, -20, 7
        settle()
    assert not any(k[1] >= 6 and abs(k[2] / 2 ** k[1] * 360 - 180 - 110) < 5 for k in view._pix)
    view.center_lon, view.center_lat, view.zoom = 110, 35, 6.5
    assert settle().green() > 100
    assert len(view._pix) <= gridmap.PIX_CACHE


def test_coarse_tiles_stay_in_memory_as_stand_ins(view, monkeypatch):
    monkeypatch.setattr(gridmap, "PIX_CACHE", 5)
    released = []
    monkeypatch.setattr(view.fetcher, "release", lambda *key: released.append(key))
    tile = _png_bytes(QColor("blue"))
    for x in range(4):
        view._store_tile(maptiles.NASA.key, 2, x, 1, tile)
    for x in range(6):
        view._store_tile(maptiles.NASA.key, 7, x, 40, tile)
    assert sum(1 for k in view._pix if k[1] == 2) == 4  # every level-2 tile kept
    assert len(view._pix) == 5 and len(released) == 5 and all(k[1] == 7 for k in released)
