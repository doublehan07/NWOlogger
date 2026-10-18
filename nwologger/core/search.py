"""Filter specs → SQL WHERE clauses.

A spec is a JSON-friendly dict::

    {
      "quick": {"call": "BA1*", "band": "2m", "mode": "SSB", "sat": "SO-50", "country": "China",
                "preset": "lotw_confirmed"},
      "conditions": [{"field": "time_on", "op": "between", "value": ["2026-01-01", "2026-12-31"]}],
      "mode_exact": false,
    }

Wildcards: ``*`` = any run of characters, ``?`` = one character, case-insensitive, anchored. Text without
wildcards means *contains*.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from . import modes, timeutil, uploads
from .fields import BY_COL

PRESETS: dict[str, str] = {
    "all": "All",
    "today": "Today (UTC)",
    "last7": "Last 7 days",
    "this_contest": "This contest",
    "sat": "Satellite QSOs",
    "not_lotw": "Not uploaded to LoTW",
    "not_eqsl": "Not uploaded to eQSL",
    "not_qrz": "Not uploaded to QRZ",
    "not_hrdlog": "Not uploaded to HRDLog",
    "not_all": "Not uploaded to every service",
    "lotw_confirmed": "LoTW confirmed",
    "any_confirmed": "Confirmed (any)",
    "unconfirmed": "Not confirmed",
    "paper_to_send": "Paper to send",
    "paper_awaiting": "Paper sent, awaiting reply",
    "paper_unanswered": "Paper received, not answered",
    "suspicious": "Suspicious dupes",
}

TEXT_OPS = ["contains", "is", "is_not", "matches", "starts", "empty", "not_empty"]
NUM_OPS = ["=", "!=", "<", ">", "between", "empty", "not_empty"]
TIME_OPS = ["on", "before", "after", "between", "empty", "not_empty"]
FLAG_OPS = ["yes", "no", "is", "empty", "not_empty"]

OP_LABELS = {
    "contains": "contains", "is": "is", "is_not": "is not", "matches": "matches (* ?)", "starts": "starts with",
    "empty": "is empty", "not_empty": "is not empty", "=": "=", "!=": "≠", "<": "<", ">": ">",
    "between": "between", "on": "on", "before": "before", "after": "after", "yes": "is yes", "no": "is not yes",
}


def ops_for(col: str) -> list[str]:
    f = BY_COL.get(col)
    if col == "mode":
        return ["mode"]
    if not f:
        return TEXT_OPS
    return {"int": NUM_OPS, "real": NUM_OPS, "time": TIME_OPS, "date": TIME_OPS, "flag": FLAG_OPS}.get(
        f.kind, TEXT_OPS)


def has_wildcards(s: str) -> bool:
    return "*" in s or "?" in s


def glob_pattern(s: str) -> str:
    """User wildcard text → SQLite GLOB pattern (upper-case, '[' escaped)."""
    return s.upper().replace("[", "[[]")


def like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


GRID_LISTS = {"gridsquare": "vucc_grids", "my_gridsquare": "my_vucc_grids"}  # one Grid field, two columns
GRID_SPLIT = re.compile(r"[,;]")  # the separators a Grid search accepts ("/" and spaces are not, as asked)


def grid_list_sql(col: str) -> str:
    """A QSO's grids as one upper-case text: the grid, then a comma and its VUCC grids when it has any
    (``OM88DA,OM88,OM89``); a QSO with one grid has no comma."""
    vucc = GRID_LISTS[col]
    return (f"UPPER(CASE WHEN {vucc} IS NOT NULL AND {vucc} != '' THEN COALESCE({col}, '') || ',' || {vucc} "
            f"ELSE COALESCE({col}, '') END)")


def grid_terms(text: str) -> list[str]:
    """The grids a Grid search asks for: the typed text split on commas and semicolons, upper case."""
    return [t.strip().upper() for t in GRID_SPLIT.split(text) if t.strip()]


def grid_match(col: str, op: str, value: Any) -> tuple[str, list[Any]]:
    """A Grid (or My grid) condition against the QSO's whole grid list, GRIDSQUARE and its VUCC_GRIDS, so a station
    on a grid line is found by any of its grids: every typed grid must appear, in any order (``OM89, OM88`` finds
    ``OM88,OM89``); ``is`` and ``starts`` apply to each grid of the list; a comma or semicolon alone asks for the
    QSOs with several grids."""
    vucc, expr = GRID_LISTS[col], grid_list_sql(col)
    several = f"({vucc} IS NOT NULL AND {vucc} != '')"
    if op == "empty":
        return f"(({col} IS NULL OR {col} = '') AND NOT {several})", []
    if op == "not_empty":
        return f"(({col} IS NOT NULL AND {col} != '') OR {several})", []
    v = str(value or "")
    terms = grid_terms(v)
    if not terms:
        return (several, []) if ("," in v or ";" in v) else ("", [])
    parts: list[str] = []
    params: list[Any] = []
    for t in terms:
        if op == "matches" or has_wildcards(t):
            parts.append(f"(',' || {expr} || ',') GLOB ?")
            params.append(f"*,{glob_pattern(t)},*")
        elif op == "is":
            parts.append(f"(',' || {expr} || ',') LIKE ? ESCAPE '\\'")
            params.append(f"%,{like_escape(t)},%")
        elif op == "is_not":
            parts.append(f"(',' || {expr} || ',') NOT LIKE ? ESCAPE '\\'")
            params.append(f"%,{like_escape(t)},%")
        elif op == "starts":
            parts.append(f"(',' || {expr}) LIKE ? ESCAPE '\\'")
            params.append(f"%,{like_escape(t)}%")
        else:
            parts.append(f"{expr} LIKE ? ESCAPE '\\'")
            params.append(f"%{like_escape(t)}%")
    return "(" + " AND ".join(parts) + ")", params


def text_match(col: str, value: str) -> tuple[str, list[Any]]:
    v = value.strip()
    if has_wildcards(v):
        return f"UPPER({col}) GLOB ?", [glob_pattern(v)]
    return f"{col} LIKE ? ESCAPE '\\'", [f"%{like_escape(v)}%"]


def mode_match(term: str, exact: bool = False) -> tuple[str, list[Any]]:
    """Mode filter. Vague (default) finds the mode, anything more specific, and anything that could be it."""
    t = " ".join(term.strip().upper().split())
    if not t:
        return "", []
    t = modes.ALIASES.get(t, t)
    if t in ("PHONE", "DATA", "IMAGE"):
        if exact:
            return "mode = ?", [t]
        return "mode_group = ?", [t]
    m, s = modes.normalize(t)
    g = modes.group(m, s)
    placeholder = g if g in ("PHONE", "DATA", "IMAGE") else None
    if exact:
        if s:
            return "(mode = ? AND submode = ?)", [m, s]
        return "(mode = ? AND (submode IS NULL OR submode = ''))", [m]
    if s:
        sql = "((mode = ? AND (submode = ? OR submode IS NULL OR submode = ''))"
        params: list[Any] = [m, s]
    else:
        sql = "((mode = ?)"
        params = [m]
    if placeholder:
        sql += " OR mode = ?"
        params.append(placeholder)
    return sql + ")", params


def _day_bounds(d: str) -> tuple[str, str]:
    d = d.strip()[:10]
    return f"{d}T00:00:00Z", f"{d}T23:59:59Z"


def condition(col: str, op: str, value: Any, *, mode_exact: bool = False) -> tuple[str, list[Any]]:
    if col not in BY_COL:
        return "", []
    if col in GRID_LISTS:
        return grid_match(col, op, value)
    f = BY_COL[col]
    if op == "empty":
        return f"({col} IS NULL OR {col} = '')", []
    if op == "not_empty":
        return f"({col} IS NOT NULL AND {col} != '')", []
    if col == "mode" or op == "mode":
        return mode_match(str(value or ""), mode_exact)
    if f.kind in ("time", "date"):
        vals = value if isinstance(value, (list, tuple)) else [value]
        vals = [str(v) for v in vals if v]
        if not vals:
            return "", []
        if f.kind == "time":
            if op == "on":
                lo, hi = _day_bounds(vals[0])
                return f"({col} BETWEEN ? AND ?)", [lo, hi]
            if op == "before":
                return f"{col} < ?", [_day_bounds(vals[0])[0] if len(vals[0]) <= 10 else vals[0]]
            if op == "after":
                return f"{col} > ?", [_day_bounds(vals[0])[1] if len(vals[0]) <= 10 else vals[0]]
            if op == "between" and len(vals) >= 2:
                lo = _day_bounds(vals[0])[0] if len(vals[0]) <= 10 else vals[0]
                hi = _day_bounds(vals[1])[1] if len(vals[1]) <= 10 else vals[1]
                return f"({col} BETWEEN ? AND ?)", [lo, hi]
        else:
            if op == "on":
                return f"{col} = ?", [vals[0][:10]]
            if op == "before":
                return f"{col} < ?", [vals[0][:10]]
            if op == "after":
                return f"{col} > ?", [vals[0][:10]]
            if op == "between" and len(vals) >= 2:
                return f"({col} BETWEEN ? AND ?)", [vals[0][:10], vals[1][:10]]
        return "", []
    if f.kind == "flag":
        if op == "yes":
            return f"{col} = 'Y'", []
        if op == "no":
            return f"({col} IS NULL OR {col} != 'Y')", []
        return f"{col} = ?", [str(value).upper()[:1]]
    if f.kind in ("int", "real"):
        cast = int if f.kind == "int" else float
        try:
            if op == "between":
                a, b = value
                return f"({col} BETWEEN ? AND ?)", [cast(a), cast(b)]
            v = cast(value)
        except (TypeError, ValueError):
            return "", []
        sqlop = {"=": "=", "!=": "!=", "<": "<", ">": ">"}.get(op, "=")
        if op == "!=":
            return f"({col} IS NULL OR {col} != ?)", [v]
        return f"{col} {sqlop} ?", [v]
    v = str(value or "")
    if op == "is":
        return f"UPPER({col}) = UPPER(?)", [v.strip()]
    if op == "is_not":
        return f"({col} IS NULL OR UPPER({col}) != UPPER(?))", [v.strip()]
    if op == "starts":
        return f"{col} LIKE ? ESCAPE '\\'", [f"{like_escape(v.strip())}%"]
    if op == "matches":
        return f"UPPER({col}) GLOB ?", [glob_pattern(v.strip())]
    return text_match(col, v)


def preset(name: str | None, *, contest_session_id: int | None = None) -> tuple[str, list[Any]]:
    if not name or name == "all":
        return "", []
    now = timeutil.now_utc()
    if name == "today":
        return "time_on >= ?", [now.strftime("%Y-%m-%dT00:00:00Z")]
    if name == "last7":
        return "time_on >= ?", [timeutil.to_iso(now - timedelta(days=7))]
    if name == "this_contest":
        return "contest_session_id = ?", [contest_session_id if contest_session_id is not None else -1]
    if name == "sat":
        return "prop_mode = 'SAT'", []
    for svc in uploads.SERVICES:
        if name == f"not_{svc}":
            return uploads.PENDING_SQL[svc], []  # the same rule as Upload pending
    if name == "not_all":
        return uploads.ANY_PENDING_SQL, []
    if name == "lotw_confirmed":
        return "lotw_qsl_rcvd = 'Y'", []
    confirmed = "(lotw_qsl_rcvd = 'Y' OR eqsl_qsl_rcvd = 'Y' OR qrz_qsl_rcvd = 'Y' OR qsl_rcvd = 'Y')"
    if name == "any_confirmed":
        return confirmed, []
    if name == "unconfirmed":
        return f"NOT {confirmed}", []
    if name == "paper_to_send":
        return "qsl_sent IN ('Q', 'R')", []
    if name == "paper_awaiting":
        return "qsl_sent = 'Y' AND (qsl_rcvd IS NULL OR qsl_rcvd != 'Y')", []
    if name == "paper_unanswered":
        return "qsl_rcvd = 'Y' AND (qsl_sent IS NULL OR qsl_sent != 'Y')", []
    if name == "suspicious":
        return "dupe_flag IN ('DUPE', 'SUSPECT')", []
    return "", []


def build_where(spec: dict | None, *, contest_session_id: int | None = None) -> tuple[str, list[Any]]:
    if not spec:
        return "", []
    parts: list[str] = []
    params: list[Any] = []

    def add(sql: str, p: list[Any]) -> None:
        if sql:
            parts.append(sql)
            params.extend(p)

    exact = bool(spec.get("mode_exact"))
    quick = spec.get("quick") or {}
    if quick.get("call"):
        add(*text_match("call", str(quick["call"]).upper()))
    if quick.get("band"):
        add("(band = ? OR band_rx = ?)", [quick["band"].lower(), quick["band"].lower()])
    if quick.get("mode"):
        add(*mode_match(quick["mode"], exact))
    if quick.get("sat"):
        add(*text_match("sat_name", str(quick["sat"]).upper()))
    if quick.get("country"):
        add(*text_match("country", quick["country"]))
    if quick.get("preset"):
        add(*preset(quick["preset"], contest_session_id=contest_session_id))
    for c in spec.get("conditions") or []:
        add(*condition(c.get("field", ""), c.get("op", "contains"), c.get("value"), mode_exact=exact))
    return " AND ".join(parts), params
