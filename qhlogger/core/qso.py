"""QSO dicts: cleaning, normalisation and ADIF record conversion.

A QSO is a plain ``dict`` keyed by column name (see :mod:`fields`). ``extra`` holds every ADIF field that has
no column of its own, as ``{ADIF_TAG: value}``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import bands, modes, timeutil
from .fields import BY_ADIF, BY_COL, FIELDS

GRID_RE = re.compile(r"^([A-Ra-r]{2})(\d{2})([A-Xa-x]{2})?(\d{2})?$")

PRIVATE_COLS = {"notes", "email"}
# Record tags that look like account credentials (e.g. EQSL_PSWD pasted into a QSO by another program): never
# kept on import, never written to an export or a service upload.
_CREDENTIAL_TAG = re.compile(r"PASSWORD|PASSWD|PSWD|APIKEY|API_KEY|^EQSL_USER$")


def is_credential_tag(tag: str) -> bool:
    return bool(_CREDENTIAL_TAG.search(tag.upper()))


def clean(col: str, value: Any) -> Any:
    """Coerce a value to its column's kind. Empty → None. Raises ValueError for unparsable numbers."""
    if value is None:
        return None
    f = BY_COL.get(col)
    kind = f.kind if f else "text"
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
    if kind == "int":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        return int(str(value).strip())
    if kind == "real":
        if isinstance(value, (int, float)):
            return float(value)
        return float(str(value).strip().replace(",", "."))
    if kind == "flag":
        return str(value).strip().upper()[:1] or None
    if kind == "date":
        return timeutil.adif_date_to_iso(str(value)) or str(value)
    return str(value)


def normalize_grid(grid: str | None) -> str | None:
    if not grid:
        return None
    g = grid.strip()
    m = GRID_RE.match(g)
    if not m:
        return g.upper() if g else None
    out = m.group(1).upper() + m.group(2)
    if m.group(3):
        out += m.group(3).lower()
    if m.group(4):
        out += m.group(4)
    return out


def normalize(qso: dict) -> dict:
    """Return a cleaned copy: call upper-case, ADIF mode/submode, mode group, bands from frequencies."""
    q: dict[str, Any] = {}
    for k, v in qso.items():
        if k == "extra":
            q[k] = v
            continue
        try:
            q[k] = clean(k, v)
        except (TypeError, ValueError):
            q[k] = None
    for col in ("call", "station_callsign", "operator", "contest_id", "prop_mode", "sat_name", "sat_mode",
                "cont", "iota", "sota_ref", "pota_ref", "wwff_ref", "my_pota_ref", "my_sota_ref",
                "my_wwff_ref"):
        if q.get(col):
            q[col] = str(q[col]).upper().replace(" ", "") if col in ("call", "station_callsign", "operator") \
                else str(q[col]).upper()
    if "mode" in q or "submode" in q:
        m, s = modes.normalize(q.get("mode"), q.get("submode"))
        q["mode"], q["submode"] = m, s
        q["mode_group"] = modes.group(m, s)
    if "band" in q or "freq" in q:
        b = bands.normalize_band(q.get("band")) or bands.band_for_freq(q.get("freq"))
        q["band"] = b or (q.get("band").lower() if q.get("band") else None)
    if "band_rx" in q or "freq_rx" in q:
        b = bands.normalize_band(q.get("band_rx")) or bands.band_for_freq(q.get("freq_rx"))
        q["band_rx"] = b or (q.get("band_rx").lower() if q.get("band_rx") else None)
    for col in ("gridsquare", "my_gridsquare"):
        if q.get(col):
            q[col] = normalize_grid(q[col])
    if q.get("sat_name") and not q.get("prop_mode"):
        q["prop_mode"] = "SAT"
    for col in ("time_on", "time_off"):
        if q.get(col):
            dt = timeutil.parse_iso(q[col])
            q[col] = timeutil.to_iso(dt) if dt else None
    return q


def split_name(name: str | None) -> tuple[str | None, str | None]:
    """Split ADIF NAME at the last space, so join_name gives back exactly the same text."""
    if not name:
        return None, None
    n = " ".join(name.split())
    if " " not in n:
        return n, None
    first, last = n.rsplit(" ", 1)
    return first, last


def join_name(first: str | None, last: str | None) -> str | None:
    parts = [p.strip() for p in (first, last) if p and p.strip()]
    return " ".join(parts) or None


def load_extra(qso: dict) -> dict[str, str]:
    raw = qso.get("extra")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except (TypeError, ValueError):
        return {}


