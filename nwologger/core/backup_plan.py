"""The regular backup plan (Preferences → Database). Every N days, when the log changed, a copy goes into
``backups/`` by itself, and a reminder asks for a copy on another disk or in a cloud folder. The dates live in the
log's ``meta`` table, so the plan follows the log file; the folder for the outside copy is a setting of this computer.
Backups made at launch and before risky operations (:meth:`db.LogDB.backup`) are a separate matter."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from pathlib import Path

SCHEDULED_AT = "scheduled_backup_at"  # meta: when the last scheduled copy was made, or the clock started
SCHEDULED_FP = "scheduled_backup_of"  # meta: the QSOs' fingerprint at that copy, so an unchanged log gets no new one
EXTERNAL_AT = "external_backup_at"  # meta: when the log was last copied to the outside folder
ASKED_ON = "external_backup_asked_on"  # meta: the UTC day the reminder was last shown (Later)
SCHEDULED = "scheduled"  # the backup label; its copies have a pool of their own

log = logging.getLogger("nwologger.backup")


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _day(text: str | None) -> date | None:
    try:
        return date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


def days_since(db, key: str, now: datetime | None = None) -> int | None:
    """Whole UTC days since the date in meta ``key``; None when it is unset or unreadable."""
    d = _day(db.meta(key))
    return (_now(now).date() - d).days if d else None


def start_clock(db, key: str, now: datetime | None = None) -> None:
    """A log without the date gets it set to now: nothing is due at once, the first interval starts here."""
    if _day(db.meta(key)) is None:
        db.set_meta(key, _now(now).isoformat(timespec="seconds"))


def fingerprint(db) -> str:
    """What the QSOs look like in one line: their count, the newest change and the highest id. A logged, edited or
    deleted QSO changes it; the plan's own bookkeeping in ``meta`` does not."""
    n, updated, top = db.conn.execute("SELECT COUNT(*), MAX(updated_at), MAX(id) FROM qso").fetchone()
    return f"{n}:{updated or ''}:{top or 0}"


def run_scheduled(db, every_days: int, now: datetime | None = None) -> Path | None:
    """The scheduled copy when it is due. The first check only starts the clock. An unchanged log gets no new file
    (the last copy already shows it), but the time moves either way, so the next copy comes an interval later.
    Returns the copy made, if any."""
    if every_days <= 0:
        return None
    d = days_since(db, SCHEDULED_AT, now)
    fp = fingerprint(db)
    if d is None or d >= every_days:
        changed = d is not None and fp != db.meta(SCHEDULED_FP)
        db.set_meta(SCHEDULED_AT, _now(now).isoformat(timespec="seconds"))
        db.set_meta(SCHEDULED_FP, fp)
        return db.backup(SCHEDULED) if changed else None
    return None


def external_due(db, every_days: int, now: datetime | None = None) -> bool:
    """Whether to remind: ``every_days`` whole days since the last outside copy (the clock starts at the first
    check, as for the scheduled copy) and not already asked today."""
    if every_days <= 0:
        return False
    d = days_since(db, EXTERNAL_AT, now)
    if d is None:
        start_clock(db, EXTERNAL_AT, now)
        return False
    return d >= every_days and _day(db.meta(ASKED_ON)) != _now(now).date()


def asked(db, now: datetime | None = None) -> None:
    """Later: not again today."""
    db.set_meta(ASKED_ON, _now(now).date().isoformat())


def copy_outside(db, folder: str | Path, now: datetime | None = None) -> Path:
    """Copy the log into ``folder`` (another disk, a cloud folder) and record the time."""
    dest = db.backup_to(folder)
    db.set_meta(EXTERNAL_AT, _now(now).isoformat(timespec="seconds"))
    return dest


def size_text(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{max(1, round(n / 1e3))} KB"
