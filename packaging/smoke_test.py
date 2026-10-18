"""Start the PyInstaller build off-screen for a while: it must keep running, log its start-up line, and on macOS and
Windows keep passwords in the system keychain. Run after building: ``uv run python packaging/smoke_test.py``.

The run uses a throw-away log file, settings, diagnostic log and cache, and goes nowhere on the network.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYCHAIN = {"darwin": "macOS Keyring", "win32": "Windows WinVaultKeyring"}
RUNNING = 20  # seconds the app must stay up


def executable() -> Path:
    if sys.platform == "darwin":
        return ROOT / "dist" / "NWOlogger.app" / "Contents" / "MacOS" / "NWOlogger"
    return ROOT / "dist" / "NWOlogger" / ("NWOlogger.exe" if sys.platform == "win32" else "NWOlogger")


def main() -> int:
    exe = executable()
    if not exe.exists():
        print(f"no build at {exe}")
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="nwologger-smoke-"))
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "NWOLOGGER_OFFLINE": "1",
           "NWOLOGGER_LOG_DIR": str(tmp / "logs"), "NWOLOGGER_SETTINGS": str(tmp / "settings.ini"),
           "NWOLOGGER_CACHE_DIR": str(tmp / "cache")}
    version = subprocess.run([str(exe), "--version"], env=env, capture_output=True, text=True, timeout=60)
    print("--version:", version.returncode, version.stdout.strip())
    app = subprocess.Popen([str(exe), "--db", str(tmp / "smoke.sqlite")], env=env)
    try:
        code = app.wait(timeout=RUNNING)
    except subprocess.TimeoutExpired:
        code = None  # still running, as it should
    finally:
        if app.poll() is None:
            app.terminate()
            try:
                app.wait(timeout=15)
            except subprocess.TimeoutExpired:
                app.kill()
    log_file = tmp / "logs" / "nwologger.log"
    log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
    print(log)
    # A second run opens Preferences, closes it and closes the window by itself: it must exit cleanly, with no
    # native crash file, which is where a crash in Qt's teardown would show.
    env2 = {**env, "NWOLOGGER_SELF_TEST": "close", "NWOLOGGER_LOG_DIR": str(tmp / "logs2")}
    try:
        closing = subprocess.run([str(exe), "--db", str(tmp / "smoke2.sqlite")], env=env2, timeout=120)
        close_code: int | None = closing.returncode
    except subprocess.TimeoutExpired:
        close_code = None
    log2_file = tmp / "logs2" / "nwologger.log"
    log2 = log2_file.read_text(encoding="utf-8", errors="replace") if log2_file.exists() else ""
    print(log2)
    problems = []
    if version.returncode != 0:
        problems.append(f"--version exited with code {version.returncode}")
    if code is not None:
        problems.append(f"the app stopped within {RUNNING} s (exit code {code})")
    if close_code != 0:
        problems.append("closing the window after Preferences did not exit cleanly "
                        + ("(still running after 120 s)" if close_code is None else f"(exit code {close_code})"))
    if "Self-test: closing the window" not in log2 or "NWOlogger stopped" not in log2:
        problems.append("the self-test run did not reach its normal end")
    crash = tmp / "logs2" / "nwologger-crash.txt"
    if crash.exists() and crash.stat().st_size:
        problems.append("a crash file was written:\n" + crash.read_text(encoding="utf-8", errors="replace")[:2000])
    if " started · " not in log:
        problems.append("no start-up line in the diagnostic log")
    want = KEYCHAIN.get(sys.platform)
    if want and f"Keychain: {want}" not in log:
        problems.append(f"passwords would not go to the system keychain ({want})")
    for problem in problems:
        print("FAILED:", problem)
    if not problems:
        print("The built app starts, runs and quits cleanly" + (f", with {want}" if want else "") + ".")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
