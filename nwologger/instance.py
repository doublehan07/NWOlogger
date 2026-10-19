"""One running NWOlogger per user.

Windows and Linux start a new process on every double-click (macOS brings the running app to the front by itself),
and two NWOloggers on one log file go stale against each other, can upload a QSO twice and overwrite each other's
download positions and settings. So a starting NWOlogger first knocks on a local socket (a named pipe on Windows,
a socket file elsewhere) named after the user and the settings in use; a running copy answers, is asked to come to
the front and the new process quits. Tests and screenshot runs set NWOLOGGER_SETTINGS, which gives them a socket of
their own.
"""

from __future__ import annotations

import getpass
import hashlib
import logging
import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger("nwologger.instance")
KNOCK_MS = 1000  # how long a start waits for a running copy to answer
RAISE = "raise"  # the message: "raise" or "raise <log file the second launch was asked to open>"
ACK = b"ok\n"  # the running copy's answer once it has read the message


def socket_name(user: str | None = None, settings: str | None = None) -> str:
    """The socket's name for this user and settings file (NWOLOGGER_SETTINGS; the real preferences when unset)."""
    if user is None:
        try:
            user = getpass.getuser()
        except (OSError, KeyError, ImportError):
            user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    if settings is None:
        settings = os.environ.get("NWOLOGGER_SETTINGS") or ""
    digest = hashlib.sha1(f"{user}\0{settings}".encode()).hexdigest()[:12]
    return f"nwologger-{digest}"


def knock(name: str, message: str = RAISE, timeout_ms: int = KNOCK_MS) -> bool:
    """Tell a running copy listening on ``name`` to come to the front. True when one answered."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write((message + "\n").encode("utf-8"))
    sock.waitForBytesWritten(timeout_ms)
    # Wait for the answer, so the bytes are read before this end closes (a Windows pipe may drop them otherwise);
    # a running copy that connected but did not answer in time still counts as running.
    sock.waitForReadyRead(timeout_ms)
    sock.disconnectFromServer()
    if sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        sock.waitForDisconnected(timeout_ms)
    return True


class InstanceServer(QObject):
    """The running copy's end: listens on the socket and reports every request to come to the front, with the log
    file the other launch was asked to open (or None)."""

    raise_requested = Signal(object)

    def __init__(self, name: str, parent: QObject | None = None):
        super().__init__(parent)
        self.name = name
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._accept)
        if not self.server.listen(name):
            # A socket file left by a crash (Linux, macOS): remove it and listen again.
            QLocalServer.removeServer(name)
            if not self.server.listen(name):
                raise OSError(f"cannot listen on {name}: {self.server.errorString()}")

    def _accept(self) -> None:
        """Read each knock as it comes: the other process writes its line right after connecting, so this waits at
        most KNOCK_MS, once per launch of a second NWOlogger. The connection stays a child of the server."""
        while True:
            conn = self.server.nextPendingConnection()
            if conn is None:
                return
            if not conn.bytesAvailable():
                conn.waitForReadyRead(KNOCK_MS)
            text = bytes(conn.readAll().data()).decode("utf-8", errors="replace").strip()
            conn.write(ACK)
            conn.waitForBytesWritten(KNOCK_MS)
            conn.close()
            head, _, path = text.partition(" ")
            if head == RAISE:
                log.info("Another NWOlogger was started; bringing this window to the front")
                self.raise_requested.emit(path or None)

    def close(self) -> None:
        self.server.close()
        QLocalServer.removeServer(self.name)


def claim(db_path: str | None = None) -> InstanceServer | None:
    """Become the running NWOlogger, or ask the one that already runs to come to the front and return None."""
    name = socket_name()
    if knock(name, RAISE + (f" {db_path}" if db_path else "")):
        log.info("NWOlogger is already running: asked it to come to the front")
        return None
    return InstanceServer(name)
