# PyInstaller spec: one-folder build, plus a .app bundle on macOS.
# Build: uv run --with pyinstaller --with pillow pyinstaller packaging/nwologger.spec --noconfirm
import sys
import tomllib
from pathlib import Path

root = Path(SPECPATH).parent
icon = str(root / "nwologger" / "resources" / "brand" / "by1qh.png")  # converted by Pillow
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]


def version_resource(version: str, path: Path) -> str:
    """Write the Windows version resource (Properties → Details on NWOlogger.exe) and return its path."""
    parts = [int(x) for x in version.split(".")[:3]] + [0] * 3
    tuple4 = tuple(parts[:3]) + (0,)
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tuple4}, prodvers={tuple4}, mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable("040904B0", [
      StringStruct("CompanyName", "Han Zhang"),
      StringStruct("FileDescription", "NWOlogger, an amateur radio logger"),
      StringStruct("FileVersion", "{version}"),
      StringStruct("InternalName", "NWOlogger"),
      StringStruct("LegalCopyright", "MIT License"),
      StringStruct("OriginalFilename", "NWOlogger.exe"),
      StringStruct("ProductName", "NWOlogger"),
      StringStruct("ProductVersion", "{version}")])]),
    VarFileInfo([VarStruct("Translation", [1033, 1200])])
  ]
)
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


# The package's own metadata is not bundled: the app reports the version constant in nwologger/__init__.py, so an
# old nwologger-<version>.dist-info left behind by an upgrade over a previous install can never be read instead.
win_version = version_resource(version, root / "build" / "version_info.txt") if sys.platform == "win32" else None

a = Analysis(
    [str(root / "packaging" / "launcher.py")],
    pathex=[str(root)],
    datas=[
        (str(root / "nwologger" / "resources"), "nwologger/resources"),
        (str(root / "nwologger" / "core" / "contest" / "defs"), "nwologger/core/contest/defs"),
    ],
    hiddenimports=["keyring.backends.macOS", "keyring.backends.Windows", "keyring.backends.SecretService",
                   "keyring.backends.libsecret", "keyring.backends.kwallet", "PySide6.QtSvg"],
    excludes=["tkinter", "PIL", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts",
              "PySide6.QtDataVisualization", "PySide6.QtPdf"],
    noarchive=False,
)
# PyInstaller copies a package's metadata on its own when it sees importlib.metadata.version("nwologger") in the
# code (the source-tree path in nwologger/__init__.py): drop it, the frozen build reads the version constant.
a.datas = [d for d in a.datas if not (d[0].startswith("nwologger-") and ".dist-info" in d[0])]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="NWOlogger", console=False, icon=icon, version=win_version)
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
