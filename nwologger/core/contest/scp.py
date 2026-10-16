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
        for c in self.calls:
            if one_edit(t, c):
                out.append(c)
                if len(out) >= limit:
                    break
        return sorted(out)


def one_edit(a: str, b: str) -> bool:
    """True when ``b`` is ``a`` with one character changed, added or removed."""
    n, m = len(a), len(b)
    if a == b or abs(m - n) > 1:
        return False
    if m == n:
        return sum(1 for x, y in zip(a, b, strict=True) if x != y) == 1
    short, long_ = (a, b) if n < m else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    return short[i:] == long_[i + 1:]


def path_for(db_path: str | Path) -> Path:
    """MASTER.SCP lives next to the log file; the DX and contest panels share it."""
    return Path(db_path).parent / "MASTER.SCP"


def download(dest: str | Path) -> int:
    """Fetch MASTER.SCP from supercheckpartial.com. Returns the number of calls."""
    import httpx  # noqa: PLC0415

    r = httpx.get(SCP_URL, timeout=60, follow_redirects=True)
    r.raise_for_status()
    Path(dest).write_bytes(r.content)
    return len(Scp.from_file(dest).calls)
