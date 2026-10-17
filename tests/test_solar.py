"""Offline reference values and geometry for the globe's solar terminator."""

import math
from datetime import UTC, datetime, timedelta, timezone

import pytest

from nwologger.core import solar


@pytest.mark.parametrize(("instant", "expected"), [
    ("2000-01-01T12:00:00+00:00", (0.8253147006, -23.0325159381)),
    ("2026-03-20T14:46:00+00:00", (-39.6502874631, 0.0022674933)),
    ("2026-06-21T08:24:00+00:00", (54.4479140527, 23.4381318201)),
    ("2026-09-23T00:05:00+00:00", (176.8904212086, 0.0012721975)),
    ("2026-12-21T20:50:00+00:00", (-132.9351378210, -23.4378381736)),
    ("2024-02-29T12:00:00+00:00", (3.1039415733, -7.6709355602)),
])
def test_subsolar_point_noaa_reference(instant, expected):
    # Frozen results independently evaluated using NOAA's original main.js
    # calcSunDeclination and calcEquationOfTime, with a JavaScript UTC Julian day.
    assert solar.subsolar_point(datetime.fromisoformat(instant)) == pytest.approx(expected, abs=1e-8)


@pytest.mark.parametrize("offset", [-12, 5.75, 14])
def test_same_instant_in_another_timezone(offset):
    utc = datetime(2026, 12, 31, 22, 45, 13, 450_000, tzinfo=UTC)
    local = utc.astimezone(timezone(timedelta(hours=offset)))
    assert solar.subsolar_point(local) == solar.subsolar_point(utc)


def test_naive_datetime_explicitly_means_utc():
    naive = datetime(2026, 10, 18, 9, 20, 30)
    assert solar.subsolar_point(naive) == solar.subsolar_point(naive.replace(tzinfo=UTC))


def test_omitted_time_uses_current_utc(monkeypatch):
    instant = datetime(2026, 10, 18, 9, 20, 30, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == UTC
            return instant

    monkeypatch.setattr(solar, "datetime", FrozenDatetime)
    assert solar.subsolar_point() == solar.subsolar_point(instant)


@pytest.mark.parametrize("instant", [
    datetime(2026, 3, 20, 14, 46, tzinfo=UTC),
    datetime(2026, 6, 21, 8, 24, tzinfo=UTC),
    datetime(2026, 12, 21, 20, 50, tzinfo=UTC),
])
def test_noon_midnight_and_terminator_geometry(instant):
    lon, lat = solar.subsolar_point(instant)
    assert solar.solar_altitude(lon, lat, instant) == pytest.approx(90, abs=1e-5)
    assert solar.solar_altitude(lon + 180, -lat, instant) == pytest.approx(-90, abs=1e-5)
    assert solar.solar_altitude(lon + 90, 0, instant) == pytest.approx(0, abs=1e-10)
    assert solar.solar_altitude(lon - 90, 0, instant) == pytest.approx(0, abs=1e-10)


def test_summer_and_winter_hemispheres():
    june = datetime(2026, 6, 21, 12, tzinfo=UTC)
    december = datetime(2026, 12, 21, 12, tzinfo=UTC)
    assert solar.solar_altitude(0, 90, june) > 23
    assert solar.solar_altitude(0, -90, june) < -23
    assert solar.solar_altitude(0, 90, december) < -23
    assert solar.solar_altitude(0, -90, december) > 23


@pytest.mark.parametrize("instant", [
    datetime(2023, 12, 31, 23, 59, 59, tzinfo=UTC),
    datetime(2024, 2, 28, 23, 59, 59, tzinfo=UTC),
    datetime(2024, 2, 29, 23, 59, 59, tzinfo=UTC),
])
def test_calendar_rollover_is_continuous(instant):
    lon, lat = solar.subsolar_point(instant)
    next_lon, next_lat = solar.subsolar_point(instant + timedelta(seconds=2))
    delta = (next_lon - lon + 180) % 360 - 180
    assert -0.009 < delta < -0.008  # Earth rotates about 0.0083 degrees in two seconds.
    assert abs(next_lat - lat) < 0.0001
    assert -180 <= lon < 180 and -180 <= next_lon < 180


@pytest.mark.parametrize(("lon", "lat"), [
    (math.nan, 0), (math.inf, 0), (-math.inf, 0),
    (0, math.nan), (0, math.inf), (0, 90.1), (0, -90.1),
])
def test_invalid_geographic_coordinates(lon, lat):
    with pytest.raises(ValueError):
        solar.solar_altitude(lon, lat)
