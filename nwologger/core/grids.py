"""Maidenhead squares for the grid map: square ↔ latitude / longitude, Web Mercator, region presets, and for every
square the first QSO that confirmed it.

A square is the 4-character locator (``ON80``): 2° of longitude by 1° of latitude. A QSO counts for the square of its
``gridsquare`` and for every square in ``vucc_grids`` (a station on a grid line). "Confirmed" means confirmed by one
of the sources you choose (LoTW, eQSL, QRZ.com, paper), and the first is the one with the earliest Time On.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import geo

RESOURCES = Path(__file__).resolve().parent.parent / "resources" / "grids"
MAX_LAT = 85.05112878  # Web Mercator stops here
_SQUARE = re.compile(r"^[A-R]{2}\d{2}$")

# Which QSL column says a source confirmed the QSO.
SOURCES = {"lotw": ("lotw_qsl_rcvd", ("Y", "V")), "eqsl": ("eqsl_qsl_rcvd", ("Y",)),
           "qrz": ("qrz_qsl_rcvd", ("Y",)), "paper": ("qsl_rcvd", ("Y",))}
SOURCE_NAMES = {"lotw": "LoTW", "eqsl": "eQSL", "qrz": "QRZ.com", "paper": "Paper"}
RADIUS_KM = 200.0  # VUCC and GridMaster: every QSO from within 200 km of one place

# Region presets: (west, south, east, north) in degrees. "home" is centred on your grid.
REGIONS = {
    "home": None,
    "china": (72.0, 16.0, 136.0, 55.0),
    "conus": (-126.0, 24.0, -66.0, 50.0),
    "europe": (-26.0, 34.0, 46.0, 72.0),
    "japan": (122.0, 24.0, 148.0, 46.0),
    "world": (-180.0, -60.0, 180.0, 76.0),
}
REGION_NAMES = {"home": "Around my grid", "china": "China & around", "conus": "CONUS 488 (GridMaster)",
                "europe": "Europe", "japan": "Japan", "world": "World"}


def square(text: str | None) -> str | None:
    """``on80QW`` → ``ON80``; None when it isn't a locator."""
    s = (text or "").strip().upper()[:4]
    return s if _SQUARE.match(s) else None


def squares_of(gridsquare: str | None, vucc_grids: str | None = None) -> list[str]:
    """The squares a QSO counts for: its grid, and each grid of VUCC_GRIDS (``FN31,FN41``)."""
    out: list[str] = []
    for part in [gridsquare or "", *(vucc_grids or "").split(",")]:
        s = square(part)
        if s and s not in out:
            out.append(s)
    return out


def bounds(sq: str) -> tuple[float, float, float, float]:
    """(west, south, east, north) of a square."""
    lon = (ord(sq[0]) - 65) * 20 - 180 + int(sq[2]) * 2
    lat = (ord(sq[1]) - 65) * 10 - 90 + int(sq[3])
    return float(lon), float(lat), float(lon + 2), float(lat + 1)


def center(sq: str) -> tuple[float, float]:
    """(latitude, longitude) of a square's centre."""
    w, s, e, n = bounds(sq)
    return (s + n) / 2, (w + e) / 2


def squares_in(west: float, south: float, east: float, north: float) -> Iterator[str]:
    """Every square that overlaps the box."""
    w = max(-180.0, west)
    e = min(180.0 - 1e-9, east)
    s = max(-90.0, south)
    n = min(90.0 - 1e-9, north)
    for lon in range(int(math.floor((w + 180) / 2)), int(math.floor((e + 180) / 2)) + 1):
        for lat in range(int(math.floor(s + 90)), int(math.floor(n + 90)) + 1):
            fx, sx = divmod(lon, 10)
            fy, sy = divmod(lat, 10)
            yield f"{chr(65 + fx)}{chr(65 + fy)}{sx}{sy}"


def fields_in(west: float, south: float, east: float, north: float) -> Iterator[str]:
    """Every field (``OM``) that overlaps the box."""
    for fx in range(int(math.floor((max(-180.0, west) + 180) / 20)), int(math.floor((min(179.999, east) + 180) / 20)) + 1):
        for fy in range(int(math.floor((max(-90.0, south) + 90) / 10)), int(math.floor((min(89.999, north) + 90) / 10)) + 1):
            yield f"{chr(65 + fx)}{chr(65 + fy)}"


