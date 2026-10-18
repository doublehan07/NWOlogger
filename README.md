# NWOlogger

> **Pre-release testing — not yet officially released.**

English · [简体中文](README.zh-CN.md)

A minimal, cross-platform amateur radio logger for macOS, Ubuntu and Windows. It is modelled on
**MacLoggerDX** for everyday logging and on **N1MM Logger+** for contesting, with its own
modern UI, and built with Python and Qt (PySide6).

> - Logging, search, bulk edit, dupe review, paper QSL tracking, ADIF, MacLoggerDX migration and contest mode work and are tested.
> - Uploads to LoTW, eQSL, QRZ.com and HRDLog, downloads from LoTW, eQSL and QRZ.com, and QRZ.com lookups have been tried with real accounts. HamQTH has not yet.
>
> - **Design:** [PLAN.md](PLAN.md)
> - **Remaining work:** [TODO.md](TODO.md)

## Screenshots

![Main window: entry form above the log. A possible dupe is flagged while typing.](docs/screenshots/main.png)
*Main window: the entry form above the log. The call is flagged as a possible dupe while you type, and **Compare…** opens the dupe window. Worked before lists similar calls: here three calls one letter away from JA1TSQ.*

| | |
|---|---|
| ![Dupe compare](docs/screenshots/compare.png) | ![Contest mode](docs/screenshots/contest.png) |
| *A group of three possible dupes side by side. Cells are editable; Merge… picks, row by row, what the merged QSO keeps.* | *Contest mode (CQ WW): entry line, live score, Check Partial and N+1.* |
| ![Dark theme](docs/screenshots/dark.png) | ![Simplified Chinese](docs/screenshots/zh_CN.png) |
| *Dark theme* | *简体中文* |
| ![Preferences: modes](docs/screenshots/prefs-modes.png) | ![Preferences: devices](docs/screenshots/prefs-devices.png) |
| *Preferences → Modes: choose and order the modes in the dropdown* | *Preferences → Devices: your rigs, antennas and setups* |

All screenshots use made-up QSOs. To regenerate them, run `uv run python tools/screenshots.py` (the grid map shots fetch NASA imagery; `--offline` leaves it out).

## Grid map

The grid map (Statistics tab, Ctrl+4) shows every 4-character Maidenhead square you have worked on a globe you can spin, over satellite imagery, in the style of GridMaster and GridTracker.

![Grid map, China & around: satellite squares worked from ON80, each with its name and first call; the evening twilight darkens the east.](docs/screenshots/gridmap.jpg)
*China & around, half a zoom step in: every square shows its name and the first call that confirmed it. Green squares are confirmed (here by LoTW), amber ones only worked. The count line keeps the VUCC tally.*

| | |
|---|---|
| ![Zoomed in on ON80 and around](docs/screenshots/gridmap-detail.jpg) | ![The whole globe, all bands](docs/screenshots/gridmap-world.jpg) |
| *Zoomed in: names and calls at full size. A worked-only square's call is in italics. Your square is outlined, and the dashed circle marks 200 km around it.* | *All bands on the whole globe. The night side for the current UTC time fades in through twilight.* |

