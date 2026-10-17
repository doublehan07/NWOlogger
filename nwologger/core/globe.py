"""Spherical geometry for a globe viewed with an orthographic projection.

Angles are in degrees. Projected coordinates use a unit globe, with east to the
right, north up and positive depth towards the viewer. No map or UI dependency
is needed for these transformations.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from .geo import EARTH_KM

ProjectedVertex = tuple[float, float, float, float, float]
_EPSILON = 1e-12


def normalize_lon(lon: float) -> float:
    """Return a finite longitude in the half-open interval [-180, 180)."""
    if not math.isfinite(lon):
        raise ValueError("Longitude must be finite")
    return (lon + 180.0) % 360.0 - 180.0


def _angles(lon: float, lat: float) -> tuple[float, float]:
    if not math.isfinite(lat) or not -90.0 <= lat <= 90.0:
        raise ValueError("Latitude must be finite and between -90 and 90 degrees")
    return math.radians(normalize_lon(lon)), math.radians(lat)


def sphere_point(lon: float, lat: float) -> tuple[float, float, float]:
    """World Cartesian position: X at 0 E, Y at 90 E, and Z at the north pole."""
    longitude, latitude = _angles(lon, lat)
    cos_lat = math.cos(latitude)
    return cos_lat * math.cos(longitude), cos_lat * math.sin(longitude), math.sin(latitude)


@dataclass(frozen=True)
class Orthographic:
    """A unit globe with the specified longitude and latitude facing the viewer."""

    center_lon: float
    center_lat: float
    _sin_lat: float = field(init=False, repr=False, compare=False)
    _cos_lat: float = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _, latitude = _angles(self.center_lon, self.center_lat)
        object.__setattr__(self, "center_lon", normalize_lon(self.center_lon))
        object.__setattr__(self, "_sin_lat", math.sin(latitude))
        object.__setattr__(self, "_cos_lat", math.cos(latitude))

    def project(self, lon: float, lat: float) -> tuple[float, float, float]:
        """Return (east, north, depth); negative depth identifies the hidden side."""
        longitude, latitude = _angles(lon, lat)
        delta = longitude - math.radians(self.center_lon)
        sin_lat, cos_lat = math.sin(latitude), math.cos(latitude)
        horizontal = cos_lat * math.cos(delta)
        return (
            cos_lat * math.sin(delta),
            self._cos_lat * sin_lat - self._sin_lat * horizontal,
            self._sin_lat * sin_lat + self._cos_lat * horizontal,
        )

    def unproject(self, x: float, y: float) -> tuple[float, float] | None:
        """Return visible (longitude, latitude), or None outside the globe disk.

        A tiny tolerance at the limb accounts for round-off from ``project``.
        The globe's far side cannot be recovered from its screen coordinates.
        """
        if not math.isfinite(x) or not math.isfinite(y):
            return None
        radius_squared = x * x + y * y
        if radius_squared > 1.0 + _EPSILON:
            return None
        if radius_squared == 0.0:
            return self.center_lon, self.center_lat
        if radius_squared > 1.0:
            radius = math.sqrt(radius_squared)
            x, y = x / radius, y / radius
            radius_squared = 1.0
        depth = math.sqrt(max(0.0, 1.0 - radius_squared))
        horizontal = depth * self._cos_lat - y * self._sin_lat
        north = y * self._cos_lat + depth * self._sin_lat
        lon = self.center_lon + math.degrees(math.atan2(x, horizontal))
        lat = math.degrees(math.atan2(north, math.hypot(x, horizontal)))
        return normalize_lon(lon), lat


def destination(lon: float, lat: float, bearing_degrees: float,
                distance_km: float) -> tuple[float, float]:
    """Travel along a great circle; bearing is clockwise from local north.

    The vector formulation also works at either pole, where the supplied
    longitude defines the orientation of the local north/east axes.
    """
    longitude, latitude = _angles(lon, lat)
    if not math.isfinite(bearing_degrees) or not math.isfinite(distance_km) or distance_km < 0:
        raise ValueError("Bearing must be finite and distance must be finite and nonnegative")
    bearing = math.radians(bearing_degrees % 360.0)
    angle = (distance_km / EARTH_KM) % math.tau
    sin_lon, cos_lon = math.sin(longitude), math.cos(longitude)
    sin_lat, cos_lat = math.sin(latitude), math.cos(latitude)
    sin_bearing, cos_bearing = math.sin(bearing), math.cos(bearing)
    cos_angle, sin_angle = math.cos(angle), math.sin(angle)
    # Position * cos(angle) + (north * cos(bearing) + east * sin(bearing)) * sin(angle).
    x = cos_lat * cos_lon * cos_angle + (-sin_lat * cos_lon * cos_bearing - sin_lon * sin_bearing) * sin_angle
    y = cos_lat * sin_lon * cos_angle + (-sin_lat * sin_lon * cos_bearing + cos_lon * sin_bearing) * sin_angle
    z = sin_lat * cos_angle + cos_lat * cos_bearing * sin_angle
    return normalize_lon(math.degrees(math.atan2(y, x))), math.degrees(math.atan2(z, math.hypot(x, y)))


def clip_polygon(vertices: Iterable[ProjectedVertex]) -> list[ProjectedVertex]:
    """Clip a small spherical polygon to the visible hemisphere.

    Each vertex is ``(x, y, depth, u, v)`` in camera coordinates, with texture
    coordinates ``u`` and ``v``. Crossing edges intersect depth zero and are
    normalized onto the globe's limb; texture coordinates interpolate along
    that chord. Edges must connect distinct, non-antipodal unit-sphere points;
    callers should subdivide large geographical polygons before clipping.
    """
    polygon = list(vertices)
    if not polygon:
        return []
    out: list[ProjectedVertex] = []

    def append(vertex: ProjectedVertex) -> None:
        if not out or vertex != out[-1]:
            out.append(vertex)

    previous = polygon[-1]
    previous_inside = previous[2] >= 0.0
    for current in polygon:
        current_inside = current[2] >= 0.0
        if current_inside != previous_inside:
            fraction = previous[2] / (previous[2] - current[2])
            cross = tuple(a + fraction * (b - a) for a, b in zip(previous, current, strict=True))
            radius = math.hypot(cross[0], cross[1])
            if radius <= _EPSILON:
                raise ValueError("Cannot clip an antipodal polygon edge")
            append((cross[0] / radius, cross[1] / radius, 0.0, cross[3], cross[4]))
        if current_inside:
            append(current)
        previous, previous_inside = current, current_inside
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out
