"""Merging duplicate QSOs into one: your pick per field, everything else filled in or combined.

Defaults give the old *Keep #n, merge others* result: the kept QSO's value, or the only one there is. Each service's
state (uploaded, confirmed) and its date / log id move together from the QSO with the best state, because they
record what the service holds, not a choice. Other programs' ``APP_…`` fields are kept from all of them.
"""

from __future__ import annotations

from . import qso as qsomod
from .fields import BY_COL

BOTH = "both"  # a pick for Comment / Notes: keep the texts of all of them
TEXT_BOTH = ("comment", "notes")
_SKIP = {"id", "uuid", "created_at", "updated_at", "extra", "dupe_flag", "dupe_of", "mode_group"}

# A service state and the fields that belong to it.
STATES: dict[str, tuple[str, ...]] = {
    "lotw_qsl_sent": ("lotw_qslsdate",),
    "lotw_qsl_rcvd": ("lotw_qslrdate",),
    "eqsl_qsl_sent": ("eqsl_qslsdate",),
    "eqsl_qsl_rcvd": ("eqsl_qslrdate", "eqsl_ag"),
    "qrzcom_qso_upload_status": ("qrzcom_qso_upload_date", "qrz_logid"),
    "qrzcom_qso_download_status": ("qrzcom_qso_download_date",),
    "qrz_qsl_rcvd": ("qrz_qslrdate",),
    "hrdlog_qso_upload_status": ("hrdlog_qso_upload_date",),
    "qsl_sent": ("qslsdate", "qsl_sent_via"),
    "qsl_rcvd": ("qslrdate", "qsl_rcvd_via"),
}
STATE_COLS = set(STATES) | {c for comp in STATES.values() for c in comp}
_RANK = {"V": 4, "Y": 3, "M": 2, "Q": 1, "R": 1}  # verified, yes, uploaded then changed, queued / requested


def _rank(v) -> int:
    return _RANK.get((v or "").upper(), 0)


def merged(qsos: list[dict], keep: int, picks: dict[str, int | str] | None = None) -> dict:
    """The kept QSO (``qsos[keep]``) as it will be after the merge. ``picks``: column → the index of the QSO whose
    value to use, or :data:`BOTH` for Comment / Notes."""
    picks = picks or {}
    base = qsos[keep]
    out = dict(base)
    for col in BY_COL:
        if col in _SKIP or col in STATE_COLS:
            continue
        pick = picks.get(col)
        if pick == BOTH and col in TEXT_BOTH:
            texts: list[str] = []
            for q in [base, *(q for i, q in enumerate(qsos) if i != keep)]:
                t = str(q.get(col) or "").strip()
                if t and t not in texts:
                    texts.append(t)
            out[col] = " · ".join(texts) or None
        elif isinstance(pick, int) and 0 <= pick < len(qsos):
            out[col] = qsos[pick].get(col)
        elif base.get(col) in (None, ""):
            out[col] = next((q.get(col) for q in qsos if q.get(col) not in (None, "")), base.get(col))
    for col, companions in STATES.items():
        best = max(_rank(q.get(col)) for q in qsos)
        cands = [i for i, q in enumerate(qsos) if _rank(q.get(col)) == best]
        src = keep if keep in cands else min(cands, key=lambda i: (qsos[i].get(companions[0]) or "9999", i))
        for c in (col, *companions):
            out[c] = qsos[src].get(c)
    tags: dict = {}
    for q in [base, *(q for i, q in enumerate(qsos) if i != keep)]:
        for k, v in qsomod.load_extra(q).items():
            tags.setdefault(k, v)
    if tags != qsomod.load_extra(base):
        out["extra"] = qsomod.dump_extra(tags)
    return out


def changes(qsos: list[dict], keep: int, picks: dict[str, int | str] | None = None) -> dict:
    """What the merge changes in the kept QSO."""
    base = qsos[keep]
    return {k: v for k, v in merged(qsos, keep, picks).items() if k != "id" and base.get(k) != v}


def problems(q: dict) -> list[str]:
    """Why a merged QSO can't be saved (the entry form's rules), as English texts to translate."""
    out = []
    if not q.get("call"):
        out.append("The call is empty.")
    if not q.get("band") and q.get("freq") is None:
        out.append("The band is unknown.")
    if not q.get("mode"):
        out.append("The mode is empty.")
    if q.get("time_on") and q.get("time_off") and q["time_off"] < q["time_on"]:
        out.append("Time Off is earlier than Time On.")
    return out


def remote_copies(q: dict) -> list[str]:
    """Where a QSO about to be deleted is also stored at a service that keeps duplicates: you delete it there."""
    out = []
    if q.get("qrzcom_qso_upload_status") in ("Y", "M"):
        out.append("QRZ.com" + (f" (log id {q['qrz_logid']})" if q.get("qrz_logid") else ""))
    if q.get("hrdlog_qso_upload_status") in ("Y", "M"):
        out.append("HRDLog")
    return out
