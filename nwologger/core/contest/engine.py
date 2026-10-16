"""Contest definitions and the scoring engine (N1MM-style live score).

A definition (JSON) gives the exchange fields, allowed bands and modes, the dupe rule, a QSO-points rule list
(first match wins) and the multiplier types. Conditions used in rules:

``same_dxcc``, ``same_cont``, ``same_ituz``, ``sat``, ``me_cont:AS``, ``they_cont:NA``, ``band:160m,80m``,
``mode:CW|PHONE|DATA``, ``me_dxcc_in:291,1``, ``they_dxcc_in:291,1``, ``exch_is_text:<key>``, and ``not:<cond>``.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import bands, callsign, dxcc, modes, timeutil

BUILTIN_DIR = Path(__file__).resolve().parent / "defs"
DUPE_RULES = ("band", "band_mode", "once")
SCOPES = ("band", "band_mode", "mode", "contest")
FIELD_KINDS = ("rst", "serial", "int", "text", "grid", "zone_or_hq")


@dataclass
class ContestDef:
    id: str
    name: str
    cabrillo: str
    modes: list[str]
    bands: list[str]
    dupe: str
    exchange: list[dict]
    points: list[dict]
    mults: list[dict]
    notes: str = ""
    sponsor: str = ""
    default_mode: str | None = None
    user: bool = False
    path: str | None = None

    @classmethod
    def from_dict(cls, d: dict, *, user: bool = False, path: str | None = None) -> ContestDef:
        problems = validate_def(d)
        if problems:
            raise ValueError("; ".join(problems))
        return cls(id=d["id"], name=d["name"], cabrillo=d.get("cabrillo") or d["id"], modes=list(d["modes"]),
                   bands=list(d["bands"]), dupe=d.get("dupe", "band"), exchange=list(d["exchange"]),
                   points=list(d.get("points") or [{"points": 1}]), mults=list(d.get("mults") or []),
                   notes=d.get("notes", ""), sponsor=d.get("sponsor", ""), default_mode=d.get("default_mode"),
                   user=user, path=path)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "cabrillo": self.cabrillo, "sponsor": self.sponsor,
                "notes": self.notes, "modes": self.modes, "default_mode": self.default_mode, "bands": self.bands,
                "dupe": self.dupe, "exchange": self.exchange, "points": self.points, "mults": self.mults}

    def field(self, key: str) -> dict | None:
        return next((f for f in self.exchange if f["key"] == key), None)

    @property
    def text_fields(self) -> list[dict]:
        return [f for f in self.exchange if f.get("kind") not in ("rst", "serial")]

    def mode_choices(self) -> list[str]:
        out: list[str] = []
        for g in self.modes:
            out += {"CW": ["CW"], "PHONE": ["SSB", "USB", "LSB", "FM", "AM"], "DATA": ["RTTY", "FT8", "FT4", "PSK31"],
                    "IMAGE": ["SSTV"]}.get(g, [g])
        if self.default_mode and self.default_mode in out:
            out.remove(self.default_mode)
            out.insert(0, self.default_mode)
        return out


def validate_def(d: dict) -> list[str]:
    p = []
    for k in ("id", "name", "modes", "bands", "exchange"):
        if not d.get(k):
            p.append(f"missing '{k}'")
    if d.get("dupe", "band") not in DUPE_RULES:
        p.append(f"dupe must be one of {', '.join(DUPE_RULES)}")
    for f in d.get("exchange") or []:
        if not f.get("key"):
            p.append("an exchange field has no key")
        if f.get("kind", "text") not in FIELD_KINDS:
            p.append(f"unknown field kind {f.get('kind')!r}")
    for m in d.get("mults") or []:
        if m.get("per", "band") not in SCOPES:
            p.append(f"mult {m.get('key')}: per must be one of {', '.join(SCOPES)}")
    for b in d.get("bands") or []:
        if b not in bands.BAND_NAMES:
            p.append(f"unknown band {b!r}")
    return p


def load_all(user_dir: str | Path | None = None) -> dict[str, ContestDef]:
    out: dict[str, ContestDef] = {}
    for p in sorted(BUILTIN_DIR.glob("*.json")):
        d = ContestDef.from_dict(json.loads(p.read_text(encoding="utf-8")), path=str(p))
        out[d.id] = d
    if user_dir and Path(user_dir).exists():
        for p in sorted(Path(user_dir).glob("*.json")):
            try:
                d = ContestDef.from_dict(json.loads(p.read_text(encoding="utf-8")), user=True, path=str(p))
            except (ValueError, KeyError, json.JSONDecodeError):
                continue
            out[d.id] = d
    return out


def save_user_def(d: ContestDef, user_dir: str | Path) -> Path:
    Path(user_dir).mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", d.id).strip("-").lower() or "contest"
    path = Path(user_dir) / f"{safe}.json"
    path.write_text(json.dumps(d.to_dict(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------------ stations and exchanges

@dataclass
class Me:
    call: str
    dxcc: int | None = None
    cont: str | None = None
    cqz: int | None = None
    ituz: int | None = None
    grid: str | None = None
    state: str | None = None

    @classmethod
    def from_profile(cls, profile: dict | None) -> Me:
        p = profile or {}
        call = (p.get("station_callsign") or "").upper()
        r = dxcc.default().resolve(call) if call else None
        return cls(call=call, dxcc=p.get("my_dxcc") or (r.dxcc if r else None), cont=r.cont if r else None,
                   cqz=p.get("my_cq_zone") or (r.cqz if r else None), ituz=p.get("my_itu_zone") or (r.ituz if r else None),
                   grid=p.get("my_gridsquare"), state=p.get("my_state"))


def mode_category(mode: str | None, submode: str | None = None) -> str:
    """Cabrillo-style mode category used for dupes and mults: CW, PH, RY/DG."""
    g = modes.group(mode, submode)
    if g == "PHONE":
        return "PH"
    if g == "CW":
        return "CW"
    return "DG"


def exchange_values(cdef: ContestDef, q: dict) -> dict[str, str]:
    """Received exchange values of a logged QSO, keyed by field."""
    out: dict[str, str] = {}
    texts = (q.get("srx_string") or "").split()
    ti = 0
    for f in cdef.exchange:
        kind = f.get("kind")
        if kind == "rst":
            out[f["key"]] = q.get("rst_rcvd") or ""
        elif kind == "serial":
            out[f["key"]] = str(q.get("srx") or "")
        else:
            out[f["key"]] = texts[ti] if ti < len(texts) else ""
            ti += 1
    return out


def store_exchange(cdef: ContestDef, values: dict[str, str], sent: dict[str, str], serial: int | None,
                   they_wve: bool = False) -> dict:
    """QSO columns for a received / sent exchange."""
    q: dict[str, Any] = {"contest_id": cdef.cabrillo}
    texts, stexts = [], []
    for f in cdef.exchange:
        k, kind = f["key"], f.get("kind")
        v = (values.get(k) or "").strip().upper()
        s = (sent.get(k) or "").strip().upper()
        if kind == "rst":
            q["rst_rcvd"] = v or None
            q["rst_sent"] = s or None
        elif kind == "serial":
            q["srx"] = int(v) if v.isdigit() else None
            q["stx"] = serial
        else:
            texts.append(v or "-")
            if f.get("serial_if_dx") and s == "#":
                stexts.append(str(serial or ""))
            else:
                stexts.append(s or "-")
            col = f.get("adif") or (f.get("adif_if_wve") if they_wve else None)
            if col and v:
                q[col] = int(v) if col in ("cqz", "ituz") and v.isdigit() else v
            if f.get("serial_if_dx") and v.isdigit():
                q["srx"] = int(v)
    q["srx_string"] = " ".join(texts) if texts else None
    q["stx_string"] = " ".join(stexts) if stexts else None
    if q.get("stx") is None and serial is not None and any(f.get("serial_if_dx") for f in cdef.exchange):
        q["stx"] = serial
    return q


GRID4 = re.compile(r"^[A-R]{2}\d{2}$", re.I)


def validate_value(f: dict, v: str) -> bool:
    v = (v or "").strip()
    kind = f.get("kind", "text")
    if not v:
        return bool(f.get("optional"))
    if kind == "rst":
        return bool(re.fullmatch(r"[1-5][1-9][1-9]?|[+-]?\d{1,2}", v))
    if kind == "serial":
        return v.isdigit() and int(v) > 0
    if kind == "int":
        if not v.isdigit():
            return False
        n = int(v)
        return f.get("min", 0) <= n <= f.get("max", 10 ** 9)
    if kind == "zone_or_hq":
        if v.isdigit():
            return f.get("min", 0) <= int(v) <= f.get("max", 10 ** 9)
        return bool(re.fullmatch(r"[A-Z0-9]{2,8}", v.upper()))
    if kind == "grid":
        return bool(GRID4.match(v[:4]))
    return True


# ------------------------------------------------------------------ engine

@dataclass
class QsoScore:
    points: int = 0
    dupe: bool = False
    dupe_of: int | None = None
    new_mults: list[tuple[str, str]] = field(default_factory=list)  # (mult label, value)


@dataclass
class BandRow:
    band: str
    qsos: int = 0
    points: int = 0
    mults: Counter = field(default_factory=Counter)


class Engine:
    def __init__(self, cdef: ContestDef, me: Me):
        self.cdef = cdef
        self.me = me
        self.cty = dxcc.default()
        self.reset()

    def reset(self) -> None:
        self.dupe_keys: dict[tuple, int] = {}
        self.mult_seen: set[tuple] = set()
        self.rows: dict[str, BandRow] = {}
        self.points_total = 0
        self.qso_count = 0
        self.dupe_count = 0
        self.times: list[str] = []
        self.scores: dict[int, QsoScore] = {}

    # ---- context
    def ctx(self, q: dict) -> dict:
        r = self.cty.resolve(q.get("call") or "")
        ex = exchange_values(self.cdef, q) if "srx_string" in q or "srx" in q or "rst_rcvd" in q else {}
        if "_exch" in q:
            ex = dict(q["_exch"])
        return {
            "call": (q.get("call") or "").upper(),
            "dxcc": q.get("dxcc") or (r.dxcc if r else None),
            "cont": q.get("cont") or (r.cont if r else None),
            "ituz": r.ituz if r else None,
            "cqz": r.cqz if r else None,
            "band": (q.get("band") or "").lower(),
            "group": modes.group(q.get("mode"), q.get("submode")),
            "modecat": mode_category(q.get("mode"), q.get("submode")),
            "sat": (q.get("prop_mode") or "").upper() == "SAT",
            "exch": {k: (v or "").upper() for k, v in ex.items()},
        }

    def cond(self, c: str, x: dict) -> bool:
        if c.startswith("not:"):
            return not self.cond(c[4:], x)
        name, _, arg = c.partition(":")
        if name == "same_dxcc":
            return x["dxcc"] is not None and x["dxcc"] == self.me.dxcc
        if name == "same_cont":
            return bool(x["cont"]) and x["cont"] == self.me.cont
        if name == "same_ituz":
            z = x["exch"].get("zone") or ""
            return z.isdigit() and self.me.ituz is not None and int(z) == int(self.me.ituz)
        if name == "sat":
            return x["sat"]
        if name == "me_cont":
            return (self.me.cont or "") in arg.split(",")
        if name == "they_cont":
            return (x["cont"] or "") in arg.split(",")
        if name == "band":
            return x["band"] in arg.split(",")
        if name == "mode":
            return (x["group"] or "") in arg.split(",") or x["modecat"] in arg.split(",")
        if name == "me_dxcc_in":
            return str(self.me.dxcc) in arg.split(",")
        if name == "they_dxcc_in":
            return str(x["dxcc"]) in arg.split(",")
        if name == "exch_is_text":
            v = x["exch"].get(arg, "")
            return bool(v) and not v.isdigit()
        return False

    def all(self, conds: list[str], x: dict) -> bool:
        return all(self.cond(c, x) for c in conds)

    def qso_points(self, x: dict) -> int:
        for rule in self.cdef.points:
            if self.all(rule.get("when", []), x):
                return int(rule.get("points", 0))
        return 0

    def mult_entries(self, q: dict, x: dict) -> list[tuple[tuple, str, str]]:
        """[(seen-key, mult label, value)] for this QSO."""
        out = []
        for m in self.cdef.mults:
            if not self.all(m.get("when", []), x):
                continue
            src = m.get("value", "dxcc")
            val: Any = None
            if src == "dxcc":
                val = x["dxcc"]
            elif src == "wpx":
                val = callsign.wpx_prefix(x["call"])
            elif src in ("cqz", "ituz"):
                val = x[src]
            elif src.startswith("exch:"):
                val = x["exch"].get(src[5:]) or None
            elif src.startswith("grid4:"):
                g = (x["exch"].get(src[6:]) or "")[:4].upper()
                val = g if GRID4.match(g) else None
            if val in (None, "", "-", 0):
                continue
            per = m.get("per", "band")
            scope = {"band": (x["band"],), "band_mode": (x["band"], x["modecat"]), "mode": (x["modecat"],),
                     "contest": ()}[per]
            out.append(((m["key"], *scope, str(val)), m.get("label", m["key"]), str(val)))
        return out

    def dupe_key(self, x: dict) -> tuple:
        if self.cdef.dupe == "once":
            return (x["call"],)
        if self.cdef.dupe == "band_mode":
            return (x["call"], x["band"], x["modecat"])
        return (x["call"], x["band"])

    # ---- scoring
    def preview(self, q: dict, exclude_id: int | None = None) -> QsoScore:
        x = self.ctx(q)
        key = self.dupe_key(x)
        other = self.dupe_keys.get(key)
        if other is not None and other != exclude_id:
            return QsoScore(points=0, dupe=True, dupe_of=other)
        pts = self.qso_points(x)
        new = [(label, val) for seen, label, val in self.mult_entries(q, x) if seen not in self.mult_seen]
        return QsoScore(points=pts, new_mults=new)

    def add(self, q: dict) -> QsoScore:
        x = self.ctx(q)
        key = self.dupe_key(x)
        row = self.rows.setdefault(x["band"], BandRow(x["band"]))
        if key in self.dupe_keys:
            self.dupe_count += 1
            s = QsoScore(points=0, dupe=True, dupe_of=self.dupe_keys[key])
            if q.get("id") is not None:
                self.scores[q["id"]] = s
            return s
        self.dupe_keys[key] = q.get("id", -1)
        pts = self.qso_points(x)
        new = []
        for seen, label, val in self.mult_entries(q, x):
            if seen not in self.mult_seen:
                self.mult_seen.add(seen)
                new.append((label, val))
                row.mults[seen[0]] += 1
        row.qsos += 1
        row.points += pts
        self.points_total += pts
        self.qso_count += 1
        if q.get("time_on"):
            self.times.append(q["time_on"])
        s = QsoScore(points=pts, new_mults=new)
        if q.get("id") is not None:
            self.scores[q["id"]] = s
        return s

    def rebuild(self, qsos: list[dict]) -> None:
        self.reset()
        for q in sorted(qsos, key=lambda q: (q.get("time_on") or "", q.get("id") or 0)):
            self.add(q)

    @property
    def mult_total(self) -> int:
        return len(self.mult_seen)

    def mult_counts(self) -> Counter:
        return Counter(k[0] for k in self.mult_seen)

    @property
    def score(self) -> int:
        return self.points_total * self.mult_total if self.cdef.mults else self.points_total

    def worked_mults(self, key: str, band: str | None = None, modecat: str | None = None) -> set[str]:
        m = next((m for m in self.cdef.mults if m["key"] == key), None)
        if not m:
            return set()
        per = m.get("per", "band")
        out = set()
        for seen in self.mult_seen:
            if seen[0] != key:
                continue
            if per == "band" and band and seen[1] != band:
                continue
            if per == "band_mode" and band and (seen[1] != band or (modecat and seen[2] != modecat)):
                continue
            if per == "mode" and modecat and seen[1] != modecat:
                continue
            out.add(seen[-1])
        return out

    def rate(self, now: str | None = None) -> dict[str, float]:
        """QSOs per hour over the last 10 and 100 QSOs, and QSOs in the last 60 minutes."""
        ts = sorted(self.times)
        now = now or timeutil.now_iso()

        def last_n(n: int) -> float:
            if len(ts) < 2:
                return 0.0
            sub = ts[-n:]
            span = timeutil.seconds_between(sub[0], now) or 1
            return len(sub) * 3600 / span

        hour = sum(1 for t in ts if timeutil.seconds_between(t, now) <= 3600)
        return {"last10": last_n(10), "last100": last_n(100), "hour": float(hour)}
