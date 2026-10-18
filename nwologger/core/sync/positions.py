"""Where downloads continue: each service's resume positions, read, described and written in one place.

LoTW keeps two (confirmations since and uploads since: LoTW's ``APP_LoTW_LASTQSL`` / ``LASTQSORX`` values,
``YYYY-MM-DD HH:MM:SS``, inclusive), eQSL one (cards received since, a day, ``YYYYMMDD``). QRZ.com has none:
every download is the whole logbook, and an unfinished run continues from a log id. HRDLog has no download.
Positions are set by downloads, by an imported LoTW report, by an imported log that carries confirmations (only
where none exists yet) and by hand in Preferences → Sync positions.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .. import timeutil

log = logging.getLogger("nwologger.sync")

SERVICE_NAMES = {"lotw": "LoTW", "eqsl": "eQSL", "qrz": "QRZ.com", "hrdlog": "HRDLog"}


@dataclass(frozen=True)
class Position:
    service: str
    key: str
    label: str
    kind: str  # "stamp": YYYY-MM-DD HH:MM:SS · "day": YYYYMMDD


POSITIONS = [
    Position("lotw", "last_qsl", "Confirmations since", "stamp"),
    Position("lotw", "last_qsorx", "Uploads since", "stamp"),
    Position("eqsl", "rcvd_since", "Cards since", "day"),
]
KINDS = {(p.service, p.key): p.kind for p in POSITIONS}
ORIGIN = ":origin"  # sync_state key suffix: where a position came from


def normalize(kind: str, text: str | None) -> str | None:
    """A typed or imported value in the form the service wants; empty means from the start. Raises ValueError."""
    t = (text or "").strip()
    if not t:
        return None
    if kind == "day":
        m = re.fullmatch(r"(\d{4})-?(\d\d)-?(\d\d)", t)
        if not m:
            raise ValueError(t)
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%Y%m%d")
    m = re.fullmatch(r"(\d{4})-(\d\d)-(\d\d)(?:[ T](\d\d):(\d\d)(?::(\d\d))?)?Z?", t)
    if not m:
        raise ValueError(t)
    parts = [int(x) for x in m.groups(default="0")]
    return datetime(*parts).strftime("%Y-%m-%d %H:%M:%S")


def read(db) -> list[dict]:
    """Every position and the state that goes with it, for the Preferences tab: the three editable positions, then
    read-only rows (LoTW's yearly parts, QRZ.com's log id, the last download and upload of each service)."""
    rows = []
    for p in POSITIONS:
        rows.append({"service": p.service, "key": p.key, "label": p.label, "kind": p.kind,
                     "value": db.sync_get(p.service, p.key) or "", "editable": True,
                     "origin": db.sync_get(p.service, p.key + ORIGIN) or ""})
    run, done, floor = (db.sync_get("lotw", k) for k in ("run", "parts_done", "parts_floor"))
    rows.append({"service": "lotw", "key": "parts", "label": "Yearly parts to do", "kind": "text", "editable": False,
                 "value": (f"{done or '-'} done, back to {floor}" if run else ""), "origin": run or ""})
    for key in db.sync_keys("qrz", "after_logid:"):
        rows.append({"service": "qrz", "key": key, "label": "Continue from log id", "kind": "text", "editable": False,
                     "value": db.sync_get("qrz", key) or "", "origin": key.split(":", 1)[1]})
    for svc in ("lotw", "eqsl", "qrz", "hrdlog"):
        for key, label in (("last_download", "Last download"), ("last_upload", "Last upload")):
            v = db.sync_get(svc, key)
            if v:
                rows.append({"service": svc, "key": key, "label": label, "kind": "text", "editable": False,
                             "value": v, "origin": ""})
    took = db.sync_get("lotw", "full_seconds")
    if took:
        rows.append({"service": "lotw", "key": "full_seconds", "label": "Full download took", "kind": "text",
                     "editable": False, "value": f"{took} s", "origin": ""})
    return rows


def write(db, changes: dict[tuple[str, str], str | None], *, origin: str, backup: bool = True) -> list[str]:
    """Check and write positions (``(service, key) → value or None``), with a backup of the log first, and a line
    in the diagnostic log per change. Raises ValueError for a value that is not a date."""
    normalized = {k: normalize(KINDS[k], v) for k, v in changes.items()}
    lines = []
    if backup and any(db.sync_get(*k) != v for k, v in normalized.items()):
        db.backup("positions")
    with db.batch("Sync positions"):
        for (svc, key), value in normalized.items():
            old = db.sync_get(svc, key)
            if old == value:
                continue
            db.sync_set(svc, key, value)
            db.sync_set(svc, key + ORIGIN, origin)
            lines.append(f"{svc}/{key} {old or 'the start'} → {value or 'the start'}, {origin}")
    for ln in lines:
        log.info("Sync position: %s", ln)
    return lines


def note_download(db, service: str, keys: tuple[str, ...], when: str) -> None:
    """A download set these positions."""
    for key in keys:
        db.sync_set(service, key + ORIGIN, f"download {when}")


# ------------------------------------------------------------------ positions an import proposes
DATE_COLUMNS = {
    "lotw_qsl": "lotw_qslrdate", "lotw_sent": "lotw_qslsdate", "eqsl_qsl": "eqsl_qslrdate",
    "eqsl_sent": "eqsl_qslsdate", "qrz_sent": "qrzcom_qso_upload_date", "qrz_down": "qrzcom_qso_download_date",
    "hrdlog_sent": "hrdlog_qso_upload_date",
}


def service_dates(qsos) -> dict[str, str]:
    """The newest confirmation and upload dates (ISO days) an import carries per service, as another program's log
    records them; dates in the future are ignored."""
    today = timeutil.today_iso()
    out: dict[str, str] = {}
    for q in qsos:
        for name, col in DATE_COLUMNS.items():
            v = q.get(col)
            if v and len(v) >= 10 and v[:10] <= today and v[:10] > out.get(name, ""):
                out[name] = v[:10]
    return out


def from_import(dates: dict[str, str]) -> dict[tuple[str, str], str]:
    """The positions an imported log proposes: each newest date minus a day, to force an overlap (a record already
    applied just matches again). Only LoTW and eQSL have positions."""
    def day_before(d: str) -> date:
        return date.fromisoformat(d) - timedelta(days=1)

    out: dict[tuple[str, str], str] = {}
    if "lotw_qsl" in dates:
        out["lotw", "last_qsl"] = day_before(dates["lotw_qsl"]).strftime("%Y-%m-%d 00:00:00")
    if "lotw_sent" in dates:
        out["lotw", "last_qsorx"] = day_before(dates["lotw_sent"]).strftime("%Y-%m-%d 00:00:00")
    if "eqsl_qsl" in dates:
        out["eqsl", "rcvd_since"] = day_before(dates["eqsl_qsl"]).strftime("%Y%m%d")
    return out


def from_report(header: dict, records: list[dict], info: dict) -> dict[tuple[str, str], str]:
    """The positions a complete LoTW report carries: LoTW's own LASTQSORX and LASTQSL header values first, the
    report's "Generated at" time when a header is missing (a report without a confirmed record has no LASTQSL). A
    confirmations-only report (QSL ONLY: YES) sets the confirmations position alone."""
    out: dict[tuple[str, str], str] = {}
    generated = info.get("generated")
    qsl_only = (info.get("QSL ONLY") or "").upper().startswith("Y")
    if not qsl_only:
        rx = header.get("APP_LOTW_LASTQSORX") or generated
        if rx:
            out["lotw", "last_qsorx"] = normalize("stamp", rx)
    qsl = header.get("APP_LOTW_LASTQSL")
    if not qsl:
        stamps = [timeutil.parse_iso(r.get("APP_LOTW_RXQSL")) for r in records
                  if (r.get("QSL_RCVD") or "").upper() == "Y"]
        stamps = [s for s in stamps if s is not None]
        qsl = max(stamps).strftime("%Y-%m-%d %H:%M:%S") if stamps else generated
    if qsl:
        out["lotw", "last_qsl"] = normalize("stamp", qsl)
    return out


def set_if_unset(db, proposed: dict[tuple[str, str], str], *, origin: str) -> dict[tuple[str, str], str]:
    """Write only the positions that do not exist yet: an import never moves a position that is already there,
    earlier or later; Download everything again is the way to re-check on purpose. Returns what was set."""
    todo = {k: v for k, v in proposed.items() if not db.sync_get(*k)}
    if todo:
        write(db, todo, origin=origin, backup=False)
    return todo


def describe(proposed: dict[tuple[str, str], str], t) -> str:
    """"LoTW confirmations from 2026-10-16, LoTW uploads from 2026-10-17, eQSL cards from 2026-10-11"."""
    names = {("lotw", "last_qsl"): "LoTW confirmations from", ("lotw", "last_qsorx"): "LoTW uploads from",
             ("eqsl", "rcvd_since"): "eQSL cards from"}
    parts = []
    for k, v in proposed.items():
        shown = v[:10] if KINDS[k] == "stamp" else f"{v[:4]}-{v[4:6]}-{v[6:]}"
        parts.append(t(names[k]) + f" {shown}")
    return ", ".join(parts)
