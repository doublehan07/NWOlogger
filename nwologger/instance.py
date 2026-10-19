"""One running NWOlogger per user.

Windows and Linux start a new process on every double-click (macOS brings the running app to the front by itself),
and two NWOloggers on one log file go stale against each other, can upload a QSO twice and overwrite each other's
download positions and settings. So a starting NWOlogger first knocks on a local socket (a named pipe on Windows,
a socket file elsewhere) named after the user and the settings in use; a running copy answers, is asked to come to
the front and the new process quits. Tests and screenshot runs set NWOLOGGER_SETTINGS, which gives them a socket of
their own.

The socket is Python's own (multiprocessing.connection), served from a thread of its own, so the handshake never
depends on the GUI event loop: the knock is answered within a second whatever the window is doing.
"""

from __future__ import annotations

import getpass
import hashlib
import logging
import os
import sys
import tempfile
import threading
from multiprocessing import connection
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .core import station

log = logging.getLogger("nwologger.instance")
KNOCK_MS = 1000  # how long a start waits for a running copy to answer
RAISE = "raise"  # the message: "raise" or "raise <log file the second launch was asked to open>"
ACK = b"ok"  # the running copy's answer once it has read the message


def socket_name(user: str | None = None, folder: str | os.PathLike | None = None) -> str:
    """The socket's name for this user and station folder (its resolved path, so every spelling of the folder meets
    the same socket; NWOLOGGER_SETTINGS, which tests set, keeps them apart from the real one)."""
    if user is None:
        try:
            user = getpass.getuser()
        except (OSError, KeyError, ImportError):
            user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    where = str(Path(folder).resolve()) if folder is not None else str(station.folder().resolve())
    settings = os.environ.get("NWOLOGGER_SETTINGS") or ""
    digest = hashlib.sha1(f"{user}\0{where}\0{settings}".encode()).hexdigest()[:12]
    return f"nwologger-{digest}"


def address(name: str) -> str:
    """Where the socket lives: a named pipe on Windows, a file in the temporary folder elsewhere."""
    if sys.platform == "win32":
        return rf"\\.\pipe\{name}"
    return os.path.join(tempfile.gettempdir(), name)


def _family() -> str:
    return "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"


def knock(name: str, message: str = RAISE, timeout_ms: int = KNOCK_MS) -> bool:
    """Tell a running copy listening on ``name`` to come to the front. True when one answered the connection."""
    try:
        conn = connection.Client(address(name), family=_family())
    except (OSError, EOFError):  # nothing listens, or a socket file left by a crash refuses the connection
        return False
    try:
        conn.send_bytes(message.encode("utf-8"))
        # Wait for the answer, so the message is read before this end closes; a running copy that accepted the
        # connection but did not answer in time still counts as running.
        if not conn.poll(timeout_ms / 1000):
            log.warning("The running NWOlogger accepted the request but did not answer within %d ms", timeout_ms)
    except (OSError, EOFError):
        pass
    finally:
        conn.close()
    return True


class InstanceServer(QObject):
    """The running copy's end: a thread accepts each knock, answers it and reports the request to come to the front
    through a Qt signal (delivered in the GUI thread), with the log file the other launch was asked to open or None."""

    raise_requested = Signal(object)

    def __init__(self, name: str, parent: QObject | None = None):
        super().__init__(parent)
        self.name = name
        self.address = address(name)
        try:
            self.listener = connection.Listener(self.address, family=_family())
        except OSError:
            if sys.platform == "win32":
                raise
            # A socket file left by a crash (Linux, macOS): nothing answered the knock, so remove it and listen.
            try:
                os.unlink(self.address)
            except OSError:
                pass
            self.listener = connection.Listener(self.address, family=_family())
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="instance", daemon=True)
        self._thread.start()

    @property
    def listening(self) -> bool:
        return not self._stop.is_set()

    def _serve(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    conn = self.listener.accept()
                except Exception:  # noqa: BLE001 - closed from the GUI thread, or the pipe is gone
                    if not self._stop.is_set():
                        log.debug("The instance socket stopped", exc_info=True)
                    return
                if self._stop.is_set():  # the wake-up connection from close(), or a knock during shutdown
                    conn.close()
                    return
                text = ""
                try:
                    if conn.poll(KNOCK_MS / 1000):
                        text = conn.recv_bytes(4096).decode("utf-8", errors="replace").strip()
                    conn.send_bytes(ACK)
                except (OSError, EOFError):
                    pass
                finally:
                    conn.close()
                head, _, path = text.partition(" ")
                if head == RAISE:
                    log.info("Another NWOlogger was started; bringing this window to the front")
                    self.raise_requested.emit(path or None)
        finally:
            try:
                self.listener.close()
            except OSError:
                pass

    def close(self) -> None:
        """Stop listening and end the thread. On Windows the thread waits on a pipe instance that Listener.close()
        does not reach, so one connection of our own wakes it; it sees the stop flag and ends. Repeated calls are
        safe. A thread that does not end within 2 s is reported, not hidden."""
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            connection.Client(self.address, family=_family()).close()  # wake accept(); no message, so no raise
        except (OSError, EOFError):
            pass
        self._thread.join(2)
        if self._thread.is_alive():
            log.warning("The instance socket's thread did not end within 2 s")
        try:
            self.listener.close()
        except OSError:
            pass


def claim(db_path: str | None = None) -> InstanceServer | None:
    """Become the running NWOlogger, or ask the one that already runs to come to the front and return None."""
    name = socket_name()
    if knock(name, RAISE + (f" {db_path}" if db_path else "")):
        log.info("NWOlogger is already running: asked it to come to the front")
        return None
    return InstanceServer(name)
