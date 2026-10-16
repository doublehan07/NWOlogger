"""Similar calls for the DX panel's Worked before: your log first, then calls QRZ.com / HamQTH know, then MASTER.SCP.

A typed call matches a known call when it is the same, the start of it, inside it, or one character away (one
changed, added or removed: a typo). Portable calls match through their base call, so BA1PK/P counts as BA1PK.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from . import callsign
from .contest.scp import one_edit

EXACT, PREFIX, CONTAINS, ONE_EDIT = range(4)
WORKED, KNOWN, SCP = range(3)


def match_kind(text: str, call: str) -> int | None:
    """How ``call`` matches what you typed (lower is better), or None."""
    if call == text:
        return EXACT
    if call.startswith(text):
        return PREFIX
    if text in call:
        return CONTAINS
    if len(text) >= 3 and one_edit(text, call):
        return ONE_EDIT
    return None


@dataclass(frozen=True)
class Match:
    call: str
    tier: int  # WORKED, KNOWN or SCP
    kind: int  # EXACT … ONE_EDIT
    source: str = ""  # QRZ.com / HamQTH for KNOWN


class CallIndex:
    """In-memory call lists, so matching while you type never queries the log file."""

    def __init__(self, worked: dict[str, str] | None = None, known: dict[str, str] | None = None,
                 scp: Iterable[str] = ()):
        self.worked: dict[str, str] = {}  # call → last time on
        self.known: dict[str, str] = {}  # call → source
        self.scp: set[str] = set()
        self._base: dict[str, str] = {}
        self.version = 0  # bumped on every change, so callers can skip re-matching
        self.add_worked(worked or {})
        self.add_known(known or {})
        self.set_scp(scp)

    def _index(self, call: str) -> None:
        if "/" in call and call not in self._base:
            self._base[call] = callsign.parse(call).base

    def add_worked(self, calls: dict[str, str]) -> None:
        self.version += 1
        for c, t in calls.items():
            c = c.upper()
            if t and t > self.worked.get(c, ""):
                self.worked[c] = t
            else:
                self.worked.setdefault(c, t or "")
            self._index(c)

    def set_worked(self, calls: dict[str, str]) -> None:
        self.worked = {}
        self.add_worked(calls)

    def add_known(self, calls: dict[str, str]) -> None:
        self.version += 1
        for c, src in calls.items():
            self.known[c.upper()] = src or ""
            self._index(c.upper())

    def set_scp(self, calls: Iterable[str]) -> None:
        self.version += 1
        self.scp = {c.upper() for c in calls if c}
        for c in self.scp:
            self._index(c)

    def _kind(self, text: str, call: str) -> int | None:
        k = match_kind(text, call)
        base = self._base.get(call)
        if base:
            kb = match_kind(text, base)
            if kb is not None and (k is None or kb < k):
                k = kb
        return k

    def find(self, text: str, limit: int = 30) -> list[Match]:
        """Best first: worked before (most recent first), then known on QRZ.com / HamQTH, then MASTER.SCP."""
        t = callsign.clean(text)
        if "/" in t:
            t = callsign.parse(t).base
        if len(t) < 2:
            return []
        out: list[Match] = []
        for c in self.worked:
            k = self._kind(t, c)
            if k is not None:
                out.append(Match(c, WORKED, k))
        for c, src in self.known.items():
            if c not in self.worked:
                k = self._kind(t, c)
                if k is not None:
                    out.append(Match(c, KNOWN, k, src))
        for c in self.scp:
            if c not in self.worked and c not in self.known:
                k = self._kind(t, c)
                if k is not None:
                    out.append(Match(c, SCP, k))
        out.sort(key=lambda m: m.call)
        out.sort(key=lambda m: self.worked.get(m.call, "") if m.tier == WORKED else "", reverse=True)
        out.sort(key=lambda m: (m.tier, m.kind))
        return out[:limit]

    def online_candidates(self, text: str, skip: Iterable[str] = (), limit: int = 3) -> list[str]:
        """MASTER.SCP calls one character away that are neither in your log nor known to QRZ.com / HamQTH yet."""
        t = callsign.clean(text)
        skip = set(skip)
        cands = [c for c in self.scp if c not in self.worked and c not in self.known and c not in skip and
                 one_edit(t, c)]
        cands.sort(key=lambda c: (len(c) != len(t), c))  # a changed character first, then one added or dropped
        return cands[:limit]
