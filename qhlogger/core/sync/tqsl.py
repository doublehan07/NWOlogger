"""TQSL command line: LoTW only accepts logs signed by TQSL."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

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
}
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


def sign_and_upload(tqsl_path: str, adif_path: str, location: str, *, timeout: float = 900) -> tuple[int, str]:
    """Run ``tqsl -x -d -u -a compliant -l <location> file.adi``. Returns (exit code, output)."""
    cmd = [tqsl_path, "-x", "-d", "-u", "-a", "compliant", "-l", location, adif_path]
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    out = (p.stderr or "") + ("\n" + p.stdout if p.stdout else "")
    return p.returncode, out.strip()


def describe(code: int) -> str:
    return EXIT_CODES.get(code, f"exit code {code}")
