"""TQSL command line: LoTW only accepts logs signed by TQSL."""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from xml.etree import ElementTree

log = logging.getLogger("nwologger.tqsl")

EXIT_CODES = {
    0: "Success",
    1: "Cancelled by user",
    2: "Rejected by LoTW",
    3: "Unexpected response from the TQSL server",
    4: "TQSL error",
    5: "TQSLlib error",
    6: "Unable to open the input file",
    7: "Unable to open the output file",
    8: "No QSOs were processed: all were duplicates or out of date range",
    9: "Some QSOs were processed; some were duplicates or out of date range",
    10: "Command syntax error",
    11: "LoTW connection error (no network or LoTW unreachable)",
    -1: "Stopped in NWOlogger before TQSL finished",
    -2: "TQSL did not finish in time and was stopped",
}
CANCELLED, TIMED_OUT = -1, -2
# Exit codes after which the QSOs are in LoTW (new or already there).
SENT_CODES = {0, 8, 9}

CANDIDATES = [
    "/Applications/TrustedQSL/tqsl.app/Contents/MacOS/tqsl",
    "/Applications/tqsl.app/Contents/MacOS/tqsl",
    os.path.expanduser("~/Applications/TrustedQSL/tqsl.app/Contents/MacOS/tqsl"),
    r"C:\Program Files (x86)\TrustedQSL\tqsl.exe",
    r"C:\Program Files\TrustedQSL\tqsl.exe",
    "/usr/bin/tqsl",
    "/usr/local/bin/tqsl",
    "/opt/homebrew/bin/tqsl",
]


def find_tqsl(configured: str | None = None) -> str | None:
    if configured and os.path.exists(configured):
        return configured
    for c in CANDIDATES:
        if os.path.exists(c):
            return c
    return shutil.which("tqsl")


def config_dir() -> Path:
    """TQSL's own settings folder (``TQSLDIR`` if set, as TQSL itself does)."""
    if os.environ.get("TQSLDIR"):
        return Path(os.environ["TQSLDIR"])
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "TrustedQSL"
    return Path.home() / ".tqsl"


def station_locations(directory: str | os.PathLike | None = None) -> list[str] | None:
    """The station locations defined in TQSL, or None when TQSL's ``station_data`` file can't be read."""
    path = Path(directory) if directory else config_dir()
    try:
        root = ElementTree.parse(path / "station_data").getroot()
    except (OSError, ElementTree.ParseError):
        return None
    return [n for n in (e.get("name") for e in root.iter("StationData")) if n]


class Job:
    """One TQSL run that can be stopped from another thread (Cancel, or quitting NWOlogger)."""

    def __init__(self) -> None:
        self.stopped = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def started(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._proc = proc
        if self.stopped.is_set():
            _stop(proc)

    def stop(self) -> None:
        self.stopped.set()
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            _stop(proc, wait=False)


def _signal(proc: subprocess.Popen, *, kill: bool) -> None:
    """TQSL runs in its own process group, so anything it started stops with it."""
    try:
        if sys.platform != "win32":
            os.killpg(proc.pid, signal.SIGKILL if kill else signal.SIGTERM)
        elif kill:
            proc.kill()
        else:
            proc.terminate()
    except OSError:
        pass


def _stop(proc: subprocess.Popen, *, wait: bool = True) -> None:
    _signal(proc, kill=False)
    if wait:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _signal(proc, kill=True)


def _drain(proc: subprocess.Popen) -> tuple[str, str]:
    """What a stopped TQSL wrote, without waiting forever on a pipe something else still holds open."""
    try:
        return proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        _signal(proc, kill=True)
        try:
            return proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            for f in (proc.stdout, proc.stderr):
                if f:
                    f.close()
            return "", ""


def sign_and_upload(tqsl_path: str, adif_path: str, location: str, *, timeout: float = 900,
                    job: Job | None = None) -> tuple[int, str]:
    """Run ``tqsl -x -d -u -a compliant -l <location> file.adi``. Returns (exit code, output).

    TQSL may wait in its own window (e.g. for a certificate passphrase); ``job.stop()`` or ``timeout`` ends it,
    returning CANCELLED or TIMED_OUT instead of leaving NWOlogger waiting."""
    cmd = [tqsl_path, "-x", "-d", "-u", "-a", "compliant", "-l", location, adif_path]
    kw: dict = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    log.info("TQSL started: %s -x -d -u -a compliant -l %r %s", os.path.basename(tqsl_path), location,
             os.path.basename(adif_path))
    t0 = time.monotonic()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)
    job = job or Job()
    job.started(p)
    code = None
    while True:
        try:
            stdout, stderr = p.communicate(timeout=0.5)  # also drains the pipes, so TQSL never blocks on them
            break
        except subprocess.TimeoutExpired:
            if job.stopped.is_set() or time.monotonic() - t0 > timeout:
                code = CANCELLED if job.stopped.is_set() else TIMED_OUT
                _stop(p)
                stdout, stderr = _drain(p)
                break
    out = ((stderr or "") + ("\n" + stdout if stdout else "")).strip()
    if code is None:
        code = CANCELLED if job.stopped.is_set() else p.returncode
    log.log(logging.INFO if code in (0, 8, 9) else logging.WARNING, "TQSL finished after %.1f s: %s (%s)%s",
            time.monotonic() - t0, describe(code), code,
            ("\n" + "\n".join(out.splitlines()[-20:])) if out else "")
    return code, out


def describe(code: int) -> str:
    return EXIT_CODES.get(code, f"exit code {code}")
