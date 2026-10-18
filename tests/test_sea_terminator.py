"""The day/night line runs over the sea as over land: satellite imagery shows the ocean nearly black, so the night
shade (which only darkens) left no visible line there; dark blue water is lifted toward a sea blue on the day side."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from nwologger.ui.stats import globeraster, gridmap


def _px(r, g, b, a=255):
    return np.array([(a << 24) | (r << 16) | (g << 8) | b], dtype=np.uint32)


def _rgb(packed):
    return (int(packed) >> 16) & 255, (int(packed) >> 8) & 255, int(packed) & 255


def test_dark_water_is_lifted_but_land_clouds_and_desert_are_not():
    ocean = _rgb(globeraster.lift_water(_px(12, 25, 50))[0])
    assert ocean[2] > 50 and ocean[1] > 25 and ocean[2] > ocean[1] > ocean[0]  # lighter, still blue
    shallow = _rgb(globeraster.lift_water(_px(8, 16, 20))[0])  # near-black coastal water, barely blue
    assert all(abs(s - o) <= 20 for s, o in zip(shallow, ocean, strict=True))  # lifted like the open sea
    coast = _rgb(globeraster.lift_water(_px(30, 40, 40))[0])  # grey: land and water in one pixel
    assert all(abs(c - o) <= 20 for c, o in zip(coast, ocean, strict=True))  # no dark rim along the coasts
    murky = _rgb(globeraster.lift_water(_px(50, 62, 64))[0])  # sediment: towards blue, a little less far
    assert murky[2] > 64 and murky[2] - 64 < ocean[2] - 50
    for r, g, b in ((34, 90, 40), (240, 240, 240), (120, 90, 60), (0, 0, 255), (95, 100, 105), (50, 45, 30)):
        assert _rgb(globeraster.lift_water(_px(r, g, b))[0]) == (r, g, b)  # forest, cloud, desert, drawing, haze, soil
    assert globeraster.lift_water(_px(0, 0, 0, 0))[0] == 0  # nothing there
    many = np.array([_px(12, 25, 50)[0], _px(240, 240, 240)[0]] * 50, dtype=np.uint32)
    out = globeraster.lift_water(many)
    assert out[1] == many[1] and out[0] != many[0] and out.dtype == np.uint32


def test_the_night_side_is_darker_than_the_day_side_over_the_sea(qtbot, monkeypatch):
    monkeypatch.setattr(gridmap, "_utc_now", lambda: datetime(2026, 3, 20, 12, 0, tzinfo=UTC))  # the sun at lon 0
    view = gridmap.GridMapView()
    view.resize(700, 700)
    view.set_provider(None)  # the bare globe: all sea
    view.set_terminator_enabled(True)
    view.set_view_state((2.0, 90.0, 0.0))  # centred on lon 90: the terminator runs down the middle
    view._update_sun(force=True)
    qtbot.addWidget(view)
    image = view.grab().toImage()
    cx, cy, r = view.width() // 2, view.height() // 2, view._radius()
    y = cy - int(r * 0.3)  # between the grid lines (the equator is one), as are the longitudes sampled
    day = image.pixelColor(cx - int(r * 0.4), y)  # about lon 66, towards the sun
    night = image.pixelColor(cx + int(r * 0.4), y)  # about lon 114, away from it
    assert day.lightness() - night.lightness() > 15, (day.getRgb(), night.getRgb())
    view.shutdown()
