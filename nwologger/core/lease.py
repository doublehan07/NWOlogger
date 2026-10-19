"""One writing NWOlogger per station folder: an advisory lock the operating system holds on ``nwologger.lock``.

The lock is what makes "one instance" true whatever the route a second one took (another user, another settings
file, a path alias, the command line): two processes on one computer contend for the same lock on the same file.
It is released by the OS the moment the process ends, crash included, so there is never a stale lock to delete by
hand. The file's contents (pid, host, version, start and heartbeat times, as text) are only a hint for the message
and for the cross-computer case: the OS lock cannot reach another computer, but a cloud-synced lock file carries
its contents there, so a recent heartbeat from another host is worth a warning.

Within one process the lease is shared: a second ``LogDB`` on the same folder joins it (reference counted), so
switching logs inside one folder never contends with itself.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

from .. import __version__

log = logging.getLogger("nwologger.lease")
STALE_SECONDS = 10 * 60  # another computer's heartbeat older than this is not "in use"
_REGION = 1 << 30  # Windows locks a byte range; one far beyond the text keeps the text readable by others
_lock = threading.Lock()
_held: dict[Path, Lease] = {}


class LeaseHeld(Exception):
    """Another NWOlogger holds the folder. ``holder`` describes it from the lock file (may be empty)."""

    def __init__(self, folder: Path, holder: dict):
        self.folder = folder
        self.holder = holder
        super().__init__(f"{folder} is in use by another NWOlogger{describe(holder)}")


def describe(holder: dict) -> str:
    if not holder:
        return ""
    bits = []
    if holder.get("version"):
        bits.append(f"NWOlogger {holder['version']}")
    if holder.get("host"):
        bits.append(f"on {holder['host']}")
    if holder.get("pid"):
        bits.append(f"pid {holder['pid']}")
    if holder.get("started"):
        bits.append(f"since {holder['started'][:16].replace('T', ' ')} UTC")
    return " (" + ", ".join(bits) + ")" if bits else ""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_holder(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(text) if text.strip() else {}
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def other_computer(folder: str | os.PathLike) -> dict | None:
    """The holder recorded in the lock file when it is another computer whose heartbeat is recent: the one case the
    OS lock cannot see (a cloud-synced folder used from two computers). None otherwise."""
    holder = read_holder(Path(folder) / "nwologger.lock")
    if not holder or holder.get("host") in (None, socket.gethostname()):
        return None
    try:
        beat = datetime.fromisoformat(holder.get("beat") or holder.get("started") or "")
    except ValueError:
        return None
    if beat.tzinfo is None:
        beat = beat.replace(tzinfo=UTC)
    return holder if (datetime.now(UTC) - beat).total_seconds() < STALE_SECONDS else None


def _try_lock(fd: int) -> bool:
    if sys.platform == "win32":
        import msvcrt  # noqa: PLC0415

        try:
            os.lseek(fd, _REGION, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl  # noqa: PLC0415

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt  # noqa: PLC0415

            os.lseek(fd, _REGION, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl  # noqa: PLC0415

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


class Lease:
    def __init__(self, folder: Path, fd: int):
        self.folder = folder
        self.fd = fd
        self.count = 1
        self.started = _now_iso()
        self.heartbeat()

    def _write(self) -> None:
        data = {"pid": os.getpid(), "host": socket.gethostname(), "version": __version__, "started": self.started,
                "beat": _now_iso()}
        text = json.dumps(data) + "\n"
        try:
            os.lseek(self.fd, 0, os.SEEK_SET)
            os.ftruncate(self.fd, 0)
            os.write(self.fd, text.encode("utf-8"))
        except OSError:
            log.debug("Could not write the lock file's contents", exc_info=True)

    def heartbeat(self) -> None:
        """Refresh the time in the lock file (once a minute from the app) for the other-computer hint."""
        self._write()

    def release(self) -> None:
        with _lock:
            self.count -= 1
            if self.count > 0:
                return
            _held.pop(self.folder, None)
        _unlock(self.fd)
        try:
            os.close(self.fd)
        except OSError:
            pass
        log.info("Lease released: %s", self.folder)


def acquire(folder: str | os.PathLike) -> Lease:
    """Hold the folder, or raise LeaseHeld at once (never waits). A second call for the same folder in this process
    shares the lease."""
    key = Path(folder).resolve()
    with _lock:
        held = _held.get(key)
        if held is not None:
            held.count += 1
            return held
    key.mkdir(parents=True, exist_ok=True)
    path = key / "nwologger.lock"
    holder_before = read_holder(path)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    if not _try_lock(fd):
        os.close(fd)
        holder = read_holder(path) or holder_before
        log.warning("Station folder in use by another NWOlogger%s: %s", describe(holder), key)
        raise LeaseHeld(key, holder)
    lease = Lease(key, fd)
    with _lock:
        _held[key] = lease
    log.info("Lease taken: %s (pid %d)", key, os.getpid())
    return lease


def held(folder: str | os.PathLike) -> Lease | None:
    with _lock:
        return _held.get(Path(folder).resolve())


def heartbeat_all() -> None:
    with _lock:
        leases = list(_held.values())
    for lease in leases:
        lease.heartbeat()


__all__ = ["Lease", "LeaseHeld", "acquire", "describe", "held", "heartbeat_all", "other_computer", "read_holder",
           "STALE_SECONDS"]
