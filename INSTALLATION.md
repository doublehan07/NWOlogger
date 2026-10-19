# Installation and first run

[English README](README.md) · [简体中文说明](README.zh-CN.md)

## Run from source

You need [uv](https://docs.astral.sh/uv/). It fetches the right Python (3.12) by itself.

| | Install uv |
|---|---|
| macOS | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Windows (PowerShell) | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |

```sh
git clone https://github.com/doublehan07/NWOlogger.git
cd NWOlogger
uv sync                                       # install dependencies (first run takes a minute)
uv run nwologger --db ~/nwologger-test.sqlite   # use a throw-away log file for testing
```

Without `--db`, NWOlogger uses `~/Documents/NWOlogger/nwolog.sqlite`. Other options: `--lang zh_CN`, `--theme dark|light`, `--debug` (more detail in the diagnostic log), `--version`.

## First run

1. **Create a station profile** when asked. Choose *Club station* if several people operate the call; the Operator field in the entry form must then be filled in. Double-click a profile to make it the default.
2. **Import the sample log:** File → Import, then pick [`tests/fixtures/sample.adi`](tests/fixtures/sample.adi). These are 17 made-up QSOs: a satellite pass, HF FT8/SSB/CW, one strict dupe and one suspicious dupe. When asked, review the dupes side by side.
3. **Log a QSO.**
   - Type a call. Country, zones and Chinese provinces fill in by themselves (tinted).
   - Try TX/RX MHz with the **= TX** toggle, the Mode, Propagation and Satellite dropdowns (choose what they list in Preferences → Modes / Satellites), and Enter to log.
   - **Device:** add your rigs, antennas and setups in Preferences → Devices. Picking a setup fills rig, antenna and power at once; change any of them for the QSO if needed.
   - **Comment (upload)** goes to eQSL, QRZ.com and HRDLog with the QSO (your name, a thank-you). **Notes (local)** are only for you: the repeater you used, their rig, antenna and power, where you operated from.
   - Change Time On and see Time Off move to Time On + 1 min. **Now** next to Time On puts both back on the clock.
   - Type part of a call: **Worked before** lists similar calls, your log first, then calls QRZ.com / HamQTH know, then LoTW users and eQSL AG members, then [MASTER.SCP](https://www.supercheckpartial.com/) (File → Update MASTER.SCP). Try a call with a letter or the digit missing, e.g. `BINWO`, or a pattern with `*` and `?`, e.g. `BA*K`. Click one to use it.
   - **Clear** (Esc) empties the contact: frequency, mode, reports, propagation and satellite included. Your Device section (rig, antenna, power) stays. The *Keep* boxes only apply after Log QSO.
4. **Search:** `BA*` or `B?1*` in the call box; mode `SSB` (finds USB/LSB) with and without *Exact*; presets such as *Suspicious dupes* and *Paper to send*.
5. **Edit many QSOs:** select several rows → right-click → Bulk edit. There is no undo: a backup of the log is saved before every bulk edit, import, delete and merge (Preferences → Database → Show backups folder).
6. **Contest mode:** toolbar → Contest → New contest. Pick CQ WW, log with Space / Enter, watch the score and the Check Partial pane, then Export Cabrillo.
7. **Your own data:** File → Import accepts ADIF files and MacLoggerDX databases (`MacLoggerDX.sql`).
8. **Services (optional):** Preferences → Lookup / Services for QRZ.com, HamQTH, LoTW (needs [TQSL](https://www.arrl.org/tqsl-download)), eQSL and HRDLog.
   - Passwords are kept in the system keychain and never shown again; *Forget* removes one.
   - Reports from real accounts are especially welcome.

## Reporting a problem

Open an issue on GitHub. Include what you did, the text from Help → About → System → *Copy system info*, and the diagnostic log: Help → *Show log folder*, then attach `nwologger.log`. The log never contains a password, API key or upload code, and the *ref* shown with a sync result points to that run in the log.

## Development and packaging

```sh
uv run pytest      # tests (Qt runs off-screen; they never reach the network or your keychain)
uv run ruff check  # lint
uv run --with pyinstaller --with pillow pyinstaller packaging/nwologger.spec --noconfirm   # stand-alone app in dist/
uv run python packaging/smoke_test.py   # start that app off-screen: it must run and use the system keychain
```

Pushing a tag `vX.Y.Z` that matches the `pyproject.toml` version makes GitHub Actions build Linux (`.tar.gz`), macOS (`.dmg`, Apple silicon: drag the app onto the Applications link beside it; the installed app ejects the disk image when it starts) and Windows (installer and ZIP), start each built app once, and put them in a draft pre-release that you publish on GitHub. Actions → Release → *Run workflow* builds the same as downloadable artifacts, without a release.

## Build a Windows x64 app

From PowerShell in this project folder, after installing uv:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\build-windows.ps1
```

The script downloads 64-bit Python 3.12 into `build/python`, keeps uv's cache in `build/uv-cache`, installs the locked dependencies into `.venv`, runs the tests and lint, and bundles the app with PyInstaller. It verifies that Python and the finished executable target Windows x64. The first build needs internet access. Later builds can use `-SkipTests` when the checks have already passed for the same source.

Open `dist/NWOlogger/NWOlogger.exe` to run the app. To share it, send `dist/NWOlogger-<version>-windows-x64.zip`; the recipient extracts it and opens `NWOlogger/NWOlogger.exe`. Keep the whole folder, including `_internal`, together. The recipient can run the app without installing Python or uv.

To also create a setup wizard, install [Inno Setup 6](https://jrsoftware.org/isinfo.php) first, then run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\build-windows.ps1 -Installer
```

This also produces `dist/NWOlogger-<version>-windows-setup.exe`. The helper finds `ISCC.exe` on PATH or in the standard Inno Setup 6 installation folders. Build on Windows: PyInstaller packages for the operating system and Python architecture used for the build.

**Upgrading on Windows.** The installer removes the previous version's `_internal` folder before copying, so an upgrade leaves exactly the new version's files (0.1.9 installed over 0.1.8 kept the old package metadata beside the new and reported 0.1.8). With the ZIP, delete the old folder before extracting the new one. The packaged app reports the version constant in `nwologger/__init__.py`, bundles no package metadata, and `NWOlogger.exe` carries a version resource (Properties → Details). One NWOlogger runs per user: a second launch brings the running window to the front and quits.
