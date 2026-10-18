"""Calls on the globe: one size for short and long calls, thin type, and all of them appearing together."""

from __future__ import annotations

import math

import numpy as np
import pytest
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
