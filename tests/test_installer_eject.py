"""macOS: the installed app ejects the NWOlogger disk image it was dragged from, never the one it runs from."""

from __future__ import annotations

from pathlib import Path

from nwologger import app


def _info(*images):
    return {"images": [{"image-path": path, "system-entities": [{"dev-entry": "/dev/disk9"},
                                                                 {"dev-entry": "/dev/disk9s1", "mount-point": mount}]}
                       for path, mount in images]}


def test_finds_the_mounted_installer_image(tmp_path):
    ours, other = tmp_path / "NWOlogger", tmp_path / "Other"
    (ours / "NWOlogger.app").mkdir(parents=True)
    (other / "NWOlogger.app").mkdir(parents=True)
    empty = tmp_path / "Empty"
    empty.mkdir()
    info = _info(("/Users/me/Downloads/NWOlogger-0.1.8-macos-arm64.dmg", str(ours)),
                 ("/Users/me/Downloads/Something.dmg", str(other)),  # another program's image
                 ("/Users/me/Downloads/NWOlogger-old.dmg", str(empty)))  # no app inside
    running = Path("/Applications/NWOlogger.app/Contents/MacOS/NWOlogger")
    assert app.installer_mounts(info, running) == [str(ours)]


def test_never_ejects_the_image_the_app_runs_from(tmp_path):
    ours = tmp_path / "NWOlogger"
    (ours / "NWOlogger.app").mkdir(parents=True)
    info = _info(("/Users/me/Downloads/NWOlogger-0.1.8-macos-arm64.dmg", str(ours)))
    assert app.installer_mounts(info, ours / "NWOlogger.app" / "Contents" / "MacOS" / "NWOlogger") == []
    assert app.installer_mounts({}, Path("/Applications/NWOlogger.app")) == []


def test_does_nothing_outside_a_macos_build(monkeypatch):
    calls = []
    monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: calls.append(a))
    app.eject_installer_image()  # tests run from source, not a frozen build
    assert calls == []
