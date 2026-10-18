"""A rotatable globe with satellite imagery and Maidenhead grid squares.

Imagery, squares, the night side and grid lines share an orthographic projection. Imagery, squares and night are
computed per pixel at the display's resolution (:mod:`globeraster`); lines, outlines and labels are vector paths on
top, and stars behind (:mod:`starfield`). Only the visible hemisphere is drawn; the same geometry supplies tooltips
and square selection, and an off-screen copy of the view the whole-globe PNG export.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from datetime import UTC, datetime

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
    QTransform,
)
from PySide6.QtWidgets import QPushButton, QToolTip, QWidget

from ... import APP_NAME, __version__, i18n
from ...core import geo, globe, grids, maptiles, solar
from ...core.grids import SOURCE_NAMES
from .. import theme
from .globegeometry import CellGeometry
from .globeraster import CellTable, GlobeRaster
from .starfield import SPACE, sky

tr = i18n.tr
BG = SPACE  # the sky's darkest colour
FILL = {"confirmed": QColor(34, 163, 90, 168), "worked": QColor(242, 184, 75, 168)}
LINE = QColor(255, 255, 255, 34)
FIELD_LINE = QColor(255, 255, 255, 120)
FIELD_WATERMARK = QColor(255, 255, 255, 20)  # 8% opacity
FIELD_LABEL_PX = 64.0
MIN_ZOOM, MAX_ZOOM = 1.0, 11.0
FINE_GRID_PX = 28.0
BORDER = QColor(255, 255, 255, 70)  # the light edge of each worked square, from BORDER_PX[0] wide squares,
BORDER_PX = 10.0, 24.0  # full at BORDER_PX[1]
CALL_MAX_PT, GRID_MAX_PT = 10.0, 8.0  # full label sizes, reached as you zoom in
CALL_SHARE = 1 / 7  # a call's size (in pixels) as a share of its square's width
NAME_SHARE = .8  # the square's name is this much smaller than its call
# Labels appear on every square in view, each at its own size, once a square at the view's centre has its call this
# large (about half a zoom step inside a whole-country view) and its name this large.
CALL_MIN_PX, NAME_MIN_PX = 5.0, 4.0
LABEL_FLOOR_PX = 2.5  # slivers at the limb, whose text would be smaller, stay blank
SIZE_CALL = "BA8TSK/9"  # every call is sized as this eight-character one, so letters match from square to square
CALL_WIDTH = .88  # SIZE_CALL fills at most this share of a square's width; a longer call is made smaller to fit
CAP = 0.7  # cap height of a label font, as a share of its size
PIX_CACHE = 600
KEEP_LEVEL = 3  # tiles this coarse (84 at most) stay in memory, so any view has a stand-in while finer ones load
TEXT_CACHE = 6000
MAX_TILES = 144  # tiles in one view's atlas; a busier view uses the next coarser level
MAX_DPR = 2.0
RASTER_PIXELS = 4_000_000  # per-pixel layers above this many device pixels are computed smaller and scaled up
NIGHT_SHADE = QColor(3, 9, 22, 165)
HALO = QColor(120, 175, 255)  # the atmosphere's glow around the globe
EXPORT_SCALE = 2.0  # Save as PNG: the view as it is, at twice its size in pixels
EXPORT_PIXELS = 8_000_000  # its per-pixel layers stay within this many pixels
EXPORT_TILES = 256  # tiles in its atlas
CAPTION_PX = 32.0  # the band above it naming NWOlogger and its version, so the caption covers nothing


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _hairline(color: QColor, style: Qt.PenStyle = Qt.PenStyle.SolidLine) -> QPen:
    """A one-device-pixel line: wider strokes on a scaled display take Qt's general stroker, many times slower."""
    pen = QPen(color, 1, style)
    pen.setCosmetic(True)
    return pen


def _samples(start: float, end: float, step: float) -> list[float]:
    count = max(1, math.ceil(abs(end - start) / step))
    low, high = sorted((start, end))
    return [start, *(max(low, min(high, start + (end - start) * (i / count)))
                     for i in range(1, count)), end]


