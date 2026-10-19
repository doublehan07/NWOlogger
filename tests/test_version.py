"""The version number: one constant, equal to pyproject.toml, and the only source a packaged build reads.

0.1.9 on Windows said 0.1.8: the installer copied the new files over the old folder, so _internal held both
nwologger-0.1.8.dist-info and nwologger-0.1.9.dist-info, and Python's metadata lookup took the first one the file
system listed. The frozen build now reports the constant and bundles no metadata; the installer deletes _internal
before copying.
"""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import nwologger

ROOT = Path(__file__).resolve().parent.parent


def pyproject_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def constant_in_source() -> str:
    text = (ROOT / "nwologger" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__ = "([^"]+)"', text, re.M).group(1)


def test_the_constant_equals_pyproject():
    assert constant_in_source() == pyproject_version()
    assert nwologger.__version__ == pyproject_version()


def test_a_frozen_build_reports_the_constant_even_beside_stale_metadata(tmp_path, monkeypatch):
    stale = tmp_path / "nwologger-0.0.1.dist-info"
    stale.mkdir()
    (stale / "METADATA").write_text("Metadata-Version: 2.1\nName: nwologger\nVersion: 0.0.1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    try:
        mod = importlib.reload(nwologger)
        assert mod.__version__ == constant_in_source()
    finally:
        monkeypatch.delattr(sys, "frozen", raising=False)
        importlib.reload(nwologger)


def test_the_command_line_prints_the_version():
    done = subprocess.run([sys.executable, "-m", "nwologger", "--version"], capture_output=True, text=True,
                          timeout=120, cwd=ROOT)
    assert done.returncode == 0
    assert done.stdout.strip() == f"NWOlogger {pyproject_version()}"


def test_the_installer_cleans_the_old_files_and_has_no_fallback_version():
    text = (ROOT / "packaging" / "nwologger.iss").read_text(encoding="utf-8")
    assert "[InstallDelete]" in text
    assert re.search(r'Type: filesandordirs; Name: "\{app\}\\_internal"', text)
    assert not re.search(r'#define AppVersion "\d', text), "a hard-coded version would ship an old number again"
    assert "#error" in text


def test_the_spec_bundles_no_package_metadata():
    text = (ROOT / "packaging" / "nwologger.spec").read_text(encoding="utf-8")
    assert "copy_metadata" not in text
    assert '".dist-info" in d[0]' in text, "PyInstaller collects the metadata by itself; the spec must drop it"
    assert "version=win_version" in text
