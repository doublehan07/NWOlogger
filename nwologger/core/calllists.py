"""Public call lists for Similar calls: ARRL's LoTW users and eQSL's Authenticity Guaranteed (AG) members.

QRZ.com and HamQTH can only look up one complete call (their APIs have no partial or wildcard search), so the
internet's call data reaches Similar calls through these two free files instead. They are downloaded next to the log
file and refreshed weekly, and matched on your computer while you type: no request per keystroke, and the download
carries no login. Together they hold about 436,000 calls; :class:`CallLists` keeps them as one sorted text with
offsets (about 7 MB) and answers exact, prefix, contains and one-character-away questions by binary search.
"""

from __future__ import annotations

import os
import re
import string
import tempfile
import time
from array import array
from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import callsign, net

EXACT, PREFIX, CONTAINS, ONE_EDIT = range(4)  # the same order as similar.match_kind


@dataclass(frozen=True)
class Source:
    key: str
    url: str
    file: str
    service: str  # net.request service: its HTTPS hosts only


SOURCES = (
    Source("lotw", "https://lotw.arrl.org/lotw-user-activity.csv", "lotw-user-activity.csv", "lotw"),
    Source("eqsl_ag", "https://www.eqsl.cc/qslcard/DownloadedFiles/AGMemberList.txt", "eqsl-ag-members.txt", "eqsl"),
)
REFRESH_DAYS = 7
_CALL = re.compile(r"^(?=[A-Z0-9/]*\d)(?=[A-Z0-9/]*[A-Z])[A-Z0-9/]{3,15}$")
_ALPHABET = string.ascii_uppercase + string.digits
_EPOCH = date(1970, 1, 1).toordinal()
_MIN_CALLS = 1000  # a real list has hundreds of thousands; fewer means an error page


def _is_call(c: str) -> bool:
    """A callsign, also with a portable part (BA1PK/P); not one of the AG list's member numbers (108CT322)."""
    return bool(_CALL.match(c)) and any(callsign.CALL_RE.match(part) for part in c.split("/"))


def folder_for(db_path: str | os.PathLike) -> Path:
    """The lists live next to the log file, like MASTER.SCP."""
    return Path(db_path).parent


