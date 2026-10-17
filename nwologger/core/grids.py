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


def collect(db, *, sources: Iterable[str] = ("lotw",), band: str | None = None, mode_group: str | None = None,
            home: str | None = None, radius_km: float | None = RADIUS_KM) -> dict[str, Square]:
    """Every square in the log, with its first worked and first confirmed QSO.

    ``band``: ``"SAT"`` for satellite QSOs, a band (``"2m"``) for that band without satellites, None for all.
    ``home`` / ``radius_km``: only QSOs made within that distance of the home square (a QSO without your grid counts
    as made at home); ``radius_km=None`` counts every location."""
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
    cols = "id, call, time_on, band, mode, submode, sat_name, gridsquare, vucc_grids, my_gridsquare, " + \
           ", ".join(c for c, _y in SOURCES.values())
    rows = db.conn.execute(f"SELECT {cols} FROM qso WHERE {' AND '.join(where)} ORDER BY time_on, id", args)
    home_ll = geo.grid_to_latlon(home) if square(home) else None
    near: dict[str, bool] = {}
    out: dict[str, Square] = {}
    for r in rows:
        q = dict(r)
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
