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
# kept on import, never written to an export. Uploads go further and send only standard ADIF fields
# (sync.apply.upload_record), since a list of names can never be complete.
_CREDENTIAL_TAG = re.compile(r"PASSWORD|PASSWD|PSWD|PASSCODE|APIKEY|API_KEY|(^|_)(SECRET|TOKEN|SESSION|AUTH)(_|$)"
                             r"|^EQSL_USER$|^APP_\w*_(CODE|KEY)$")


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
        if col in ("freq", "freq_rx"):
            mhz = bands.parse_frequency(value)
            if mhz is None:
                raise ValueError(f"Invalid frequency: {value!r}")
            return mhz
        if isinstance(value, (int, float)):
            return float(value)
        return float(str(value).strip().replace(",", "."))
    if kind == "flag":
        return str(value).strip().upper()[:1] or None
    if kind == "date":
        return timeutil.adif_date_to_iso(str(value)) or str(value)
    return str(value)


GRID_SPLIT_RE = re.compile(r"[,;/\s]+")
GRID_PAIRS = {"gridsquare": "vucc_grids", "my_gridsquare": "my_vucc_grids"}  # one Grid field, two ADIF fields


def split_grids(text: str | None) -> list[str]:
    """The grids in one Grid field: ``OM88, OM89``, ``OM88;OM89`` or ``OM88/OM89`` (a station on a grid line gives
    two or four), each normalised, in order, without repeats."""
    out: list[str] = []
    for part in GRID_SPLIT_RE.split(str(text or "").strip()):
        g = normalize_grid(part)
        if g and g not in out:
            out.append(g)
    return out


def squares_of(text: str | None) -> list[str]:
    """The 4-character squares of the valid grids in a field, in order, without repeats."""
    out: list[str] = []
    for g in split_grids(text):
        if GRID_RE.match(g) and g[:4].upper() not in out:
            out.append(g[:4].upper())
    return out


def grid_columns(text: str | None, col: str = "gridsquare", vucc: str | None = None) -> dict:
    """What one Grid field writes: the first grid to ``col`` (GRIDSQUARE) and, when there are several, all of
    their 4-character squares to the VUCC field (ADIF's VUCC_GRIDS); one grid alone clears it."""
    vucc = vucc or GRID_PAIRS[col]
    found, squares = split_grids(text), squares_of(text)
    return {col: found[0] if found else None, vucc: ",".join(squares) if len(squares) > 1 else None}


def grids_text(q: dict, col: str = "gridsquare", vucc: str | None = None) -> str:
    """One Grid field's text for a QSO or a station profile: its grid, then the other VUCC grids
    (``OM88da, OM89``); what :func:`grid_columns` reads back."""
    vucc = vucc or GRID_PAIRS[col]
    first = (q.get(col) or "").strip()
    others = [g for g in split_grids(q.get(vucc)) if g[:4].upper() != first[:4].upper()]
    return ", ".join([first, *others] if first else others)


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
    for col, vucc in GRID_PAIRS.items():
        if q.get(col):
            if len(split_grids(q[col])) > 1:  # several grids typed or imported into the one field
                q.update(grid_columns(q[col], col, vucc))
            else:
                q[col] = normalize_grid(q[col])
        if q.get(vucc):  # ADIF: 4-character squares, comma separated, no spaces
            q[vucc] = ",".join(squares_of(q[vucc])) or None
    if q.get("sat_name") and not q.get("prop_mode"):
        q["prop_mode"] = "SAT"
    for col in ("time_on", "time_off"):
        if q.get(col):
            dt = timeutil.parse_iso(q[col])
            q[col] = timeutil.to_iso(dt) if dt else None
    return q


def infer_sent(q: dict) -> list[str]:
    """A LoTW or QRZ.com confirmation is only possible for a QSO you uploaded there, so mark it uploaded when the
    source didn't say so (MacLoggerDX only records the uploads it made itself; its ADIF export none at all).
    Changes ``q`` in place; returns the services marked ("lotw", "qrz")."""
    out = []
    if q.get("lotw_qsl_rcvd") in ("Y", "V") and q.get("lotw_qsl_sent") != "Y":
        q["lotw_qsl_sent"] = "Y"
        out.append("lotw")
    if q.get("qrz_qsl_rcvd") == "Y" and q.get("qrzcom_qso_upload_status") in (None, "", "N"):
        q["qrzcom_qso_upload_status"] = "Y"  # M (changed since the upload) still needs a re-upload
        out.append("qrz")
    return out


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

# A LoTW report's QSL fields are LoTW's, not a paper card's.
LOTW_REPORT_QSL = {"QSL_RCVD": "lotw_qsl_rcvd", "QSLRDATE": "lotw_qslrdate", "QSL_SENT": "lotw_qsl_sent",
                   "QSLSDATE": "lotw_qslsdate"}


def from_adif(rec: dict[str, str], *, program_id: str | None = None) -> dict:
    """Convert one ADIF record (tags upper-case) to a normalised QSO dict. ``program_id`` ``LoTW`` (a LoTW report):
    its QSL fields are LoTW's, and every record is a QSO uploaded there (on the date LoTW received it)."""
    q: dict[str, Any] = {}
    extra: dict[str, str] = {}
    program = (program_id or "").strip().lower()
    mldx, lotw = program == "macloggerdx", program == "lotw"
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
        if lotw and tag in LOTW_REPORT_QSL:
            q[LOTW_REPORT_QSL[tag]] = clean(LOTW_REPORT_QSL[tag], value)
            continue
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
    if lotw:
        q["lotw_qsl_sent"] = "Y"
        received = timeutil.adif_date_to_iso((rec.get("APP_LOTW_RXQSO") or "")[:10].replace("-", ""))
        if received and not q.get("lotw_qslsdate"):
            q["lotw_qslsdate"] = received
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
