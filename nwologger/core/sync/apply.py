"""Applying service results to the log, and choosing QSOs to upload. Runs on the GUI thread (touches the DB)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import exporter, importer, propagation, timeutil, uploads
from .. import qso as qsomod
from ..fields import ADIF_STANDARD
from ..uploads import SENT_COL, SENT_DATE, SERVICES  # noqa: F401 - re-exported for callers

# Fields of the other station that a confirmation may fill in when empty.
DETAIL_FILL = ["gridsquare", "cqz", "ituz", "state", "cnty", "dxcc", "country", "iota"]


@dataclass
class ApplyResult:
    total: int = 0
    matched: int = 0
    changed: int = 0
    unmatched: list[dict] = field(default_factory=list)


def pending(db, service: str, ids: list[int] | None = None) -> list[dict]:
    """QSOs to upload: the given ids (re-upload allowed), or every QSO not yet sent."""
    if ids:
        rows = db.get_many(ids)
        out = sorted(rows.values(), key=lambda q: (q.get("time_on") or "", q["id"]))
    else:
        out = list(db.iter_all(uploads.PENDING_SQL[service]))
    if service == "lotw":
        # Repeater and internet-assisted QSOs are not valid for LoTW, even when selected.
        out = [q for q in out if not uploads.lotw_skipped(q)]
    return out


def upload_record(q: dict, service: str, catalog, profile: dict | None = None) -> dict[str, str]:
    """The ADIF record sent to a service: standard ADIF fields only (another program's APP_* or USERDEF fields
    stay in the log)."""
    rec = exporter.records([q], include_private=False, service=service, sat_catalog=catalog,
                           include_service_status=False)[0]
    for tag in ("QSL_SENT", "QSL_RCVD", "QSLSDATE", "QSLRDATE", "QSL_SENT_VIA", "QSL_RCVD_VIA", "DUPE"):
        rec.pop(tag, None)
    rec = {k: v for k, v in rec.items() if k in ADIF_STANDARD}
    if service == "lotw" and "PROP_MODE" in rec and not propagation.lotw_value(rec["PROP_MODE"]):
        rec.pop("PROP_MODE")
    if service == "eqsl" and profile and profile.get("eqsl_qth_nickname"):
        rec["APP_EQSL_QTH_NICKNAME"] = profile["eqsl_qth_nickname"]
    return rec


def mark_sent(db, service: str, ids: list[int], *, logids: dict[int, str] | None = None) -> int:
    today = timeutil.today_iso()

    def ch(q: dict) -> dict:
        c = {SENT_COL[service]: "Y", SENT_DATE[service]: today}
        if logids and q["id"] in logids and logids[q["id"]]:
            c["qrz_logid"] = logids[q["id"]]
        return c

    return db.update_many(ids, ch, label=f"Upload to {SERVICES[service]}", track_service=False)


def _to_qso(rec: dict[str, str], strip: tuple[str, ...], catalog, service: str) -> dict:
    r = {k: v for k, v in rec.items() if k not in strip}
    q = qsomod.from_adif(r)
    if q.get("sat_name") and catalog is not None:
        q["sat_name"] = catalog.from_service(service, q["sat_name"])
    return q


def _fill(existing: dict, incoming: dict, cols: list[str]) -> dict:
    return {c: incoming[c] for c in cols if incoming.get(c) not in (None, "") and existing.get(c) in (None, "")}


def _apply(db, recs: list[tuple[dict, dict]], service: str, catalog, label: str, changes_fn) -> ApplyResult:
    res = ApplyResult(total=len(recs))
    with db.batch(label):
        for rec, q in recs:
            mid = importer.find_match(db, q)
            if mid is None:
                res.unmatched.append(q)
                continue
            res.matched += 1
            existing = db.get(mid) or {}
            ch = changes_fn(rec, q, existing)
            ch = {k: v for k, v in ch.items() if existing.get(k) != v}
            if ch and db.update(mid, ch, track_service=False):
                res.changed += 1
    return res


def apply_lotw_qsls(db, records: list[dict[str, str]], catalog) -> ApplyResult:
    strip = ("QSL_RCVD", "QSLRDATE", "QSL_SENT", "QSLSDATE", "APP_LOTW_RXQSL", "APP_LOTW_RXQSO")
    pairs = [(r, _to_qso(r, strip, catalog, "lotw")) for r in records]

    def changes(rec: dict, q: dict, ex: dict) -> dict:
        ch: dict = {}
        if rec.get("QSL_RCVD", "").upper() == "Y":
            ch["lotw_qsl_rcvd"] = "Y"
            d = timeutil.adif_date_to_iso(rec.get("QSLRDATE")) or (rec.get("APP_LOTW_RXQSL") or "")[:10] or None
            if d:
                ch["lotw_qslrdate"] = d
        if ex.get("lotw_qsl_sent") != "Y":
            ch["lotw_qsl_sent"] = "Y"
        ch.update(_fill(ex, q, DETAIL_FILL))
        return ch

    return _apply(db, pairs, "lotw", catalog, "LoTW download", changes)


def apply_lotw_uploads(db, records: list[dict[str, str]], catalog) -> ApplyResult:
    strip = ("QSL_RCVD", "QSLRDATE", "QSL_SENT", "QSLSDATE")
    pairs = [(r, _to_qso(r, strip, catalog, "lotw")) for r in records]

    def changes(rec: dict, q: dict, ex: dict) -> dict:
        ch = {"lotw_qsl_sent": "Y"}
        d = (rec.get("APP_LOTW_RXQSO") or "")[:10]
        if d and not ex.get("lotw_qslsdate"):
            ch["lotw_qslsdate"] = d
        return ch

    return _apply(db, pairs, "lotw", catalog, "LoTW upload status", changes)


def apply_eqsl(db, records: list[dict[str, str]], catalog) -> ApplyResult:
    strip = ("QSL_RCVD", "QSLRDATE", "QSL_SENT", "QSLSDATE", "QSL_SENT_VIA", "QSL_RCVD_VIA", "QSLMSG", "RST_SENT",
             "RST_RCVD", "APP_EQSL_AG")
    pairs = [(r, _to_qso(r, strip, catalog, "eqsl")) for r in records]
    today = timeutil.today_iso()

    def changes(rec: dict, q: dict, ex: dict) -> dict:
        ch = {"eqsl_qsl_rcvd": "Y", "eqsl_qslrdate": timeutil.adif_date_to_iso(rec.get("QSLRDATE")) or
              ex.get("eqsl_qslrdate") or today}
        if (rec.get("APP_EQSL_AG") or "").upper() == "Y":
            ch["eqsl_ag"] = "Y"
        ch.update(_fill(ex, q, ["gridsquare"]))
        return ch

    return _apply(db, pairs, "eqsl", catalog, "eQSL download", changes)


def apply_qrz(db, records: list[dict[str, str]], catalog) -> ApplyResult:
    pairs = [(r, _to_qso(r, (), catalog, "qrz")) for r in records]
    today = timeutil.today_iso()

    def changes(rec: dict, q: dict, ex: dict) -> dict:
        ch = {"qrzcom_qso_download_status": "Y", "qrzcom_qso_download_date": today}
        if rec.get("APP_QRZLOG_LOGID"):
            ch["qrz_logid"] = rec["APP_QRZLOG_LOGID"]
        if (rec.get("APP_QRZLOG_STATUS") or "").upper() == "C":
            ch["qrz_qsl_rcvd"] = "Y"
            d = timeutil.adif_date_to_iso(rec.get("APP_QRZLOG_QSLDATE"))
            if d:
                ch["qrz_qslrdate"] = d
        if ex.get("qrzcom_qso_upload_status") not in ("Y", "M"):
            ch["qrzcom_qso_upload_status"] = "Y"
        return ch

    return _apply(db, pairs, "qrz", catalog, "QRZ.com download", changes)