def dump_extra(extra: dict[str, str]) -> str | None:
    return json.dumps(extra, ensure_ascii=False, sort_keys=True) if extra else None


# ---------------------------------------------------------------- ADIF conversion

def from_adif(rec: dict[str, str], *, program_id: str | None = None) -> dict:
    """Convert one ADIF record (tags upper-case) to a normalised QSO dict."""
    q: dict[str, Any] = {}
    extra: dict[str, str] = {}
    mldx = (program_id or "").strip().lower() == "macloggerdx"
    for tag, value in rec.items():
        tag = tag.upper()
        if value is None or value == "":
            continue
        if tag in ("QSO_DATE", "TIME_ON", "QSO_DATE_OFF", "TIME_OFF", "NAME") or is_credential_tag(tag):
            continue
        if tag == "APP_QRZLOG_STATUS":
            if value.strip().upper() == "C":
                q["qrz_qsl_rcvd"] = "Y"
            else:
                extra[tag] = value
            continue
        if mldx and tag == "QSL_SENT":
            continue  # MacLoggerDX writes Y here for every uploaded QSO; it is not a paper card
        f = BY_ADIF.get(tag)
        if f is None:
            extra[tag] = value
            continue
        try:
            q[f.col] = clean(f.col, value)
        except (TypeError, ValueError):
            extra[tag] = value
    on = timeutil.adif_to_iso(rec.get("QSO_DATE"), rec.get("TIME_ON"))
    q["time_on"] = on
    if rec.get("TIME_OFF"):
        off = timeutil.adif_to_iso(rec.get("QSO_DATE_OFF") or rec.get("QSO_DATE"), rec.get("TIME_OFF"))
        if off and on and not rec.get("QSO_DATE_OFF") and off < on:
            off = timeutil.shift_iso(off, 86400)
        q["time_off"] = off
    first, last = split_name(rec.get("NAME"))
    q["first_name"], q["last_name"] = first, last
    if not q.get("station_callsign") and q.get("operator"):
        q["station_callsign"] = q["operator"]
    q["extra"] = dump_extra(extra)
    return normalize(q)


def to_adif(qso: dict, *, include_private: bool = True, sat_name: str | None = None,
            include_service_status: bool = True) -> dict[str, str]:
    """Convert a QSO dict to an ADIF record ``{TAG: text}`` in a stable field order."""
    out: dict[str, str] = {}

    def put(tag: str, value: Any) -> None:
        if value is None or value == "":
            return
        out[tag] = _fmt(value)

    put("CALL", qso.get("call"))
    on = timeutil.iso_to_adif(qso.get("time_on"))
    if on:
        put("QSO_DATE", on[0])
        put("TIME_ON", on[1])
    off = timeutil.iso_to_adif(qso.get("time_off"))
    if off:
        put("QSO_DATE_OFF", off[0])
        put("TIME_OFF", off[1])
    put("NAME", join_name(qso.get("first_name"), qso.get("last_name")))
    for f in FIELDS:
        if not f.adif or f.col == "call":
            continue
        if not include_private and f.col in PRIVATE_COLS:
            continue
        if not include_service_status and f.group in ("LoTW", "eQSL", "QRZ", "HRDLog"):
            continue
        v = qso.get(f.col)
        if v is None or v == "":
            continue
        if f.col == "sat_name" and sat_name:
            v = sat_name
        if f.kind == "date":
            v = timeutil.iso_date_to_adif(str(v)) or v
        elif f.col in ("band", "band_rx"):
            v = str(v).upper()
        elif f.col == "freq" or f.col == "freq_rx":
            v = _fmt_freq(v)
        put(f.adif, v)
    if include_service_status and qso.get("qrz_qsl_rcvd") == "Y":
        put("APP_QRZLOG_STATUS", "C")
    for tag, v in load_extra(qso).items():
        if tag not in out and not is_credential_tag(tag):
            put(tag, v)
    return out


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:g}" if abs(v) >= 1e-4 or v == 0 else f"{v:.6f}"
    return str(v)


def _fmt_freq(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    s = f"{f:.6f}".rstrip("0").rstrip(".")
    return s


def summary(qso: dict) -> str:
    """One-line description, for messages."""
    t = (qso.get("time_on") or "").replace("T", " ").replace("Z", "")
    sat = f" via {qso['sat_name']}" if qso.get("sat_name") else ""
    return (f"{qso.get('call', '')} {t} {bands.display_band(qso.get('band'))} "
            f"{modes.display(qso.get('mode'), qso.get('submode'))}{sat}").strip()
