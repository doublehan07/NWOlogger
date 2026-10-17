"""Batch projection of Maidenhead cells with reusable spherical boundaries."""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Iterable

import numpy as np
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPath, QPolygonF

from ...core import globe, grids


class CellGeometry:
    """Cache camera-independent cells, then project and cull them in one batch."""

    def __init__(self, *, cache_limit: int = 6000) -> None:
        self._cache_limit = cache_limit
        self._vertices: OrderedDict[tuple[str, int, int], np.ndarray] = OrderedDict()
        self._batch_key = None
        self._batch = None

    @staticmethod
    def _subdivisions(radius: float) -> tuple[int, int]:
        # A projected chord cannot err more than its unit-sphere chord. Choose
        # segments with radius * (1 - cos(angle / 2)) <= a quarter screen pixel.
        maximum_angle = math.degrees(2 * math.acos(max(-1.0, 1 - .25 / radius)))
        return max(1, math.ceil(2 / maximum_angle)), max(1, math.ceil(1 / maximum_angle))

    def _world_batch(self, squares: tuple[str, ...], lon_count: int, lat_count: int) -> np.ndarray:
        values = []
        missing = []
        for index, square in enumerate(squares):
            key = square, lon_count, lat_count
            vertices = self._vertices.get(key)
            values.append(vertices)
            if vertices is None:
                missing.append((index, key))
            else:
                self._vertices.move_to_end(key)
        if missing:
            # Construct new cells together; per-cell trigonometry and tiny
            # NumPy allocations are much slower than these broad operations.
            bounds = np.array([grids.bounds(key[0]) for _index, key in missing])
            u = np.array([i / lon_count for i in range(lon_count)] + [1] * lat_count +
                         [1 - i / lon_count for i in range(lon_count)] + [0] * lat_count)
            v = np.array([1] * lon_count + [1 - i / lat_count for i in range(lat_count)] +
                         [0] * lon_count + [i / lat_count for i in range(lat_count)])
            longitude = np.radians(bounds[:, 0, None] + 2 * u[None, :])
            latitude = np.radians(bounds[:, 1, None] + v[None, :])
            cosine_latitude = np.cos(latitude)
            generated = np.stack((cosine_latitude * np.cos(longitude),
                                  cosine_latitude * np.sin(longitude), np.sin(latitude)), axis=-1)
            for (index, key), vertices in zip(missing, generated, strict=True):
                # Independent storage keeps the LRU memory bound meaningful.
                values[index] = self._vertices[key] = vertices.copy()
        while len(self._vertices) > self._cache_limit:
            self._vertices.popitem(last=False)
        return np.stack(values)

    def paths(self, cells: Iterable[str], camera: globe.Orthographic, radius: float,
              width: int, height: int) -> dict[str, QPainterPath]:
        """Return cell paths in widget coordinates, clipped at the limb.

        Hidden or offscreen cells have empty paths. Treat returned paths as
        read-only. Boundary sampling has at most 0.25 pixel chord error at the
        supplied radius.
        """
        squares = tuple(cells)
        result = dict.fromkeys(squares, QPainterPath())
        if not squares or radius <= 0 or width <= 0 or height <= 0:
            return result
        lon_count, lat_count = self._subdivisions(radius)
        batch_key = squares, lon_count, lat_count
        if self._batch_key != batch_key:
            self._batch = self._world_batch(squares, lon_count, lat_count)
            self._batch_key = batch_key
        longitude, latitude = math.radians(camera.center_lon), math.radians(camera.center_lat)
        sin_lon, cos_lon = math.sin(longitude), math.cos(longitude)
        sin_lat, cos_lat = math.sin(latitude), math.cos(latitude)
        world_x, world_y, world_z = (self._batch[:, :, index] for index in range(3))
        horizontal = world_x * cos_lon + world_y * sin_lon
        east = world_y * cos_lon - world_x * sin_lon
        north = world_z * cos_lat - horizontal * sin_lat
        depth = world_z * sin_lat + horizontal * cos_lat
        front = np.all(depth >= 0, axis=1)
        crossing = np.any(depth >= 0, axis=1) & ~front
        screen_x = east * radius + width / 2
        screen_y = height / 2 - north * radius
        visible = (front & (np.max(screen_x, axis=1) >= 0) & (np.min(screen_x, axis=1) <= width) &
                   (np.max(screen_y, axis=1) >= 0) & (np.min(screen_y, axis=1) <= height))
        for index in np.flatnonzero(visible):
            polygon = QPolygonF([QPointF(x, y) for x, y in zip(screen_x[index], screen_y[index], strict=True)])
            path = QPainterPath()
            path.addPolygon(polygon)
            path.closeSubpath()
            result[squares[index]] = path
        for index in np.flatnonzero(crossing):
            clipped = globe.clip_polygon((x, y, z, 0.0, 0.0) for x, y, z in
                                         zip(east[index], north[index], depth[index], strict=True))
            if len(clipped) < 3:
                continue
            polygon = QPolygonF([QPointF(width / 2 + x * radius, height / 2 - y * radius)
                                 for x, y, _depth, _u, _v in clipped])
            bounds = polygon.boundingRect()
            if bounds.right() < 0 or bounds.left() > width or bounds.bottom() < 0 or bounds.top() > height:
                continue
            path = QPainterPath()
            path.addPolygon(polygon)
            path.closeSubpath()
            result[squares[index]] = path
        return result