def parse_lotw(text: str) -> dict[str, str]:
    """``lotw-user-activity.csv``: ``CALL,YYYY-MM-DD,HH:MM:SS`` per line → {call: date of the last upload}."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split(",")
        call = parts[0].strip().upper()
        if _is_call(call):
            out[call] = parts[1].strip() if len(parts) > 1 else ""
    return out


def parse_ag(text: str) -> set[str]:
    """``AGMemberList.txt``: a heading line, then one call per line."""
    return {c for c in (ln.strip().upper() for ln in text.splitlines()) if _is_call(c)}


def _day(iso: str | None) -> int:
    """Days since 1970 + 1 for the LoTW date (0 = not a LoTW user, 1 = date unknown)."""
    try:
        return max(2, date.fromisoformat(iso or "").toordinal() - _EPOCH + 1) if iso else 1
    except ValueError:
        return 1


def _iso(day: int) -> str:
    return date.fromordinal(day - 1 + _EPOCH).isoformat() if day > 1 else ""


@dataclass(frozen=True)
class Hit:
    call: str
    kind: int  # EXACT … ONE_EDIT
    lotw: str | None  # date of the last LoTW upload ("" when unknown), None when not a LoTW user
    ag: bool  # an eQSL AG member


class CallLists:
    """Both lists in one sorted index that is quick to search while you type."""

    def __init__(self, lotw: dict[str, str] | None = None, ag: Iterable[str] = ()):
        lotw = lotw or {}
        ag = set(ag)
        order = sorted(set(lotw) | ag)
        self._blob = "\n".join(order) + "\n"
        self._starts = array("I")
        pos = 0
        for c in order:
            self._starts.append(pos)
            pos += len(c) + 1
        self._lotw = array("H", (_day(lotw[c]) if c in lotw else 0 for c in order))
        self._ag = bytes(1 if c in ag else 0 for c in order)
        self.lotw_count, self.ag_count = len(lotw), len(ag)
        self._n = len(order)

    def __len__(self) -> int:
        return self._n

    def _call(self, i: int) -> str:
        s = self._starts[i]
        return self._blob[s:self._blob.index("\n", s)]

    def _lower(self, text: str) -> int:
        return bisect_left(range(self._n), text, key=self._call)

    def _index(self, call: str) -> int | None:
        i = self._lower(call)
        return i if i < self._n and self._call(i) == call else None

    def __contains__(self, call: str) -> bool:
        return self._index(call) is not None

    def _hit(self, i: int, kind: int) -> Hit:
        d = self._lotw[i]
        return Hit(self._call(i), kind, _iso(d) if d else None, bool(self._ag[i]))

    def get(self, call: str) -> Hit | None:
        i = self._index(call)
        return self._hit(i, EXACT) if i is not None else None

    def matches(self, text: str, limit: int = 40) -> list[Hit]:
        """Calls that are ``text``, start with it, contain it, or are one character away (at most ``limit`` of
        each kind)."""
        t = text.strip().upper()
        if len(t) < 2 or not self._n:
            return []
        out: dict[int, int] = {}
        lo, hi = self._lower(t), self._lower(t + "~")  # "~" sorts after every call character
        for i in range(lo, min(hi, lo + limit + 1)):
            out[i] = EXACT if self._call(i) == t else PREFIX
        found, p = 0, self._blob.find(t)
        while p >= 0 and found < limit:
            i = bisect_right(self._starts, p) - 1
            if lo <= i < hi:  # starts with t: already listed; jump past that block
                nxt = self._starts[hi] if hi < self._n else len(self._blob)
            else:
                out.setdefault(i, CONTAINS)
                found += 1
                nxt = self._starts[i + 1] if i + 1 < self._n else len(self._blob)
            p = self._blob.find(t, nxt)
        if len(t) >= 3:
            for v in _one_edit_variants(t):
                i = self._index(v)
                if i is not None:
                    out.setdefault(i, ONE_EDIT)
        return [self._hit(i, k) for i, k in out.items()]


def _one_edit_variants(t: str) -> set[str]:
    """Every text one character changed, added or removed (a typo or a missing letter or digit)."""
    out = {t[:i] + t[i + 1:] for i in range(len(t))}
    for i in range(len(t) + 1):
        for ch in _ALPHABET:
            out.add(t[:i] + ch + t[i:])
            if i < len(t):
                out.add(t[:i] + ch + t[i + 1:])
    out.discard(t)
    return out


# ------------------------------------------------------------------ files

def age_days(folder: str | os.PathLike) -> float | None:
    """Age of the older list, or None when one is missing."""
    ages = []
    for s in SOURCES:
        p = Path(folder) / s.file
        if not p.exists():
            return None
        ages.append((time.time() - p.stat().st_mtime) / 86400)
    return max(ages)


def load(folder: str | os.PathLike) -> CallLists:
    """The lists found in ``folder`` (an empty index when none were downloaded yet)."""
    texts = {}
    for s in SOURCES:
        p = Path(folder) / s.file
        texts[s.key] = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    return CallLists(parse_lotw(texts["lotw"]), parse_ag(texts["eqsl_ag"]))


def download(folder: str | os.PathLike) -> dict[str, int]:
    """Fetch both lists over HTTPS from lotw.arrl.org and www.eqsl.cc (no login). Returns calls per list. A list
    that fails keeps its previous copy."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    counts = {}
    for s in SOURCES:
        r = net.request(s.service, "GET", s.url, timeout=180.0, redirects=True)
        text = r.content.decode("utf-8", errors="replace")
        n = len(parse_lotw(text) if s.key == "lotw" else parse_ag(text))
        if n < _MIN_CALLS:
            raise net.NetError(f"{net.NAMES[s.service]}: the call list looks wrong ({n} calls); kept the old one")
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{s.file}.")
        with os.fdopen(fd, "wb") as fh:
            fh.write(r.content)
        os.replace(tmp, folder / s.file)
        counts[s.key] = n
    return counts
