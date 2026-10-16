"""Cabrillo 3.0 export."""

from __future__ import annotations

from ... import __version__
from .. import timeutil
from .engine import ContestDef, Me, exchange_values, mode_category

CATEGORY_KEYS = ["CATEGORY-OPERATOR", "CATEGORY-BAND", "CATEGORY-POWER", "CATEGORY-MODE", "CATEGORY-ASSISTED",
                 "CATEGORY-TRANSMITTER", "CATEGORY-STATION", "CATEGORY-OVERLAY"]
CATEGORY_CHOICES = {
    "CATEGORY-OPERATOR": ["SINGLE-OP", "MULTI-OP", "CHECKLOG"],
    "CATEGORY-BAND": ["ALL", "160M", "80M", "40M", "20M", "15M", "10M", "6M", "2M", "222", "432", "902", "1.2G",
                      "VHF-3-BAND", "VHF-FM-ONLY"],
    "CATEGORY-POWER": ["HIGH", "LOW", "QRP"],
    "CATEGORY-MODE": ["CW", "SSB", "RTTY", "DIGI", "FM", "MIXED"],
    "CATEGORY-ASSISTED": ["NON-ASSISTED", "ASSISTED"],
    "CATEGORY-TRANSMITTER": ["ONE", "TWO", "LIMITED", "UNLIMITED", "SWL"],
    "CATEGORY-STATION": ["FIXED", "PORTABLE", "MOBILE", "ROVER", "EXPEDITION", "HQ", "SCHOOL", "DISTRIBUTED"],
    "CATEGORY-OVERLAY": ["", "CLASSIC", "ROOKIE", "TB-WIRES", "YOUTH", "NOVICE-TECH", "OVER-50"],
}

_VHF = {"6m": "50", "4m": "70", "2m": "144", "1.25m": "222", "70cm": "432", "33cm": "902", "23cm": "1.2G",
        "13cm": "2.3G", "9cm": "3.4G", "6cm": "5.7G", "3cm": "10G", "1.25cm": "24G"}


def freq_field(q: dict) -> str:
    band = (q.get("band") or "").lower()
    if band in _VHF:
        return _VHF[band]
    f = q.get("freq")
    if f:
        return str(int(round(float(f) * 1000)))
    return {"160m": "1800", "80m": "3500", "40m": "7000", "20m": "14000", "15m": "21000", "10m": "28000"}.get(band, "0")


def mode_field(q: dict) -> str:
    m = (q.get("mode") or "").upper()
    if m == "FM":
        return "FM"
    if m == "RTTY":
        return "RY"
    cat = mode_category(q.get("mode"), q.get("submode"))
    return {"CW": "CW", "PH": "PH"}.get(cat, "DG")


def qso_line(cdef: ContestDef, q: dict, my_call: str) -> str:
    on = timeutil.parse_iso(q.get("time_on"))
    date = on.strftime("%Y-%m-%d") if on else ""
    hhmm = on.strftime("%H%M") if on else ""
    sent: list[str] = []
    rcvd = exchange_values(cdef, q)
    stexts = (q.get("stx_string") or "").split()
    ti = 0
    for f in cdef.exchange:
        kind = f.get("kind")
        if kind == "rst":
            sent.append(q.get("rst_sent") or "599")
        elif kind == "serial":
            sent.append(str(q.get("stx") or ""))
        else:
            sent.append(stexts[ti] if ti < len(stexts) else "-")
            ti += 1
    r = [rcvd.get(f["key"]) or "-" for f in cdef.exchange]
    parts = [f"{freq_field(q):>5}", f"{mode_field(q):<2}", date, hhmm, f"{my_call:<13}",
             *[f"{s:<6}" for s in sent], f"{(q.get('call') or ''):<13}", *[f"{v:<6}" for v in r]]
    return "QSO: " + " ".join(parts).rstrip()


def export(path: str, cdef: ContestDef, session: dict, qsos: list[dict], me: Me, claimed_score: int) -> int:
    data = session.get("data") or {}
    cats = data.get("categories") or {}
    lines = ["START-OF-LOG: 3.0", f"CREATED-BY: QHlogger {__version__}", f"CONTEST: {cdef.cabrillo}",
             f"CALLSIGN: {me.call}"]
    if data.get("location"):
        lines.append(f"LOCATION: {data['location']}")
    for k in CATEGORY_KEYS:
        if cats.get(k):
            lines.append(f"{k}: {cats[k]}")
    lines.append(f"CLAIMED-SCORE: {claimed_score}")
    for key, tag in (("operators", "OPERATORS"), ("club", "CLUB"), ("name", "NAME"), ("email", "EMAIL")):
        if data.get(key):
            lines.append(f"{tag}: {data[key]}")
    if me.grid:
        lines.append(f"GRID-LOCATOR: {me.grid[:6]}")
    for ln in (data.get("address") or "").splitlines()[:6]:
        if ln.strip():
            lines.append(f"ADDRESS: {ln.strip()}")
    for ln in (data.get("soapbox") or "").splitlines():
        if ln.strip():
            lines.append(f"SOAPBOX: {ln.strip()}")
    rows = sorted(qsos, key=lambda q: (q.get("time_on") or "", q["id"]))
    for q in rows:
        lines.append(qso_line(cdef, q, q.get("station_callsign") or me.call))
    lines.append("END-OF-LOG:")
    with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return len(rows)
