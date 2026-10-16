"""Who is operating. Personal stations use their own call; club stations ask for the operator.

For a club station the operator is asked on the first QSO after start, kept while the app runs, and confirmed
again on a new UTC day or after a break of ``GAP_HOURS`` without logging.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import timeutil

GAP_HOURS = 3


def is_club(profile: dict | None) -> bool:
    return bool(profile) and (profile.get("station_type") or "personal") == "club"


class OperatorSession:
    def __init__(self) -> None:
        self._ops: dict[str, tuple[str, datetime]] = {}  # station call → (operator, last activity)

    def personal_operator(self, profile: dict) -> str | None:
        return (profile.get("operator") or profile.get("station_callsign") or "").upper() or None

    def current(self, profile: dict | None) -> str | None:
        if not profile:
            return None
        if not is_club(profile):
            return self.personal_operator(profile)
        got = self._ops.get((profile.get("station_callsign") or "").upper())
        return got[0] if got else None

    def needs_confirmation(self, profile: dict | None, now: datetime | None = None) -> bool:
        """True when a club station has no operator yet, a new UTC day started, or after a long break."""
        if not is_club(profile):
            return False
        got = self._ops.get((profile.get("station_callsign") or "").upper())
        if not got:
            return True
        now = now or timeutil.now_utc()
        _op, last = got
        return last.date() != now.date() or now - last > timedelta(hours=GAP_HOURS)

    def set(self, profile: dict, operator: str, now: datetime | None = None) -> None:
        self._ops[(profile.get("station_callsign") or "").upper()] = (operator.upper().strip(),
                                                                     now or timeutil.now_utc())

    def touch(self, profile: dict | None, now: datetime | None = None) -> None:
        """Record activity (a logged QSO) so the break timer restarts."""
        if not is_club(profile):
            return
        key = (profile.get("station_callsign") or "").upper()
        if key in self._ops:
            self._ops[key] = (self._ops[key][0], now or timeutil.now_utc())
