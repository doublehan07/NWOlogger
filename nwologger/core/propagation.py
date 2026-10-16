"""Propagation modes (ADIF PROP_MODE) and which ones LoTW accepts.

The LoTW list is TQSL's configuration (``config.tq6`` v11.34 from lotw.arrl.org, 2026-10-16).
"""

from __future__ import annotations

# TQSL / LoTW propagation modes, in TQSL's order.
LOTW = {
    "AS": "Aircraft Scatter",
    "AUE": "Aurora-E",
    "AUR": "Aurora",
    "BS": "Back scatter",
    "ECH": "EchoLink",
    "EME": "Earth-Moon-Earth",
    "ES": "Sporadic E",
    "F2": "F2 Reflection",
    "FAI": "Field Aligned Irregularities",
    "ION": "Ionoscatter",
    "IRL": "IRLP",
    "MS": "Meteor scatter",
    "RS": "Rain scatter",
    "SAT": "Satellite",
    "TEP": "Trans-equatorial",
    "TR": "Tropospheric ducting",
}
# Further ADIF values that LoTW does not accept, offered in the dropdown.
ADIF_ONLY = {
    "GWAVE": "Ground wave",
    "INTERNET": "Internet-assisted",
    "LOS": "Line of sight",
}
# Known but not offered: a repeater is written in Notes (local) instead. A QSO imported with RPT keeps it.
NOT_OFFERED = {
    "RPT": "Terrestrial repeater or transponder",
}
ALL = {**LOTW, **ADIF_ONLY, **NOT_OFFERED}

# QSOs with these are not sent to LoTW at all; other ADIF-only values are just left out of the upload.
LOTW_SKIP = {"INTERNET", "RPT"}


def describe(code: str | None) -> str:
    return ALL.get((code or "").upper(), code or "")


def lotw_value(code: str | None) -> str | None:
    """The PROP_MODE to send to LoTW, or None to leave it out."""
    c = (code or "").upper()
    return c if c in LOTW else None
