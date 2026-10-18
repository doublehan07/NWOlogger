"""Calls on the globe: one size for short and long calls, thin type, and all of them appearing together."""

from __future__ import annotations

import math

import numpy as np
import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont, QImage, QPainter

from nwologger.core import grids
from nwologger.ui.stats import gridmap
from nwologger.ui.stats.gridmap import GridMapView

# Squares side by side on the equator; JJ00 and JJ20 are mirror images about the view's centre (3° E).
CALLS = {"JJ00": "BA1AB", "JJ10": "BA8TSK/9", "JJ20": "BG5CZD"}


@pytest.fixture
def view(qtbot):
    widget = GridMapView()
    widget.resize(900, 640)
    widget.center_lon, widget.center_lat = 3, 0.5
    widget.zoom = 6.5
    widget.set_terminator_enabled(False)
    qtbot.addWidget(widget)
    yield widget
    widget.shutdown()


def _cells(calls: dict[str, str]) -> dict[str, grids.Square]:
    cells = {}
    for sq, call in calls.items():
        cell = grids.Square(sq)
        cell.confirmed, cell.source = {"call": call}, "lotw"
        cells[sq] = cell
    return cells


def _labels(view, monkeypatch) -> dict[str, QFont]:
    drawn = {}
    monkeypatch.setattr(view, "_label_text",
                        lambda _painter, _pos, text, font, *_args: drawn.__setitem__(text, QFont(font)))
    image = QImage(view.size(), QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    try:
        view._draw_labels(painter)
    finally:
        painter.end()
    return drawn


def test_a_five_letter_call_is_as_large_as_a_six_letter_one(view, monkeypatch):
    view.set_cells(_cells(CALLS))
    fonts = _labels(view, monkeypatch)
    assert set(CALLS.values()) <= set(fonts)
    sizes = {call: fonts[call].pointSizeF() for call in CALLS.values()}
    assert sizes["BA1AB"] == sizes["BG5CZD"]
    assert abs(sizes["BA8TSK/9"] - sizes["BA1AB"]) <= 0.25  # the middle square is a hair wider


def test_calls_are_thin_and_fit_their_square(view, monkeypatch):
    view.set_cells(_cells(CALLS))
    fonts = _labels(view, monkeypatch)
    for call in CALLS.values():
        font = fonts[call]
        assert not font.bold() and font.weight() == QFont.Weight.Normal
        assert font.pointSizeF() <= gridmap.CALL_MAX_PT
    *_rest, width, _height = view._projected_extents(np.radians([3.0]), np.radians([0.5]), math.radians(1),
                                                     math.radians(.5))
    px = fonts["BA8TSK/9"].pointSizeF() * view.logicalDpiY() / 72
    assert view._call_advance("BA8TSK/9", False) * px <= gridmap.CALL_WIDTH * float(width[0])


def test_a_call_longer_than_eight_characters_is_made_smaller_to_fit(view, monkeypatch):
    view.set_cells(_cells({"JJ00": "BA1AB", "JJ20": "BG5CZD/QRPP"}))
    view.zoom = 5.4  # the squares, not the full size, set the size
    fonts = _labels(view, monkeypatch)
    assert fonts["BA1AB"].pointSizeF() < gridmap.CALL_MAX_PT
    assert fonts["BG5CZD/QRPP"].pointSizeF() < fonts["BA1AB"].pointSizeF()


def test_every_call_appears_at_the_same_zoom(view, monkeypatch):
    view.set_cells(_cells(CALLS))
    seen = set()
    for step in range(60):
        view.zoom = 4.0 + step * 0.05
        view._geometry_key = None
        drawn = set(_labels(view, monkeypatch)) & set(CALLS.values())
        assert drawn in (set(), set(CALLS.values())), view.zoom
        seen.add(bool(drawn))
    assert seen == {False, True}


def test_once_calls_appear_every_square_in_view_has_one_far_from_the_centre_too(view, monkeypatch):
    # A view centred at 35° N, squares from 20° N to 52° N: the northern ones are narrower on the globe.
    view.resize(900, 900)
    view.center_lon, view.center_lat = 116, 35
    squares = {"OL60": "BA1AB", "OM65": "BG5CZD", "ON70": "BH4TSC", "ON72": "BA8TSK/9", "OO72": "BD7TSD"}
    view.set_cells(_cells(squares))
    for step in range(80):
        view.zoom = 4.0 + step * 0.05
        view._geometry_key = None
        fonts = _labels(view, monkeypatch)
        drawn = set(fonts) & set(squares.values())
        assert drawn in (set(), set(squares.values())), (view.zoom, drawn)
        assert all(QRectF(view.rect()).contains(view.to_screen(*grids.center(sq)[::-1])) for sq in squares)
        if drawn:
            north, south = fonts["BD7TSD"].pointSizeF(), fonts["BA1AB"].pointSizeF()
            assert north < south  # each at its own square's size
            break
    else:
        pytest.fail("no calls at any zoom")
    names = set(fonts) & set(squares)
    assert names in (set(), set(squares))  # names, too, on every square or none


def test_slivers_at_the_limb_stay_blank(view, monkeypatch):
    view.resize(2400, 2400)  # the whole disk in view at a zoom where the centre has labels
    view.center_lon, view.center_lat = 1, 0.5
    view.zoom = 4.7
    assert view._centre_call_px() >= gridmap.CALL_MIN_PX and view._radius() * 2 < 2400
    # At 0°, 40° and 74° to the east: the last is still on the front of the globe, but its text would be tiny.
    view.set_cells(_cells({"JJ00": "BA1AB", "LJ00": "BG6TSX", "MJ70": "BG5CZD"}))
    lat, lon = grids.center("MJ70")
    assert view._camera().project(lon, lat)[2] >= 0.15  # not left out for facing away
    for sq in ("LJ00", "MJ70"):
        lat, lon = grids.center(sq)
        assert QRectF(view.rect()).contains(view.to_screen(lon, lat))  # on screen, not just beyond the view
    fonts = _labels(view, monkeypatch)
    assert "BA1AB" in fonts and "BG6TSX" in fonts  # well inside the disk: labelled, smaller
    assert fonts["BG6TSX"].pointSizeF() < fonts["BA1AB"].pointSizeF()
    assert "BG5CZD" not in fonts  # a sliver at the limb: blank
