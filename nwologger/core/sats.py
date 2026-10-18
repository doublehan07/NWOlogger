"""Satellites: names accepted by LoTW, default frequencies, and per-service name mapping."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

RESOURCE = Path(__file__).resolve().parent.parent / "resources" / "sats.json"


@dataclass(frozen=True)
class Satellite:
    name: str  # LoTW SAT_NAME
    description: str
    uplink: float | None = None  # MHz, what you transmit on (FREQ)
    downlink: float | None = None  # MHz, what you receive on (FREQ_RX)
    mode: str | None = None
    sat_mode: str | None = None
    orbit: str = "LEO"  # LEO, MEO, HEO or GEO (sats.json "orbits")


DEFAULT_ORBITS = {"LEO": "Low Earth orbit", "MEO": "Medium Earth orbit", "HEO": "Highly elliptical orbit",
                  "GEO": "Geostationary orbit"}
NON_GEO = "NONGEO"  # the grid map's category spanning every orbit but GEO, as Gridmaster has it


@dataclass
class SatCatalog:
    sats: list[Satellite]
    # service → {stored name: service name}
    service_names: dict[str, dict[str, str]]
    orbits: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ORBITS))
    non_geostationary: list[str] = field(default_factory=lambda: ["LEO", "MEO", "HEO"])

    def names(self) -> list[str]:
        return [s.name for s in self.sats]

    def orbit(self, name: str | None) -> str:
        """A satellite's orbit; one not in the list (a name only in the log) counts as LEO."""
        s = self.get(name)
        return (s.orbit if s else "LEO").upper()

    def names_in(self, category: str) -> set[str]:
        """Upper-case names of the satellites in an orbit category (``LEO`` …) or in ``NONGEO``."""
        wanted = set(self.non_geostationary) if category == NON_GEO else {category}
        return {s.name.upper() for s in self.sats if s.orbit.upper() in wanted}

    def get(self, name: str | None) -> Satellite | None:
        if not name:
            return None
        n = name.upper()
        for s in self.sats:
            if s.name.upper() == n:
                return s
        return None

    def to_service(self, service: str, name: str | None) -> str | None:
        """The name a service expects for a stored satellite name (e.g. ARISS → ISS for eQSL)."""
        if not name:
            return name
        return self.service_names.get(service, {}).get(name.upper(), name)

    def from_service(self, service: str, name: str | None) -> str | None:
        """The stored name for a name that came back from a service."""
        if not name:
            return name
        rev = {v.upper(): k for k, v in self.service_names.get(service, {}).items()}
        return rev.get(name.upper(), name)


def load(path: Path = RESOURCE, overrides: dict[str, dict[str, str]] | None = None,
         extra: list[dict] | None = None) -> SatCatalog:
    """Bundled list, plus satellites you added in Prefs (``extra``) and per-service name overrides."""
    data = json.loads(path.read_text(encoding="utf-8"))
    sats = [Satellite(**s) for s in data["satellites"]]
    known = {s.name.upper() for s in sats}
    for e in extra or []:
        name = (e.get("name") or "").strip().upper()
        if name and name not in known:
            sats.append(Satellite(name=name, description=e.get("description") or "", uplink=e.get("uplink"),
                                  downlink=e.get("downlink"), mode=e.get("mode"), sat_mode=e.get("sat_mode"),
                                  orbit=(e.get("orbit") or "LEO").upper()))
            known.add(name)
    names = {svc: {k.upper(): v for k, v in m.items()} for svc, m in data.get("service_names", {}).items()}
    for svc, m in (overrides or {}).items():
        names.setdefault(svc, {}).update({k.upper(): v for k, v in m.items()})
    return SatCatalog(sats, names, orbits=dict(data.get("orbits") or DEFAULT_ORBITS),
                      non_geostationary=list(data.get("non_geostationary") or ["LEO", "MEO", "HEO"]))


@lru_cache(maxsize=1)
def default() -> SatCatalog:
    return load()
