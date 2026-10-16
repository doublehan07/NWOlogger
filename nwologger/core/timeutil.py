"""UTC time helpers. QSO times are stored as ISO-8601 text: ``2026-10-10T15:19:00Z``."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def now_iso() -> str:
    return to_iso(now_utc())


def today_iso() -> str:
    return now_utc().date().isoformat()


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime(ISO_FMT)


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    for fmt in (ISO_FMT, "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%MZ", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def shift_iso(s: str, seconds: float) -> str:
    dt = parse_iso(s)
    assert dt is not None, s
    return to_iso(dt + timedelta(seconds=seconds))


def seconds_between(a: str, b: str) -> float:
    da, db = parse_iso(a), parse_iso(b)
    if da is None or db is None:
        return float("inf")
    return abs((da - db).total_seconds())


def adif_to_iso(qso_date: str | None, time_on: str | None) -> str | None:
    """ADIF ``YYYYMMDD`` + ``HHMM[SS]`` to ISO text."""
    if not qso_date:
        return None
    d = qso_date.strip()
    t = (time_on or "0000").strip()
    if len(t) == 4:
        t += "00"
    if len(d) != 8 or len(t) != 6 or not (d.isdigit() and t.isdigit()):
        return None
    try:
        dt = datetime.strptime(d + t, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None
    return to_iso(dt)


def iso_to_adif(s: str | None) -> tuple[str, str] | None:
    dt = parse_iso(s)
    if dt is None:
        return None
    return dt.strftime("%Y%m%d"), dt.strftime("%H%M%S")


def adif_date_to_iso(d: str | None) -> str | None:
    """ADIF ``YYYYMMDD`` to ``YYYY-MM-DD``."""
    if not d:
        return None
    d = d.strip()
    if len(d) == 8 and d.isdigit():
        try:
            return date(int(d[:4]), int(d[4:6]), int(d[6:])).isoformat()
        except ValueError:
            return None
    if len(d) == 10 and d[4] == "-" and d[7] == "-":
        return d
    return None


def iso_date_to_adif(d: str | None) -> str | None:
    if not d:
        return None
    d = d.strip()[:10]
    if len(d) == 10 and d[4] == "-":
        return d.replace("-", "")
    if len(d) == 8 and d.isdigit():
        return d
    return None


def epoch_to_iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return to_iso(datetime.fromtimestamp(float(ts), UTC))
