"""Quitting: closing the window after Preferences was open ends the process cleanly, with no crash file.

0.1.8 could quit with a crash on the close button after Preferences had been opened: the window, its dialogs and
the application object were destroyed in the garbage collector's order at interpreter exit, after Python's last log
line. The app now tears them down in order while the application object exists; this runs the real exit path.
"""

from __future__ import annotations

import os
import subprocess
import sys

from nwologger import applog
from nwologger.core import db as dbm


def test_the_app_quits_cleanly_after_opening_preferences(tmp_path):
    path = tmp_path / "exit.sqlite"
    db = dbm.LogDB(path, backup_on_open=False)
    db.save_profile({"name": "BI1ZZZ", "station_callsign": "BI1ZZZ", "operator": "BI1ZZZ", "my_gridsquare": "ON80",
                     "is_default": True})
    db.close()
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "NWOLOGGER_OFFLINE": "1", "NWOLOGGER_SELF_TEST": "close",
           "NWOLOGGER_LOG_DIR": str(tmp_path / "logs"), "NWOLOGGER_SETTINGS": str(tmp_path / "settings.ini"),
           "NWOLOGGER_CACHE_DIR": str(tmp_path / "cache"), "TQSLDIR": str(tmp_path / "no-tqsl")}
    done = subprocess.run([sys.executable, "-m", "nwologger", "--db", str(path)], env=env, capture_output=True,
                          text=True, timeout=180)
    log_file = tmp_path / "logs" / applog.FILE_NAME
    log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
    assert done.returncode == 0, (done.returncode, done.stderr[-3000:], log[-3000:])
    for line in ("Self-test: Preferences opened", "Self-test: Preferences closed", "Self-test: closing the window",
                 "NWOlogger stopped"):
        assert line in log, log[-3000:]
    assert not (tmp_path / "logs" / applog.CRASH_FILE).exists()
