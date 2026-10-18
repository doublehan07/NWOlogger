"""MacLoggerDX database import (PLAN §8.1.1).

Reads ``qso_table_v008`` from a MacLoggerDX ``.sql`` (SQLite) file, read-only. MLDX keeps all upload and
confirmation history as comma-separated text in ``qsl_sent`` / ``qsl_received``; those tokens are split into
NWOlogger's per-service fields here. MacLoggerDX only records the uploads it made itself, so a QSO confirmed by
LoTW or QRZ.com with no upload record is marked uploaded too (:func:`qso.infer_sent`).
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import modes, regions, timeutil
from . import qso as qsomod

TABLE = "qso_table_v008"
KEY_TAG = "APP_MLDX_PK"

_DATE = r"(\d{4}-\d{2}-\d{2})"
SENT_PATTERNS = [
    (re.compile(rf"^Uploaded to LoTW {_DATE}"), "lotw"),
    (re.compile(rf"^Uploaded to eQSL {_DATE}"), "eqsl"),
    (re.compile(rf"^(?:Uploaded to|Updated in) QRZ Logbook {_DATE}"), "qrz"),
    (re.compile(rf"^Uploaded to HRDLOG {_DATE}"), "hrdlog"),
    (re.compile(rf"^Mail? Card {_DATE}"), "paper"),
]
RCVD_PATTERNS = [
    (re.compile(r"^LoTW:(\d{8})"), "lotw"),
    (re.compile(r"^QRZLogbook:(\d{8})"), "qrz"),
    (re.compile(r"^eQSL\.cc DownloadInBox:Y"), "eqsl"),
    (re.compile(rf"^Mail? Card {_DATE}"), "paper"),
]


@dataclass
class MldxReport:
    rows: int = 0
    tokens: Counter = field(default_factory=Counter)
    unknown_tokens: Counter = field(default_factory=Counter)
    province_conflicts: list[tuple[str, str, str]] = field(default_factory=list)  # (call, state, ext_val_3)
    province_unrecognised: Counter = field(default_factory=Counter)
    profiles: Counter = field(default_factory=Counter)  # (call, grid) pairs
    inferred: Counter = field(default_factory=Counter)  # service → confirmed QSOs with no upload record


def _latest(a: str | None, b: str) -> str:
    return b if a is None or b > a else a


def parse_sent(text: str | None, q: dict, report: MldxReport) -> None:
    for tok in (t.strip() for t in (text or "").split(",")):
        if not tok:
            continue
        if tok == "Y":
            report.tokens["bare Y (ignored)"] += 1
            continue
        for pat, svc in SENT_PATTERNS:
            m = pat.match(tok)
            if not m:
                continue
            d = m.group(1)
            report.tokens[f"sent: {svc}"] += 1
            if svc == "lotw":
                q["lotw_qsl_sent"] = "Y"
                q["lotw_qslsdate"] = _latest(q.get("lotw_qslsdate"), d)
            elif svc == "eqsl":
                q["eqsl_qsl_sent"] = "Y"
                q["eqsl_qslsdate"] = _latest(q.get("eqsl_qslsdate"), d)
            elif svc == "qrz":
                q["qrzcom_qso_upload_status"] = "Y"
                q["qrzcom_qso_upload_date"] = _latest(q.get("qrzcom_qso_upload_date"), d)
            elif svc == "hrdlog":
                q["hrdlog_qso_upload_status"] = "Y"
                q["hrdlog_qso_upload_date"] = _latest(q.get("hrdlog_qso_upload_date"), d)
            elif svc == "paper":
                q["qsl_sent"] = "Y"
                q["qslsdate"] = _latest(q.get("qslsdate"), d)
            break
        else:
            report.unknown_tokens[f"sent: {tok[:40]}"] += 1


def parse_rcvd(text: str | None, q: dict, report: MldxReport) -> None:
    for tok in (t.strip() for t in (text or "").split(",")):
        if not tok:
            continue
        for pat, svc in RCVD_PATTERNS:
            m = pat.match(tok)
            if not m:
                continue
            report.tokens[f"received: {svc}"] += 1
            if svc == "lotw":
                q["lotw_qsl_rcvd"] = "Y"
                q["lotw_qslrdate"] = _latest(q.get("lotw_qslrdate"), timeutil.adif_date_to_iso(m.group(1)) or "")
            elif svc == "qrz":
                q["qrz_qsl_rcvd"] = "Y"
                q["qrz_qslrdate"] = _latest(q.get("qrz_qslrdate"), timeutil.adif_date_to_iso(m.group(1)) or "")
            elif svc == "eqsl":
                q["eqsl_qsl_rcvd"] = "Y"
            elif svc == "paper":
                q["qsl_rcvd"] = "Y"
                q["qslrdate"] = _latest(q.get("qslrdate"), m.group(1))
            break
        else:
            report.unknown_tokens[f"received: {tok[:40]}"] += 1


def _s(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _province(row: dict, report: MldxReport) -> tuple[str | None, str | None]:
    """Returns (province, raw ext_val_3 to keep in extra or None)."""
    state, ext = _s(row.get("state")), _s(row.get("ext_val_3"))
    china = row.get("dxcc_id") == regions.CHINA_DXCC
    if not china:
        if state:
            return state, ext if ext and ext != state else None
        return ext, None
    s_code = regions.normalize_cn(state) if state else None
    e_code = regions.normalize_cn(ext) if ext else None
    if state and not s_code:
        report.province_unrecognised[state] += 1
    if ext and not e_code:
        report.province_unrecognised[ext] += 1
    if s_code and e_code and s_code != e_code:
        report.province_conflicts.append((row.get("call") or "", state or "", ext or ""))
    code = s_code or e_code
    keep_raw = ext if ext and (not e_code or (s_code and e_code != s_code)) else None
    if not code and state:
        return state, keep_raw
    return code, keep_raw


def _grids(text: str | None) -> tuple[str | None, str | None]:
    """MLDX puts grid-line pairs ("OM32,OM33") in the grid field; ADIF keeps them in *_VUCC_GRIDS."""
    if not text:
        return None, None
    parts = [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]
    if len(parts) > 1:
        return parts[0], ",".join(qsomod.normalize_grid(p) or p for p in parts)
    return parts[0], None


def row_to_qso(row: dict, report: MldxReport) -> dict:
    q: dict = {}
    extra: dict[str, str] = {KEY_TAG: str(row["pk"])}
    q["call"] = _s(row.get("call"))
    q["time_on"] = timeutil.epoch_to_iso(row.get("qso_start"))
    q["time_off"] = timeutil.epoch_to_iso(row.get("qso_done")) if row.get("qso_done") else None
    my_call = _s(row.get("my_call"))
    q["station_callsign"] = my_call
    q["operator"] = my_call
    q["my_gridsquare"], q["my_vucc_grids"] = _grids(_s(row.get("my_grid")))
    q["my_rig"] = _s(row.get("my_rig"))
    q["first_name"] = _s(row.get("first_name"))
    q["last_name"] = _s(row.get("last_name"))
    street, city, zipc, pcountry = (_s(row.get(k)) for k in ("street", "city", "zip", "postal_country"))
    q["qth"] = city
    addr = [p for p in (street, " ".join(x for x in (city, zipc) if x) or None, pcountry) if p]
    q["address"] = "\n".join(addr) if addr else None
    q["cnty"] = _s(row.get("county"))
    prov, raw_ext = _province(row, report)
    q["state"] = prov
    if raw_ext:
        extra["APP_MLDX_EXT_VAL_3"] = raw_ext
    q["gridsquare"], q["vucc_grids"] = _grids(_s(row.get("grid")))
    q["country"] = _s(row.get("dxcc_country"))
    q["dxcc"] = row.get("dxcc_id") or None
    q["iota"] = _s(row.get("iota"))
    q["sota_ref"] = _s(row.get("sota"))
    q["my_sota_ref"] = _s(row.get("my_sota"))
    q["pota_ref"] = _s(row.get("pota"))
    q["my_pota_ref"] = _s(row.get("my_pota"))
    q["wwff_ref"] = _s(row.get("wwff"))
    q["my_wwff_ref"] = _s(row.get("my_wwff"))
    q["sig"] = _s(row.get("sig"))
    q["sig_info"] = _s(row.get("sig_info"))
    q["skcc"] = _s(row.get("skcc"))
    q["cqz"] = _s(row.get("cq_zone"))
    q["ituz"] = _s(row.get("itu"))
    q["email"] = _s(row.get("email"))
    q["web"] = _s(row.get("url"))
    m, sub = modes.normalize(_s(row.get("mode")))
    q["mode"], q["submode"] = m, sub
    q["band"] = _s(row.get("band_tx"))
    q["band_rx"] = _s(row.get("band_rx"))
    q["freq"] = row.get("tx_frequency") or None
    q["freq_rx"] = row.get("rx_frequency") or None
    q["rst_sent"] = _s(row.get("rst_sent"))
    q["rst_rcvd"] = _s(row.get("rst_received"))
    q["tx_pwr"] = row.get("power") or None
    q["qsl_via"] = _s(row.get("qsl_via"))
    q["comment"] = _s(row.get("comments"))
    sat = _s(row.get("satellite"))
    if sat:
        q["sat_name"] = sat
        q["prop_mode"] = "SAT"
    q["contest_id"] = _s(row.get("contest_id"))
    for src, dst in (("srx", "SRX_STRING"), ("stx", "STX_STRING")):
        v = _s(row.get(src))
        if v:
            q[dst.lower()] = v
    for src, dst in (("srx_numeric", "srx"), ("stx_numeric", "stx")):
        if row.get(src):
            q[dst] = row.get(src)
    if row.get("ten_ten"):
        extra["TEN_TEN"] = str(row["ten_ten"])
    for src, tag in (("azimuth", "ANT_AZ"), ("elevation", "ANT_EL"), ("distance", "DISTANCE")):
        if row.get(src) not in (None, 0, 0.0, ""):
            extra[tag] = f"{float(row[src]):g}"
    if row.get("latitude") not in (None, 0, 0.0):
        extra["APP_MLDX_LAT"] = f"{float(row['latitude']):.5f}"
    if row.get("longitude") not in (None, 0, 0.0):
        extra["APP_MLDX_LON"] = f"{float(row['longitude']):.5f}"
    for k in ("ext_val_1", "ext_val_2", "ext_val_4", "my_sig", "my_sig_info"):
        v = _s(row.get(k))
        if v:
            extra[f"APP_MLDX_{k.upper()}"] = v
    parse_sent(row.get("qsl_sent"), q, report)
    parse_rcvd(row.get("qsl_received"), q, report)
    for svc in qsomod.infer_sent(q):
        report.inferred[svc] += 1
    q["extra"] = qsomod.dump_extra(extra)
    if my_call:
        report.profiles[(my_call, q.get("my_gridsquare") or "")] += 1
    return qsomod.normalize(q)


def read(path: str | Path) -> tuple[list[dict], MldxReport]:
    """Read a MacLoggerDX database (read-only) into normalised QSO dicts plus a report."""
    uri = f"file:{Path(path).resolve()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if TABLE not in tables:
            raise ValueError(f"No {TABLE} table: not a MacLoggerDX v8 database")
        report = MldxReport()
        out = []
        for r in con.execute(f"SELECT * FROM {TABLE} ORDER BY qso_start"):
            row = {k: r[k] for k in r.keys()}
            out.append(row_to_qso(row, report))
            report.rows += 1
        return out, report
    finally:
        con.close()


def is_mldx_db(path: str | Path) -> bool:
    try:
        with open(path, "rb") as fh:
            if fh.read(16) != b"SQLite format 3\x00":
                return False
        con = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
        try:
            return bool(con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone())
        finally:
            con.close()
    except sqlite3.Error:
        return False