# ------------------------------------------------------------------ Web Mercator (the tiles' projection)
def to_world(lon: float, lat: float, zoom: float) -> tuple[float, float]:
    """Pixel position at ``zoom`` (256 × 2^zoom pixels around the world)."""
    size = 256.0 * 2 ** zoom
    lat = max(-MAX_LAT, min(MAX_LAT, lat))
    r = math.radians(lat)
    return (lon + 180.0) / 360.0 * size, (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * size


def from_world(x: float, y: float, zoom: float) -> tuple[float, float]:
    """(longitude, latitude) of a pixel position at ``zoom``."""
    size = 256.0 * 2 ** zoom
    lon = x / size * 360.0 - 180.0
    n = math.pi - 2 * math.pi * y / size
    return lon, math.degrees(math.atan(math.sinh(n)))


@lru_cache(maxsize=1)
def conus() -> frozenset[str]:
    """GridMaster's 488 squares of the contiguous United States."""
    return frozenset(json.loads((RESOURCES / "conus488.json").read_text(encoding="utf-8"))["grids"])


def region_box(name: str, home: str | None = None) -> tuple[float, float, float, float]:
    box = REGIONS.get(name)
    if box is not None:
        return box
    sq = square(home) or "JN58"
    lat, lon = center(sq)
    return lon - 16, lat - 9, lon + 16, lat + 9


# ------------------------------------------------------------------ what the log says about each square
@dataclass
class Square:
    square: str
    qsos: int = 0
    calls: set[str] = field(default_factory=set)
    first: dict | None = None  # the first QSO worked there
    confirmed: dict | None = None  # the first QSO confirmed by a chosen source
    source: str | None = None  # which source confirmed that one

    @property
    def status(self) -> str:
        return "confirmed" if self.confirmed else "worked"


def _confirmed_by(q: dict, sources: Iterable[str]) -> str | None:
    for s in sources:
        col, yes = SOURCES[s]
        if q.get(col) in yes:
            return s
    return None


def location_key(q: dict) -> str:
    """Where a QSO was made, as the log records it: its MY_VUCC_GRIDS (``OM32,OM33``, a station on a grid line)
    or its MY_GRIDSQUARE, upper case; "" when the log doesn't say."""
    vucc = (q.get("my_vucc_grids") or "").upper().replace(" ", "")
    return vucc or (q.get("my_gridsquare") or "").strip().upper()


def my_station_places(db) -> list[tuple[str, str, int]]:
    """Every station callsign + place pair in the log with its QSO count, most QSOs first; the place is the
    :func:`location_key` (case folded, so ON80eb and ON80EB are one row), "" when the QSOs don't say.
    Preferences → Station profiles → Add from my log… offers a profile for each."""
    counts: dict[tuple[str, str], int] = {}
    for call, grid, vucc, n in db.conn.execute(
            "SELECT UPPER(station_callsign), UPPER(COALESCE(my_gridsquare, '')), UPPER(COALESCE(my_vucc_grids, '')), "
            "COUNT(*) FROM qso WHERE station_callsign IS NOT NULL AND station_callsign != '' GROUP BY 1, 2, 3"):
        key = (call, location_key({"my_gridsquare": grid, "my_vucc_grids": vucc}))
        counts[key] = counts.get(key, 0) + n
    return sorted(((c, p, n) for (c, p), n in counts.items()), key=lambda r: (-r[2], r[0], r[1]))


def my_stations(db) -> list[tuple[str, int]]:
    """Every station callsign in the log with its QSO count, most QSOs first: one operator often holds more than
    one call (BI1NWO is also AI6OT), and a portable call (VR2/BD1AJF) is its own entry, as logged."""
    rows = db.conn.execute("SELECT UPPER(station_callsign) AS c, COUNT(*) FROM qso WHERE station_callsign IS NOT NULL "
                           "AND station_callsign != '' GROUP BY c ORDER BY 2 DESC, c")
    return [(r[0], r[1]) for r in rows]


def my_locations(db, calls: Iterable[str] | None = None) -> list[tuple[str, int]]:
    """Every place the log says you operated from (see :func:`location_key`), with its QSO count, most QSOs first,
    limited to the given station calls when some are given."""
    calls = [c.upper() for c in calls] if calls is not None else None
    sql = ("SELECT k, COUNT(*) FROM (SELECT UPPER(COALESCE(NULLIF(REPLACE(my_vucc_grids, ' ', ''), ''), "
           "my_gridsquare)) AS k, UPPER(station_callsign) AS c FROM qso) WHERE k IS NOT NULL AND k != ''")
    args: list = []
    if calls:
        sql += f" AND (c IS NULL OR c = '' OR c IN ({','.join('?' * len(calls))}))"
        args.extend(calls)
    rows = db.conn.execute(sql + " GROUP BY k ORDER BY 2 DESC, k", args)
    return [(r[0], r[1]) for r in rows]


def distance_from(home: str | None, key: str) -> float | None:
    """Kilometres from the home grid's centre to a location's first grid: 0 inside the home square, which VUCC
    counts as one place; None without a home or a readable grid."""
    first = key.split(",")[0].strip() if key else ""
    if not home or not first:
        return None
    if square(home) is not None and square(home) == square(first):
        return 0.0
    a, b = geo.grid_to_latlon(home), geo.grid_to_latlon(first)
    return geo.distance_km(a, b) if a is not None and b is not None else None


def within(keys: Iterable[str], home: str | None, radius_km: float = RADIUS_KM) -> list[str]:
    """The locations whose first grid lies within ``radius_km`` of the home grid: what VUCC and GridMaster count."""
    out = []
    for k in keys:
        d = distance_from(home, k)
        if d is not None and d <= radius_km:
            out.append(k)
    return out


def collect(db, *, sources: Iterable[str] = ("lotw",), band: str | None = None, mode_group: str | None = None,
            home: str | None = None, radius_km: float | None = RADIUS_KM, sat_names: Iterable[str] | None = None,
            station_calls: Iterable[str] | None = None, my_grids: Iterable[str] | None = None) -> dict[str, Square]:
    """Every square in the log, with its first worked and first confirmed QSO.

    ``band``: ``"SAT"`` for satellite QSOs, a band (``"2m"``) for that band without satellites, None for all.
    ``sat_names``: only QSOs via these satellites (upper-case names; a category's names from the catalog).
    ``station_calls``: only QSOs made under these calls (a QSO without a station call counts for any).
    ``my_grids``: only QSOs made from these locations (:func:`location_key`; a QSO without your grid counts for
    any), which is how VUCC's 200 km rule is applied: tick the locations within it.
    ``home`` / ``radius_km``: the older rule, only QSOs made within that distance of the home square; ``None``
    counts every location."""
    sources = [s for s in ("lotw", "eqsl", "qrz", "paper") if s in set(sources)]
    where = ["((gridsquare IS NOT NULL AND gridsquare != '') OR (vucc_grids IS NOT NULL AND vucc_grids != ''))"]
    args: list = []
    if band == "SAT":
        where.append("(prop_mode = 'SAT' OR (sat_name IS NOT NULL AND sat_name != ''))")
    elif band:
        where.append("band = ? AND (prop_mode IS NULL OR prop_mode != 'SAT') AND (sat_name IS NULL OR sat_name = '')")
        args.append(band)
    if mode_group:
        where.append("mode_group = ?")
        args.append(mode_group)
    if sat_names is not None:
        names = sorted({n.upper() for n in sat_names})
        where.append(f"UPPER(sat_name) IN ({','.join('?' * len(names))})" if names else "0")
        args.extend(names)
    calls = {c.upper() for c in station_calls} if station_calls is not None else None
    places = {k.upper() for k in my_grids} if my_grids is not None else None
    cols = "id, call, time_on, band, mode, submode, sat_name, gridsquare, vucc_grids, my_gridsquare, " \
           "my_vucc_grids, station_callsign, " + ", ".join(c for c, _y in SOURCES.values())
    rows = db.conn.execute(f"SELECT {cols} FROM qso WHERE {' AND '.join(where)} ORDER BY time_on, id", args)
    home_ll = geo.grid_to_latlon(home) if square(home) else None
    near: dict[str, bool] = {}
    out: dict[str, Square] = {}
    for r in rows:
        q = dict(r)
        if calls is not None:
            mine_call = (q.get("station_callsign") or "").upper()
            if mine_call and mine_call not in calls:
                continue
        if places is not None:
            key = location_key(q)
            if key and key not in places:
                continue
        if radius_km is not None and home_ll is not None:
            mine = (q.get("my_gridsquare") or "").strip().upper()
            if mine and mine not in near:
                ll = geo.grid_to_latlon(mine)
                near[mine] = ll is None or geo.distance_km(home_ll, ll) <= radius_km
            if mine and not near[mine]:
                continue
        src = _confirmed_by(q, sources)
        for sq in squares_of(q.get("gridsquare"), q.get("vucc_grids")):
            cell = out.get(sq) or out.setdefault(sq, Square(sq))
            cell.qsos += 1
            cell.calls.add(q.get("call") or "")
            if cell.first is None:
                cell.first = q
            if src and cell.confirmed is None:
                cell.confirmed, cell.source = q, src
    return out
