"""Bounded-resolution satellite imagery on the globe, independent of display DPI.

Inverse projection samples a small image in array operations instead of painting
hundreds of individually transformed texture patches. Grid geometry and picking
continue to use the full-precision orthographic camera.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from ...core import globe, grids


class GlobeRaster:
    def __init__(self) -> None:
        self._key = None
        self._screen = None

    def image(self, atlas: QImage, size: QSize, radius: float, camera: globe.Orthographic, *,
              level: int, tile_left: int, tile_top: int, max_pixels: int = 480) -> QImage:
        """Project a contiguous Mercator tile atlas into a transparent viewport.

        Atlas tiles occupy 256 pixels each. ``tile_left`` may be unwrapped at
        the dateline; ``tile_top`` is the first Mercator row. The returned image
        owns its storage and is safe to retain after the atlas is replaced.
        """
        if atlas.isNull() or size.isEmpty() or radius <= 0:
            return QImage()
        key = size.width(), size.height(), radius, max_pixels
        if key != self._key:
            scale = min(1.0, max_pixels / max(size.width(), size.height()))
            width, height = max(1, math.ceil(size.width() * scale)), max(1, math.ceil(size.height() * scale))
            x = ((np.arange(width, dtype=np.float32) + .5) * (size.width() / width) - size.width() / 2) / radius
            y = (size.height() / 2 - (np.arange(height, dtype=np.float32) + .5) * (size.height() / height)) / radius
            squared = x[None, :] ** 2 + y[:, None] ** 2
            depth = np.sqrt(np.maximum(0, 1 - squared))
            self._screen = x[None, :], y[:, None], depth, squared <= 1
            self._key = key
        x, y, depth, inside = self._screen
        sin_lat, cos_lat = math.sin(math.radians(camera.center_lat)), math.cos(math.radians(camera.center_lat))
        north = y * cos_lat + depth * sin_lat
        horizontal = depth * cos_lat - y * sin_lat
        longitude = math.radians(camera.center_lon) + np.arctan2(x, horizontal)
        count = 2 ** level
        world_x = (longitude / math.tau + .5) * count
        # atan(h) is the Mercator ordinate; clamping prevents infinities at the poles.
        limit = math.sin(math.radians(grids.MAX_LAT))
        world_y = (.5 - np.arctanh(np.clip(north, -limit, limit)) / math.tau) * count
        unwrapped_x = world_x - tile_left
        tex_x = (unwrapped_x - np.floor(unwrapped_x / count) * count) * 256
        tex_y = (world_y - tile_top) * 256
        valid = (inside & (np.abs(north) <= limit) & (tex_x < atlas.width()) &
                 (tex_y >= 0) & (tex_y < atlas.height()))
        ix = np.clip(tex_x, 0, atlas.width() - 1).astype(np.int32)
        iy = np.clip(tex_y, 0, atlas.height() - 1).astype(np.int32)
        texture = atlas.convertToFormat(QImage.Format.Format_ARGB32)
        pixels = np.frombuffer(texture.constBits(), dtype=np.uint32).reshape(texture.height(), texture.width())
        output = np.where(valid, pixels[iy, ix], np.uint32(0)).astype(np.uint32)
        height, width = output.shape
        return QImage(output.data, width, height, width * 4, QImage.Format.Format_ARGB32).copy()
