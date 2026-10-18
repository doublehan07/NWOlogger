"""Per-pixel globe layers: satellite imagery, worked squares and the night side.

Each layer inverts the orthographic projection for every output pixel with array operations, split into row bands
that run on a small thread pool (NumPy releases the GIL), so imagery and squares can be drawn at the display's own
resolution. Square edges are antialiased from each edge pixel's exact screen-space footprint; the night side ends at
a narrow, clear edge (the grey line). Grid lines, outlines and text stay vector paths drawn on top.
"""

from __future__ import annotations

import math
import os
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage

from ...core import globe

COLUMNS = ROWS = 180  # 2° × 1° squares
EMPTY = COLUMNS * ROWS  # the transparent table entry
# The solar altitude where the night shade starts to show, and where it is complete: about 220 km either side of
# the terminator, as on PSK Reporter's map, so the grey line stands out at any zoom.
DAY_ALTITUDE, NIGHT_ALTITUDE = 1.0, -1.0
_WORKERS = max(1, min(8, os.cpu_count() or 1))
_POOL = ThreadPoolExecutor(_WORKERS, thread_name_prefix="globe") if _WORKERS > 1 else None


def shutdown() -> None:
    """Stop the render threads (quitting): nothing is waited for, frames in flight are dropped."""
    if _POOL is not None:
        _POOL.shutdown(wait=False, cancel_futures=True)
_PARALLEL_PIXELS = 60_000
_FORMAT = QImage.Format.Format_ARGB32_Premultiplied
# Satellite imagery shows the ocean nearly black (Blue Marble: about 12, 25, 50), so the night shade, which can
# only darken, left no visible line over the sea. Dark, blue water is lifted toward this sea blue on the day side;
# the night shade then shows the same day-to-night gradient over water as over land. Land, clouds and ice are
# brighter or not bluer than green and red, and keep their colours.
SEA = (46, 84, 140)
SEA_LIFT = 0.62  # the share of the way to SEA for dark water
SEA_LUMA = 90.0  # water this light or lighter is left alone (the lift fades out over the 25 units below it)
SEA_BLUE_MAX = 120  # and so is anything bluer than the ocean ever is (a saturated blue is a drawing, not water)
_PER_LON = 90 / math.pi  # square columns per radian of longitude (2° each)
_PER_LAT = 180 / math.pi  # square rows per radian of latitude
# Column of u = longitude / 2° + 180, which lies in (0, 360): positive, so truncation is floor.
_COLUMN = (np.arange(361, dtype=np.int32) - 90) % COLUMNS
# Row offset of v = latitude / 1° + 90 in [0, 180]; the north pole belongs to the last row.
_ROW = np.minimum(np.arange(181, dtype=np.int32), ROWS - 1) * COLUMNS


def square_index(sq: str) -> int:
    """The table entry of a 4-character square: latitude row × 180 + longitude column."""
    column = (ord(sq[0]) - 65) * 10 + int(sq[2])
    row = (ord(sq[1]) - 65) * 10 + int(sq[3])
    return row * COLUMNS + column


def _premultiplied(color: QColor) -> tuple[int, int, int, int]:
    alpha = color.alpha()
    return alpha, *(round(channel * alpha / 255) for channel in (color.red(), color.green(), color.blue()))


def _pack(channels: np.ndarray) -> np.ndarray:
    """(n, 4) alpha, red, green, blue → ARGB32 words."""
    c = np.rint(channels).astype(np.uint32)
    return c[:, 0] << 24 | c[:, 1] << 16 | c[:, 2] << 8 | c[:, 3]


class CellTable:
    """Premultiplied fill colours of the squares, with a transparent entry for every other square."""

    def __init__(self, colors: dict[str, QColor]):
        channels = np.zeros((EMPTY + 1, 4), np.float32)  # alpha, red, green, blue
        for sq, color in colors.items():
            channels[square_index(sq)] = _premultiplied(color)
        self.channels = channels
        self.packed = _pack(channels)
        self.occupied = bool(self.packed.any())