class GridMapView(QWidget):
    square_clicked = Signal(str)
    full_screen_toggled = Signal()  # the full-screen key, or Esc / closing the window while full screen
    save_requested = Signal()  # Save as PNG, from the full-screen map's corner
    _tile_arrived = Signal(str, int, int, int, bytes)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(360)
        self.render_dpr: float | None = None  # a fixed scale instead of the display's (an export)
        self.raster_pixels = RASTER_PIXELS
        self.caption: str | None = None  # an export's band above the map (see snapshot)
        self.max_tiles = MAX_TILES
        self._sky_key = None
        self._sky = QPixmap()
        self._full_screen = False
        self._overlay = []  # the full-screen map's buttons, in its top right corner
        for text, signal in ((tr("Save as PNG"), self.save_requested),
                             (tr("Exit full screen (Esc)"), self.full_screen_toggled)):
            button = QPushButton(text, self)
            button.setObjectName("MapOverlayButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the keys stay with the map
            button.setStyleSheet("QPushButton#MapOverlayButton { background: rgba(0, 0, 0, 150); color: white; "
                                 "border: 1px solid rgba(255, 255, 255, 70); border-radius: 6px; "
                                 "padding: 5px 12px; }")
            button.clicked.connect(signal)
            button.hide()
            self._overlay.append(button)
        self._save, self._leave = self._overlay
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
        self._drag: QPointF | None = None  # a rotation in progress, with the right or middle button
        self._press: QPointF | None = None  # a left press, which selects a square on release unless it moved
        self._moved = False
        self._hover: str | None = None
        self.badge: str | None = None  # the counts shown in the bottom right corner, in the sky
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)  # the right button rotates
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
        self._table_key = None
        self._table: CellTable | None = None
        self._atlas_layout = None
        self._atlas = QImage()
        self._atlas_drawn: dict[tuple[int, int], tuple] = {}
        self._atlas_revision = None
        self._tile_timer = QTimer(self)
        self._tile_timer.setSingleShot(True)
        self._tile_timer.setInterval(40)  # tiles arriving together repaint once
        self._tile_timer.timeout.connect(self.update)
        self._field_glyphs: dict[str, tuple[QPainterPath, QRectF]] = {}
        self._field_glyph_font = None
        self._text_sprites: OrderedDict[tuple, tuple[QPixmap, QPointF, float]] = OrderedDict()
        self._label_key = None
        self._label_data = None
        self._label_font_key = None
        self._label_fonts: dict[tuple, QFont] = {}
        self._advances: dict[tuple[str, bool], float] = {}
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
            self._atlas_layout = None
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

    # ------------------------------------------------------------------ full screen and export
    def set_full_screen(self, on: bool) -> None:
        """Show or hide Save as PNG and the way back (in the top right corner) while the map fills the screen."""
        self._full_screen = on
        for button in self._overlay:
            button.setVisible(on)
            button.raise_()
        self._place_overlay()

    def _place_overlay(self) -> None:
        right = self.width() - 14
        for button in reversed(self._overlay):
            button.adjustSize()
            button.move(right - button.width(), 14)
            right -= button.width() + 8

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if self._full_screen:
            self._place_overlay()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.isWindow() and self._full_screen:
            event.ignore()  # closing the full-screen window (an export view) brings the map back
            self.full_screen_toggled.emit()
            return
        super().closeEvent(event)

    def export_view(self) -> GridMapView:
        """This view as it is (size, zoom, centre, squares, imagery and night side) on its own off screen, drawn at
        EXPORT_SCALE, for Save as PNG (see :class:`GlobeSnapshot`)."""
        view = GridMapView()
        view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        view.setFont(self.font())
        view.resize(self.size())
        view.render_dpr, view.raster_pixels, view.max_tiles = EXPORT_SCALE, EXPORT_PIXELS, EXPORT_TILES
        view.caption = f"{APP_NAME} {__version__}"
        view.zoom, view.center_lon, view.center_lat = self.view_state()
        view.home, view.radius_km, view.highlight = self.home, self.radius_km, self.highlight
        view.badge = self.badge
        view.set_cells(self.cells)
        view.provider, view.api_key = self.provider, self.api_key
        view._pix = OrderedDict(self._pix)  # the tiles already here; the rest come from the cache or the provider
        view.fetcher.offline = self.fetcher.offline
        view.fetcher.stopped = dict(self.fetcher.stopped)
        view.terminator_enabled = self.terminator_enabled
        view._sun_position, view._solar_time = self._sun_position, self._solar_time
        view._solar_stopped = True
        return view

    def missing_tiles(self) -> int:
        """Imagery tiles this view still waits for at its own level; asks for them."""
        if self.provider is None or self.provider.key in self.fetcher.stopped:
            return 0
        self._prepare_geometry()
        self._prepare_atlas()
        z = self._imagery_level()
        return sum(1 for tx, ty in self._tile_indices(z) if self._tile(z, tx, ty) is None)

    def snapshot(self) -> QImage:
        """The scene as an image at ``render_dpr`` (default 1), with the caption, if any, in a band of its own
        above it, so it covers nothing of the map."""
        dpr = self.render_dpr or 1.0
        band = CAPTION_PX if self.caption else 0.0
        image = QImage(math.ceil(self.width() * dpr), math.ceil((self.height() + band) * dpr),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        painter = QPainter(image)
        try:
            self._update_sun()
            self._prepare_geometry()
            if band:
                painter.fillRect(QRectF(0, 0, self.width(), band), BG)
                font = QFont(self.font())
                font.setPixelSize(round(band * .5))
                font.setWeight(QFont.Weight.DemiBold)
                painter.setFont(font)
                painter.setPen(QColor(255, 255, 255, 215))
                painter.drawText(QRectF(0, 0, self.width(), band), Qt.AlignmentFlag.AlignCenter, self.caption)
                painter.translate(0, band)
                painter.setClipRect(QRectF(self.rect()))  # a globe larger than the view stays out of the band
            self._paint_scene(painter)
        finally:
            painter.end()
        return image

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
        """Outlines of the highlighted squares and the home square, projected in one batch. Fills are per pixel."""
        self._prepare_geometry()
        key = "outlines", self.highlight, self.home
        if key in self._geometry:
            return
        squares = dict.fromkeys(self.highlight)
        if home := grids.square(self.home):
            squares[home] = None
        paths = self._cell_geometry.paths(squares, self._camera(), self._radius(), self.width(), self.height())
        self._geometry.update((("square", sq), path) for sq, path in paths.items())
        self._geometry[key] = True

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
        self._pix.move_to_end((provider, z, x, y))
        self._tile_revision += 1
        self._drop_tiles()
        if not self._tile_timer.isActive():
            self._tile_timer.start()

    def _drop_tiles(self) -> None:
        """Keep at most PIX_CACHE tiles, dropping the least recently used finer ones first; the fetcher may then
        ask for a dropped tile again when a view needs it (it used to stay missing for the session)."""
        while len(self._pix) > PIX_CACHE:
            key = next((k for k in self._pix if k[1] > KEEP_LEVEL), None)
            if key is None:
                return
            del self._pix[key]
            self.fetcher.release(*key)

    def _tile(self, z: int, x: int, y: int) -> QPixmap | None:
        key = self.provider.key, z, x, y
        pix = self._pix.get(key)
        if pix is not None:
            self._pix.move_to_end(key)
        return pix

    def _visible_samples(self) -> tuple[np.ndarray, np.ndarray]:
        """Longitudes relative to the centre and latitudes (degrees) of points spread over the visible globe: a
        grid across the viewport and the limb. A latitude-longitude box around the view's cap is far larger than
        the viewport once zoomed in; these points bound the tiles actually seen."""
        key = "samples"
        self._prepare_geometry()
        if key in self._geometry:
            return self._geometry[key]
        width, height, radius = self.width(), self.height(), self._radius()
        px, py = np.meshgrid(np.linspace(0, width, 33), np.linspace(0, height, 25))
        x, y = ((px - width / 2) / radius).ravel(), ((height / 2 - py) / radius).ravel()
        angle = np.linspace(0, math.tau, 241)
        limb_x, limb_y = width / 2 + radius * np.cos(angle), height / 2 - radius * np.sin(angle)
        on_screen = (limb_x >= 0) & (limb_x <= width) & (limb_y >= 0) & (limb_y <= height)
        x = np.concatenate((x, np.cos(angle[on_screen]) * (1 - 1e-9), [0.0]))
        y = np.concatenate((y, np.sin(angle[on_screen]) * (1 - 1e-9), [0.0]))
        squared = x * x + y * y
        x, y = x[squared <= 1], y[squared <= 1]
        camera = self._camera()
        depth = np.sqrt(np.maximum(0, 1 - x * x - y * y))
        north = y * camera._cos_lat + depth * camera._sin_lat
        horizontal = depth * camera._cos_lat - y * camera._sin_lat
        result = (np.degrees(np.arctan2(x, horizontal)), np.degrees(np.arcsin(np.clip(north, -1, 1))))
        self._geometry[key] = result
        return result

    def _tile_indices(self, z: int):
        relative, lat = self._visible_samples()
        if not lat.size:
            return
        n = 2 ** z
        if self._overlaps_pole() or relative.max() - relative.min() > 300:
            x0, x1 = 0, n - 1
        else:
            # Quarter-tile margins cover extremes that fall between the samples.
            x0 = math.floor((self.center_lon + relative.min() + 180) / 360 * n - .25)
            x1 = math.floor((self.center_lon + relative.max() + 180) / 360 * n + .25)
        south, north = max(-grids.MAX_LAT, float(lat.min())), min(grids.MAX_LAT, float(lat.max()))
        if south > north:
            return
        _, y0 = grids.to_world(0, north, z)
        _, y1 = grids.to_world(0, south, z)
        xs = dict.fromkeys(x % n for x in range(x0, min(x1 + 1, x0 + n)))
        for x in xs:
            for y in range(max(0, math.floor(y0 / 256 - .25)), min(n - 1, math.floor(y1 / 256 + .25)) + 1):
                yield x, y

    def _overlaps_pole(self) -> bool:
        """Whether either pole is in view (every longitude meets there)."""
        for lat in (90, -90):
            x, y, depth = self._camera().project(0, lat)
            pos = self._screen(x, y)
            if depth >= 0 and QRectF(self.rect()).contains(pos):
                return True
        return False

    def _frame_dpr(self) -> float:
        """Device pixels per logical pixel for the scene: the display's own, up to MAX_DPR."""
        if self.render_dpr is not None:
            return self.render_dpr
        return max(1.0, min(MAX_DPR, self.devicePixelRatioF()))

    def _raster_scale(self, dpr: float, moving: bool = False) -> float:
        """Pixels of the per-pixel layers per logical pixel: the frame's, within ``raster_pixels``, and at most one
        while the globe is dragged (restored on release)."""
        budget = math.sqrt(self.raster_pixels / max(1, self.width() * self.height()))
        scale = min(dpr, budget)
        if moving:
            scale = min(scale, 1.0)
        return max(0.25, scale)

    def _imagery_level(self) -> int:
        """About one tile pixel per screen pixel at the centre (Mercator stretches tiles by 1 / cos latitude),
        dropping a level while the view would need more than ``max_tiles`` tiles."""
        scale = self._raster_scale(self._frame_dpr())
        level = self.zoom + math.log2(scale) + math.log2(max(0.12, math.cos(math.radians(self.center_lat))))
        z = max(self.provider.min_zoom, min(self.provider.max_zoom, round(level)))
        while z > self.provider.min_zoom and sum(1 for _ in self._tile_indices(z)) > self.max_tiles:
            z -= 1
        return z

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

    def _prepare_atlas(self) -> tuple[QImage, int, int, int] | None:
        """The tiles around the view as one image, with its level, first column (unwrapped) and first row.

        The atlas reaches one tile beyond the view on every side, so rotating a little reuses it; only tiles whose
        source changed are drawn again, so a tile arriving costs one tile.
        """
        if self.provider is None:
            return None
        z = self._imagery_level()
        indices = tuple(self._tile_indices(z))
        if not indices:
            return None
        n = 2 ** z
        xs = tuple(dict.fromkeys(tx for tx, _ty in indices))  # consecutive columns, west to east
        top, bottom = min(ty for _tx, ty in indices), max(ty for _tx, ty in indices)
        layout = self._atlas_layout
        fits = (layout is not None and layout[:2] == (self.provider.key, z) and layout[4] <= top and
                bottom <= layout[5] and all((tx - layout[2]) % n < layout[3] for tx in (xs[0], xs[-1])))
        if not fits:
            columns = min(n, len(xs) + 2)
            left = xs[0] - (1 if columns > len(xs) else 0)
            first, last = max(0, top - 1), min(n - 1, bottom + 1)
            layout = self._atlas_layout = self.provider.key, z, left, columns, first, last
            self._atlas = QImage(columns * 256, (last - first + 1) * 256, QImage.Format.Format_ARGB32_Premultiplied)
            self._atlas.fill(Qt.GlobalColor.transparent)
            self._atlas_drawn, self._atlas_revision = {}, None
            margin = [(left + c) % n for c in range(columns)]
            self._draw_atlas(layout, ((tx, ty) for tx in margin for ty in range(first, last + 1)), fetch=False)
        if self._atlas_revision != (self._tile_revision, indices):
            self._draw_atlas(layout, indices, fetch=True)
            self._atlas_revision = self._tile_revision, indices
        if not self._atlas_drawn:
            return None
        return self._atlas, z, layout[2], layout[4]

    def _draw_atlas(self, layout, tiles, *, fetch: bool) -> None:
        """Draw tiles whose best available source changed; ``fetch`` also asks for missing ones."""
        _provider, z, left, _columns, first, _last = layout
        n = 2 ** z
        assembler = None
        try:
            for tx, ty in tiles:
                if fetch:
                    pix, source = self._tile_source(z, tx, ty)
                else:
                    pix = self._tile(z, tx, ty)
                    source = QRectF(0, 0, pix.width(), pix.height()) if pix is not None else QRectF()
                if pix is None:
                    continue
                state = pix.cacheKey(), source.x(), source.y(), source.width()
                if self._atlas_drawn.get((tx, ty)) == state:
                    continue
                if assembler is None:
                    assembler = QPainter(self._atlas)
                    assembler.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                target = QRectF((tx - left) % n * 256, (ty - first) * 256, 256, 256)
                assembler.drawPixmap(target, pix, source)
                self._atlas_drawn[(tx, ty)] = state
        finally:
            if assembler is not None:
                assembler.end()

    def _cell_table(self) -> CellTable | None:
        if not self.cells:
            return None
        key = self._cell_revision, id(self.cells)
        if self._table_key != key:
            self._table = CellTable({sq: FILL[cell.status] for sq, cell in self.cells.items()})
            self._table_key = key
        return self._table

    def _layers(self, painter: QPainter, *, squares: bool = True) -> tuple[QImage, QImage]:
        """Imagery and squares for this frame, each covering the whole viewport."""
        moving = self._drag is not None and self._moved
        scale = self._raster_scale(painter.device().devicePixelRatioF(), moving)
        options = {}
        atlas = self._prepare_atlas()
        if atlas is not None:
            image, z, left, top = atlas
            # Tile pixels per logical pixel at the centre; when the tiles are coarser than the screen (beyond the
            # provider's deepest level), a smaller image is sampled and scaled up smoothly instead of blockily.
            texels = 2 ** (z - self.zoom) / max(0.12, math.cos(math.radians(self.center_lat)))
            image_scale = scale
            if texels < scale * 0.8:  # steps of √2, so rotating reuses the same sampling grid
                image_scale = max(scale / 16, scale / 2 ** (round(2 * math.log2(scale / texels)) / 2))
            options = {"atlas": image, "level": z, "tile_left": left, "tile_top": top, "image_scale": image_scale}
        table = self._cell_table() if squares else None
        return self._raster.layers(self.size(), self._radius(), self._camera(), scale=scale, table=table,
                                   border=self._border(), **options)

    def _border(self) -> QColor:
        """Worked squares get an edge once they are big enough for it not to crowd them."""
        width = math.radians(2) * self._radius() * math.cos(math.radians(min(70.0, abs(self.center_lat))))
        low, high = BORDER_PX
        color = QColor(BORDER)
        color.setAlpha(round(BORDER.alpha() * max(0.0, min(1.0, (width - low) / (high - low)))))
        return color

    def _draw_tiles(self, painter: QPainter) -> None:
        self._draw_layer(painter, self._layers(painter, squares=False)[0])

    def _draw_layer(self, painter: QPainter, image: QImage) -> None:
        """Draw a viewport-sized layer; one smaller than the device pixels is scaled up smoothly."""
        if image.isNull():
            return
        device = self.width() * painter.device().devicePixelRatioF()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, image.width() < device - 1)
        painter.drawImage(QRectF(self.rect()), image)

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        self._update_sun()
        self._prepare_geometry()
        moving = self._drag is not None and self._moved
        dpr = self._frame_dpr()
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

    def _sky_pixmap(self, dpr: float) -> QPixmap:
        key = self.width(), self.height(), dpr
        if self._sky_key != key:
            self._sky, self._sky_key = sky(self.size(), dpr), key
        return self._sky

    def _draw_halo(self, painter: QPainter, middle: QPointF, radius: float) -> None:
        """The atmosphere: a thin glow just outside the limb, while the limb is in view."""
        if radius > math.hypot(self.width() / 2, self.height() / 2):
            return  # the globe fills the view
        width = max(6.0, min(40.0, radius * 0.045))
        ring = QPainterPath()
        ring.addEllipse(middle, radius + width, radius + width)
        inner = QPainterPath()
        inner.addEllipse(middle, radius, radius)
        glow = QRadialGradient(middle, radius + width)
        start = radius / (radius + width)
        for at, alpha in ((0, 95), (start, 95), (start + (1 - start) * .35, 38), (1, 0)):
            color = QColor(HALO)
            color.setAlpha(alpha)
            glow.setColorAt(at, color)
        painter.fillPath(ring.subtracted(inner), glow)

    def _paint_scene(self, painter: QPainter) -> None:
        painter.drawPixmap(QPointF(), self._sky_pixmap(painter.device().devicePixelRatioF()))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        radius = self._radius()
        self._prepare_cell_paths()
        middle = QPointF(self.width() / 2, self.height() / 2)
        self._draw_halo(painter, middle, radius)
        sphere = QPainterPath()
        sphere.addEllipse(middle, radius, radius)
        base = QRadialGradient(middle - QPointF(radius * 0.25, radius * 0.25), radius * 1.5)
        base.setColorAt(0, QColor("#25465c"))
        base.setColorAt(1, QColor("#0b1e30"))
        painter.fillPath(sphere, base)
        painter.save()
        try:
            painter.setClipPath(sphere, Qt.ClipOperation.IntersectClip)  # within any clip already set (an export)
            imagery, cells = self._layers(painter)
            self._draw_layer(painter, imagery)
            self._draw_layer(painter, cells)
            if self.terminator_enabled:
                self._draw_night(painter)  # over the squares too, so the line runs unbroken through them
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._draw_field_watermarks(painter)
            cell_px = math.radians(2) * radius
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if cell_px >= FINE_GRID_PX:
                painter.setPen(_hairline(LINE))
                painter.drawPath(self._grid_path(2, 1))
            painter.setPen(_hairline(FIELD_LINE))
            painter.drawPath(self._grid_path(20, 10))
            if self.highlight and cell_px >= 3:
                painter.setPen(_hairline(QColor(255, 255, 255, 150), Qt.PenStyle.DotLine))
                for sq in self.highlight:
                    painter.drawPath(self._geometry["square", sq])
            self._draw_home(painter)
            self._draw_labels(painter, cell_px)
            shade = QRadialGradient(middle, radius)
            shade.setColorAt(0, QColor(0, 0, 0, 0))
            shade.setColorAt(0.72, QColor(0, 0, 0, 0))
            shade.setColorAt(1, QColor(0, 0, 0, 75))
            painter.fillPath(sphere, shade)
        finally:
            painter.restore()
        painter.setPen(_hairline(QColor(150, 195, 225, 120)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(sphere)
        self._draw_badge(painter, self._draw_credit(painter))

    def _night_image(self) -> QImage:
        """The night side, with a narrow, clear edge at the terminator (see :func:`GlobeRaster.night`)."""
        return self._raster.night(self.size(), self._radius(), self._camera(), self._sun_position, NIGHT_SHADE)

    def _draw_night(self, painter: QPainter) -> None:
        image = self._night_image()
        if not image.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(QRectF(self.rect()), image)

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

    def _label_text(self, painter: QPainter, pos: QPointF, text: str, font: QFont, color: QColor,
                    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft, key: tuple | None = None) -> None:
        """Draw text from a cached sprite; ``key`` (font, text and colour) saves building one."""
        dpr = painter.device().devicePixelRatioF()
        key = (key or (font.toString(), text, color.rgba())) + (dpr,)
        cached = self._text_sprites.get(key)
        if cached is None:
            metrics = QFontMetricsF(font)
            bounds = metrics.tightBoundingRect(text)
            if bounds.isEmpty():
                return
            offset = bounds.topLeft() - QPointF(2, 2)
            sprite = QPixmap(math.ceil((bounds.width() + 4) * dpr), math.ceil((bounds.height() + 4) * dpr))
            sprite.setDevicePixelRatio(dpr)
            sprite.fill(Qt.GlobalColor.transparent)
            renderer = QPainter(sprite)
            try:
                renderer.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
                renderer.translate(-offset)
                renderer.setFont(font)  # native glyphs keep new labels cheap to build as the globe rotates
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
            # The bold system font on macOS draws M, N and P (also A, B, K, Q, X) with strokes that overlap each
            # other; Qt's default odd-even rule leaves those overlaps unfilled, so the letters showed notches.
            path.setFillRule(Qt.FillRule.WindingFill)
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

    def _projected_extents(self, lon: np.ndarray, lat: np.ndarray, half_lon: float, half_lat: float):
        """Screen centre, depth and on-screen width and height of boxes centred at the given radians, for many
        boxes at once. Mid-edge extents size text without building and clipping each polygon."""
        camera = self._camera()
        delta = lon - math.radians(camera.center_lon)
        cos_lat, sin_lat, cos_delta = np.cos(lat), np.sin(lat), np.cos(delta)
        horizontal = cos_lat * cos_delta
        radius = self._radius()
        x = self.width() / 2 + radius * cos_lat * np.sin(delta)
        y = self.height() / 2 - radius * (camera._cos_lat * sin_lat - camera._sin_lat * horizontal)
        depth = camera._sin_lat * sin_lat + camera._cos_lat * horizontal
        width = np.abs(2 * radius * math.sin(half_lon) * horizontal)
        height = np.abs(2 * radius * math.sin(half_lat) * (camera._cos_lat * cos_lat + camera._sin_lat * sin_lat * cos_delta))
        return x, y, depth, width, height

    _FIELD_LON = np.radians([(x * 20 - 170) for x in range(18) for _y in range(18)])
    _FIELD_LAT = np.radians([(y * 10 - 85) for _x in range(18) for y in range(18)])

    def _draw_field_watermarks(self, painter: QPainter) -> None:
        _x, _y, depth, width, height = self._projected_extents(self._FIELD_LON, self._FIELD_LAT,
                                                               math.radians(10), math.radians(5))
        candidates = np.flatnonzero((depth >= 0.15) & (width >= FIELD_LABEL_PX) & (height >= FIELD_LABEL_PX / 2))
        if not candidates.size:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(FIELD_WATERMARK)
        viewport = QRectF(self.rect())
        for index in candidates:
            path = self._field_watermark_path(chr(65 + index // 18) + chr(65 + index % 18))
            if path is not None and path.boundingRect().intersects(viewport):
                painter.drawPath(path)
        painter.restore()

    def _labels(self):
        """Every square's centre in radians, the call shown, whether it is confirmed, and the call's width per point
        of font size (0 without a call)."""
        font_key = self.font().toString()
        if self._label_font_key != font_key:
            self._label_fonts.clear()
            self._advances.clear()
            self._label_font_key = font_key
        key = self._cell_revision, id(self.cells), font_key
        if self._label_key != key:
            squares = list(self.cells)
            centers = [grids.center(sq) for sq in squares]
            calls = [((cell.confirmed or cell.first or {}).get("call") or "") for cell in self.cells.values()]
            confirmed = [bool(cell.confirmed) for cell in self.cells.values()]
            advance = np.array([self._call_advance(call, not sure) if call else 0.0
                                for call, sure in zip(calls, confirmed, strict=True)])
            self._label_data = (squares, np.radians([lon for _lat, lon in centers]),
                                np.radians([lat for lat, _lon in centers]), calls, confirmed, advance)
            self._label_key = key
        return self._label_data

    def _label_font(self, kind: str, points: float, italic: bool = False) -> QFont:
        key = kind, points, italic
        font = self._label_fonts.get(key)
        if font is None:
            font = QFont(self._mono if kind == "grid" else self.font())
            if kind == "call":
                font.setWeight(QFont.Weight.Normal)  # thin, so a small call stays clear in a small square
                font.setItalic(italic)
            font.setPointSizeF(points)
            font = self._label_fonts[key] = font
        return font

    def _call_advance(self, call: str, italic: bool) -> float:
        """Width of a call per point of font size."""
        key = call, italic
        if key not in self._advances:
            self._advances[key] = QFontMetricsF(self._label_font("call", 100.0, italic)).horizontalAdvance(call) / 100
        return self._advances[key]

    def _call_points(self, width: np.ndarray, height: np.ndarray) -> np.ndarray:
        """The call size (points) of squares this wide and high on screen: a seventh of the width, never so large
        that an eight-character call (:data:`SIZE_CALL`) fills more than CALL_WIDTH of it, nor beyond CALL_MAX_PT."""
        px_per_pt = self.logicalDpiY() / 72
        return np.minimum.reduce([width * CALL_SHARE / px_per_pt,
                                  CALL_WIDTH * (width - 2) / self._call_advance(SIZE_CALL, False),
                                  height * .8 / px_per_pt, np.full(np.shape(width), CALL_MAX_PT)])

    def _centre_call_px(self) -> float:
        """The call size (pixels) of a square at the view's centre, which decides whether the view shows labels."""
        lon, lat = np.radians([self.center_lon]), np.radians([self.center_lat])
        *_rest, width, height = self._projected_extents(lon, lat, math.radians(1), math.radians(.5))
        return float(self._call_points(width, height)[0]) * self.logicalDpiY() / 72

    def _draw_labels(self, painter: QPainter, _cell_px: float = 0.0) -> None:
        """The square's name and its first call, in two lines, once they are big enough to read at the view's centre
        (about half a zoom step inside a whole-country view), and then on every square in view, each at its own
        size (squares farther from the equator are narrower on the globe); a square too low for two lines shows only
        the call. A square's calls have one size, so a short call is no larger than a long one; only a longer call
        than :data:`SIZE_CALL` is made smaller to fit. The name is a fifth smaller. Text grows with the square up to
        its full size (call 10 pt, name 8 pt)."""
        if not self.cells or (self._drag is not None and self._moved):
            return
        centre = self._centre_call_px()
        if centre < CALL_MIN_PX:
            return
        names = centre * NAME_SHARE >= NAME_MIN_PX
        squares, lon, lat, calls, confirmed, advance = self._labels()
        x, y, depth, width, height = self._projected_extents(lon, lat, math.radians(1), math.radians(.5))
        px_per_pt = self.logicalDpiY() / 72
        # Sizes for every square at once; only squares that get some text reach the drawing loop.
        size = self._call_points(width, height)
        shown = size * px_per_pt >= LABEL_FLOOR_PX
        with np.errstate(divide="ignore", invalid="ignore"):
            fit = np.where(advance > 0, CALL_WIDTH * (width - 2) / advance, 0)
        call_pt = np.where(shown, np.floor(np.minimum(size, fit) * 4) / 4, 0)  # quarter points: neighbours match
        # The name comes with the call, a fifth smaller (a square without a call shows its name at call size).
        name_pt = np.floor(np.minimum(np.where(advance > 0, size * NAME_SHARE, size), GRID_MAX_PT) * 4) / 4
        name_pt = np.where(shown & names, name_pt, 0)
        call_px, name_px = call_pt * px_per_pt, name_pt * px_per_pt
        gap = np.where(call_pt > 0, np.maximum(2, call_px * .25), 0)
        two_lines = (name_pt > 0) & (height >= CAP * (name_px + call_px) + gap + 4)
        drawn = ((depth >= 0.15) & (x >= 0) & (x <= self.width()) & (y >= 0) & (y <= self.height()) &
                 (two_lines | (call_pt > 0)))
        grid_color, call_color, worked_color = QColor(255, 255, 255, 235), QColor("white"), QColor(255, 243, 214)
        center = Qt.AlignmentFlag.AlignHCenter
        for index in np.flatnonzero(drawn):
            cx, cy, pt, below = float(x[index]), float(y[index]), float(call_pt[index]), float(call_px[index])
            if two_lines[index]:
                name, above = float(name_pt[index]), float(name_px[index])
                base = cy - (CAP * above + float(gap[index]) + CAP * below) / 2 + CAP * above
                sq = squares[index]
                self._label_text(painter, QPointF(cx, base), sq, self._label_font("grid", name), grid_color,
                                 center, ("grid", name, sq))
                base += float(gap[index]) + CAP * below
            else:
                base = cy + CAP * below / 2
            if pt:
                call, sure = calls[index], confirmed[index]
                self._label_text(painter, QPointF(cx, base), call, self._label_font("call", pt, not sure),
                                 call_color if sure else worked_color, center, ("call", pt, sure, call))

    def _draw_credit(self, painter: QPainter) -> QRectF:
        """The imagery credit in the bottom left corner; returns its box."""
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
        box = QRectF(6, self.height() - metrics.height() - 8, width, metrics.height() + 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 120))
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QColor(255, 255, 255, 220))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        return box

    def _draw_badge(self, painter: QPainter, credit: QRectF) -> None:
        """The counts (confirmed, worked only, VUCC or GridMaster) in the bottom right corner, in the sky outside the
        globe, so they cover no square; above the credit when the view is too narrow for both side by side."""
        if not self.badge:
            return
        font = QFont(self.font())
        font.setPointSizeF(10)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = min(self.width() - 12, metrics.horizontalAdvance(self.badge) + 18)
        text = metrics.elidedText(self.badge, Qt.TextElideMode.ElideRight, max(1, width - 18))
        bottom = self.height() - 8
        if self.width() - width - 8 < credit.right() + 8:
            bottom = credit.top() - 6
        box = QRectF(self.width() - width - 8, bottom - metrics.height() - 8, width, metrics.height() + 8)
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1))
        painter.setBrush(QColor(0, 0, 0, 140))
        painter.drawRoundedRect(box, 6, 6)
        painter.setPen(QColor(255, 255, 255, 235))
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

    DRAG_BUTTONS = (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.setFocus()
        if event.button() in self.DRAG_BUTTONS:
            self._drag, self._moved = event.position(), False
        elif event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position()  # a click selects a square; a drag with the left button does nothing

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
        if event.button() in self.DRAG_BUTTONS and self._drag is not None:
            self._drag = None
            self.update()  # Restore full image quality after interactive rotation.
        elif event.button() == Qt.MouseButton.LeftButton and self._press is not None:
            moved = (event.position() - self._press).manhattanLength() > 4
            self._press = None
            if not moved:
                sq = self.square_at(event.position())
                if sq in self.cells:
                    self.square_clicked.emit(sq)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.zoom_at(event.position(), 1)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        mid = QPointF(self.width() / 2, self.height() / 2)
        key = event.key()
        if event.matches(QKeySequence.StandardKey.FullScreen) or key == Qt.Key.Key_F11 or \
                (key == Qt.Key.Key_Escape and self._full_screen):
            self.full_screen_toggled.emit()
        elif key == Qt.Key.Key_Escape:
            event.ignore()  # the pane decides: back into the tab when it is a window
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_at(mid, 0.5)
        elif key == Qt.Key.Key_Minus:
            self.zoom_at(mid, -0.5)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._rotate({Qt.Key.Key_Left: 80, Qt.Key.Key_Right: -80}.get(key, 0),
                         {Qt.Key.Key_Up: 80, Qt.Key.Key_Down: -80}.get(key, 0))
        else:
            super().keyPressEvent(event)


class GlobeSnapshot(QObject):
    """Draws an off-screen view (:meth:`GridMapView.export_view`) once every imagery tile it asked for has been
    answered (loaded, from the cache, or failed), or after ``deadline`` seconds, then hands over the image with
    ``finished``; ``cancel`` drops it. Either way the view is shut."""

    finished = Signal(QImage)

    def __init__(self, view: GridMapView, *, deadline: float = 60.0, parent=None):
        super().__init__(parent)
        self.view, self.deadline = view, deadline
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._poll)
        self._started = 0.0
        self._answered = False
        self._done = False

    def start(self) -> None:
        self._started = time.monotonic()
        self._poll()

    def cancel(self) -> None:
        self._close()

    def _close(self) -> None:
        if not self._done:
            self._done = True
            self._timer.stop()
            self.view.shutdown()
            self.view.deleteLater()

    def _poll(self) -> None:
        if self._done:
            return
        missing = self.view.missing_tiles()
        waiting = self.view.fetcher.pending()
        # After the last answer, one more round, so the tiles it brought are stored before drawing.
        if missing and time.monotonic() - self._started < self.deadline and (waiting or not self._answered):
            self._answered = not waiting
            self._timer.start()
            return
        image = self.view.snapshot()
        self._close()
        self.finished.emit(image)
