"""The grid map widget: Maidenhead squares on satellite imagery, the way GridMaster and GridTracker show them.

Each square you've confirmed is green with its grid and the first call that confirmed it; a square only worked is
amber with the first call worked there (italics). Zoomed out you see fields, then squares, then grid names, then
calls. Wheel or +/- zooms, dragging (or the arrow keys) pans, a double-click zooms in, a click on a square shows its
QSOs. Imagery tiles are drawn under the grid when a provider is set; a coarser tile stands in while a finer one loads.
"""

from __future__ import annotations

import math
from collections import OrderedDict

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QToolTip, QWidget

from ... import i18n
from ...core import geo, grids, maptiles
from ...core.grids import SOURCE_NAMES
from .. import theme

tr = i18n.tr

BG = QColor("#0d1726")
FILL = {"confirmed": QColor(34, 163, 90, 168), "worked": QColor(242, 184, 75, 168)}
LINE = QColor(255, 255, 255, 34)
FIELD_LINE = QColor(255, 255, 255, 120)
MIN_ZOOM, MAX_ZOOM = 1.0, 11.0
GRID_LABEL_PX, CALL_PX, LINES_PX = 34.0, 50.0, 7.0  # square width on screen that shows labels / calls / lines
PIX_CACHE = 600


class GridMapView(QWidget):
    square_clicked = Signal(str)
    _tile_arrived = Signal(str, int, int, int, bytes)  # from the fetcher's threads to this one

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(360)
        self.zoom = 3.0
        self.cx, self.cy = grids.to_world(110.0, 35.0, 0)  # the view's centre, in world pixels at zoom 0
        self.cells: dict[str, grids.Square] = {}
        self.home: str | None = None
        self.radius_km: float | None = None
        self.highlight: frozenset[str] = frozenset()  # e.g. GridMaster's 488 squares, outlined
        self.provider: maptiles.Provider | None = None
        self.api_key: str | None = None
        self._pix: OrderedDict[tuple, QPixmap] = OrderedDict()
        self.fetcher = maptiles.Fetcher(lambda *a: self._emit_tile(*a))
        self._tile_arrived.connect(self._store_tile, Qt.ConnectionType.QueuedConnection)
        self._drag: QPointF | None = None
        self._moved = False
        self._hover: str | None = None
        self._mono = theme.mono_font()

    # ------------------------------------------------------------------ data and view
    def set_cells(self, cells: dict[str, grids.Square]) -> None:
        self.cells = cells
        self.update()

    def set_provider(self, provider: maptiles.Provider | None, api_key: str | None = None) -> None:
        if provider != self.provider or api_key != self.api_key:
            self.fetcher.log_summary(self.provider)
            self.fetcher.forget()
            self._pix.clear()
        self.provider, self.api_key = provider, api_key
        self.update()

    def fit(self, box: tuple[float, float, float, float]) -> None:
        """Show this (west, south, east, north) box as large as the widget allows."""
        w, s, e, n = box
        x0, y0 = grids.to_world(w, n, 0)
        x1, y1 = grids.to_world(e, s, 0)
        self.cx, self.cy = (x0 + x1) / 2, (y0 + y1) / 2
        width, height = max(1, self.width()), max(1, self.height())
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, math.log2(min(width / max(1e-6, x1 - x0),
                                                               height / max(1e-6, y1 - y0)))))
        self.update()

    def view_state(self) -> tuple[float, float, float]:
        return self.zoom, self.cx, self.cy

    def set_view_state(self, state) -> None:
        try:
            self.zoom, self.cx, self.cy = (float(v) for v in state)
        except (TypeError, ValueError):
            return
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom))
        self.update()

    def shutdown(self) -> None:
        self.fetcher.log_summary(self.provider)
        self.fetcher.shutdown()

    # ------------------------------------------------------------------ coordinates
    def _scale(self) -> float:
        return 2.0 ** self.zoom

    def to_screen(self, lon: float, lat: float) -> QPointF:
        x, y = grids.to_world(lon, lat, 0)
        k = self._scale()
        return QPointF((x - self.cx) * k + self.width() / 2, (y - self.cy) * k + self.height() / 2)

    def to_lonlat(self, p: QPointF) -> tuple[float, float]:
        k = self._scale()
        return grids.from_world((p.x() - self.width() / 2) / k + self.cx, (p.y() - self.height() / 2) / k + self.cy, 0)

    def square_at(self, p: QPointF) -> str | None:
        lon, lat = self.to_lonlat(p)
        if not (-180 <= lon < 180 and -90 <= lat < 90):
            return None
        return next(grids.squares_in(lon, lat, lon, lat), None)

    def _rect(self, sq: str) -> QRectF:
        w, s, e, n = grids.bounds(sq)
        return QRectF(self.to_screen(w, n), self.to_screen(e, s))

    def _visible_box(self) -> tuple[float, float, float, float]:
        w, n = self.to_lonlat(QPointF(0, 0))
        e, s = self.to_lonlat(QPointF(self.width(), self.height()))
        return max(-180.0, w), max(-89.9, s), min(180.0, e), min(89.9, n)

    # ------------------------------------------------------------------ tiles
    def _emit_tile(self, provider: str, z: int, x: int, y: int, data: bytes) -> None:
        try:
            self._tile_arrived.emit(provider, z, x, y, data)
        except RuntimeError:  # the widget is gone
            pass

    def _store_tile(self, provider: str, z: int, x: int, y: int, data: bytes) -> None:
        img = QImage.fromData(data)
        if img.isNull():
            return
        self._pix[(provider, z, x, y)] = QPixmap.fromImage(img)
        while len(self._pix) > PIX_CACHE:
            self._pix.popitem(last=False)
        self.update()

    def _tile(self, z: int, x: int, y: int) -> QPixmap | None:
        k = (self.provider.key, z, x, y)
        pix = self._pix.get(k)
        if pix is not None:
            self._pix.move_to_end(k)
        return pix

    def _draw_tiles(self, p: QPainter) -> None:
        prov = self.provider
        if prov is None:
            return
        tz = int(max(prov.min_zoom, min(prov.max_zoom, round(self.zoom))))
        n = 2 ** tz
        size = 256.0 / n * self._scale()  # a tile on screen
        k = self._scale()
        left = self.cx - self.width() / 2 / k
        top = self.cy - self.height() / 2 / k
        x0, y0 = max(0, int(left * n / 256)), max(0, int(top * n / 256))
        x1 = min(n - 1, int((left + self.width() / k) * n / 256))
        y1 = min(n - 1, int((top + self.height() / k) * n / 256))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        for tx in range(x0, x1 + 1):
            for ty in range(y0, y1 + 1):
                target = QRectF((tx * 256.0 / n - left) * k, (ty * 256.0 / n - top) * k, size + 0.5, size + 0.5)
                pix = self._tile(tz, tx, ty)
                if pix is None:
                    self.fetcher.want(prov, tz, tx, ty, self.api_key)
                    for d in range(1, 4):  # a coarser tile, cropped, until this one arrives
                        pz = tz - d
                        if pz < prov.min_zoom:
                            break
                        parent = self._tile(pz, tx >> d, ty >> d)
                        if parent is not None:
                            part = 256.0 / (1 << d)
                            src = QRectF((tx - ((tx >> d) << d)) * part, (ty - ((ty >> d) << d)) * part, part, part)
                            p.drawPixmap(target, parent, src)
                            break
                    continue
                p.drawPixmap(target, pix, QRectF(0, 0, pix.width(), pix.height()))

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt API
        p = QPainter(self)
        p.fillRect(self.rect(), BG)
        self._draw_tiles(p)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        west, south, east, north = self._visible_box()
        cell_px = 2.0 / 360.0 * 256.0 * self._scale()
        # Squares you worked, then the lines.
        for sq, cell in self.cells.items():
            r = self._rect(sq)
            if r.right() < 0 or r.left() > self.width() or r.bottom() < 0 or r.top() > self.height():
                continue
            p.fillRect(r, FILL[cell.status])
        if cell_px >= LINES_PX:
            p.setPen(QPen(LINE, 1))
            for lon in range(int(math.floor(west / 2) * 2), int(east) + 2, 2):
                x = self.to_screen(lon, 0).x()
                p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            for lat in range(int(math.floor(south)), int(north) + 2):
                y = self.to_screen(0, lat).y()
                p.drawLine(QPointF(0, y), QPointF(self.width(), y))
        p.setPen(QPen(FIELD_LINE, 1))
        for lon in range(int(math.floor((west + 180) / 20)) * 20 - 180, int(east) + 21, 20):
            x = self.to_screen(lon, 0).x()
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        for lat in range(int(math.floor((south + 90) / 10)) * 10 - 90, int(north) + 11, 10):
            y = self.to_screen(0, max(-85.0, min(85.0, lat))).y()
            p.drawLine(QPointF(0, y), QPointF(self.width(), y))
        if self.highlight and cell_px >= 3:
            p.setPen(QPen(QColor(255, 255, 255, 150), 1, Qt.PenStyle.DotLine))
            for sq in self.highlight:
                r = self._rect(sq)
                if r.right() >= 0 and r.left() <= self.width() and r.bottom() >= 0 and r.top() <= self.height():
                    p.drawRect(r)
        self._draw_home(p)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_labels(p, west, south, east, north, cell_px)
        self._draw_credit(p)
        p.end()

    def _draw_home(self, p: QPainter) -> None:
        sq = grids.square(self.home)
        if not sq:
            return
        p.setPen(QPen(QColor("white"), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(self._rect(sq))
        if self.radius_km:
            lat0, lon0 = geo.grid_to_latlon(self.home) or grids.center(sq)
            dlat = self.radius_km / 111.2
            dlon = self.radius_km / (111.2 * max(0.05, math.cos(math.radians(lat0))))
            path = QPainterPath()
            for i in range(73):
                a = math.radians(i * 5)
                pt = self.to_screen(lon0 + dlon * math.cos(a), lat0 + dlat * math.sin(a))
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            p.setPen(QPen(QColor(255, 255, 255, 220), 1.3, Qt.PenStyle.DashLine))
            p.drawPath(path)

    def _halo_text(self, p: QPainter, pt: QPointF, text: str, font: QFont, color: QColor,
                   align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft) -> None:
        """Text with a dark outline, readable on any imagery."""
        path = QPainterPath()
        width = QFontMetricsF(font).horizontalAdvance(text)
        x = pt.x() - width / 2 if align == Qt.AlignmentFlag.AlignHCenter else pt.x()
        path.addText(QPointF(x, pt.y()), font, text)
        p.setPen(QPen(QColor(0, 0, 0, 170), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(path)

    def _draw_labels(self, p: QPainter, west: float, south: float, east: float, north: float, cell_px: float) -> None:
        field_px = cell_px * 10
        if field_px >= 36:
            f = QFont(self.font())
            f.setBold(True)
            f.setPointSizeF(max(9.0, min(18.0, field_px / 12)))
            for fld in grids.fields_in(west, south, east, north):
                lon = (ord(fld[0]) - 65) * 20 - 180
                lat = (ord(fld[1]) - 65) * 10 - 90 + 10
                pt = self.to_screen(lon, min(85.0, lat))
                self._halo_text(p, QPointF(pt.x() + 6, pt.y() + f.pointSizeF() * 1.6), fld, f, QColor(255, 255, 255, 230))
        if cell_px < GRID_LABEL_PX:
            return
        small = QFont(self._mono)
        small.setPointSizeF(max(7.0, min(11.0, cell_px / 7)))
        call_font = QFont(self.font())
        call_font.setBold(True)
        call_font.setPointSizeF(max(8.0, min(14.0, cell_px / 6)))
        italic = QFont(call_font)
        italic.setItalic(True)
        for sq, cell in self.cells.items():
            r = self._rect(sq)
            if r.right() < 0 or r.left() > self.width() or r.bottom() < 0 or r.top() > self.height():
                continue
            self._halo_text(p, QPointF(r.left() + 3, r.top() + small.pointSizeF() * 1.5), sq, small,
                            QColor(255, 255, 255, 235))
            if cell_px >= CALL_PX and r.height() >= 26:
                q = cell.confirmed or cell.first or {}
                call = q.get("call") or ""
                font = QFont(call_font if cell.confirmed else italic)
                width = QFontMetricsF(font).horizontalAdvance(call)
                if width > r.width() - 6:  # a long call (BA8AFK/9) shrinks to fit its square
                    font.setPointSizeF(font.pointSizeF() * (r.width() - 6) / width)
                if font.pointSizeF() >= 6.0:
                    self._halo_text(p, QPointF(r.center().x(), r.center().y() + font.pointSizeF() * 0.8), call, font,
                                    QColor("white") if cell.confirmed else QColor(255, 243, 214),
                                    Qt.AlignmentFlag.AlignHCenter)

    def _draw_credit(self, p: QPainter) -> None:
        if self.provider is None:
            text = tr("Grid only: no imagery (Preferences → Map)")
        elif self.provider.key in self.fetcher.stopped:
            text = tr("Imagery unavailable:") + " " + self.fetcher.stopped[self.provider.key]
        else:
            text = self.provider.credit
        f = QFont(self.font())
        f.setPointSizeF(9)
        p.setFont(f)
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(text) + 12
        box = QRectF(self.width() - w - 6, self.height() - fm.height() - 8, w, fm.height() + 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 120))
        p.drawRoundedRect(box, 4, 4)
        p.setPen(QColor(255, 255, 255, 220))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    # ------------------------------------------------------------------ what a square says
    def describe(self, sq: str) -> str:
        cell = self.cells.get(sq)
        if cell is None:
            return f"{sq} · " + tr("not worked")
        n_calls = len(cell.calls)
        counts = f"{cell.qsos} " + tr("QSOs") + f", {n_calls} " + tr("calls")
        if cell.confirmed:
            q = cell.confirmed
            head = tr("first confirmed by") + f" {SOURCE_NAMES[cell.source]}: {q.get('call')}"
        else:
            q = cell.first or {}
            head = tr("worked only, first:") + f" {q.get('call')}"
        where = q.get("sat_name") or q.get("band") or ""
        return " · ".join(x for x in (sq, head, (q.get("time_on") or "")[:10], where, counts) if x)

    # ------------------------------------------------------------------ interaction
    def zoom_at(self, pos: QPointF, steps: float) -> None:
        before = self.to_lonlat(pos)
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom + steps))
        bx, by = grids.to_world(*before, 0)
        k = self._scale()
        self.cx = bx - (pos.x() - self.width() / 2) / k
        self.cy = by - (pos.y() - self.height() / 2) / k
        self.update()

    def wheelEvent(self, e) -> None:  # noqa: N802 - Qt API
        self.zoom_at(e.position(), e.angleDelta().y() / 120.0 * 0.35)

    def mousePressEvent(self, e) -> None:  # noqa: N802 - Qt API
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag, self._moved = e.position(), False

    def mouseMoveEvent(self, e) -> None:  # noqa: N802 - Qt API
        if self._drag is not None:
            d = e.position() - self._drag
            if self._moved or abs(d.x()) + abs(d.y()) > 4:
                self._moved = True
                k = self._scale()
                self.cx -= d.x() / k
                self.cy -= d.y() / k
                self._drag = e.position()
                QToolTip.hideText()
                self.update()
            return
        sq = self.square_at(e.position())
        if sq != self._hover:
            self._hover = sq
            if sq:
                QToolTip.showText(e.globalPosition().toPoint(), self.describe(sq), self)
            else:
                QToolTip.hideText()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802 - Qt API
        if e.button() == Qt.MouseButton.LeftButton and self._drag is not None:
            if not self._moved:
                sq = self.square_at(e.position())
                if sq and sq in self.cells:
                    self.square_clicked.emit(sq)
            self._drag = None

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802 - Qt API
        self.zoom_at(e.position(), 1.0)

    def keyPressEvent(self, e) -> None:  # noqa: N802 - Qt API
        mid = QPointF(self.width() / 2, self.height() / 2)
        step = 80 / self._scale()
        key = e.key()
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_at(mid, 0.5)
        elif key == Qt.Key.Key_Minus:
            self.zoom_at(mid, -0.5)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            dx = {Qt.Key.Key_Left: -step, Qt.Key.Key_Right: step}.get(key, 0)
            dy = {Qt.Key.Key_Up: -step, Qt.Key.Key_Down: step}.get(key, 0)
            self.cx += dx
            self.cy += dy
            self.update()
        else:
            super().keyPressEvent(e)
