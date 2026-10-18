"""Interactive globe labels avoid expensive work until their details are visible."""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath

from nwologger.core import grids
from nwologger.ui.stats.gridmap import GridMapView


@pytest.fixture
def view(qtbot):
    widget = GridMapView()
    widget.resize(900, 640)
    widget.center_lon, widget.center_lat = 1, 0.5
    widget.zoom = 6
    qtbot.addWidget(widget)
    yield widget
    widget.shutdown()


def _draw_labels(view):
    image = QImage(view.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        view._draw_labels(painter, math.radians(2) * view._radius())
    finally:
        painter.end()


def test_grid_and_call_labels_return_after_interactive_rotation(view, monkeypatch, qtbot):
    cell = grids.Square("JJ00")
    cell.first = {"call": "N0CALL"}
    view.set_cells({cell.square: cell})
    drawn = []
    monkeypatch.setattr(view, "_label_text", lambda _painter, _pos, text, *_args: drawn.append(text))

    _draw_labels(view)
    assert drawn == ["JJ00", "N0CALL"]

    drawn.clear()
    view._drag = QPointF(view.width() / 2, view.height() / 2)
    view._moved = False
    _draw_labels(view)
    assert drawn == ["JJ00", "N0CALL"]

    drawn.clear()
    view._moved = True
    _draw_labels(view)
    assert drawn == []

    qtbot.mouseRelease(view, Qt.MouseButton.RightButton, pos=view._drag.toPoint())
    assert view._drag is None
    _draw_labels(view)
    assert drawn == ["JJ00", "N0CALL"]


def test_offscreen_label_rejected_before_constructing_cell_geometry(view, monkeypatch):
    view.zoom = 8
    cell = grids.Square("KJ00")
    cell.first = {"call": "N0FAR"}
    view.set_cells({cell.square: cell})
    latitude, longitude = grids.center(cell.square)
    assert view._camera().project(longitude, latitude)[2] > 0.15
    assert not QRectF(view.rect()).contains(view.to_screen(longitude, latitude))

    def unexpected_geometry(*_args):
        pytest.fail("an offscreen label constructed cell geometry")

    monkeypatch.setattr(view, "_square_path", unexpected_geometry)
    monkeypatch.setattr(view, "_label_text", unexpected_geometry)
    _draw_labels(view)


@pytest.mark.parametrize(("zoom", "fine_grid"), [(4, False), (5, True)])
def test_fine_grid_waits_for_readable_spacing_while_field_lines_remain(view, monkeypatch, zoom, fine_grid):
    view.zoom = zoom
    view.set_terminator_enabled(False)
    paths = []

    def grid_path(longitude_step, latitude_step):
        paths.append((longitude_step, latitude_step))
        return QPainterPath()

    monkeypatch.setattr(view, "_grid_path", grid_path)
    for name in ("_draw_tiles", "_draw_field_watermarks", "_draw_home", "_draw_labels", "_draw_credit"):
        monkeypatch.setattr(view, name, lambda *_args: None)
    image = QImage(view.size(), QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    try:
        view._paint_scene(painter)
    finally:
        painter.end()
    assert (20, 10) in paths
    assert ((2, 1) in paths) is fine_grid


def test_repeated_label_reuses_sprite_and_draws_at_each_requested_position(view):
    font = QFont(view.font())
    font.setPixelSize(18)
    image = QImage(320, 80, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        view._label_text(painter, QPointF(80, 45), "JJ00", font, QColor("white"),
                        Qt.AlignmentFlag.AlignHCenter)
        assert len(view._text_sprites) == 1
        sprite_key = next(iter(view._text_sprites.values()))[0].cacheKey()
        view._label_text(painter, QPointF(240, 45), "JJ00", font, QColor("white"),
                        Qt.AlignmentFlag.AlignHCenter)
        assert len(view._text_sprites) == 1
        assert next(iter(view._text_sprites.values()))[0].cacheKey() == sprite_key
        assert image.copy(QRect(0, 0, 160, 80)) == image.copy(QRect(160, 0, 160, 80))
        assert any(image.pixelColor(x, y).alpha() for x in range(160) for y in range(80))

        view._label_text(painter, QPointF(80, 45), "JJ01", font, QColor("white"))
        assert len(view._text_sprites) == 2
        font.setPixelSize(20)
        view._label_text(painter, QPointF(80, 45), "JJ01", font, QColor("white"))
        assert len(view._text_sprites) == 3
    finally:
        painter.end()
