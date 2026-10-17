"""A rotatable globe with satellite imagery and Maidenhead grid squares.

Imagery, cells and grid lines share an orthographic projection. Only the visible hemisphere is drawn; the same
geometry supplies tooltips, square selection and PNG exports.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from datetime import UTC, datetime

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
    QTransform,
)
from PySide6.QtWidgets import QToolTip, QWidget

from ... import i18n
from ...core import geo, globe, grids, maptiles, solar
from ...core.grids import SOURCE_NAMES
from .. import theme
from .globegeometry import CellGeometry
from .globeraster import GlobeRaster

tr = i18n.tr
BG = QColor("#080f1a")
FILL = {"confirmed": QColor(34, 163, 90, 168), "worked": QColor(242, 184, 75, 168)}
LINE = QColor(255, 255, 255, 34)
FIELD_LINE = QColor(255, 255, 255, 120)
FIELD_WATERMARK = QColor(255, 255, 255, 20)  # 8% opacity
FIELD_LABEL_PX = 64.0
MIN_ZOOM, MAX_ZOOM = 1.0, 11.0
GRID_LABEL_PX, CALL_PX, FINE_GRID_PX = 34.0, 50.0, 28.0
PIX_CACHE = 600
TEXT_CACHE = 2048
NIGHT_SHADE = QColor(3, 9, 22, 130)
TERMINATOR_LINE = QColor(255, 215, 128, 220)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _samples(start: float, end: float, step: float) -> list[float]:
    count = max(1, math.ceil(abs(end - start) / step))
    low, high = sorted((start, end))
    return [start, *(max(low, min(high, start + (end - start) * (i / count)))
                     for i in range(1, count)), end]


class GridMapView(QWidget):
    square_clicked = Signal(str)
    _tile_arrived = Signal(str, int, int, int, bytes)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(360)
        self.zoom = 3.0
        self.center_lon, self.center_lat = 110.0, 35.0
        self.cells: dict[str, grids.Square] = {}
        self.home: str | None = None
        self.radius_km: float | None = None
        self.highlight: frozenset[str] = frozenset()
        self.provider: maptiles.Provider | None = None
        self.api_key: str | None = None
        self._pix: OrderedDict[tuple, QPixmap] = OrderedDict()
        self.fetcher = maptiles.Fetcher(lambda *a: self._emit_tile(*a))
        self._tile_arrived.connect(self._store_tile, Qt.ConnectionType.QueuedConnection)
        self._drag: QPointF | None = None
        self._moved = False
        self._hover: str | None = None
        self._mono = theme.mono_font()
        self._geometry_key = None
        self._geometry: dict[tuple, object] = {}
        self._camera_key = None
        self._camera_value = None
        self._tile_revision = 0
        self._cell_revision = 0
        self._frame_key = None
        self._frame = None
        self._raster = GlobeRaster()
        self._cell_geometry = CellGeometry()
        self._atlas_key = None
        self._atlas = None
        self._field_glyphs: dict[str, tuple[QPainterPath, QRectF]] = {}
        self._field_glyph_font = None
        self._text_sprites: OrderedDict[tuple[str, str, int], tuple[QPixmap, QPointF, float]] = OrderedDict()
        self.terminator_enabled = True
        self._solar_time: datetime | None = None
        self._sun_position = (0.0, 0.0)
        self._solar_stopped = False
        self._solar_timer = QTimer(self)
        self._solar_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._solar_timer.setInterval(60_000)
        self._solar_timer.timeout.connect(self._refresh_sun)

    def set_cells(self, cells: dict[str, grids.Square]) -> None:
        self.cells = cells
        self._cell_revision += 1
        self.update()

    def set_provider(self, provider: maptiles.Provider | None, api_key: str | None = None) -> None:
        if provider != self.provider or api_key != self.api_key:
            self.fetcher.log_summary(self.provider)
            self.fetcher.forget()
            self._pix.clear()
            self._frame_key = None
            self._tile_revision += 1
        self.provider, self.api_key = provider, api_key
        self.update()

    def set_terminator_enabled(self, enabled: bool) -> None:
        self.terminator_enabled = bool(enabled)
        if self.terminator_enabled:
            self._update_sun(force=True)
        self._frame_key = None
        self._sync_solar_timer()
        self.update()

    def _update_sun(self, *, force: bool = False) -> bool:
        if not self.terminator_enabled:
            return False
        now = _utc_now()
        if (force or self._solar_time is None or now < self._solar_time or
                (now - self._solar_time).total_seconds() >= 60):
            self._sun_position = solar.subsolar_point(now)
            self._solar_time = now
            return True
        return False

    def _sync_solar_timer(self) -> None:
        if self.terminator_enabled and self.isVisible() and not self._solar_stopped:
            self._solar_timer.start()
        else:
            self._solar_timer.stop()

    def _refresh_sun(self) -> None:
        if self.terminator_enabled and not self._solar_stopped:
            self._update_sun(force=True)
            self.update()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self._refresh_sun()
        self._sync_solar_timer()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._solar_timer.stop()
        super().hideEvent(event)

    def fit(self, box: tuple[float, float, float, float]) -> None:
        """Rotate to a region and fit it; World shows the whole globe at the current orientation."""
        west, south, east, north = box
        if not all(math.isfinite(v) for v in box):
            return
        span = east - west
        if abs(span) >= 359:
            radius = min(self.width(), self.height()) * 0.46
        else:
            if span < 0:
                span %= 360
            east = west + span
            self.center_lon = globe.normalize_lon((west + east) / 2)
            self.center_lat = max(-90.0, min(90.0, (south + north) / 2))
            # A home region near a pole can extend past the geographic domain.
            south, north = (max(-90.0, min(90.0, value)) for value in (south, north))
            cam = self._camera()
            points = [cam.project(lon, lat) for lon in _samples(west, east, 2) for lat in (south, north)]
            points += [cam.project(lon, lat) for lat in _samples(south, north, 2) for lon in (west, east)]
            if any(z < 0 for _x, _y, z in points):
                radius = min(self.width(), self.height()) * 0.46
            else:
                extent_x = max(abs(x) for x, _y, _z in points)
                extent_y = max(abs(y) for _x, y, _z in points)
                radius = min(self.width() * 0.45 / max(0.01, extent_x),
                             self.height() * 0.45 / max(0.01, extent_y))
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, math.log2(max(1, radius) * math.tau / 256)))
        self.update()

    def view_state(self) -> tuple[float, float, float]:
        return self.zoom, self.center_lon, self.center_lat

    def set_view_state(self, state) -> bool:
        try:
            zoom, lon, lat = (float(v) for v in state)
        except (TypeError, ValueError, OverflowError):
            return False
        if not all(math.isfinite(v) for v in (zoom, lon, lat)) or not -90 <= lat <= 90:
            return False
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        self.center_lon, self.center_lat = globe.normalize_lon(lon), lat
        self.update()
        return True

    def shutdown(self) -> None:
        self._solar_stopped = True
        self._solar_timer.stop()
        self.fetcher.log_summary(self.provider)
        self.fetcher.shutdown()

    # ------------------------------------------------------------------ coordinates and geometry
    def _scale(self) -> float:
        return 2.0 ** self.zoom

    def _radius(self) -> float:
        return 256.0 * self._scale() / math.tau

    def _camera(self) -> globe.Orthographic:
        key = self.center_lon, self.center_lat
        if self._camera_key != key:
            self._camera_value = globe.Orthographic(*key)
            self._camera_key = key
        return self._camera_value

    def _prepare_geometry(self) -> None:
        key = (self.view_state(), self.width(), self.height(), self.font().toString())
        if key != self._geometry_key:
            self._geometry.clear()
            self._geometry_key = key

    def _screen(self, x: float, y: float) -> QPointF:
        radius = self._radius()
        return QPointF(self.width() / 2 + radius * x, self.height() / 2 - radius * y)

    def to_screen(self, lon: float, lat: float) -> QPointF:
        x, y, _depth = self._camera().project(lon, lat)
        return self._screen(x, y)

    def to_lonlat(self, pos: QPointF) -> tuple[float, float] | None:
        radius = self._radius()
        return self._camera().unproject((pos.x() - self.width() / 2) / radius,
                                        (self.height() / 2 - pos.y()) / radius)

    def square_at(self, pos: QPointF) -> str | None:
        ll = self.to_lonlat(pos)
        if ll is None:
            return None
        lon, lat = ll
        lat = max(-89.999999999, min(89.999999999, lat))
        return next(grids.squares_in(lon, lat, lon, lat), None)

    def _visible_box(self) -> tuple[float, float, float, float]:
        # A spherical cap encloses the viewport, including any visible part of the globe's limb.
        angle = math.asin(min(1.0, math.hypot(self.width() / 2, self.height() / 2) / self._radius()))
        degrees = math.degrees(angle)
        south, north = max(-90.0, self.center_lat - degrees), min(90.0, self.center_lat + degrees)
        if degrees + abs(self.center_lat) >= 90 - 1e-9:
            return self.center_lon - 180, south, self.center_lon + 180, north
        delta = math.degrees(math.asin(min(1.0, math.sin(angle) / math.cos(math.radians(self.center_lat)))))
        return self.center_lon - delta, south, self.center_lon + delta, north

    def _geo_polygon(self, west: float, south: float, east: float, north: float) -> QPainterPath:
        if not self._overlaps_view(west, south, east, north):
            return QPainterPath()
        step = min(2.0, max(0.1, math.degrees(20 / self._radius())))
        coords = ([(lon, north) for lon in _samples(west, east, step)] +
                  [(east, lat) for lat in _samples(north, south, step)[1:]] +
                  [(lon, south) for lon in _samples(east, west, step)[1:]] +
                  [(west, lat) for lat in _samples(south, north, step)[1:]])
        vertices = [(*self._camera().project(lon, lat), 0.0, 0.0) for lon, lat in coords]
        path = QPainterPath()
        clipped = globe.clip_polygon(vertices)
        if len(clipped) >= 3:
            path.addPolygon(QPolygonF([self._screen(x, y) for x, y, _z, _u, _v in clipped]))
            path.closeSubpath()
        return path

    def _overlaps_view(self, west: float, south: float, east: float, north: float) -> bool:
        left, bottom, right, top = self._visible_box()
        middle = self.center_lon + globe.normalize_lon((west + east) / 2 - self.center_lon)
        half_width = (east - west) / 2
        return north >= bottom and south <= top and middle + half_width >= left and middle - half_width <= right

    def _square_path(self, sq: str) -> QPainterPath:
        self._prepare_geometry()
        key = "square", sq
        if key not in self._geometry:
            self._geometry[key] = self._geo_polygon(*grids.bounds(sq))
        return self._geometry[key]

    def _prepare_cell_paths(self) -> None:
        self._prepare_geometry()
        key = "cells", self._cell_revision, id(self.cells), self.highlight, self.home
        if key in self._geometry:
            return
        squares = dict.fromkeys((*self.cells, *self.highlight))
        if home := grids.square(self.home):
            squares[home] = None
        paths = self._cell_geometry.paths(squares, self._camera(), self._radius(), self.width(), self.height())
        self._geometry.update((("square", sq), path) for sq, path in paths.items())
        self._geometry[key] = True

    def _rect(self, sq: str) -> QRectF:
        return self._square_path(sq).boundingRect()

    @staticmethod
    def _field_bounds(field: str) -> tuple[float, float, float, float]:
        west = (ord(field[0]) - 65) * 20 - 180
        south = (ord(field[1]) - 65) * 10 - 90
        return west, south, west + 20, south + 10

    def _field_rect(self, field: str) -> QRectF:
        self._prepare_geometry()
        key = "field", field
        if key not in self._geometry:
            self._geometry[key] = self._geo_polygon(*self._field_bounds(field)).boundingRect()
        return self._geometry[key]

    def _line_path(self, coords) -> QPainterPath:
        path = QPainterPath()
        previous = None
        camera = self._camera()
        sin_center, cos_center = camera._sin_lat, camera._cos_lat
        radius = self._radius()
        center_x, center_y = self.width() / 2, self.height() / 2
        longitude = math.radians(camera.center_lon)
        for lon, lat in coords:
            # Grid and great-circle samples already have valid geographic coordinates.
            # Keep the camera and screen scale local rather than rebuilding Qt points for every sample.
            delta, latitude = math.radians(lon) - longitude, math.radians(lat)
            sin_lat, cos_lat = math.sin(latitude), math.cos(latitude)
            horizontal = cos_lat * math.cos(delta)
            point = (cos_lat * math.sin(delta),
                     cos_center * sin_lat - sin_center * horizontal,
                     sin_center * sin_lat + cos_center * horizontal)
            if previous is not None and (point[2] >= 0) != (previous[2] >= 0):
                t = previous[2] / (previous[2] - point[2])
                x = previous[0] + t * (point[0] - previous[0])
                y = previous[1] + t * (point[1] - previous[1])
                length = math.hypot(x, y)
                screen_x, screen_y = center_x + radius * x / length, center_y - radius * y / length
                if previous[2] >= 0:
                    path.lineTo(screen_x, screen_y)
                else:
                    path.moveTo(screen_x, screen_y)
            if point[2] >= 0:
                screen_x, screen_y = center_x + radius * point[0], center_y - radius * point[1]
                if previous is None:
                    path.moveTo(screen_x, screen_y)
                else:
                    path.lineTo(screen_x, screen_y)
            previous = point
        return path

    def _grid_path(self, lon_step: int, lat_step: int) -> QPainterPath:
        self._prepare_geometry()
        key = "grid", lon_step, lat_step
        if key in self._geometry:
            return self._geometry[key]
        west, south, east, north = self._visible_box()
        # A chord error below one logical pixel keeps curves smooth without oversampling them.
        step = min(10.0, math.degrees(math.sqrt(8 / self._radius())))
        path = QPainterPath()
        meridians = {globe.normalize_lon(lon) for lon in range(math.floor(west / lon_step) * lon_step,
                                                             math.ceil(east / lon_step) * lon_step + 1, lon_step)}
        for lon in meridians:
            path.addPath(self._line_path((lon, lat) for lat in _samples(south, north, step)))
        for lat in range(max(-90, math.ceil(south / lat_step) * lat_step),
                         min(90, math.floor(north / lat_step) * lat_step) + 1, lat_step):
            path.addPath(self._line_path((lon, lat) for lon in _samples(west, east, step)))
        self._geometry[key] = path
        return path

    # ------------------------------------------------------------------ warped imagery tiles
    def _emit_tile(self, provider: str, z: int, x: int, y: int, data: bytes) -> None:
        try:
            self._tile_arrived.emit(provider, z, x, y, data)
        except RuntimeError:
            pass  # the widget has been deleted

    def _store_tile(self, provider: str, z: int, x: int, y: int, data: bytes) -> None:
        img = QImage.fromData(data)
        if img.isNull():
            return
        self._pix[(provider, z, x, y)] = QPixmap.fromImage(img)
        self._tile_revision += 1
        while len(self._pix) > PIX_CACHE:
            self._pix.popitem(last=False)
        self.update()

    def _tile(self, z: int, x: int, y: int) -> QPixmap | None:
        key = self.provider.key, z, x, y
        pix = self._pix.get(key)
        if pix is not None:
            self._pix.move_to_end(key)
        return pix

    def _tile_indices(self, z: int):
        west, south, east, north = self._visible_box()
        south, north = max(-grids.MAX_LAT, south), min(grids.MAX_LAT, north)
        if south > north:
            return
        n = 2 ** z
        x0 = math.floor((west + 180) / 360 * n)
        x1 = math.floor((east + 180) / 360 * n)
        _, y0 = grids.to_world(0, north, z)
        _, y1 = grids.to_world(0, south, z)
        xs = dict.fromkeys(x % n for x in range(x0, min(x1 + 1, x0 + n)))
        for x in xs:
            for y in range(max(0, math.floor(y0 / 256)), min(n - 1, math.floor(y1 / 256)) + 1):
                yield x, y

    def _imagery_level(self) -> int:
        level = self.zoom - 1 + math.log2(max(0.12, math.cos(math.radians(self.center_lat))))
        return max(self.provider.min_zoom, min(self.provider.max_zoom, 5, round(level)))

    def _tile_source(self, z: int, tx: int, ty: int):
        pix = self._tile(z, tx, ty)
        if pix is not None:
            return pix, QRectF(0, 0, pix.width(), pix.height())
        self.fetcher.want(self.provider, z, tx, ty, self.api_key)
        for parent_z in range(z - 1, self.provider.min_zoom - 1, -1):
            delta = z - parent_z
            parent = self._tile(parent_z, tx >> delta, ty >> delta)
            if parent is not None:
                count = 2 ** delta
                width, height = parent.width() / count, parent.height() / count
                return parent, QRectF((tx % count) * width, (ty % count) * height, width, height)
        return None, QRectF()

    def _draw_tiles(self, painter: QPainter) -> None:
        if self.provider is None:
            return
        z = self._imagery_level()
        indices = tuple(self._tile_indices(z))
        if not indices:
            return
        xs = tuple(dict.fromkeys(tx for tx, _ty in indices))
        top, bottom = min(ty for _tx, ty in indices), max(ty for _tx, ty in indices)
        key = self.provider, z, indices, self._tile_revision, len(self._pix)
        if self._atlas_key != key:
            # Only the visible tile rectangle is assembled; even deep zoom uses a small atlas.
            atlas = QImage(len(xs) * 256, (bottom - top + 1) * 256, QImage.Format.Format_ARGB32)
            atlas.fill(Qt.GlobalColor.transparent)
            assembler = QPainter(atlas)
            available = False
            columns = {tx: column for column, tx in enumerate(xs)}
            try:
                for tx, ty in indices:
                    pix, source = self._tile_source(z, tx, ty)
                    if pix is not None:
                        assembler.drawPixmap(QRectF(columns[tx] * 256, (ty - top) * 256, 256, 256), pix, source)
                        available = True
            finally:
                assembler.end()
            self._atlas = atlas if available else QImage()
            self._atlas_key = key
        if self._atlas.isNull():
            return
        moving = self._drag is not None and self._moved
        image = self._raster.image(self._atlas, self.size(), self._radius(), self._camera(),
                                   level=z, tile_left=xs[0], tile_top=top,
                                   max_pixels=256 if moving else 320)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(QRectF(self.rect()), image)

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        self._update_sun()
        self._prepare_geometry()
        moving = self._drag is not None and self._moved
        # Bound globe raster work on scaled displays; the surrounding controls retain native resolution.
        dpr = min(1.0, self.devicePixelRatioF())
        key = (self._geometry_key, self._tile_revision, len(self._pix), self._cell_revision, id(self.cells),
               self.home, self.radius_km, self.highlight, self.provider, tuple(self.fetcher.stopped.items()),
               i18n.lang(), dpr, moving, self.terminator_enabled,
               self._solar_time if self.terminator_enabled else None)
        if self._frame_key != key:
            frame = QPixmap(math.ceil(self.width() * dpr), math.ceil(self.height() * dpr))
            frame.setDevicePixelRatio(dpr)
            renderer = QPainter(frame)
            try:
                self._paint_scene(renderer)
            finally:
                renderer.end()
            self._frame, self._frame_key = frame, key
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawPixmap(QPointF(), self._frame)
        finally:
            painter.end()

    def _paint_scene(self, painter: QPainter) -> None:
        painter.fillRect(self.rect(), BG)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        radius = self._radius()
        self._prepare_cell_paths()
        middle = QPointF(self.width() / 2, self.height() / 2)
        sphere = QPainterPath()
        sphere.addEllipse(middle, radius, radius)
        base = QRadialGradient(middle - QPointF(radius * 0.25, radius * 0.25), radius * 1.5)
        base.setColorAt(0, QColor("#25465c"))
        base.setColorAt(1, QColor("#0b1e30"))
        painter.fillPath(sphere, base)
        painter.save()
        try:
            painter.setClipPath(sphere)
            self._draw_tiles(painter)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            night, terminator = self._terminator_paths() if self.terminator_enabled else (QPainterPath(), QPainterPath())
            painter.fillPath(night, NIGHT_SHADE)
            fills = {status: QPainterPath() for status in FILL}
            viewport = QRectF(self.rect())
            for path in fills.values():
                path.setFillRule(Qt.FillRule.WindingFill)
            for sq, cell in self.cells.items():
                path = self._geometry["square", sq]
                if path.boundingRect().intersects(viewport):
                    fills[cell.status].addPath(path)
            for status, path in fills.items():
                painter.fillPath(path, FILL[status])
            self._draw_field_watermarks(painter)
            cell_px = math.radians(2) * radius
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if cell_px >= FINE_GRID_PX:
                painter.setPen(QPen(LINE, 1))
                painter.drawPath(self._grid_path(2, 1))
            painter.setPen(QPen(FIELD_LINE, 1))
            painter.drawPath(self._grid_path(20, 10))
            if self.highlight and cell_px >= 3:
                painter.setPen(QPen(QColor(255, 255, 255, 150), 1, Qt.PenStyle.DotLine))
                for sq in self.highlight:
                    painter.drawPath(self._geometry["square", sq])
            if not terminator.isEmpty():
                painter.setPen(QPen(TERMINATOR_LINE, 1.4))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(terminator)
            self._draw_home(painter)
            self._draw_labels(painter, cell_px)
            shade = QRadialGradient(middle, radius)
            shade.setColorAt(0, QColor(0, 0, 0, 0))
            shade.setColorAt(0.72, QColor(0, 0, 0, 0))
            shade.setColorAt(1, QColor(0, 0, 0, 75))
            painter.fillPath(sphere, shade)
        finally:
            painter.restore()
        painter.setPen(QPen(QColor(150, 195, 225, 120), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(sphere)
        self._draw_credit(painter)

    def _terminator_paths(self) -> tuple[QPainterPath, QPainterPath]:
        """Project the solar great circle and close the night side along the globe's limb."""
        sun_x, sun_y, sun_z = self._camera().project(*self._sun_position)
        horizontal = math.hypot(sun_x, sun_y)
        night, boundary = QPainterPath(), QPainterPath()
        if horizontal < 1e-10:
            if sun_z < 0:
                night.addEllipse(QPointF(self.width() / 2, self.height() / 2), self._radius(), self._radius())
            return night, boundary
        # T lies on both horizons. V points from it along the visible half of the solar great circle.
        tangent_x, tangent_y = -sun_y / horizontal, sun_x / horizontal
        visible_x, visible_y = -sun_z * sun_x / horizontal, -sun_z * sun_y / horizontal
        radius, cx, cy = self._radius(), self.width() / 2, self.height() / 2
        step = min(math.pi / 32, math.sqrt(2 / radius))
        points = [QPointF(cx + radius * (tangent_x * math.cos(angle) + visible_x * math.sin(angle)),
                         cy - radius * (tangent_y * math.cos(angle) + visible_y * math.sin(angle)))
                  for angle in _samples(0, math.pi, step)]
        boundary.moveTo(points[0])
        for point in points[1:]:
            boundary.lineTo(point)
        angle = math.atan2(sun_y, sun_x)
        limb = [QPointF(cx + radius * math.cos(a), cy - radius * math.sin(a))
                for a in _samples(angle + 1.5 * math.pi, angle + 0.5 * math.pi, step)]
        night.addPolygon(QPolygonF(points + limb[1:]))
        night.closeSubpath()
        return night, boundary

    def _draw_home(self, painter: QPainter) -> None:
        sq = grids.square(self.home)
        if not sq:
            return
        painter.setPen(QPen(QColor("white"), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._square_path(sq))
        if self.radius_km:
            lat, lon = geo.grid_to_latlon(self.home) or grids.center(sq)
            ring = [globe.destination(lon, lat, bearing, self.radius_km) for bearing in range(0, 361, 3)]
            painter.setPen(QPen(QColor(255, 255, 255, 220), 1.3, Qt.PenStyle.DashLine))
            painter.drawPath(self._line_path(ring))

    def _halo_text(self, painter: QPainter, pos: QPointF, text: str, font: QFont, color: QColor,
                   align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft) -> None:
        key = font.toString(), text, color.rgba()
        cached = self._text_sprites.get(key)
        if cached is None:
            metrics = QFontMetricsF(font)
            bounds = metrics.tightBoundingRect(text)
            if bounds.isEmpty():
                return
            offset = bounds.topLeft() - QPointF(2, 2)
            sprite = QPixmap(math.ceil(bounds.width() + 4), math.ceil(bounds.height() + 4))
            sprite.fill(Qt.GlobalColor.transparent)
            renderer = QPainter(sprite)
            try:
                renderer.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
                renderer.translate(-offset)
                renderer.setFont(font)
                # Native glyphs and one dark shadow keep new labels cheap to build as the globe rotates.
                renderer.setPen(QColor(0, 0, 0, 170))
                renderer.drawText(QPointF(1, 1), text)
                renderer.setPen(color)
                renderer.drawText(QPointF(), text)
            finally:
                renderer.end()
            cached = sprite, offset, metrics.horizontalAdvance(text)
            self._text_sprites[key] = cached
            while len(self._text_sprites) > TEXT_CACHE:
                self._text_sprites.popitem(last=False)
        else:
            self._text_sprites.move_to_end(key)
        sprite, offset, width = cached
        x = pos.x() - width / 2 if align == Qt.AlignmentFlag.AlignHCenter else pos.x()
        painter.drawPixmap(QPointF(x, pos.y()) + offset, sprite)

    def _field_watermark_path(self, field: str) -> QPainterPath | None:
        west, south, east, north = self._field_bounds(field)
        longitude, latitude = (west + east) / 2, (south + north) / 2
        camera = self._camera()
        x, y, depth = camera.project(longitude, latitude)
        if depth < 0.15:
            return None
        # Mid-edge extents size the glyph without constructing and clipping a complete field polygon.
        delta, latitude = math.radians(longitude - camera.center_lon), math.radians(latitude)
        cos_lat, sin_lat, cos_delta = math.cos(latitude), math.sin(latitude), math.cos(delta)
        radius = self._radius()
        width = abs(2 * radius * math.sin(math.radians(10)) * cos_lat * cos_delta)
        height = abs(2 * radius * math.sin(math.radians(5)) *
                     (camera._cos_lat * cos_lat + camera._sin_lat * sin_lat * cos_delta))
        if width < FIELD_LABEL_PX or height < FIELD_LABEL_PX / 2:
            return None
        font_key = self.font().toString()
        if self._field_glyph_font != font_key:
            self._field_glyphs.clear()
            self._field_glyph_font = font_key
        if field not in self._field_glyphs:
            font = QFont(self.font())
            font.setBold(True)
            font.setPixelSize(100)
            path = QPainterPath()
            path.addText(QPointF(), font, field)
            self._field_glyphs[field] = path, path.boundingRect()
        path, bounds = self._field_glyphs[field]
        if bounds.isEmpty():
            return None
        scale = min(width * 0.68 / bounds.width(), height * 0.56 / bounds.height())
        transform = QTransform()
        center = self._screen(x, y)
        transform.translate(center.x(), center.y())
        transform.scale(scale, scale)
        transform.translate(-bounds.center().x(), -bounds.center().y())
        return transform.map(path)

    def _draw_field_watermarks(self, painter: QPainter) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(FIELD_WATERMARK)
        for x in range(18):
            for y in range(18):
                path = self._field_watermark_path(chr(65 + x) + chr(65 + y))
                if path is not None and path.boundingRect().intersects(QRectF(self.rect())):
                    painter.drawPath(path)
        painter.restore()

    def _draw_labels(self, painter: QPainter, cell_px: float) -> None:
        if cell_px < GRID_LABEL_PX or (self._drag is not None and self._moved):
            return
        camera = self._camera()
        viewport = QRectF(self.rect())
        for sq, cell in self.cells.items():
            lat, lon = grids.center(sq)
            x, y, depth = camera.project(lon, lat)
            if depth < 0.15:
                continue
            center = self._screen(x, y)
            if not viewport.contains(center):
                continue
            path = self._geometry.get(("square", sq))
            rect = path.boundingRect() if path is not None else self._rect(sq)
            if rect.width() < GRID_LABEL_PX or rect.height() < 16:
                continue
            small = QFont(self._mono)
            small.setPointSizeF(max(7.0, min(11.0, math.floor(rect.width() / 7 * 2) / 2)))
            self._halo_text(painter, QPointF(center.x(), center.y() - 3), sq, small,
                            QColor(255, 255, 255, 235), Qt.AlignmentFlag.AlignHCenter)
            if rect.width() >= CALL_PX and rect.height() >= 26:
                call = ((cell.confirmed or cell.first or {}).get("call") or "")
                font = QFont(self.font())
                font.setBold(True)
                font.setItalic(not cell.confirmed)
                font.setPointSizeF(max(8.0, min(14.0, math.floor(rect.width() / 6 * 2) / 2)))
                width = QFontMetricsF(font).horizontalAdvance(call)
                if width > rect.width() - 6:
                    font.setPointSizeF(max(.5, math.floor(font.pointSizeF() * (rect.width() - 6) / width * 2) / 2))
                if font.pointSizeF() >= 6:
                    self._halo_text(painter, QPointF(center.x(), center.y() + font.pointSizeF() * 1.5), call, font,
                                    QColor("white") if cell.confirmed else QColor(255, 243, 214),
                                    Qt.AlignmentFlag.AlignHCenter)

    def _draw_credit(self, painter: QPainter) -> None:
        if self.provider is None:
            text = tr("Grid only: no imagery (Preferences → Map)")
        elif self.provider.key in self.fetcher.stopped:
            text = tr("Imagery unavailable:") + " " + self.fetcher.stopped[self.provider.key]
        else:
            text = self.provider.credit
        font = QFont(self.font())
        font.setPointSizeF(9)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = min(self.width() - 12, metrics.horizontalAdvance(text) + 12)
        text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(1, width - 12))
        box = QRectF(self.width() - width - 6, self.height() - metrics.height() - 8, width, metrics.height() + 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 120))
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QColor(255, 255, 255, 220))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def describe(self, sq: str) -> str:
        cell = self.cells.get(sq)
        if cell is None:
            return f"{sq} · " + tr("not worked")
        counts = f"{cell.qsos} " + tr("QSOs") + f", {len(cell.calls)} " + tr("calls")
        if cell.confirmed:
            q = cell.confirmed
            head = tr("first confirmed by") + f" {SOURCE_NAMES[cell.source]}: {q.get('call')}"
        else:
            q = cell.first or {}
            head = tr("worked only, first:") + f" {q.get('call')}"
        where = q.get("sat_name") or q.get("band") or ""
        return " · ".join(x for x in (sq, head, (q.get("time_on") or "")[:10], where, counts) if x)

    # ------------------------------------------------------------------ interaction
    def _anchor_to(self, lon: float, lat: float, pos: QPointF) -> None:
        target_x = (pos.x() - self.width() / 2) / self._radius()
        target_y = (self.height() / 2 - pos.y()) / self._radius()
        squared = target_x * target_x + target_y * target_y
        if squared > 1:
            return
        phi = math.radians(lat)
        cos_phi = math.cos(phi)
        q = math.atan2(target_y, math.sqrt(max(0, 1 - squared)))
        candidates = []
        if cos_phi < 1e-12:
            if abs(target_x) > 1e-10:
                return
            candidates.append((self.center_lon, math.copysign(90, lat) - math.degrees(q)))
        else:
            if abs(target_x) > cos_phi + 1e-12:
                return  # A north-up globe cannot keep this point anchored farther sideways.
            delta = math.asin(max(-1, min(1, target_x / cos_phi)))
            for angle in (delta, math.pi - delta):
                center_lon = globe.normalize_lon(lon - math.degrees(angle))
                center_lat = globe.normalize_lon(math.degrees(math.atan2(math.sin(phi), cos_phi * math.cos(angle)) - q))
                candidates.append((center_lon, center_lat))
        valid = []
        for center_lon, center_lat in candidates:
            if not -90 <= center_lat <= 90:
                continue
            x, y, depth = globe.Orthographic(center_lon, center_lat).project(lon, lat)
            if depth >= -1e-10 and abs(x - target_x) + abs(y - target_y) < 1e-8:
                valid.append((center_lon, center_lat))
        if valid:
            self.center_lon, self.center_lat = min(valid, key=lambda ll: geo.distance_km(
                (self.center_lat, self.center_lon), (ll[1], ll[0])))

    def zoom_at(self, pos: QPointF, steps: float) -> None:
        anchor = self.to_lonlat(pos)
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom + steps))
        if anchor is not None:
            self._anchor_to(*anchor, pos)
        self.update()

    def _rotate(self, dx: float, dy: float) -> None:
        self.center_lon = globe.normalize_lon(self.center_lon - math.degrees(dx / self._radius()) /
                                               max(0.15, math.cos(math.radians(self.center_lat))))
        self.center_lat = max(-90, min(90, self.center_lat + math.degrees(dy / self._radius())))
        self.update()

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.zoom_at(event.position(), event.angleDelta().y() / 120 * 0.35)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self._drag, self._moved = event.position(), False

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._drag is not None:
            delta = event.position() - self._drag
            if self._moved or abs(delta.x()) + abs(delta.y()) > 4:
                anchor = self.to_lonlat(self._drag)
                self._moved = True
                self._rotate(delta.x(), delta.y())
                if anchor is not None:
                    self._anchor_to(*anchor, event.position())
                self._drag = event.position()
                QToolTip.hideText()
            return
        sq = self.square_at(event.position())
        if sq != self._hover:
            self._hover = sq
            if sq:
                QToolTip.showText(event.globalPosition().toPoint(), self.describe(sq), self)
            else:
                QToolTip.hideText()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and self._drag is not None:
            if not self._moved:
                sq = self.square_at(event.position())
                if sq in self.cells:
                    self.square_clicked.emit(sq)
            self._drag = None
            self.update()  # Restore full image quality after interactive rotation.

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.zoom_at(event.position(), 1)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        mid = QPointF(self.width() / 2, self.height() / 2)
        key = event.key()
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_at(mid, 0.5)
        elif key == Qt.Key.Key_Minus:
            self.zoom_at(mid, -0.5)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._rotate({Qt.Key.Key_Left: 80, Qt.Key.Key_Right: -80}.get(key, 0),
                         {Qt.Key.Key_Up: 80, Qt.Key.Key_Down: -80}.get(key, 0))
        else:
            super().keyPressEvent(event)
