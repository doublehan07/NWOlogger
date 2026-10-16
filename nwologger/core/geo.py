"""Maidenhead grids, bearings and great-circle distance."""

from __future__ import annotations

import math

EARTH_KM = 6371.0


def grid_to_latlon(grid: str | None) -> tuple[float, float] | None:
    """Centre of a 4/6/8-character Maidenhead locator, as (lat, lon) in degrees."""
    if not grid:
        return None
    g = grid.strip().upper()
    if len(g) < 4 or len(g) % 2:
        return None
    try:
        lon = (ord(g[0]) - 65) * 20 - 180
        lat = (ord(g[1]) - 65) * 10 - 90
        lon += int(g[2]) * 2
        lat += int(g[3])
        size_lon, size_lat = 2.0, 1.0
        if len(g) >= 6:
            lon += (ord(g[4]) - 65) * (2 / 24)
            lat += (ord(g[5]) - 65) * (1 / 24)
            size_lon, size_lat = 2 / 24, 1 / 24
        if len(g) >= 8:
            lon += int(g[6]) * (2 / 240)
            lat += int(g[7]) * (1 / 240)
            size_lon, size_lat = 2 / 240, 1 / 240
    except (ValueError, IndexError):
        return None
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return None
    return lat + size_lat / 2, lon + size_lon / 2


def bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    d = lon2 - lon1
    x = math.sin(d) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


def path(my_grid: str | None, dx_grid: str | None = None, dx_latlon: tuple[float, float] | None = None
         ) -> tuple[float, float, float] | None:
    """(short-path bearing, long-path bearing, km) from my grid to theirs."""
    a = grid_to_latlon(my_grid)
    b = grid_to_latlon(dx_grid) if dx_grid else dx_latlon
    if a is None or b is None:
        return None
    sp = bearing(a, b)
    return sp, (sp + 180) % 360, distance_km(a, b)
