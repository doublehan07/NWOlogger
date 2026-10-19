"""One running NWOlogger per user (nwologger/instance.py).

Windows and Linux start a new process on every double-click; two NWOloggers on one log went stale against each
other, could upload a QSO twice and overwrote each other's download positions and settings. A starting NWOlogger
now knocks on a local socket named after the user and the settings in use; a running copy is asked to come to the
front and the new process quits.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from nwologger import applog, instance
from nwologger.core import db as dbm


def test_the_name_depends_on_the_user_and_the_settings_file():
    a = instance.socket_name("han", "/a/settings.ini")
    assert a == instance.socket_name("han", "/a/settings.ini")
    assert a != instance.socket_name("han", "/b/settings.ini")
    assert a != instance.socket_name("other", "/a/settings.ini")
    assert a.startswith("nwologger-") and "/" not in a and "\\" not in a


def test_a_knock_without_a_running_copy_fails_quickly(qapp, tmp_path):
    name = instance.socket_name("nobody", str(tmp_path / "nothing.ini"))
    started = time.monotonic()
    assert not instance.knock(name, timeout_ms=300)
    assert time.monotonic() - started < 5


def test_a_second_launch_asks_the_running_copy_to_come_to_the_front(qapp, qtbot, tmp_path):
    name = instance.socket_name("test", str(tmp_path / "settings.ini"))
    server = instance.InstanceServer(name)
    got: list = []
    server.raise_requested.connect(got.append)
    # The knock blocks until the server's thread answers; the GUI loop only delivers the signal afterwards.
    with qtbot.waitSignal(server.raise_requested, timeout=5000):
        assert instance.knock(name, f"{instance.RAISE} {tmp_path / 'other.sqlite'}")
    assert got == [str(tmp_path / "other.sqlite")]
    with qtbot.waitSignal(server.raise_requested, timeout=5000):
        assert instance.knock(name)
    assert got[-1] is None
    server.close()
    assert not instance.knock(name, timeout_ms=300)


@pytest.mark.skipif(sys.platform == "win32", reason="named pipes vanish with their process")
def test_a_socket_file_left_by_a_crash_is_taken_over(qapp, qtbot, tmp_path):
    name = instance.socket_name("stale", str(tmp_path / "settings.ini"))
    stale = Path(instance.address(name))
    stale.write_text("", encoding="utf-8")
    assert not instance.knock(name, timeout_ms=300), "a plain file answers nothing"
    server = instance.InstanceServer(name)
    try:
        assert server.listening
        with qtbot.waitSignal(server.raise_requested, timeout=5000):
            assert instance.knock(name)
    finally:
        server.close()
    assert not stale.exists()


def test_run_quits_at_once_when_another_copy_answers(qapp, monkeypatch, caplog):
    from nwologger import app as appmod

    knocked: list = []

    def fake_knock(name, message=instance.RAISE, timeout_ms=instance.KNOCK_MS):
        knocked.append(message)
        return True

    monkeypatch.setattr(instance, "knock", fake_knock)
    caplog.set_level(logging.INFO, logger="nwologger.instance")
    assert appmod._run([sys.argv[0]], db_path="/tmp/some.sqlite", lang=None, theme_mode=None) == 0
    assert knocked == [f"{instance.RAISE} /tmp/some.sqlite"]
    assert "already running" in caplog.text


def _wait_for(path: Path, text: str, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        content = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        if text in content:
            return content
        time.sleep(0.2)
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def test_two_real_processes_the_second_raises_the_first_and_quits(tmp_path):
    """The real thing on this platform: a running NWOlogger and a second launch of it, as two processes."""
    path = tmp_path / "two.sqlite"
    db = dmb_profile(path)
    db.close()
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "NWOLOGGER_OFFLINE": "1",
           "NWOLOGGER_SETTINGS": str(tmp_path / "settings.ini"), "NWOLOGGER_CACHE_DIR": str(tmp_path / "cache"),
           "TQSLDIR": str(tmp_path / "no-tqsl")}
    first_log = tmp_path / "logs1" / applog.FILE_NAME
    second_log = tmp_path / "logs2" / applog.FILE_NAME
    first = subprocess.Popen([sys.executable, "-m", "nwologger", "--db", str(path)],
                             env={**env, "NWOLOGGER_SELF_TEST": "linger", "NWOLOGGER_LOG_DIR": str(first_log.parent)},
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        content = _wait_for(first_log, "Window ready", 120)
        assert "Window ready" in content, content[-2000:]
        second = subprocess.run([sys.executable, "-m", "nwologger", "--db", str(tmp_path / "other.sqlite")],
                                env={**env, "NWOLOGGER_LOG_DIR": str(second_log.parent)},
                                capture_output=True, text=True, timeout=120)
        second_content = second_log.read_text(encoding="utf-8", errors="replace") if second_log.exists() else ""
        assert second.returncode == 0, (second.returncode, second.stderr[-2000:], second_content[-2000:])
        assert "already running" in second_content, second_content[-2000:]
        assert "Window ready" not in second_content, "the second launch must not open a window"
        content = _wait_for(first_log, "Another NWOlogger was started", 30)
        assert "Another NWOlogger was started" in content, content[-2000:]
    finally:
        if first.poll() is None:
            first.terminate()
            try:
                first.wait(timeout=30)
            except subprocess.TimeoutExpired:
                first.kill()
    crash = first_log.parent / applog.CRASH_FILE  # opened at start for faulthandler; a crash would fill it
    assert not (crash.exists() and crash.stat().st_size), crash.read_text(encoding="utf-8", errors="replace")[:2000]


def dmb_profile(path: Path) -> dbm.LogDB:
    db = dbm.LogDB(path, backup_on_open=False)
    db.save_profile({"name": "BI1ZZZ", "station_callsign": "BI1ZZZ", "operator": "BI1ZZZ", "my_gridsquare": "ON80",
                     "is_default": True})
    return db
