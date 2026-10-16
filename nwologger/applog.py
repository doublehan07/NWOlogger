"""The diagnostic log file: what NWOlogger did, so a bug report can carry it instead of the whole story.

Each line is written through :func:`core.net.redact` with every secret NWOlogger has read this session and the
secrets of the requests in flight, so no password, API key or upload code reaches the file, whatever a message or
traceback holds. Sync code logs hosts and paths, never URL queries; user names are not logged either.

Where: ``~/Library/Logs/NWOlogger`` (macOS), ``%LOCALAPPDATA%\\NWOlogger\\Logs`` (Windows),
``~/.local/state/nwologger`` (Linux); ``NWOLOGGER_LOG_DIR`` overrides it. One file of 1 MB plus five old ones.
"""

from __future__ import annotations

import linecache
import logging
import logging.handlers
import os
import platform
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Iterable
from pathlib import Path

from . import APP_NAME, __version__
from .core import net

log = logging.getLogger("nwologger")

FILE_NAME = "nwologger.log"
MAX_BYTES = 1_000_000
BACKUPS = 5
HANG_SECONDS = 15.0
_EVENT_LOOP = ".exec("  # the source line where Qt's event loop runs (app.exec(), dialog.exec())

_known: set[str] = set()
_lock = threading.Lock()
_handler: logging.Handler | None = None
_watchdog = None


def log_dir() -> Path:
    env = os.environ.get("NWOLOGGER_LOG_DIR")
    if env:
        return Path(env)
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Logs" / APP_NAME
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / APP_NAME / "Logs"
    return Path(os.environ.get("XDG_STATE_HOME") or home / ".local" / "state") / "nwologger"


def log_file() -> Path:
    return log_dir() / FILE_NAME


def remember_secrets(values: Iterable[str | None]) -> None:
    """Secrets read from the keychain, and account user names: masked in every later line of this session."""
    vals = {v for v in values if v}
    if vals:
        with _lock:
            _known.update(vals)


def _secrets() -> list[str]:
    with _lock:
        known = list(_known)
    return [*known, *net.active_secrets()]


def tidy_path(p: str | os.PathLike | None) -> str:
    """A path with the home folder shown as ~ (the folder name is often the user's own name)."""
    if not p:
        return ""
    s, home = str(p), str(Path.home())
    return "~" + s[len(home):] if s == home or s.startswith(home + os.sep) else s


def new_ref() -> str:
    """A short id for one sync run, shown in the results window and written in the log."""
    return uuid.uuid4().hex[:4].upper()


class RedactingFormatter(logging.Formatter):
    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        return net.redact(super().format(record), _secrets())


FORMAT = "%(asctime)s.%(msecs)03dZ %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"


def setup(*, debug: bool = False, directory: str | os.PathLike | None = None) -> Path | None:
    """Start writing the log file. Returns its path, or None when it can't be written (the app runs anyway)."""
    global _handler
    path = Path(directory) / FILE_NAME if directory else log_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
    except OSError:
        return None
    h.setFormatter(RedactingFormatter(FORMAT, "%Y-%m-%d %H:%M:%S"))
    root = logging.getLogger()
    if _handler is not None:
        root.removeHandler(_handler)
        _handler.close()
    root.addHandler(h)
    _handler = h
    if root.level == logging.NOTSET or root.level > logging.WARNING:
        root.setLevel(logging.WARNING)  # other libraries: warnings and errors only
    log.setLevel(logging.DEBUG if debug else logging.INFO)
    net.install_log_filter()  # HTTPX / httpcore stay at WARNING, also at DEBUG
    return path


def shutdown() -> None:
    global _handler
    stop_watchdog()
    if _handler is not None:
        log.info("%s stopped", APP_NAME)
        logging.getLogger().removeHandler(_handler)
        _handler.close()
        _handler = None
    with _lock:
        _known.clear()


def startup_banner(db_path: str | os.PathLike | None = None) -> None:
    try:
        from PySide6 import __version__ as pyside  # noqa: PLC0415
        from PySide6.QtCore import qVersion  # noqa: PLC0415

        qt = f"PySide6 {pyside} · Qt {qVersion()}"
    except ImportError:  # pragma: no cover - Qt is a dependency
        qt = "no Qt"
    built = " · packaged build" if getattr(sys, "frozen", False) else ""
    log.info("%s %s started · Python %s · %s · %s %s %s%s", APP_NAME, __version__, platform.python_version(), qt,
             platform.system(), platform.release(), platform.machine(), built)
    if db_path:
        log.info("Log file (database): %s", tidy_path(db_path))


