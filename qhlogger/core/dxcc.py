"""Country / DXCC resolution from the AD1C ``cty.csv`` country file (country-files.com).

Format, one entity per line::

    prefix,name,dxcc,cont,cqz,ituz,lat,lon,utc_offset,PFX PFX(cq)[itu] =EXACTCALL ...;

Longitudes are positive to the West in this file; they are converted to the usual East-positive sign here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from . import callsign

RESOURCE = Path(__file__).resolve().parent.parent / "resources" / "cty.csv"
USER_COPY = Path.home() / "Documents" / "QHlogger" / "cty.csv"
CTY_URL = "https://www.country-files.com/bigcty/cty.csv"

_OVR = re.compile(r"\((\d+)\)|\[(\d+)\]|<([-\d.]+)/([-\d.]+)>|\{([A-Z]{2})\}|~([-\d.]+)~")


@dataclass(frozen=True)
class Entity:
    prefix: str
    name: str
    dxcc: int
    cont: str
    cqz: int
    ituz: int
    lat: float
    lon: float
    utc_offset: float


@dataclass(frozen=True)
class Resolution:
    country: str
    dxcc: int
    cont: str
    cqz: int
    ituz: int
    lat: float
    lon: float
    utc_offset: float
    prefix: str


class CtyDB:
    def __init__(self, text: str):
        self.prefixes: dict[str, Resolution] = {}
        self.exact: dict[str, Resolution] = {}
        self.entities: dict[int, str] = {}
        self._max_prefix = 1
        self._load(text)

    @classmethod
    def from_file(cls, path: str | Path = RESOURCE) -> CtyDB:
        return cls(Path(path).read_text(encoding="utf-8", errors="replace"))

    def _load(self, text: str) -> None:
        star_names: dict[int, str] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            parts = line.split(",", 9)
            if len(parts) < 10:
                continue
            pfx, name, dxcc, cont, cqz, ituz, lat, lon, tz, aliases = parts
            try:
                ent = Entity(pfx.lstrip("*"), name, int(dxcc), cont, int(cqz), int(ituz), float(lat),
                             -float(lon), -float(tz))
            except ValueError:
                continue
            if pfx.startswith("*"):
                star_names.setdefault(ent.dxcc, name)
            else:
                self.entities.setdefault(ent.dxcc, name)
            base = Resolution(name, ent.dxcc, ent.cont, ent.cqz, ent.ituz, ent.lat, ent.lon, ent.utc_offset,
                              ent.prefix)
            for token in aliases.rstrip(";").split():
                exact = token.startswith("=")
                t = token.lstrip("=")
                res = base
                m_end = len(t)
                for m in _OVR.finditer(t):
                    m_end = min(m_end, m.start())
                    if m.group(1):
                        res = replace(res, cqz=int(m.group(1)))
                    elif m.group(2):
                        res = replace(res, ituz=int(m.group(2)))
                    elif m.group(3):
                        res = replace(res, lat=float(m.group(3)), lon=-float(m.group(4)))
                    elif m.group(5):
                        res = replace(res, cont=m.group(5))
                    elif m.group(6):
                        res = replace(res, utc_offset=-float(m.group(6)))
                key = t[:m_end].upper()
                if not key:
                    continue
                if exact:
                    self.exact[key] = res
                else:
                    self.prefixes[key] = res
                    self._max_prefix = max(self._max_prefix, len(key))
        for dx, name in star_names.items():
            self.entities.setdefault(dx, name)

    def resolve(self, call: str) -> Resolution | None:
        c = callsign.clean(call)
        if not c:
            return None
        r = self._find(c)
        if r is None:
            return None
        # CQ-only regions (e.g. European Turkey) report their DXCC entity's name as the country.
        name = self.entities.get(r.dxcc)
        return replace(r, country=name) if name and name != r.country else r

    def _find(self, c: str) -> Resolution | None:
        if c in self.exact:
            return self.exact[c]
        s = callsign.dxcc_lookup_string(c)
        if s is None:
            return None
        if s in self.exact:
            return self.exact[s]
        for n in range(min(len(s), self._max_prefix), 0, -1):
            r = self.prefixes.get(s[:n])
            if r is not None:
                return r
        return None

    def entity_name(self, dxcc: int | None) -> str | None:
        return self.entities.get(dxcc) if dxcc is not None else None


@lru_cache(maxsize=1)
def default() -> CtyDB:
    """The newer of the downloaded copy (Documents/QHlogger/cty.csv) and the bundled file."""
    if USER_COPY.exists() and USER_COPY.stat().st_size > 50_000:
        try:
            return CtyDB.from_file(USER_COPY)
        except (OSError, ValueError):
            pass
    return CtyDB.from_file(RESOURCE)


def download(dest: Path = USER_COPY) -> int:
    """Fetch the latest Big CTY country file. Network only; call ``default.cache_clear()`` afterwards."""
    import httpx  # noqa: PLC0415

    r = httpx.get(CTY_URL, timeout=60, follow_redirects=True)
    r.raise_for_status()
    text = r.text
    CtyDB(text)  # parse check before replacing
    if text.count("\n") < 300:
        raise ValueError("country file looks incomplete")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dest)
    return text.count("\n")


def user_copy_age_days() -> float | None:
    import time  # noqa: PLC0415

    if not USER_COPY.exists():
        return None
    return (time.time() - USER_COPY.stat().st_mtime) / 86400