@dataclass(frozen=True)
class _Band:
    top: int  # first output row
    rows: int
    above: int  # rows of overlap computed before ``top`` (0 or 1); one more may follow the band
    y: np.ndarray  # (rows + overlap, 1) unit-disk ordinates
    depth: np.ndarray  # zero outside the disk
    outside: np.ndarray  # flat indices of pixels beyond the limb


class _Raster:
    """Unit-disk coordinates of one output raster, in row bands for the thread pool. Each band also covers the
    rows next to it, so a consumer can compare pixels across band boundaries."""

    def __init__(self, size: QSize, radius: float, scale: float):
        self.width = max(1, math.ceil(size.width() * scale))
        self.height = max(1, math.ceil(size.height() * scale))
        step_x, step_y = size.width() / self.width, size.height() / self.height
        self.pixel = (step_x + step_y) / 2 / radius  # one raster pixel in unit-disk units
        self.x = ((np.arange(self.width, dtype=np.float32) + .5) * step_x - size.width() / 2) / radius
        y = (size.height() / 2 - (np.arange(self.height, dtype=np.float32) + .5) * step_y) / radius
        count = _WORKERS if self.width * self.height >= _PARALLEL_PIXELS else 1
        edges = [round(self.height * i / count) for i in range(count + 1)]
        self.bands = []
        for top, bottom in zip(edges, edges[1:], strict=False):
            if bottom <= top:
                continue
            first, last = max(0, top - 1), min(self.height, bottom + 1)
            rows = y[first:last, None]
            squared = self.x[None, :] ** 2 + rows ** 2
            self.bands.append(_Band(top, bottom - top, top - first, rows, np.sqrt(np.maximum(0, 1 - squared)),
                                    np.flatnonzero(squared > 1)))

    def run(self, camera: globe.Orthographic, consumers) -> None:
        """Call each consumer with every band's (band, north, horizontal, relative longitude) arrays: north is the
        sine of latitude, the longitude (radians) is relative to the centre's, so it stays small and precise."""
        sin_lat, cos_lat = math.sin(math.radians(camera.center_lat)), math.cos(math.radians(camera.center_lat))

        def work(band: _Band) -> None:
            north = band.y * cos_lat + band.depth * sin_lat
            horizontal = band.depth * cos_lat - band.y * sin_lat
            relative = np.arctan2(self.x[None, :], horizontal)
            for consume in consumers:
                consume(band, north, horizontal, relative)

        if _POOL is not None and len(self.bands) > 1:
            for _ in _POOL.map(work, self.bands):
                pass
        else:
            for band in self.bands:
                work(band)


def lift_water(packed: np.ndarray) -> np.ndarray:
    """Premultiplied ARGB pixels with dark, blue water lifted toward :data:`SEA` (see there); other pixels as is."""
    a = packed >> 24
    r = ((packed >> 16) & 255).astype(np.float32)
    g = ((packed >> 8) & 255).astype(np.float32)
    b = (packed & 255).astype(np.float32)
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    # How much of a sea pixel this is: dark (fading out towards SEA_LUMA), not bluer than the ocean, and not
    # clearly greener or redder than it is blue. The gates are wide and soft, so near-black shallow water, grey
    # coastal pixels that mix land and water (they drew a dark rim around every coast) and sediment lift like the
    # open ocean, while green forest, brown soil, red desert and bright pixels keep their colours.
    dark = np.clip((SEA_LUMA - luma) / 25, 0, 1)
    blue = np.clip((b - g + 16) / 16, 0, 1) * np.clip((b - r + 16) / 16, 0, 1)
    k = (SEA_LIFT * dark * blue * (a > 0) * (b < SEA_BLUE_MAX)).astype(np.float32)
    if not k.any():
        return packed
    scale = (a / 255).astype(np.float32)  # premultiplied: the lifted colour is scaled by the pixel's alpha
    r = r + (SEA[0] * scale - r) * k
    g = g + (SEA[1] * scale - g) * k
    b = b + (SEA[2] * scale - b) * k
    return ((a << 24) | (np.rint(r).astype(np.uint32) << 16) | (np.rint(g).astype(np.uint32) << 8)
            | np.rint(b).astype(np.uint32))


