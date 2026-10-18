# PyInstaller spec: one-folder build, plus a .app bundle on macOS.
# Build: uv run --with pyinstaller --with pillow pyinstaller packaging/nwologger.spec --noconfirm
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH).parent
icon = str(root / "nwologger" / "resources" / "brand" / "by1qh.png")  # converted by Pillow
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]

a = Analysis(
    [str(root / "packaging" / "launcher.py")],
    pathex=[str(root)],
    datas=[
        (str(root / "nwologger" / "resources"), "nwologger/resources"),
        (str(root / "nwologger" / "core" / "contest" / "defs"), "nwologger/core/contest/defs"),
        *copy_metadata("nwologger"),
    ],
    hiddenimports=["keyring.backends.macOS", "keyring.backends.Windows", "keyring.backends.SecretService",
                   "keyring.backends.libsecret", "keyring.backends.kwallet", "PySide6.QtSvg"],
    excludes=["tkinter", "PIL", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts",
              "PySide6.QtDataVisualization", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="NWOlogger", console=False, icon=icon)
coll = COLLECT(exe, a.binaries, a.datas, name="NWOlogger")
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="NWOlogger.app",
        icon=icon,
        bundle_identifier="io.github.doublehan07.nwologger",
        info_plist={
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
        },
    )