def install_hooks() -> None:
    """Uncaught exceptions (also in Qt slots and worker threads) go to the log with their traceback."""
    prev = sys.excepthook

    def hook(t, v, tb):
        log.critical("Uncaught exception", exc_info=(t, v, tb))
        prev(t, v, tb)

    sys.excepthook = hook
    prev_thread = threading.excepthook

    def thread_hook(args):
        if args.exc_type is not SystemExit:
            name = args.thread.name if args.thread else "?"
            log.critical("Uncaught exception in thread %s", name,
                         exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        prev_thread(args)

    threading.excepthook = thread_hook


def install_qt_messages() -> None:
    """Qt's own warnings (e.g. a failed font or plugin load) go to the log instead of a console nobody sees."""
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler  # noqa: PLC0415

    qt = logging.getLogger("nwologger.qt")
    levels = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
              QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
              QtMsgType.QtFatalMsg: logging.CRITICAL}

    def handler(mode, _context, message):
        qt.log(levels.get(mode, logging.WARNING), "%s", message)

    qInstallMessageHandler(handler)


def thread_stacks() -> str:
    """Where every thread is right now (code lines only, no variable values)."""
    names = {t.ident: t.name for t in threading.enumerate()}
    parts = []
    for ident, frame in sys._current_frames().items():  # noqa: SLF001 - the documented way to get them
        parts.append(f"--- thread {names.get(ident, ident)}\n" + "".join(traceback.format_stack(frame)).rstrip())
    return "\n".join(parts)


class Watchdog:
    """Writes every thread's stack to the log once when the window stops responding: a hang someone may
    force-quit leaves this behind, and it shows what the program was waiting for."""

    def __init__(self, after: float = HANG_SECONDS):
        from PySide6.QtCore import QTimer  # noqa: PLC0415

        self.after = after
        self.beat = time.monotonic()
        self.stuck_since: float | None = None
        self.late = False
        self._stop = threading.Event()
        self.timer = QTimer()
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._beat)
        self.timer.start()
        self.thread = threading.Thread(target=self._watch, name="watchdog", daemon=True)
        self.thread.start()

    def _beat(self) -> None:
        now = time.monotonic()
        if self.stuck_since is not None:
            log.warning("The window responds again after %.0f s", now - self.stuck_since)
            self.stuck_since = None
        self.late = False
        self.beat = now

    @staticmethod
    def _main_thread_idle() -> bool:
        """The GUI thread is waiting in Qt's event loop (``….exec()``), not running or blocked in Python code: a late
        timer then means macOS App Nap or a sleeping computer, not a hang."""
        if threading.current_thread() is threading.main_thread():
            return False  # asked by the GUI thread itself: it is running Python
        frame = sys._current_frames().get(threading.main_thread().ident)  # noqa: SLF001
        return frame is not None and _EVENT_LOOP in linecache.getline(frame.f_code.co_filename, frame.f_lineno)

    def check(self) -> bool:
        """True (and logged with every thread's stack) when the window has just been found not responding."""
        gap = time.monotonic() - self.beat
        if gap < self.after or self.stuck_since is not None or self.late:
            return False
        if self._main_thread_idle():
            self.late = True
            log.info("Timers ran %.0f s late while the window was idle (in the background, or the computer slept)",
                     gap)
            return False
        self.stuck_since = self.beat
        log.warning("The window has not responded for %.0f s. Where each thread is:\n%s", gap, thread_stacks())
        return True

    def _watch(self) -> None:
        while not self._stop.wait(2.0):
            self.check()

    def stop(self) -> None:
        self._stop.set()
        self.timer.stop()


def start_watchdog(after: float = HANG_SECONDS) -> Watchdog:
    global _watchdog
    stop_watchdog()
    _watchdog = Watchdog(after)
    return _watchdog


def stop_watchdog() -> None:
    global _watchdog
    if _watchdog is not None:
        _watchdog.stop()
        _watchdog = None
