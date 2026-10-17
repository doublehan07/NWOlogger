"""Solar geometry for the globe's current-time day/night overlay.

The declination and equation of time follow NOAA's Meeus-based solar calculator:
https://gml.noaa.gov/grad/solcalc/calcdetails.html
https://gml.noaa.gov/grad/solcalc/main.js

This is a geometric solar-center terminator on a spherical Earth. Atmospheric
refraction, the Sun's apparent radius, terrain, and twilight are not included.
The approximation is intended for a contemporary map display.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime


def subsolar_point(when: datetime | None = None) -> tuple[float, float]:
    """Return the Sun's overhead point as (longitude east, latitude north), degrees.

    The longitude is in [-180, 180). Omitted time means the current UTC instant;
    aware datetimes are converted to UTC and naive datetimes are treated as UTC.
    """
    when = datetime.now(UTC) if when is None else when
    when = when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)
    minutes = when.hour * 60 + when.minute + (when.second + when.microsecond / 1_000_000) / 60
    julian_day = when.toordinal() + 1721424.5 + minutes / 1440
    centuries = (julian_day - 2451545.0) / 36525

    mean_longitude = (280.46646 + centuries * (36000.76983 + centuries * 0.0003032)) % 360
    mean_anomaly = math.radians(357.52911 + centuries * (35999.05029 - centuries * 0.0001537))
    eccentricity = 0.016708634 - centuries * (0.000042037 + centuries * 0.0000001267)
    center = (
        math.sin(mean_anomaly) * (1.914602 - centuries * (0.004817 + centuries * 0.000014))
        + math.sin(2 * mean_anomaly) * (0.019993 - centuries * 0.000101)
        + math.sin(3 * mean_anomaly) * 0.000289
    )
    omega = math.radians(125.04 - 1934.136 * centuries)
    apparent_longitude = math.radians(mean_longitude + center - 0.00569 - 0.00478 * math.sin(omega))
    seconds = 21.448 - centuries * (46.8150 + centuries * (0.00059 - centuries * 0.001813))
    obliquity = math.radians(23 + (26 + seconds / 60) / 60 + 0.00256 * math.cos(omega))
    declination = math.degrees(math.asin(math.sin(obliquity) * math.sin(apparent_longitude)))

    y = math.tan(obliquity / 2) ** 2
    longitude_rad = math.radians(mean_longitude)
    equation_of_time = 4 * math.degrees(
        y * math.sin(2 * longitude_rad)
        - 2 * eccentricity * math.sin(mean_anomaly)
        + 4 * eccentricity * y * math.sin(mean_anomaly) * math.cos(2 * longitude_rad)
        - 0.5 * y * y * math.sin(4 * longitude_rad)
        - 1.25 * eccentricity * eccentricity * math.sin(2 * mean_anomaly)
    )
    longitude = (180 - (minutes + equation_of_time) / 4 + 180) % 360 - 180
    return longitude, declination


def solar_altitude(longitude: float, latitude: float, when: datetime | None = None) -> float:
    """Return geometric solar-center elevation above a point's horizon, degrees."""
    if not math.isfinite(longitude) or not math.isfinite(latitude) or not -90 <= latitude <= 90:
        raise ValueError("Longitude must be finite and latitude must be between -90 and 90 degrees")
    sun_lon, sun_lat = subsolar_point(when)
    lat = math.radians(latitude)
    declination = math.radians(sun_lat)
    hour_angle = math.radians((longitude - sun_lon) % 360)
    sine = math.sin(lat) * math.sin(declination) + math.cos(lat) * math.cos(declination) * math.cos(hour_angle)
    return math.degrees(math.asin(max(-1.0, min(1.0, sine))))
