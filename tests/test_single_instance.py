"""One running NWOlogger per user (nwologger/instance.py).

Windows and Linux start a new process on every double-click; two NWOloggers on one log went stale against each
other, could upload a QSO twice and overwrote each other's download positions and settings. A starting NWOlogger
now knocks on a local socket named after the user and the settings in use; a running copy is asked to come to the
front and the new process quits.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QDir

from nwologger import instance


def knock_from_another_thread(name: str, message: str = instance.RAISE) -> tuple[threading.Thread, list]:
    """A second launch is another process: knocking from this thread would wait for a server that needs this
    thread's event loop to answer. A thread of its own stands in for the other process."""
    result: list = []
    thread = threading.Thread(target=lambda: result.append(instance.knock(name, message)), daemon=True)
    thread.start()
    return thread, result


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
    with qtbot.waitSignal(server.raise_requested, timeout=5000):
        thread, result = knock_from_another_thread(name, f"{instance.RAISE} {tmp_path / 'other.sqlite'}")
    thread.join(5)
    assert result == [True]
    assert got == [str(tmp_path / "other.sqlite")]
    with qtbot.waitSignal(server.raise_requested, timeout=5000):
        thread, result = knock_from_another_thread(name)
    thread.join(5)
    assert result == [True]
    assert got[-1] is None
    server.close()
    assert not instance.knock(name, timeout_ms=300)


@pytest.mark.skipif(sys.platform == "win32", reason="named pipes vanish with their process")
def test_a_socket_file_left_by_a_crash_is_taken_over(qapp, qtbot, tmp_path):
    name = instance.socket_name("stale", str(tmp_path / "settings.ini"))
    stale = Path(QDir.tempPath()) / name
    stale.write_text("", encoding="utf-8")
    server = instance.InstanceServer(name)
    try:
        assert server.server.isListening()
        with qtbot.waitSignal(server.raise_requested, timeout=5000):
            thread, result = knock_from_another_thread(name)
        thread.join(5)
        assert result == [True]
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
