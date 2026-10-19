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

![Grid map, China & around: satellite squares worked from ON80, each with its name and first call; the evening night side darkens the east.](docs/screenshots/gridmap.jpg)
*China & around, half a zoom step in: every square shows its name and the first call that confirmed it. Green squares are confirmed (here by LoTW), amber ones only worked. The counts in the corner keep the VUCC tally; the dashed ring is 200 km around the own grid.*

| | |
|---|---|
| ![Zoomed in on ON80 and around](docs/screenshots/gridmap-detail.jpg) | ![The whole globe, all bands](docs/screenshots/gridmap-world.jpg) |
| *Zoomed in: names and calls at full size. A worked-only square's call is in italics. Your square is outlined, and the dashed circle marks 200 km around it.* | *All bands on the whole globe. The night side for the current UTC time ends at a clear grey line.* |

- **Squares:** green when confirmed by the sources you tick (LoTW by default; any mix of eQSL, QRZ.com and paper), amber when only worked, each with the first call. Names and calls appear about half a zoom step inside a whole-country view and grow as you zoom in. Every call has the same size and thin type, so a short call is no larger than a long one and all of them appear together. Worked squares get a light edge once they are big enough, so neighbours stay apart. Two-letter field names sit at 8% opacity over their 100 squares.
- **Filters:** a satellite or an orbit (LEO, MEO, HEO, GEO, or every satellite but the geostationary ones; the list holds every satellite with its QSO count), one band or all bands, and mode group. *Callsigns* and *Grids* list the station callsigns and the places your log says you operated from, with counts: tick several calls when you hold more than one, and *Within 200 km* ticks the places VUCC and GridMaster count (the default). The count line in the map's corner shows confirmed and worked squares and VUCC progress, and goes into the saved PNG.
- **Regions:** around my grid, China & around, CONUS 488 (GridMaster's squares outlined, with the count), Europe, Japan, World.
- **Moving around:** drag with the right or middle mouse button (Ctrl + drag on a trackpad) or use the arrow keys to rotate the globe; a left click shows the square's QSOs in the log. The wheel, a double-click or +/− zoom. *Open in a window* puts the map and its controls in a window of their own, whose size and place are remembered; its full-screen button or F11 fills the screen, and Esc comes back.
- **Save as PNG:** the map as you see it, at twice its size in pixels, named after the ticked callsigns and the centre grid (`BI1NWO at ON80 - NWOlogger 0.2.0.png`; several calls read *(et al)*), with that name in a band above the map and the counts, confirmed in green and worked only in amber, large in its corner.
- **Day/night:** shades the night side for the current UTC time, squares included, with a clear edge along the grey line (as on PSK Reporter's map), over the sea as over land: the imagery's near-black ocean is lifted to a sea blue on the day side so the line shows across water. It updates by itself and remembers whether it is on (on by default). Stars and a faint Milky Way sit behind the globe.
- **Imagery** (Preferences → Map): NASA Blue Marble (no key) or Tianditu 天地图 (a free developer key). Imagery, squares and the night side are drawn at your display's own resolution, Retina included. Tiles load in the background and are kept in the system cache folder (up to 300 MB), so views seen before open offline.

## Installation

See [INSTALLATION.md](INSTALLATION.md) for setup, first-run steps, troubleshooting, and macOS / Windows build instructions.

## Features

- **One SQLite file** holds every QSO.
  - Columns are plain ADIF names, so the log can be queried with any SQLite tool.
  - The log is backed up automatically on open and before every import or bulk edit.
  - A **regular backup** every 7 days (Preferences → Database, 1 to 365 days): a copy goes into `backups/` by itself when QSOs were logged, edited or deleted since the last one, kept apart from the launch copies, and a small window reminds you to copy the log to another disk or a cloud folder, with *Back up to…* remembering the folder.
  - Deleting QSOs doesn't shrink the file; File → Compact log file rebuilds it without the space they left, after a backup.
  - The file records which NWOlogger created it and last wrote it (About → System info). Opening it with an older NWOlogger than the one that last wrote it warns first, and a file that is not an NWOlogger log is refused rather than altered. Help → Show log file folder and Show backups folder open the folders.
- **ADIF import and export:** export all, selected, or filtered QSOs. Fields NWOlogger doesn't know are kept and written back out. A LoTW report saved from the LoTW website (`lotwreport.adi`) is applied like a LoTW download: the QSOs in your log get LoTW's confirmation and its date, and the ones not in your log are added (a possible duplicate waits to be ticked). An imported log also sets where downloads resume, from the confirmations it holds (a day before the newest one, never later than a position you already have); a LoTW report uses its own header timestamps.
- **MacLoggerDX migration** straight from its database. It keeps the LoTW, eQSL, QRZ and HRDLog upload history and the paper card dates that MLDX's own ADIF export drops.
  - MacLoggerDX only records the uploads it made itself. A QSO that LoTW or QRZ.com confirmed must have been uploaded there, so it is marked uploaded too (ADIF imports get the same rule); the import preview counts them. Download from LoTW and QRZ.com before your first upload to mark the rest.
- **Search** across several criteria at once, with `?` / `*` wildcards on callsigns. A Grid condition looks at all of a QSO's grids (`GRIDSQUARE` and `VUCC_GRIDS`): `OM89` finds a station that gave `OM88, OM89`, several grids may be typed in any order, and a comma or semicolon alone lists the QSOs with several grids; `/` in the Call box lists portable calls. Mode search is vague by default (`SSB` finds USB/LSB, `DIGI` finds FT8), with an *Exact* option. Searches can be saved.
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
  - The arrow at the end of Time On and Time Off opens a picker: the calendar for the date, then hour, minute and second columns with large ▲▼ buttons, *Now* and *Done*, so the time can be set by touch. Every tap changes the box at once; the picker stays until *Done*, Esc or a tap outside it.
  - The button row (Lookup, Previous, Clear, Keep, Log QSO) sits under the form, outside the part that scrolls, so it stays in view on a short screen and follows the splitter handle.
  - Fields stay set for a whole satellite pass (*Keep*). *Clear* empties the contact and keeps your Device section.
  - An empty rig, antenna or power uses the station profile's, shown in grey.
  - One *Grid* field takes the two or four grids of a station on a grid line (`OM88, OM89`, also with `;` or `/`): the first goes to `GRIDSQUARE`, all of them to `VUCC_GRIDS`, and the form, the log's Grid column, the compare window and Worked before show them together. Your own grids in the station profile work the same way.
- **Grid map** (Statistics tab, Ctrl+4): every square you worked on a rotatable globe over satellite imagery, confirmed in green and worked in amber, with the first call in each square. See [Grid map](#grid-map) above.
- **Similar calls** in Worked before, while you type: the same call, calls that start with or contain what you typed, and calls one character away (a typo, or a missing letter or digit). Your log comes first (most recent first), then calls QRZ.com / HamQTH know, then the calls of ARRL's [LoTW user list](https://lotw.arrl.org/lotw-user-activity.csv) and eQSL's AG member list (about 320,000 calls, with the date of each call's last LoTW upload), then MASTER.SCP. When a call is not found on QRZ.com / HamQTH, up to three MASTER.SCP calls one letter away are looked up there. With `*` or `?` the call field searches every source like the log's search box (`BA*K` → BA1PK, BA8BLK).
  - QRZ.com and HamQTH can only look up a complete call, so the lists are downloaded instead (next to the log file, refreshed weekly; File → Update LoTW and eQSL call lists) and searched on your computer while you type.
- **Station profiles:** *Add from my log…* lists every station callsign + grid pair your QSOs carry, with counts, and creates a profile for each one you tick. Importing never moves a QSO to a profile: a record keeps the station callsign and grid it came with, and the import preview says how many records have none (the only ones the chosen profile fills). Closing Preferences with unsaved station changes offers Save, Discard or Cancel, including when you use the window's close button or Esc.
- **Operators:** an Operator field in the entry form.
  - Personal stations fill in your call.
  - Club stations must enter the operator, which then stays for the session.
  - On a new UTC day or after a 3-hour break, the field turns amber once to be checked.
- **Strict dupe checking:**
  - Only the same call, band and exact mode within 30 seconds blocks logging.
  - The same mode within the next minute (the warning window, set in Preferences; an old 24-hour setting becomes 1 minute), or a related mode (SSB vs USB, USB vs LSB, FT8 vs FT4), only warns. Reviewing dupes or changing the windows rescans the marks first, so a mark an earlier window made does not linger.
  - A side-by-side compare window shows a whole group of possible dupes, one column per QSO. Edit the cells there, merge them field by field (click the value to keep on each row that differs; confirmations and upload states are combined), delete one, or mark them as not dupes, also all of them at once (asks twice). Edit → Merge selected… merges 2–5 selected QSOs with the same call.
- **Bulk edit** of a selection, with a preview that keeps up while you type, even for thousands of QSOs. A time or date it can't read is refused, never written. A backup is saved first.
- **Electronic confirmations, tracked per service:** LoTW (via TQSL), eQSL and QRZ.com Logbook (upload and download), and HRDLog.net (upload only). Satellite names are translated per service: the ISS is `ARISS` for LoTW and `ISS` for eQSL.
  - A call is **green** when LoTW confirmed the QSO (eQSL, QRZ.com and paper cards don't make it green). It stays green, and a dupe red, when the row is selected.
  - **Uploads are always your choice.** *Sync everything* only downloads. QSOs not yet uploaded to all four services are shown in grey (the tooltip names the missing services); the preset *Not uploaded to every service* lists them, and *Upload selected* sends them.
  - Downloads also record what is already uploaded: LoTW's report of accepted QSOs and the QRZ.com logbook mark those QSOs as uploaded. eQSL (it only offers your inbox) and HRDLog (no download) can't tell NWOlogger what you uploaded there before.
  - **Downloaded QSOs not in your log are added** (LoTW, eQSL, QRZ.com, and a LoTW report imported from a file), with the service's confirmation. A record within a minute of a logged QSO with the same call, whatever its band, mode or satellite, waits in a *Possible duplicates* window instead: tick the ones to add, or open the QSO it may duplicate. Adding downloaded QSOs to an empty log keeps their LoTW, eQSL or QRZ.com confirmations.
  - **Downloads survive a bad connection:** every download goes to a file next to the log first (`downloads/`, beside `backups/`; the last complete file per service and any unfinished one are kept), with retries and continuation where the service allows it: LoTW in yearly parts, eQSL by byte range, QRZ.com page by page. What arrived is applied even when the rest fails, and the next run continues from there. Failed downloads can be retried immediately; only a successfully applied download starts the wait between downloads. *Download from a date…* fetches LoTW confirmations from a day you choose.
  - **Sync positions** (Preferences → Sync positions) show where each service's next download continues: LoTW's confirmation and upload timestamps, eQSL's day, QRZ.com's last log id. They are locked; *Unlock* lets you move one, in a red danger zone, and *Lock and save* writes it.
  - The first LoTW download reuses its full QSO report for confirmations when the report provides the required timestamps, avoiding a second full download.
  - Every outcome goes to one **Sync results** window that never blocks the app (QSL → Show sync results). A row that a setting can fix has *Open Preferences*.
  - **Sync history** keeps every run; *Clear history…* (or QSL → Clear sync history…) deletes it for the services you choose, e.g. only QRZ.com. Your QSOs and their upload and confirmation status are not touched.
  - Checked before anything is sent: the TQSL station location (Preferences offers the locations TQSL has) and the eQSL QTH nickname of each station profile.
  - TQSL can wait in its own window (e.g. for your certificate passphrase); *Cancel* stops it, and quitting NWOlogger asks first, then stops it instead of waiting.
- **Paper QSL cards** tracked separately (sent and received dates, via bureau, direct or manager), with a "to send" queue. *Queue replies to received cards…* puts the QSOs with a card received and none sent in the queue; the context menu's *Mark paper QSL not sent / not received* clear one direction.
- **Callsign lookup:** offline country file (`cty.csv`, updated weekly), then QRZ.com XML with HamQTH as fallback. Chinese provinces are normalised to standard codes (BJ, SC, …).
- **Contest mode (N1MM style):**
  - Contest picked from a dropdown: CQ WW, CQ WPX, IARU HF, ARRL DX, ARRL 10 m, All Asian, CQ WW VHF, generic templates, and your own.
  - Live dupe check, Check Partial / N+1, and live score / multipliers / rate.
  - Cabrillo export.
- **One station folder, one running NWOlogger** (0.2.0, see [docs/data-safety.md](docs/data-safety.md)). Everything NWOlogger has lives in one folder in the system's application-data location (`~/Library/Application Support/NWOlogger` on macOS, `%LOCALAPPDATA%\NWOlogger` on Windows, `~/.local/share/nwologger` on Linux): the logs, `backups/`, `downloads/`, `settings.ini`, the diagnostic log and `nwologger.lock`; nothing is kept anywhere else. Cloud sync clients never see the live log. `Documents/NWOlogger` keeps one complete **backup copy** of the current log, refreshed about once an hour and at quit, safe to sync, with a README. Starting NWOlogger again brings the running window to the front and quits; a lock the operating system holds makes one writing NWOlogger per folder certain whatever the route (another user, a path alias), and is released when the process ends, crash included. The log file carries a **write guard**: any program can read it at any time, only NWOlogger writes it; one `DROP TRIGGER` per trigger (the README gives the command) or *Save a copy… for other programs* lifts it. A log written by a newer NWOlogger is refused with a link to the download page. A log from elsewhere is added by *File → Add log file…*, which copies it in with SQLite's backup API (progress bar, the original untouched) and switches to it.
- **Modern UI** with light and dark themes, keyboard-first (command palette ⌘K / Ctrl+K), in **English and 简体中文**. Windows and Preferences fit the available desktop and scroll when needed, including at 250% display scaling; Preferences lists its pages down the left side, so every page stays in view at any width.

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
│   │   ├── net.py, secrets.py HTTPS to the services only, downloads to files with retries and resume, no secrets in messages; keychain access
│   │   ├── sats.py, propagation.py                satellites and per-service names; TQSL propagation modes
│   │   ├── operators.py       personal / club station operator
│   │   ├── devices.py         rigs, antennas and setups for the Device section
│   │   ├── lookup/            QRZ.com XML, HamQTH
│   │   ├── sync/              LoTW + TQSL, eQSL, QRZ.com Logbook, HRDLog; applying confirmations; positions.py: where downloads resume
│   │   └── contest/           scoring engine, built-in definitions (defs/*.json), Check Partial, Cabrillo
│   ├── ui/                    Qt windows and widgets
│   │   ├── main_window.py     toolbar, menus, tabs, actions
│   │   ├── entry/dx_panel.py  the DX entry form
│   │   ├── log/               log table model / view, filter bar, advanced search
│   │   ├── contest/           contest entry line and panes, setup, user-defined contests
│   │   ├── stats/             the Statistics tab's filters and the map in its own window (statistics_tab.py); globe view (gridmap.py); imagery, squares and night per pixel (globeraster.py); outlines (globegeometry.py)
│   │   ├── dupe_dialog.py     side-by-side dupe groups: compare, edit, merge field by field
│   │   ├── operator_field.py  the Operator field (personal / club station rules)
│   │   ├── list_picker.py     "Other mode… / Other satellite…" search
│   │   ├── sync_controller.py, sync_dialogs.py   downloads and uploads from the window; possible duplicates among downloaded QSOs
│   │   ├── bulk_edit.py, qsl_paper.py, import_dialog.py, prefs_dialog.py, profiles_from_log.py, about_dialog.py, …
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
- The diagnostic log (`logs/` in the station folder, see above; `NWOLOGGER_LOG_DIR` overrides) masks every secret and account user name NWOlogger has read, also inside tracebacks and echoed error pages, logs requests by host and path only (never the URL query), and shows your home folder as `~`. It holds counts and settings names, not your QSOs.
- The LoTW user and eQSL AG lists are public files, downloaded without a login; nothing you type is sent anywhere to search them.
- The grid map asks the imagery service only for the tiles in view (that shows which area you look at, not your QSOs). Tiles are kept in the system cache folder (`~/Library/Caches/NWOlogger` on macOS), up to 300 MB; Preferences → Map → Clear map cache empties it. A Tianditu key is kept in the keychain.
- Uploads to LoTW, eQSL, QRZ.com and HRDLog send only standard ADIF fields, never another program's `APP_…` fields. Imported fields that look like credentials (`EQSL_PSWD`, `…PASSWORD…`, `…TOKEN…`, `APP_…_KEY`) are left out, and never exported.
- Never commit real log files. `*.sql`, `*.sqlite`, `*.adi` and similar are git-ignored because they contain other operators' personal data. Only synthetic files under `tests/fixtures/` are committed.

## Credits

- **Authors:** Han Zhang (BI1NWO), Claude Opus 5.5 Extra High (Anthropic), and GPT-6.1 Sol Ultra (OpenAI). Copyright © 2026 Han Zhang.
- **Special thanks** to the [BY1QH](https://www.qrz.com/db/BY1QH) club station, whose logo is the NWOlogger icon. The logo was designed by 王亦童 (BI1TYE).
- **Data:** AD1C country files ([country-files.com](https://www.country-files.com/)), ARRL LoTW / TQSL lists, Super Check Partial.
- **Software:** icons by [Lucide](https://lucide.dev/) (ISC); Qt for Python (PySide6).
- **Grid-map visualization** inspired by [GridmasterMap (FG8OJ)](https://gridmaster.fr/) and [GridTracker](https://gridtracker.org/).


## License

[MIT](LICENSE)
