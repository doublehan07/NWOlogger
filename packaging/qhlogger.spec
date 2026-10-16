# PyInstaller spec: one-folder build, plus a .app bundle on macOS.
# Build: uv run --with pyinstaller pyinstaller packaging/qhlogger.spec --noconfirm
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH).parent
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]

a = Analysis(
    [str(root / "packaging" / "launcher.py")],
    pathex=[str(root)],
    datas=[
        (str(root / "qhlogger" / "resources"), "qhlogger/resources"),
        (str(root / "qhlogger" / "core" / "contest" / "defs"), "qhlogger/core/contest/defs"),
        *copy_metadata("qhlogger"),
    ],
    hiddenimports=["keyring.backends.macOS", "keyring.backends.Windows", "PySide6.QtSvg"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts",
              "PySide6.QtDataVisualization", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="QHlogger", console=False,
          icon=None)
coll = COLLECT(exe, a.binaries, a.datas, name="QHlogger")
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="QHlogger.app",
        bundle_identifier="io.github.by1qh.qhlogger",
        info_plist={
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
        },
    )
