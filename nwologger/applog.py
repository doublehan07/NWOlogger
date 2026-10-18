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
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from . import APP_NAME, __version__
from .core import net

log = logging.getLogger("nwologger")

FILE_NAME = "nwologger.log"
CRASH_FILE = "nwologger-crash.txt"  # where faulthandler writes the native stack of a crash (segfault, abort)
PREVIOUS_CRASH_FILE = "nwologger-crash-previous.txt"
MAX_BYTES = 1_000_000
BACKUPS = 5
HANG_SECONDS = 15.0  # every thread's stack is written once the window has not responded this long
BLOCKED_SECONDS = 2.0  # a shorter freeze of at least this long is written with the GUI thread's stack
SLOW_SECONDS = 0.5  # timed() steps taking longer are written
BEAT = 0.5  # the GUI thread's heartbeat (s)
_EVENT_LOOP = ".exec("  # the source line where Qt's event loop runs (app.exec(), dialog.exec())

_known: set[str] = set()
_lock = threading.Lock()
_handler: logging.Handler | None = None
_crash_fh = None  # the open crash file faulthandler writes to
_faulthandler_before = False  # whether faulthandler was already on (PYTHONFAULTHANDLER) when the crash file took over
_watchdog = None
_retired: list = []  # watchdogs whose thread did not end in time: never destroyed from that thread


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


def crash_file() -> Path:
    return log_dir() / CRASH_FILE


def _watch_crashes(folder: Path) -> None:
    """Let faulthandler write the native stack of a crash (a segmentation fault, an abort in Qt's teardown) to a
    file beside the log, which the normal log cannot hold: Python is already dead when it happens. A file left with
    content by the previous run is the evidence of a crash: it is noted here and kept as the previous crash file."""
    global _crash_fh, _faulthandler_before
    import faulthandler  # noqa: PLC0415

    path = folder / CRASH_FILE
    if _crash_fh is None:
        _faulthandler_before = faulthandler.is_enabled()
    try:
        if path.exists() and path.stat().st_size:
            previous = folder / PREVIOUS_CRASH_FILE
            previous.unlink(missing_ok=True)
            path.rename(previous)
            log.warning("The last run ended in a crash; its native stack is in %s", tidy_path(previous))
        if _crash_fh is not None:
            faulthandler.disable()
            _crash_fh.close()
        _crash_fh = open(path, "a", encoding="utf-8")  # noqa: SIM115 - kept open for faulthandler
        faulthandler.enable(file=_crash_fh, all_threads=True)
    except OSError:
        _crash_fh = None


def _stop_watching_crashes() -> None:
    global _crash_fh
    if _crash_fh is None:
        return
    import faulthandler  # noqa: PLC0415

    faulthandler.disable()
    name = _crash_fh.name
    _crash_fh.close()
    _crash_fh = None
    if _faulthandler_before:  # a test run with PYTHONFAULTHANDLER: a later crash still prints its stack
        faulthandler.enable(all_threads=True)
    try:
        if os.path.getsize(name) == 0:
            os.unlink(name)  # a clean run leaves nothing behind
    except OSError:
        pass


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


class _Steps:
    def __init__(self) -> None:
        self.start = self.last = time.monotonic()
        self.parts: list[tuple[str, float]] = []

    def step(self, name: str) -> None:
        """The time since the previous step (or the start) was spent on ``name``."""
        now = time.monotonic()
        self.parts.append((name, now - self.last))
        self.last = now


@contextmanager
def timed(label: str, *, at_least: float = SLOW_SECONDS, logger: logging.Logger | None = None) -> Iterator[_Steps]:
    """Write how long ``label`` took, with its steps, when it took ``at_least`` seconds (0: always)::

        with applog.timed("Preferences OK") as t:
            apply_theme(); t.step("theme")
            reload(); t.step("reload")
    """
    t = _Steps()
    try:
        yield t
    finally:
        total = time.monotonic() - t.start
        if total >= at_least:
            parts = ", ".join(f"{n} {d:.2f}" for n, d in t.parts)
            (logger or log).info("%s took %.2f s%s", label, total, f" ({parts})" if parts else "")


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
    _watch_crashes(path.parent)
    return path


