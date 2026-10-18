"""Dupe rules for DX mode and ADIF import (PLAN §5.2).

Two QSOs are compared when they share call + band (+ satellite when via satellite).

* same MODE+SUBMODE within the block window (30 s)   → ``DUPE``   (logging is blocked)
* same mode later, or a related mode, within warning  → ``SUSPECT`` (warning only; the window is 1 minute, so a
  second contact an hour later is a second contact, not a suspect)
* unrelated modes or outside the warning window       → nothing

``dupe_flag`` values: ``DUPE``, ``SUSPECT``, ``OK`` (you said "not a dupe"; scans leave it alone).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import NamedTuple

from . import modes, timeutil
from .modes import Relation

BLOCK_SECONDS = 30
WARN_SECONDS = 60


@dataclass
class DupeHit:
    level: str  # DUPE or SUSPECT
    other_id: int
    relation: Relation
    seconds: float
    other: dict


def _sat_key(q: dict) -> tuple[bool, str]:
    sat = (q.get("prop_mode") or "").upper() == "SAT"
    return sat, (q.get("sat_name") or "").upper() if sat else ""


def classify(a: dict, b: dict, *, block_s: float = BLOCK_SECONDS, warn_s: float = WARN_SECONDS
             ) -> tuple[str, Relation, float] | None:
    """Compare two QSOs. Returns (level, relation, seconds apart) or None."""
    if (a.get("call") or "").upper() != (b.get("call") or "").upper():
        return None
    if (a.get("band") or "").lower() != (b.get("band") or "").lower():
        return None
    if _sat_key(a) != _sat_key(b):
        return None
    secs = timeutil.seconds_between(a.get("time_on") or "", b.get("time_on") or "")
    if secs > warn_s:
        return None
    rel = modes.relation((a.get("mode"), a.get("submode")), (b.get("mode"), b.get("submode")))
    if rel == Relation.UNRELATED:
        return None
    if rel == Relation.SAME and secs <= block_s:
        return "DUPE", rel, secs
    return "SUSPECT", rel, secs


def check(db, q: dict, *, block_s: float = BLOCK_SECONDS, warn_s: float = WARN_SECONDS,
          exclude_id: int | None = None, skip=None) -> DupeHit | None:
    """Check a QSO (new or edited) against the log. The most severe, nearest hit wins."""
    call = (q.get("call") or "").upper()
    band = (q.get("band") or "").lower()
    t = timeutil.parse_iso(q.get("time_on"))
    if not call or not band or t is None:
        return None
    lo = timeutil.to_iso(t - timedelta(seconds=warn_s))
    hi = timeutil.to_iso(t + timedelta(seconds=warn_s))
    rows = db.conn.execute(
        "SELECT * FROM qso WHERE call = ? AND band = ? AND time_on BETWEEN ? AND ?", (call, band, lo, hi)).fetchall()
    best: DupeHit | None = None
    for r in rows:
        other = {k: r[k] for k in r.keys()}
        if exclude_id is not None and other["id"] == exclude_id:
            continue
        if skip is not None and skip(other):
            continue
        res = classify(q, other, block_s=block_s, warn_s=warn_s)
        if not res:
            continue
        level, rel, secs = res
        hit = DupeHit(level, other["id"], rel, secs, other)
        if best is None or (level == "DUPE" and best.level != "DUPE") or \
                (level == best.level and secs < best.seconds):
            best = hit
    return best


def mark(db, ids: list[int], *, block_s: float = BLOCK_SECONDS, warn_s: float = WARN_SECONDS) -> tuple[int, int]:
    """Set dupe_flag / dupe_of on the given QSOs. Returns (dupes, suspects)."""
    rows = db.get_many(ids)
    idset = set(ids)
    d = s = 0
    for qid in ids:
        q = rows.get(qid)
        if q is None or q.get("dupe_flag") == "OK":
            continue

        def later_in_set(other: dict, q: dict = q) -> bool:
            # Within the marked set only the later QSO of a pair is marked.
            if other["id"] not in idset:
                return False
            return (other["time_on"], other["id"]) > (q["time_on"], q["id"])

        hit = check(db, q, block_s=block_s, warn_s=warn_s, exclude_id=qid, skip=later_in_set)
        flag = hit.level if hit else None
        other = hit.other_id if hit else None
        if flag == "DUPE":
            d += 1
        elif flag == "SUSPECT":
            s += 1
        if q.get("dupe_flag") != flag or q.get("dupe_of") != other:
            db.update(qid, {"dupe_flag": flag, "dupe_of": other}, track_service=False, normalized=True)
    return d, s


class RescanResult(NamedTuple):
    dupes: int  # QSOs marked DUPE after the scan
    suspects: int  # QSOs marked SUSPECT after the scan
    cleared: int  # marks the current rule no longer makes (a wider window before, or the partner is gone)
    added: int  # QSOs newly marked


def rescan(db, *, block_s: float = BLOCK_SECONDS, warn_s: float = WARN_SECONDS) -> RescanResult:
    """Re-check the whole log. The later QSO of each pair is marked; ``OK`` marks are kept. Marks the windows no
    longer make are cleared and counted, so the caller can say so (0.1.8's 24-hour window left SUSPECT marks on
    pairs the 1-minute window of 0.1.9 never shows)."""
    rows = [dict(r) for r in db.conn.execute(
        "SELECT id, call, band, prop_mode, sat_name, mode, submode, time_on, dupe_flag, dupe_of FROM qso "
        "WHERE call IS NOT NULL AND band IS NOT NULL AND time_on IS NOT NULL ORDER BY call, band, time_on, id")]
    result: dict[int, tuple[str | None, int | None]] = {}
    i = 0
    while i < len(rows):
        j = i
        key = (rows[i]["call"], rows[i]["band"])
        while j < len(rows) and (rows[j]["call"], rows[j]["band"]) == key:
            j += 1
        group = rows[i:j]
        for k, q in enumerate(group):
            best: tuple[str, int, float] | None = None
            for p in reversed(group[:k]):
                if timeutil.seconds_between(q["time_on"], p["time_on"]) > warn_s:
                    break
                res = classify(q, p, block_s=block_s, warn_s=warn_s)
                if not res:
                    continue
                level, _rel, secs = res
                if best is None or (level == "DUPE" and best[0] != "DUPE"):
                    best = (level, p["id"], secs)
            result[q["id"]] = (best[0], best[1]) if best else (None, None)
        i = j
    d = s = cleared = added = 0
    with db.batch("Find duplicates"):
        for q in rows:
            if q["dupe_flag"] == "OK":
                continue
            flag, other = result.get(q["id"], (None, None))
            if flag == "DUPE":
                d += 1
            elif flag == "SUSPECT":
                s += 1
            if q["dupe_flag"] != flag or q["dupe_of"] != other:
                was = q["dupe_flag"] in ("DUPE", "SUSPECT")
                if was and flag is None:
                    cleared += 1
                elif flag is not None and not was:
                    added += 1
                db.update(q["id"], {"dupe_flag": flag, "dupe_of": other}, track_service=False, normalized=True)
    return RescanResult(d, s, cleared, added)


def hits(db, q: dict, *, block_s: float = BLOCK_SECONDS, warn_s: float = WARN_SECONDS,
         exclude_id: int | None = None) -> list[DupeHit]:
    """Every QSO this one may duplicate, most severe and nearest first."""
    call = (q.get("call") or "").upper()
    band = (q.get("band") or "").lower()
    t = timeutil.parse_iso(q.get("time_on"))
    if not call or not band or t is None:
        return []
    lo = timeutil.to_iso(t - timedelta(seconds=warn_s))
    hi = timeutil.to_iso(t + timedelta(seconds=warn_s))
    out: list[DupeHit] = []
    for r in db.conn.execute("SELECT * FROM qso WHERE call = ? AND band = ? AND time_on BETWEEN ? AND ?",
                             (call, band, lo, hi)):
        other = {k: r[k] for k in r.keys()}
        if exclude_id is not None and other["id"] == exclude_id:
            continue
        res = classify(q, other, block_s=block_s, warn_s=warn_s)
        if res:
            out.append(DupeHit(res[0], other["id"], res[1], res[2], other))
    out.sort(key=lambda h: (h.level != "DUPE", h.seconds))
    return out


def group(db, qid: int, *, block_s: float = BLOCK_SECONDS, warn_s: float = WARN_SECONDS,
          limit: int = 12) -> list[dict]:
    """A QSO and every QSO linked to it by possible-dupe relations (transitively), oldest first."""
    seen: dict[int, dict] = {}
    todo = [qid]
    while todo and len(seen) < limit:
        i = todo.pop()
        if i in seen:
            continue
        q = db.get(i)
        if not q:
            continue
        seen[i] = q
        for h in hits(db, q, block_s=block_s, warn_s=warn_s, exclude_id=i):
            if h.other_id not in seen:
                todo.append(h.other_id)
    return sorted(seen.values(), key=lambda q: (q.get("time_on") or "", q["id"]))


def groups_for(db, ids: list[int], *, block_s: float = BLOCK_SECONDS, warn_s: float = WARN_SECONDS
               ) -> list[list[int]]:
    """Cluster QSOs into dupe groups (each QSO in at most one group), groups of two or more, oldest first."""
    placed: set[int] = set()
    out: list[list[int]] = []
    for i in ids:
        if i in placed:
            continue
        g = group(db, i, block_s=block_s, warn_s=warn_s)
        gids = [q["id"] for q in g if q["id"] not in placed]
        placed.update(q["id"] for q in g)
        if len(gids) >= 2:
            out.append(gids)
    return out