def _new_image(raster: _Raster) -> tuple[QImage, np.ndarray]:
    image = QImage(raster.width, raster.height, _FORMAT)
    return image, np.frombuffer(image.bits(), np.uint32).reshape(raster.height, raster.width)


def _store(band: _Band, values: np.ndarray, out: np.ndarray) -> None:
    """Write a band's own rows (without its overlap), clearing pixels beyond the limb."""
    flat = values.ravel()
    flat[band.outside] = 0
    out[band.top:band.top + band.rows] = values[band.above:band.above + band.rows]


class GlobeRaster:
    def __init__(self) -> None:
        self._rasters: OrderedDict[tuple, _Raster] = OrderedDict()

    def _raster(self, size: QSize, radius: float, scale: float) -> _Raster:
        key = size.width(), size.height(), radius, scale
        raster = self._rasters.get(key)
        if raster is None:
            raster = self._rasters[key] = _Raster(size, radius, scale)
            while len(self._rasters) > 4:  # imagery, squares and night, plus one spare while sizes change
                self._rasters.popitem(last=False)
        else:
            self._rasters.move_to_end(key)
        return raster

    # ------------------------------------------------------------------ consumers
    @staticmethod
    def _imagery(atlas: QImage, level: int, tile_left: int, tile_top: int, camera: globe.Orthographic,
                 out: np.ndarray):
        """Sample a contiguous Mercator tile atlas: 256 pixels a tile, ``tile_left`` may be unwrapped at the dateline,
        ``tile_top`` is the first Mercator row."""
        texture = atlas if atlas.format() in (QImage.Format.Format_ARGB32, _FORMAT) else \
            atlas.convertToFormat(_FORMAT)
        pixels = np.frombuffer(texture.constBits(), np.uint32).reshape(texture.height(), texture.bytesPerLine() // 4)
        width, height = texture.width(), texture.height()
        world = 256 * 2 ** level  # pixels around the world: a power of two, so wrapping is a mask
        per_radian = world / math.tau
        # The centre's atlas column, split into whole pixels (added as integers) and a fraction, so float32 only
        # ever holds offsets within the view, precise even at the deepest level.
        center = math.radians(camera.center_lon) * per_radian + world / 2 - tile_left * 256
        whole = math.floor(center)
        fraction = center - whole
        top = world / 2 - tile_top * 256

        def consume(band, north, _horizontal, relative):
            ix = (np.floor(relative * per_radian + fraction).astype(np.int32) + whole) & (world - 1)
            # atanh(sin latitude) is the Mercator ordinate. Beyond its 85.05° limit rows fall outside the atlas;
            # negative rows become large unsigned ones, so one comparison checks both ends.
            iy = np.floor(top - np.arctanh(np.clip(north, -.999999, .999999)) * per_radian).astype(np.int32)
            iy = iy.view(np.uint32)
            valid = (ix < width) & (iy < height)
            sampled = lift_water(pixels[np.minimum(iy, height - 1), np.minimum(ix, width - 1)])
            _store(band, np.where(valid, sampled, np.uint32(0)), out)

        return consume, texture  # the texture must outlive the run

    @staticmethod
    def _cells(table: CellTable, raster: _Raster, camera: globe.Orthographic, out: np.ndarray,
               border: QColor | None):
        sin_c, cos_c = math.sin(math.radians(camera.center_lat)), math.cos(math.radians(camera.center_lat))
        edge = np.array(_premultiplied(border), np.float32) if border is not None and border.alpha() else None
        u0 = math.radians(camera.center_lon) * _PER_LON + 180
        pixel, width = raster.pixel, raster.width

        def consume(band, north, horizontal, relative):
            u = relative * _PER_LON + u0
            v = np.arcsin(np.clip(north, -1, 1)) * _PER_LAT + 90
            index = _ROW[v.astype(np.int32)] + _COLUMN[u.astype(np.int32)]
            colors = table.packed[index]
            colors.ravel()[band.outside] = 0
            # Antialiasing: only pixels next to a different colour sit on a visible edge (with a border, every
            # edge of a filled square). Each blends with the squares across its nearest edges by how far its
            # centre is from them, from the exact screen-space derivatives of the projection.
            mark = np.zeros(colors.shape, bool)
            for a, b in (((slice(None), slice(1, None)), (slice(None), slice(None, -1))),
                         ((slice(1, None),), (slice(None, -1),))):
                across = colors[a] != colors[b]
                if edge is not None:
                    across |= (index[a] != index[b]) & ((colors[a] | colors[b]) != 0)
                mark[a] |= across
                mark[b] |= across
            mark.ravel()[band.outside] = False
            flat = np.flatnonzero(mark[band.above:band.above + band.rows]) + band.above * width
            if flat.size:
                x = raster.x[flat % width]
                y = band.y[flat // width, 0]
                d = np.maximum(band.depth.ravel()[flat], 1e-4)
                h = horizontal.ravel()[flat]
                pu, pv = u.ravel()[flat], v.ravel()[flat]
                own = index.ravel()[flat]
                column, row = own % COLUMNS, own // COLUMNS
                fu, fv = pu - np.floor(pu), pv - np.floor(pv)
                side_u = np.mod(column + np.where(fu >= .5, 1, -1), COLUMNS)
                side_v = row + np.where(fv >= .5, 1, -1)
                on_globe = (side_v >= 0) & (side_v < ROWS)
                across_u = row * COLUMNS + side_u
                across_v = np.where(on_globe, side_v * COLUMNS + column, EMPTY)
                diagonal = np.where(on_globe, side_v * COLUMNS + side_u, EMPTY)
                dd_dx, dd_dy = -x / d, -y / d
                cos2 = np.maximum(x * x + h * h, 1e-12)
                dlat = np.hypot(sin_c * dd_dx, cos_c + sin_c * dd_dy) / np.sqrt(cos2)
                dlon = np.hypot(h - x * cos_c * dd_dx, x * (cos_c * dd_dy - sin_c)) / cos2
                du = np.minimum(fu, 1 - fu) / np.maximum(dlon * _PER_LON * pixel, 1e-9)
                dv = np.minimum(fv, 1 - fv) / np.maximum(dlat * _PER_LAT * pixel, 1e-9)
                wu = np.clip(.5 + du, 0, 1)[:, None]
                wv = np.clip(.5 + dv, 0, 1)[:, None]
                ch = table.channels
                mixed = (ch[own] * (wu * wv) + ch[across_u] * ((1 - wu) * wv) +
                         ch[across_v] * (wu * (1 - wv)) + ch[diagonal] * ((1 - wu) * (1 - wv)))
                if edge is not None:
                    # A line one pixel wide on every edge of a filled square.
                    filled = table.packed != 0
                    line_u = np.where(filled[own] | filled[across_u], np.clip(1 - du, 0, 1), 0)
                    line_v = np.where(filled[own] | filled[across_v], np.clip(1 - dv, 0, 1), 0)
                    line = np.maximum(line_u, line_v)[:, None]
                    mixed = mixed * (1 - line * (edge[0] / 255)) + line * edge
                colors.ravel()[flat] = _pack(mixed)
            out[band.top:band.top + band.rows] = colors[band.above:band.above + band.rows]

        return consume

    # ------------------------------------------------------------------ layers
    def layers(self, size: QSize, radius: float, camera: globe.Orthographic, *, scale: float,
               atlas: QImage | None = None, level: int = 0, tile_left: int = 0, tile_top: int = 0,
               image_scale: float | None = None, table: CellTable | None = None,
               border: QColor | None = None) -> tuple[QImage, QImage]:
        """The imagery and squares layers (null images when not asked for), each covering the whole viewport.

        Squares use ``scale`` pixels per viewport unit, edged by a ``border`` line one pixel wide; imagery uses ``image_scale`` (default: the same), lower when the tiles are coarser than the
        screen, to be drawn smoothly scaled. Matching scales share one pass.
        """
        imagery, cells = QImage(), QImage()
        if size.isEmpty() or radius <= 0:
            return imagery, cells
        image_scale = scale if image_scale is None else image_scale
        consumers, keep = [], []
        raster = self._raster(size, radius, scale)
        if table is not None and table.occupied:
            cells, out = _new_image(raster)
            consumers.append(self._cells(table, raster, camera, out, border))
        if atlas is not None and not atlas.isNull():
            image_raster = raster if image_scale == scale else self._raster(size, radius, image_scale)
            imagery, out = _new_image(image_raster)
            consume, texture = self._imagery(atlas, level, tile_left, tile_top, camera, out)
            keep.append(texture)
            if image_raster is raster:
                consumers.append(consume)
            else:
                image_raster.run(camera, [consume])
        if consumers:
            raster.run(camera, consumers)
        return imagery, cells

    def image(self, atlas: QImage, size: QSize, radius: float, camera: globe.Orthographic, *,
              level: int, tile_left: int, tile_top: int, scale: float = 1.0) -> QImage:
        """Project a contiguous Mercator tile atlas into a transparent viewport-sized image."""
        return self.layers(size, radius, camera, scale=scale, atlas=atlas, level=level, tile_left=tile_left,
                           tile_top=tile_top)[0]

    def cells(self, table: CellTable, size: QSize, radius: float, camera: globe.Orthographic, *,
              scale: float = 1.0, border: QColor | None = None) -> QImage:
        return self.layers(size, radius, camera, scale=scale, table=table, border=border)[1]

    def night(self, size: QSize, radius: float, camera: globe.Orthographic, sun: tuple[float, float],
              color: QColor, *, max_pixels: int = 560) -> QImage:
        """The night side as a small premultiplied overlay, to be drawn smoothly scaled over the whole viewport.

        Its opacity follows the geometric solar altitude: none above :data:`DAY_ALTITUDE`, ``color``'s alpha below
        :data:`NIGHT_ALTITUDE`, with a smooth step between. Pixels beyond the limb repeat the limb's value, so
        scaling up leaves no seam inside the disk.
        """
        if size.isEmpty() or radius <= 0:
            return QImage()
        raster = self._raster(size, radius, min(1.0, max_pixels / max(size.width(), size.height())))
        sun_x, sun_y, sun_z = camera.project(*sun)
        high, low = math.sin(math.radians(DAY_ALTITUDE)), math.sin(math.radians(NIGHT_ALTITUDE))
        image, out = _new_image(raster)
        alpha_max = color.alpha()

        def work(band: _Band) -> None:
            x = np.broadcast_to(raster.x[None, :], band.depth.shape)
            y = np.broadcast_to(band.y, band.depth.shape)
            length = np.maximum(1.0, np.hypot(x, y))
            altitude = (x * sun_x + y * sun_y) / length + band.depth * sun_z
            t = np.clip((high - altitude) / (high - low), 0, 1)
            alpha = (t * t * (3 - 2 * t) * alpha_max)[band.above:band.above + band.rows].reshape(-1, 1)
            channels = np.concatenate([alpha, *(alpha * (c / 255) for c in (color.red(), color.green(),
                                                                          color.blue()))], axis=1)
            out[band.top:band.top + band.rows] = _pack(channels).reshape(band.rows, raster.width)

        if _POOL is not None and len(raster.bands) > 1:
            for _ in _POOL.map(work, raster.bands):
                pass
        else:
            for band in raster.bands:
                work(band)
        return image