def shutdown() -> None:
    global _handler
    stop_watchdog()
    if _handler is not None:
        log.info("%s stopped", APP_NAME)
        logging.getLogger().removeHandler(_handler)
        _handler.close()
        _handler = None
    _stop_watching_crashes()
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
    from .core import secrets  # noqa: PLC0415

    log.info("Keychain: %s", secrets.backend_name() or "none")  # the store passwords go to (a packaged build check)
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


def _stack(frame, limit: int | None = None) -> str:
    """Code lines only (no variable values), with the home folder shown as ~."""
    home = str(Path.home())
    return "".join(traceback.format_stack(frame, limit)).rstrip().replace(f'"{home}{os.sep}', f'"~{os.sep}')


def thread_stacks() -> str:
    """Where every thread is right now."""
    names = {t.ident: t.name for t in threading.enumerate()}
    parts = []
    for ident, frame in sys._current_frames().items():  # noqa: SLF001 - the documented way to get them
        parts.append(f"--- thread {names.get(ident, ident)}\n" + _stack(frame))
    return "\n".join(parts)


def main_thread_stack(limit: int = 14) -> str | None:
    frame = sys._current_frames().get(threading.main_thread().ident)  # noqa: SLF001
    return _stack(frame, limit) if frame is not None else None


class Watchdog:
    """Writes every thread's stack to the log once when the window stops responding: a hang someone may
    force-quit leaves this behind, and it shows what the program was waiting for. A shorter freeze (from
    :data:`BLOCKED_SECONDS`) is written when the window responds again, with where the GUI thread was."""

    def __init__(self, after: float = HANG_SECONDS, blocked: float = BLOCKED_SECONDS):
        from PySide6.QtCore import QTimer  # noqa: PLC0415

        self.after, self.blocked = after, blocked
        self.beat = time.monotonic()
        self.stuck_since: float | None = None
        self.late = False
        # (heartbeat, stack): where the GUI thread was half a second into a freeze; "" while it idled
        self.sample: tuple[float, str] | None = None
        self._stop = threading.Event()
        self.timer = QTimer()
        self.timer.setInterval(int(BEAT * 1000))
        self.timer.timeout.connect(self._beat)
        self.timer.start()
        self.thread = threading.Thread(target=self._watch, name="watchdog", daemon=True)
        self.thread.start()

    def _beat(self) -> None:
        now = time.monotonic()
        last, self.beat = self.beat, now
        gap = now - last - BEAT
        sample, self.sample = self.sample, None
        if self.stuck_since is not None:
            log.warning("The window responds again after %.0f s", now - self.stuck_since)
            self.stuck_since = None
        elif gap >= self.blocked and sample and sample[0] == last and sample[1] and not self.late:
            log.warning("The window was blocked for %.1f s in:\n%s", gap, sample[1])
        self.late = False

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
        beat = self.beat
        gap = time.monotonic() - beat
        if gap >= min(1.0, self.blocked) and self.sample is None and not self.late and self.stuck_since is None:
            self.sample = (beat, "" if self._main_thread_idle() else main_thread_stack() or "")
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
        while not self._stop.wait(BEAT):
            self.check()

    def stop(self) -> None:
        """Called on the GUI thread. The timer is disconnected and the thread joined before the last reference
        goes, so the Watchdog, the receiver of the timer's signal, is never destroyed on its own thread: PySide
        disconnects a dying receiver inside Qt's connection mutex while taking the GIL, and a GUI thread that holds
        the GIL inside a Qt call wanting that mutex (QApplication.setStyle on a restart) then waits forever."""
        self._stop.set()
        self.timer.stop()
        try:
            self.timer.timeout.disconnect(self._beat)
        except (RuntimeError, TypeError):
            pass
        if threading.current_thread() is not self.thread:
            self.thread.join(timeout=4 * BEAT)
        if self.thread.is_alive():
            _retired.append(self)  # keep it referenced here rather than let the other thread drop it


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
