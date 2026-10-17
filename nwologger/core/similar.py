"""Similar calls for the DX panel's Worked before: your log first, then calls QRZ.com / HamQTH know, then the
public LoTW user and eQSL AG member lists (:mod:`calllists`), then MASTER.SCP.

A typed call matches a known call when it is the same, the start of it, inside it, or one character away (one
changed, added or removed: a typo). Portable calls match through their base call, so BA1PK/P counts as BA1PK.

With ``*`` or ``?`` the typed text is a pattern, as in the log's search box: ``*`` is any run of characters (also
none), ``?`` exactly one, and the pattern covers the whole call, so ``BA*K`` finds BA1PK and BA8BLK in every source.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import islice

from . import callsign
from .calllists import CallLists
from .contest.scp import one_edit

EXACT, PREFIX, CONTAINS, ONE_EDIT, PATTERN = range(5)
WORKED, KNOWN, LISTED, SCP = range(4)
PATTERN_CAP = 500  # pattern matches kept per source; more are only counted as "more"


def has_wildcards(text: str) -> bool:
    return "*" in text or "?" in text


def pattern_regex(pattern: str, *, lines: bool = False) -> str:
    """A regex for a ``*`` / ``?`` pattern over a whole call. ``lines``: for a text of one call per line, where a
    call also matches through its portable parts (``VK/BA1PK``, ``BA1PK/P``) unless the pattern has a ``/``."""
    any_run, one = (r"[^\n]*", r"[^\n]") if lines else (".*", ".")
    body = "".join(any_run if ch == "*" else one if ch == "?" else re.escape(ch) for ch in pattern)
    if lines and "/" not in pattern:
        body = rf"(?:[^\n/]*/)?{body}(?:/[^\n]*)?"
    return f"^{body}$"


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
    tier: int  # WORKED, KNOWN, LISTED or SCP
    kind: int  # EXACT … ONE_EDIT, or PATTERN
    source: str = ""  # QRZ.com / HamQTH for KNOWN
    lotw: str | None = None  # LISTED: date of the last LoTW upload ("" unknown); None: not a LoTW user
    ag: bool = False  # LISTED: an eQSL AG member


class CallIndex:
    """In-memory call lists, so matching while you type never queries the log file."""

    def __init__(self, worked: dict[str, str] | None = None, known: dict[str, str] | None = None,
                 scp: Iterable[str] = ()):
        self.worked: dict[str, str] = {}  # call → last time on
        self.known: dict[str, str] = {}  # call → source
        self.scp: set[str] = set()
        self.lists: CallLists | None = None
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

    def set_lists(self, lists: CallLists | None) -> None:
        self.version += 1
        self.lists = lists

    def _kind(self, text: str, call: str) -> int | None:
        k = match_kind(text, call)
        base = self._base.get(call)
        if base:
            kb = match_kind(text, base)
            if kb is not None and (k is None or kb < k):
                k = kb
        return k

    def find(self, text: str, limit: int = 30) -> list[Match]:
        """Best first: worked before (most recent first), then known on QRZ.com / HamQTH, then LoTW users and eQSL
        AG members, then MASTER.SCP. A ``*`` / ``?`` pattern goes to :meth:`find_pattern`."""
        if has_wildcards(text):
            return self.find_pattern(text)[0][:limit]
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
        listed: set[str] = set()
        if self.lists is not None and len(self.lists):
            for h in self.lists.matches(t):
                if h.call not in self.worked and h.call not in self.known:
                    out.append(Match(h.call, LISTED, h.kind, lotw=h.lotw, ag=h.ag))
                    listed.add(h.call)
        for c in self.scp:
            if c not in self.worked and c not in self.known and c not in listed:
                k = self._kind(t, c)
                if k is not None and (self.lists is None or c not in self.lists):
                    out.append(Match(c, SCP, k))
        return self._sort(out)[:limit]

    def _sort(self, out: list[Match]) -> list[Match]:
        out.sort(key=lambda m: m.call)
        out.sort(key=lambda m: self.worked.get(m.call, "") if m.tier == WORKED else "", reverse=True)
        out.sort(key=lambda m: (m.tier, m.kind))
        return out

    def find_pattern(self, text: str, cap: int = PATTERN_CAP) -> tuple[list[Match], bool]:
        """Every call matching a ``*`` / ``?`` pattern, best first as :meth:`find`: at most ``cap`` from each
        source, and True when a source had more. Empty without at least one letter or digit."""
        p = callsign.clean(text)
        if len(p) < 2 or not any(ch not in "*?" for ch in p):
            return [], False
        rx = re.compile(pattern_regex(p))
        portable = "/" not in p

        def hit(call: str) -> bool:
            if rx.match(call):
                return True
            base = self._base.get(call)
            return bool(portable and base and rx.match(base))

        more = False

        def capped(calls: Iterable[str]) -> list[str]:
            nonlocal more
            got = list(islice(calls, cap + 1))
            more = more or len(got) > cap
            return got[:cap]

        out = [Match(c, WORKED, PATTERN) for c in capped(c for c in self.worked if hit(c))]
        out += [Match(c, KNOWN, PATTERN, self.known[c]) for c in
                capped(c for c in self.known if c not in self.worked and hit(c))]
        listed: set[str] = set()
        if self.lists is not None and len(self.lists):
            hits, over = self.lists.glob(pattern_regex(p, lines=True), cap)
            more = more or over
            for h in hits:
                if h.call not in self.worked and h.call not in self.known:
                    out.append(Match(h.call, LISTED, PATTERN, lotw=h.lotw, ag=h.ag))
                    listed.add(h.call)
        out += [Match(c, SCP, PATTERN) for c in capped(
            c for c in self.scp if c not in self.worked and c not in self.known and c not in listed and hit(c) and
            (self.lists is None or c not in self.lists))]
        return self._sort(out), more

    def online_candidates(self, text: str, skip: Iterable[str] = (), limit: int = 3) -> list[str]:
        """MASTER.SCP calls one character away that are neither in your log nor known to QRZ.com / HamQTH yet."""
        t = callsign.clean(text)
        skip = set(skip)
        cands = [c for c in self.scp if c not in self.worked and c not in self.known and c not in skip and
                 one_edit(t, c)]
        cands.sort(key=lambda c: (len(c) != len(t), c))  # a changed character first, then one added or dropped
        return cands[:limit]
