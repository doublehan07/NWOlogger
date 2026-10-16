"""ADIF import: everything is imported and suspicious dupes are marked (PLAN §8.1)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import timedelta

from . import adif, dupes, dxcc, modes, regions, timeutil
from . import qso as qsomod
from .modes import Relation

CONFIRM_FIELDS = [
    "lotw_qsl_sent", "lotw_qslsdate", "lotw_qsl_rcvd", "lotw_qslrdate",
    "eqsl_qsl_sent", "eqsl_qslsdate", "eqsl_qsl_rcvd", "eqsl_qslrdate", "eqsl_ag",
    "qrzcom_qso_upload_status", "qrzcom_qso_upload_date", "qrzcom_qso_download_status",
    "qrzcom_qso_download_date", "qrz_logid", "qrz_qsl_rcvd", "qrz_qslrdate",
    "hrdlog_qso_upload_status", "hrdlog_qso_upload_date",
    "qsl_sent", "qslsdate", "qsl_rcvd", "qslrdate", "qsl_sent_via", "qsl_rcvd_via",
]

MATCH_WINDOW = 30 * 60


class _Rollback(Exception):
    pass


@dataclass
class ImportResult:
    total: int = 0
    added: int = 0
    updated: int = 0
    merged: int = 0
    dupes: int = 0
    suspects: int = 0
    ids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def fill_location(q: dict, cty: dxcc.CtyDB | None = None) -> dict:
    """Fill empty country / DXCC / zones / continent from the country file, and China province guesses."""
    cty = cty or dxcc.default()
    if not q.get("call"):
        return q
    r = cty.resolve(q["call"])
    if r:
        for col, val in (("country", r.country), ("dxcc", r.dxcc), ("cqz", r.cqz), ("ituz", r.ituz),
                         ("cont", r.cont)):
            if q.get(col) in (None, ""):
                q[col] = val
    if q.get("dxcc") == regions.CHINA_DXCC:
        if q.get("state"):
            code = regions.normalize_cn(q["state"])
            if code:
                q["state"] = code
    return q


def apply_profile(q: dict, profile: dict | None) -> dict:
    """Give records without a station callsign the chosen station profile."""
    if not profile or q.get("station_callsign"):
        return q
    for col in ("station_callsign", "operator", "my_gridsquare"):
        if not q.get(col) and profile.get(col):
            q[col] = profile[col]
    if profile.get("id"):
        q["station_profile_id"] = profile["id"]
    return q


def find_match(db, q: dict, window: float = MATCH_WINDOW) -> int | None:
    """Find the local QSO a downloaded / imported record refers to (exact mode preferred, then nearest)."""
    call = (q.get("call") or "").upper()
    band = (q.get("band") or "").lower()
    t = timeutil.parse_iso(q.get("time_on"))
    if not call or t is None:
        return None
    lo = timeutil.to_iso(t - timedelta(seconds=window))
    hi = timeutil.to_iso(t + timedelta(seconds=window))
    sql = "SELECT * FROM qso WHERE call = ? AND time_on BETWEEN ? AND ?"
    params: list = [call, lo, hi]
    if band:
        sql += " AND band = ?"
        params.append(band)
    best = None
    best_key = None
    sat = (q.get("prop_mode") or "").upper() == "SAT"
    for r in db.conn.execute(sql, params):
        o = {k: r[k] for k in r.keys()}
        if sat and o.get("sat_name") and q.get("sat_name") and o["sat_name"].upper() != q["sat_name"].upper():
            continue
        rel = modes.relation((q.get("mode"), q.get("submode")), (o.get("mode"), o.get("submode")))
        if q.get("mode") and o.get("mode") and rel == Relation.UNRELATED:
            continue
        key = (-int(rel), timeutil.seconds_between(q["time_on"], o["time_on"]))
        if best_key is None or key < best_key:
            best, best_key = o["id"], key
    return best


def merge_changes(existing: dict, incoming: dict) -> dict:
    """Fill empty fields and take newer confirmation data. Never overwrites typed data with blanks."""
    ch: dict = {}
    for col, v in incoming.items():
        if col in ("id", "uuid", "created_at", "updated_at", "extra", "dupe_flag", "dupe_of", "mode_group"):
            continue
        if v in (None, ""):
            continue
        cur = existing.get(col)
        if col in CONFIRM_FIELDS:
            if col.endswith(("_rcvd", "_sent", "_status", "_ag")) and cur == "Y" and v != "Y":
                continue
            if cur != v:
                ch[col] = v
        elif cur in (None, ""):
            ch[col] = v
    ex = qsomod.load_extra(existing)
    inc = qsomod.load_extra(incoming)
    new_tags = {k: v for k, v in inc.items() if k not in ex}
    if new_tags:
        ex.update(new_tags)
        ch["extra"] = qsomod.dump_extra(ex)
    return ch


def run(db, qsos: Iterable[dict], *, merge: bool = False, dry_run: bool = False, label: str = "Import",
        profile: dict | None = None, fill: bool = True, key_tag: str | None = None,
        block_s: float = dupes.BLOCK_SECONDS, warn_s: float = dupes.WARN_SECONDS,
        progress: Callable[[int, int], None] | None = None) -> ImportResult:
    """Import normalised QSO dicts.

    * merge=False: every record is added; suspicious dupes are marked.
    * merge=True: records that match an existing QSO update it instead of being added.
    * key_tag: an ``extra`` tag (e.g. ``APP_MLDX_PK``) that identifies records from an earlier run; matching
      QSOs are updated, so re-running an import does not duplicate.
    * dry_run: do everything in a transaction and roll it back; the result has the counts.
    """
    items = list(qsos)
    res = ImportResult(total=len(items))
    if not dry_run:
        db.backup("import")
    try:
        with db.batch(label):
            new_ids: list[int] = []
            for n, q in enumerate(items):
                if progress and n % 100 == 0:
                    progress(n, len(items))
                q = dict(q)
                if fill:
                    fill_location(q)
                apply_profile(q, profile)
                existing_id = None
                if key_tag:
                    key = qsomod.load_extra(q).get(key_tag)
                    if key:
                        existing_id = db.find_by_extra(key_tag, key)
                if existing_id is None and merge:
                    existing_id = find_match(db, q)
                if existing_id is not None:
                    existing = db.get(existing_id)
                    if existing is None:
                        continue
                    if key_tag:
                        ch = {k: v for k, v in q.items() if k not in ("dupe_flag", "dupe_of") and
                              existing.get(k) != v and v is not None}
                    else:
                        ch = merge_changes(existing, q)
                    if ch and db.update(existing_id, ch, track_service=False):
                        if key_tag:
                            res.updated += 1
                        else:
                            res.merged += 1
                    res.ids.append(existing_id)
                    continue
                try:
                    qid = db.insert(q, normalized=True)
                except Exception as e:  # noqa: BLE001 - report and keep going
                    res.errors.append(f"{qsomod.summary(q)}: {e}")
                    continue
                new_ids.append(qid)
                res.ids.append(qid)
                res.added += 1
            res.dupes, res.suspects = dupes.mark(db, new_ids, block_s=block_s, warn_s=warn_s)
            if dry_run:
                raise _Rollback
    except _Rollback:
        pass
    if progress:
        progress(len(items), len(items))
    return res


def read_adif(data: bytes | str) -> tuple[dict, list[dict]]:
    header, records = adif.parse(data)
    pid = header.get("PROGRAMID")
    qsos = [qsomod.from_adif(r, program_id=pid) for r in records]
    return header, qsos
