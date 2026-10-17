"""Field watermarks stay centered on their spherical 100-square groups."""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath

from nwologger.ui.stats import gridmap
from nwologger.ui.stats.gridmap import GridMapView


@pytest.fixture
def view(qtbot):
    widget = GridMapView()
    widget.resize(900, 640)
    qtbot.addWidget(widget)
    yield widget
    widget.shutdown()


@pytest.mark.parametrize("field", ["OM", "AR", "AA"])
def test_field_watermark_centres_on_spherical_field_including_poles(view, field):
    west, south, east, north = view._field_bounds(field)
    view.center_lon, view.center_lat = (west + east) / 2, (south + north) / 2
    view.zoom = 6
    label = view._field_watermark_path(field).boundingRect()
    center = view.to_screen((west + east) / 2, (south + north) / 2)
    group = view._field_rect(field)
    assert label.center().x() == pytest.approx(center.x())
    assert label.center().y() == pytest.approx(center.y())
    assert label.width() <= group.width() * 0.68 + 1e-6
    assert label.height() <= group.height() * 0.56 + 1e-6
    assert label.width() > group.width() * 0.2


def test_field_watermark_rotates_and_scales_with_globe(view):
    view.zoom = 5
    before = view._field_watermark_path("OM").boundingRect()
    view._rotate(20, 10)
    after_pan = view._field_watermark_path("OM").boundingRect()
    center = view.to_screen(110, 35)
    assert after_pan.center().x() == pytest.approx(center.x())
    assert after_pan.center().y() == pytest.approx(center.y())
    assert after_pan.center() != before.center()
    view.zoom += 0.5
    after_zoom = view._field_watermark_path("OM").boundingRect()
    assert after_zoom.width() == pytest.approx(after_pan.width() * math.sqrt(2))
    assert after_zoom.height() == pytest.approx(after_pan.height() * math.sqrt(2))
    assert after_zoom.center().x() == pytest.approx(
        (after_pan.center().x() - view.width() / 2) * math.sqrt(2) + view.width() / 2
    )
    assert after_zoom.center().y() == pytest.approx(
        (after_pan.center().y() - view.height() / 2) * math.sqrt(2) + view.height() / 2
    )


def test_watermark_render_has_8_percent_opacity_without_halo_and_hides_when_small(view):
    view.zoom = 4
    image = QImage(view.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    view._draw_field_watermarks(painter)
    painter.end()
    alphas = {image.pixelColor(x, y).alpha() for y in range(image.height()) for x in range(image.width())}
    assert max(alphas) == round(255 * 0.08)
    assert len(alphas) > 2
    view.zoom = 1
    assert view._field_watermark_path("OM") is None


def test_watermarks_hide_on_the_far_side_and_at_the_limb(view):
    view.zoom = 5
    view.center_lon, view.center_lat = -70, -35
    assert view._field_watermark_path("OM") is None
    view.center_lon, view.center_lat = 20, 0
    assert view._field_watermark_path("OM") is None


def test_watermark_skips_empty_font_glyphs_without_dividing_by_zero(view, monkeypatch):
    class MissingGlyphPath(QPainterPath):
        def addText(self, *_args):  # noqa: N802 - Qt API
            pass

    view.zoom = 5
    monkeypatch.setattr(gridmap, "QPainterPath", MissingGlyphPath)
    assert view._field_watermark_path("OM") is None