- **Squares:** green when confirmed by the sources you tick (LoTW by default; any mix of eQSL, QRZ.com and paper), amber when only worked, each with the first call. Names and calls appear about half a zoom step inside a whole-country view and grow as you zoom in. Worked squares get a light edge once they are big enough, so neighbours stay apart. Two-letter field names sit at 8% opacity over their 100 squares.
- **Filters:** satellite, one band or all bands, and mode group. By default only QSOs made within 200 km of your grid count (VUCC, GridMaster). The count line shows confirmed and worked squares and VUCC progress.
- **Regions:** around my grid, China & around, CONUS 488 (GridMaster's squares outlined, with the count), Europe, Japan, World.
- **Moving around:** drag or the arrow keys rotate the globe. The wheel, a double-click or +/− zoom. A click shows the square's QSOs in the log, and *Save as PNG* keeps the current view.
- **Day/night:** shades the night side for the current UTC time, fading from day to night through twilight. It updates by itself and remembers whether it is on (on by default).
- **Imagery** (Preferences → Map): NASA Blue Marble (no key) or Tianditu 天地图 (a free developer key). Imagery, squares and the night side are drawn at your display's own resolution, Retina included. Tiles load in the background and are kept in the system cache folder (up to 300 MB), so views seen before open offline.

## Installation

See [INSTALLATION.md](INSTALLATION.md) for setup, first-run steps, troubleshooting, and macOS / Windows build instructions.

## Features

- **One SQLite file** holds every QSO.
  - Columns are plain ADIF names, so the log can be queried with any SQLite tool.
  - The log is backed up automatically on open and before every import or bulk edit.
- **ADIF import and export:** export all, selected, or filtered QSOs. Fields NWOlogger doesn't know are kept and written back out.
- **MacLoggerDX migration** straight from its database. It keeps the LoTW, eQSL, QRZ and HRDLog upload history and the paper card dates that MLDX's own ADIF export drops.
  - MacLoggerDX only records the uploads it made itself. A QSO that LoTW or QRZ.com confirmed must have been uploaded there, so it is marked uploaded too (ADIF imports get the same rule); the import preview counts them. Download from LoTW and QRZ.com before your first upload to mark the rest.
- **Search** across several criteria at once, with `?` / `*` wildcards on callsigns. Mode search is vague by default (`SSB` finds USB/LSB, `DIGI` finds FT8), with an *Exact* option. Searches can be saved.
- **Sorting** by any column, including satellite name, time on and time off. Columns can be shown, hidden and moved.
- **Entry form:**
  - TX / RX frequencies cover all 33 ADIF bands, including 30 m, 17 m and 12 m, with an *= TX* sync toggle. Enter MHz directly or add Hz, kHz, MHz or GHz units; contest entry uses kHz for bare numbers.
  - Dropdowns for mode, propagation (TQSL's list) and satellite.
    - You choose which modes and satellites are listed; *Other…* reaches the rest.
    - You can add your own satellites.
    - A satellite with a non-SAT propagation is kept, with a warning.
  - A **Device** section with your rig (`MY_RIG`), antenna (`MY_ANTENNA`) and power (`TX_PWR`). A *setup* you put together in Preferences → Devices fills all three in one pick, and each can still be changed. Empty fields log the station profile's.
  - *Comment (upload)* (`COMMENT`, sent to eQSL, QRZ.com and HRDLog) and *Notes (local)* (`NOTES`, never uploaded): the repeater you used, the other station's rig, antenna and power, your QTH.
  - Time Off follows Time On + 1 min. A Time Off earlier than Time On is refused. *Now* next to Time On goes back to the live clock.
  - Fields stay set for a whole satellite pass (*Keep*). *Clear* empties the contact and keeps your Device section.
  - An empty rig, antenna or power uses the station profile's, shown in grey.
- **Grid map** (Statistics tab, Ctrl+4): every square you worked on a rotatable globe over satellite imagery, confirmed in green and worked in amber, with the first call in each square. See [Grid map](#grid-map) above.
- **Similar calls** in Worked before, while you type: the same call, calls that start with or contain what you typed, and calls one character away (a typo, or a missing letter or digit). Your log comes first (most recent first), then calls QRZ.com / HamQTH know, then the calls of ARRL's [LoTW user list](https://lotw.arrl.org/lotw-user-activity.csv) and eQSL's AG member list (about 320,000 calls, with the date of each call's last LoTW upload), then MASTER.SCP. When a call is not found on QRZ.com / HamQTH, up to three MASTER.SCP calls one letter away are looked up there. With `*` or `?` the call field searches every source like the log's search box (`BA*K` → BA1PK, BA8BLK).
  - QRZ.com and HamQTH can only look up a complete call, so the lists are downloaded instead (next to the log file, refreshed weekly; File → Update LoTW and eQSL call lists) and searched on your computer while you type.
- **Station profiles:** closing Preferences with unsaved station changes offers Save, Discard or Cancel, including when you use the window's close button or Esc.
- **Operators:** an Operator field in the entry form.
  - Personal stations fill in your call.
  - Club stations must enter the operator, which then stays for the session.
  - On a new UTC day or after a 3-hour break, the field turns amber once to be checked.
- **Strict dupe checking:**
  - Only the same call, band and exact mode within 30 seconds blocks logging.
  - The same mode later, or a related mode (SSB vs USB, USB vs LSB, FT8 vs FT4), only warns.
  - A side-by-side compare window shows a whole group of possible dupes, one column per QSO. Edit the cells there, merge them field by field (click the value to keep on each row that differs; confirmations and upload states are combined), delete one, or mark them as not dupes, also all of them at once (asks twice). Edit → Merge selected… merges 2–5 selected QSOs with the same call.
- **Bulk edit** of a selection, with a preview that keeps up while you type, even for thousands of QSOs. A time or date it can't read is refused, never written. A backup is saved first.
- **Electronic confirmations, tracked per service:** LoTW (via TQSL), eQSL and QRZ.com Logbook (upload and download), and HRDLog.net (upload only). Satellite names are translated per service: the ISS is `ARISS` for LoTW and `ISS` for eQSL.
  - A call is **green** when LoTW confirmed the QSO (eQSL, QRZ.com and paper cards don't make it green). It stays green, and a dupe red, when the row is selected.
  - **Uploads are always your choice.** *Sync everything* only downloads. QSOs not yet uploaded to all four services are shown in grey (the tooltip names the missing services); the preset *Not uploaded to every service* lists them, and *Upload selected* sends them.
  - Downloads also record what is already uploaded: LoTW's report of accepted QSOs and the QRZ.com logbook mark those QSOs as uploaded. eQSL (it only offers your inbox) and HRDLog (no download) can't tell NWOlogger what you uploaded there before.
  - Adding downloaded QSOs to an empty log keeps their LoTW, eQSL or QRZ.com confirmations. Failed downloads can be retried immediately; only a successfully applied download starts the wait between downloads.
  - The first LoTW download reuses its full QSO report for confirmations when the report provides the required timestamps, avoiding a second full download.
  - Every outcome goes to one **Sync results** window that never blocks the app (QSL → Show sync results). A row that a setting can fix has *Open Preferences*.
  - **Sync history** keeps every run; *Clear history…* (or QSL → Clear sync history…) deletes it for the services you choose, e.g. only QRZ.com. Your QSOs and their upload and confirmation status are not touched.
  - Checked before anything is sent: the TQSL station location (Preferences offers the locations TQSL has) and the eQSL QTH nickname of each station profile.
  - TQSL can wait in its own window (e.g. for your certificate passphrase); *Cancel* stops it, and quitting NWOlogger asks first, then stops it instead of waiting.
- **Paper QSL cards** tracked separately (sent and received dates, via bureau, direct or manager), with a "to send" queue.
- **Callsign lookup:** offline country file (`cty.csv`, updated weekly), then QRZ.com XML with HamQTH as fallback. Chinese provinces are normalised to standard codes (BJ, SC, …).
- **Contest mode (N1MM style):**
  - Contest picked from a dropdown: CQ WW, CQ WPX, IARU HF, ARRL DX, ARRL 10 m, All Asian, CQ WW VHF, generic templates, and your own.
  - Live dupe check, Check Partial / N+1, and live score / multipliers / rate.
  - Cabrillo export.
- **Modern UI** with light and dark themes, keyboard-first (command palette ⌘K / Ctrl+K), in **English and 简体中文**. Windows and Preferences fit the available desktop and scroll when needed, including at 250% display scaling.

No rig control, by design: NWOlogger never talks to a radio.

## Keyboard

| Keys | Action |
|---|---|
| Enter | Log the QSO (DX); in contest mode, go to the next empty field and then log |
| Esc | Clear the whole form / wipe the contest entry line |
| Space (contest) | Next field |
| `14025` + Enter, `CW` + Enter (contest call field) | Set the frequency or mode |
| ⌘K / Ctrl+K | Command palette |
| ⌘L / Ctrl+L | Focus the call field |
| ⌘F / Ctrl+F | Search log |
| ⌘B / Ctrl+B | Bulk edit the selection |
| ⇧⌘D / Ctrl+Shift+D | Review suspicious dupes |
| ⇧⌘O / Ctrl+Shift+O | Go to the Operator field |
| ⌘1–3 / Ctrl+1–3 | Log · Paper QSL · Sync history tabs |
| ⇧⌘1 / ⇧⌘2 | DX view / Contest view |

## What is where

```
NWOlogger/
├── nwologger/                  the app (Python package)
│   ├── __main__.py            command line: nwologger [--db FILE] [--lang zh_CN] [--theme dark] [--debug] [--version]
│   ├── app.py                 starts Qt: language, theme, log file, main window
│   ├── applog.py              the diagnostic log file (secrets masked), hang watchdog
│   ├── i18n.py                tr(): looks up UI text in resources/translations/
│   ├── core/                  all logic, no Qt (unit-tested)
│   │   ├── db.py              the SQLite log file: schema, migrations, backups
│   │   ├── fields.py          one entry per QSO column: ADIF name, type, label
│   │   ├── qso.py, adif.py    QSO cleaning; ADIF reader / writer
│   │   ├── importer.py, exporter.py, mldx.py      import (dupe marking, merge), export, MacLoggerDX
│   │   ├── search.py          filters → SQL (wildcards, vague / exact mode)
│   │   ├── dupes.py, modes.py, bands.py           dupe rules, mode relations, band plan
│   │   ├── callsign.py, dxcc.py, regions.py, geo.py   calls, country file, China provinces, grids
│   │   ├── grids.py, globe.py, solar.py              grid statistics, spherical projection, solar position
│   │   ├── similar.py         similar calls for Worked before (log, QRZ.com / HamQTH cache, call lists, MASTER.SCP)
│   │   ├── calllists.py       LoTW user and eQSL AG member lists: download and fast search
│   │   ├── net.py, secrets.py HTTPS to the services only, no secrets in messages; keychain access
│   │   ├── sats.py, propagation.py                satellites and per-service names; TQSL propagation modes
│   │   ├── operators.py       personal / club station operator
│   │   ├── devices.py         rigs, antennas and setups for the Device section
│   │   ├── lookup/            QRZ.com XML, HamQTH
│   │   ├── sync/              LoTW + TQSL, eQSL, QRZ.com Logbook, HRDLog; applying confirmations
│   │   └── contest/           scoring engine, built-in definitions (defs/*.json), Check Partial, Cabrillo
│   ├── ui/                    Qt windows and widgets
│   │   ├── main_window.py     toolbar, menus, tabs, actions
│   │   ├── entry/dx_panel.py  the DX entry form
│   │   ├── log/               log table model / view, filter bar, advanced search
│   │   ├── contest/           contest entry line and panes, setup, user-defined contests
│   │   ├── stats/             globe view (gridmap.py); imagery, squares and night per pixel (globeraster.py); outlines (globegeometry.py)
│   │   ├── dupe_dialog.py     side-by-side dupe groups: compare, edit, merge field by field
│   │   ├── operator_field.py  the Operator field (personal / club station rules)
│   │   ├── list_picker.py     "Other mode… / Other satellite…" search
│   │   ├── bulk_edit.py, qsl_paper.py, import_dialog.py, prefs_dialog.py, about_dialog.py, …
│   │   └── theme.py           colours, light / dark stylesheet, icons
│   └── resources/             cty.csv, sats.json, icons (Lucide), brand/ (app icon), translations/zh_CN.json
├── tests/                     pytest suite; fixtures/sample.adi is a synthetic log for trying the app
├── tools/screenshots.py       renders docs/screenshots/ from made-up data
├── docs/screenshots/          the README screenshots
├── packaging/                 PyInstaller spec, Inno Setup script (Windows installer), smoke test of a built app
├── .github/workflows/         CI (tests on Linux, macOS and Windows) and release builds for all three
├── PLAN.md, TODO.md           design and remaining work
└── pyproject.toml, uv.lock    project metadata and pinned dependencies
```

## Privacy

- Passwords, upload codes and API keys go to the system keychain only (macOS Keychain / Windows Credential Manager). Any other keyring backend, such as a plaintext file, is refused.
  - Saved secrets are never shown again. *Forget* in Preferences removes one; renaming an account removes the old account's entry.
  - Deleting the last station profile with a callsign removes that callsign's QRZ Logbook key.
- Error messages, sync history and logs never contain a password, key, upload code or session token, even when a service echoes one back encoded. Requests go over HTTPS to each service's own hosts only, redirects included. QRZ.com XML gets the password and session key in the request body; LoTW and eQSL only accept them in the (encrypted) URL. HTTP request logging stays off even when the app logs at DEBUG.
- The diagnostic log (`~/Library/Logs/NWOlogger/` on macOS, `%LOCALAPPDATA%\NWOlogger\Logs` on Windows, `~/.local/state/nwologger/` on Linux) masks every secret and account user name NWOlogger has read, also inside tracebacks and echoed error pages, logs requests by host and path only (never the URL query), and shows your home folder as `~`. It holds counts and settings names, not your QSOs.
- The LoTW user and eQSL AG lists are public files, downloaded without a login; nothing you type is sent anywhere to search them.
- The grid map asks the imagery service only for the tiles in view (that shows which area you look at, not your QSOs). Tiles are kept in the system cache folder (`~/Library/Caches/NWOlogger` on macOS), up to 300 MB; Preferences → Map → Clear map cache empties it. A Tianditu key is kept in the keychain.
- Uploads to LoTW, eQSL, QRZ.com and HRDLog send only standard ADIF fields, never another program's `APP_…` fields. Imported fields that look like credentials (`EQSL_PSWD`, `…PASSWORD…`, `…TOKEN…`, `APP_…_KEY`) are left out, and never exported.
- Never commit real log files. `*.sql`, `*.sqlite`, `*.adi` and similar are git-ignored because they contain other operators' personal data. Only synthetic files under `tests/fixtures/` are committed.

## Credits

- **Authors:** Han Zhang (BI1NWO), Claude Opus 5.5 Extra High (Anthropic), and GPT-6.1 Sol Ultra (OpenAI). Copyright © 2026 Han Zhang.
- **Special thanks** to the [BY1QH](https://www.qrz.com/db/BY1QH) club station.
- **Data:** AD1C country files ([country-files.com](https://www.country-files.com/)), ARRL LoTW / TQSL lists, Super Check Partial.
- **Software:** icons by [Lucide](https://lucide.dev/) (ISC); Qt for Python (PySide6).
- **Grid-map visualization** inspired by [GridmasterMap (FG8OJ)](https://gridmaster.fr/) and [GridTracker](https://gridtracker.org/).


## License

[MIT](LICENSE)
