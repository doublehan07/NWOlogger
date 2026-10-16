"""Check Partial (MASTER.SCP + your own log) and N+1 calls."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

SCP_URL = "https://www.supercheckpartial.com/MASTER.SCP"


class Scp:
    def __init__(self, calls: Iterable[str] = ()):
        self.calls: set[str] = {c.strip().upper() for c in calls if c and c.strip()}

    @classmethod
    def from_file(cls, path: str | Path) -> Scp:
        p = Path(path)
        if not p.exists():
            return cls()
        lines = p.read_text(encoding="latin-1", errors="replace").splitlines()
        return cls(ln for ln in lines if ln and not ln.startswith("#"))

    def add(self, calls: Iterable[str]) -> None:
        self.calls.update(c.upper() for c in calls if c)

    def partial(self, text: str, limit: int = 60) -> list[str]:
        t = text.strip().upper()
        if len(t) < 2:
            return []
        exact, starts, contains = [], [], []
        for c in self.calls:
            if c == t:
                exact.append(c)
            elif c.startswith(t):
                starts.append(c)
            elif t in c:
                contains.append(c)
        return (exact + sorted(starts) + sorted(contains))[:limit]

    def n_plus_one(self, call: str, limit: int = 30) -> list[str]:
        """Calls one edit away: one character changed, added or removed."""
        t = call.strip().upper()
        if len(t) < 3:
            return []
        out = []
        n = len(t)
        for c in self.calls:
            m = len(c)
            if c == t or abs(m - n) > 1:
                continue
            if m == n:
                if sum(1 for a, b in zip(c, t, strict=True) if a != b) == 1:
                    out.append(c)
            else:
                short, long_ = (c, t) if m < n else (t, c)
                i = 0
                while i < len(short) and short[i] == long_[i]:
                    i += 1
                if short[i:] == long_[i + 1:]:
                    out.append(c)
            if len(out) >= limit:
                break
        return sorted(out)
