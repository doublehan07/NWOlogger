# NWOlogger — Design Plan

Status: **v0.1.7** (2026-10-17): milestones M0–M8 are built (v0.1.0), plus the credential fixes and real-account tests (v0.1.1), the Device section, the Sync results window and the diagnostic log (v0.1.2), the LoTW / eQSL call lists for Similar calls and per-service sync history clearing (v0.1.3), and the fixes from testing 0.1.3: a quick Preferences → OK, language and theme switches without a restart, wildcards in the call field, sorting from the first row, new confirmations selected after a sync, and a more telling diagnostic log (v0.1.4), and the fixes from testing 0.1.4: no undo (backups instead), downloads that say what is new, Download everything again, and a wait between syncs (v0.1.5), and merging field by field, Ignore all marked dupes, Look up and fill with progress and safer bulk edit times (v0.1.6), and the grid map with satellite imagery (v0.1.7); see §14 for where the code differs from
this plan, and [TODO.md](TODO.md) for what is left. Open questions are collected in §13.

## 1. Requirements

A minimal, fast, cross-platform (macOS + Windows) amateur radio logger, modelled on
**MacLoggerDX** for day-to-day logging and on **N1MM Logger+** for contesting.

The project owner's requirements, grouped by topic. The rest of this plan is the design that meets them.

| Area | Requirement | Design |
|---|---|---|
| Goal | A minimal logger, closest to MacLoggerDX (the main reference). Club Log, Wavelog and N1MM were consulted for ideas | — |
| Storage | All QSOs in one SQLite file, so the log is easy to search | §4 |
| Platform | Qt UI, cross-platform, mainly Windows and macOS | §2 |
| UI | Like the MacLoggerDX main window, without the rig panel. The logger never connects to a real rig | §6 |
| UI | The app shows its version number | §2.1 |
| UI | English and Simplified Chinese | §11 |
| Logging | QSO entry is important | §6 |
| Contest | Contest mode is crucial: N1MM style, contest picked from a dropdown, live scoring | §10 |
| Editing | Edit several selected QSOs at once after logging (BBLogger style) | §6.1 |
| ADIF | Import; export all, export selected, export filtered | §8.1 |
| Migration | Bring over the existing MacLoggerDX log, including its upload and confirmation history | §8.1 |
| Search | Several filters combined: call (with `?` and `*`), mode, time, satellite, … | §7 |
| Search | Mode search is vague: `SSB` finds USB/LSB, `DIGI` finds FT8 | §7 |
| Sorting | Sort by any column: satellite name, time on, time off, … | §6 |
| Lookup | QRZ.com XML lookup with HamQTH fallback, plus offline `cty.csv` | §9 |
| Services | Download from and upload to LoTW, eQSL, QRZ.com and HRDLog. No Club Log | §8 |
| Paper QSL | A separate section records the dates paper cards were sent and received. No generic "QSL sent / received" columns: paper and electronic records stay apart | §4.1, §6 |
| Dupes | While logging, block input only when the QSO is strictly the same, i.e. same mode **and** submode | §5 |
| Dupes | USB ≠ LSB and FT8 ≠ FT4. Related modes (SSB vs USB, DIGI vs FT8) get a warning, never a block, and are never filtered out | §5 |
| Dupes | ADIF import accepts everything and marks every suspicious dupe | §5, §8.1 |
| Repo | README, TODO and PLAN in the repo. Commits are by the owner only, with no LLM co-author, and each message is one sentence | — |

**Out of scope (non-goals)**

- Rig control (CAT), rotor, keyer, CW or voice messages.
- DX cluster, maps, schedules, label printing, full award tracking.
- Digital-mode decoding.

These may move to TODO later, but they are not part of the core.

## 2. Tech stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python ≥ 3.12, managed with `uv` | Fast iteration, rich stdlib for HTTP/XML/CSV |
| GUI | **PySide6** (Qt 6, official bindings) | Native look on macOS + Windows |
| Database | stdlib `sqlite3`, WAL mode | One portable file, no server |
| HTTP | `httpx`, run in `QThreadPool` workers | Synchronous code that can be tested without a Qt event loop |
| Secrets | `keyring` (macOS Keychain / Windows Credential Manager) | Passwords are never stored in plain text |
| App prefs | `QSettings` | Window layout and UI preferences |
| i18n | Qt Linguist (`pyside6-lupdate` → `.ts` → `.qm`) | `en` source strings plus `zh_CN` |
| Tests / lint | `pytest`, `pytest-qt`, `ruff` | |
| Packaging | PyInstaller, one-folder → macOS `.app`/`.dmg`, Windows installer (Inno Setup) | Built by a GitHub Actions matrix |

### 2.1 Versioning

- **One source of truth:** the `version` in `pyproject.toml`, read at runtime through `importlib.metadata` and exposed as `nwologger.__version__`. Versions follow SemVer and start at `0.1.0`. While in `0.x`, a new milestone bumps the minor number and a fix bumps the patch number.
- **Shown in the app** in four places:
  - the window title (`NWOlogger v0.1.7 — nwolog.sqlite`)
  - the right end of the toolbar, as in MacLoggerDX
  - Help → About (version, Qt/PySide6 version, DB schema version, data-file path)
  - `nwologger --version`
- **Sent outward:** the same string goes into ADIF `PROGRAMVERSION`, the service User-Agent (`NWOlogger/0.1.7 (BI1NWO)`), and the Cabrillo `CREATED-BY` line.
- **Releases:** a git tag such as `v0.1.7` triggers the release build. CI fails if the tag and `pyproject.toml` disagree.

Architecture rule: `nwologger/core/` never imports Qt. All logic lives there: DB, ADIF, search, dupes, DXCC, contest scoring and sync.
The UI layer is thin, so the core is unit-testable and could also back a CLI.

## 3. Repository layout (planned)

```
nwologger/
  __main__.py              # `python -m nwologger`
  app.py                   # QApplication setup, translators, main window
  core/                    # pure Python, no Qt
    db.py                  # connection, schema, migrations, backups
    fields.py              # field registry: ADIF name ↔ column, type, label, group
    modes.py               # ADIF mode/submode table, mode groups, input aliases
    bands.py               # band edges, freq → band
    adif.py                # ADI reader/writer (ADX later)
    mldx.py                # MacLoggerDX database importer
    search.py              # filter spec → SQL WHERE (wildcards)
    dupes.py               # mode relations, dupe checks and suspicious-dupe marking
    callsign.py            # call normalisation, portable suffixes, WPX prefix
    regions.py             # province normalisation (China: ISO 3166-2:CN codes), call-district guess
    dxcc.py                # cty.csv loader + call → entity resolver
    lookup/                # qrz_xml.py, hamqth.py, cache.py
    sync/                  # base.py, matcher.py, lotw.py, tqsl.py, eqsl.py, qrz.py, hrdlog.py
    contest/               # engine.py, scoring.py, cabrillo.py, scp.py, defs/*.json, rules/*.py
  ui/
    main_window.py
    entry/                 # dx_panel.py, contest_panel.py, worked_before.py
    log/                   # log_model.py (lazy SQL model), log_view.py, filter_bar.py, search_dialog.py
    bulk_edit.py
    qsl_paper.py
    sync_dialog.py
    contest/               # setup_dialog.py, check_partial.py, score.py, mults.py, rate.py
    prefs/
  resources/               # icons, cty.csv (bundled fallback), sats.json, translations/
tests/
```

## 4. Data model (SQLite)

Default file: `~/Documents/NWOlogger/nwolog.sqlite`. It can be changed in Prefs, and several log files can be opened, one at a time.

Conventions:

- Column names are lower-case ADIF field names. The few non-ADIF columns (`first_name`, `last_name`, `qrz_*`, `dupe_*`, `extra`) are converted on ADIF export.
- Callsigns are stored upper-case.
- Times are **UTC ISO-8601 text with seconds** (`2026-10-10T15:19:00Z`), so the file stays readable with plain SQL.
- Frequencies are stored in MHz as `REAL`.

### 4.1 `qso` (one row per QSO)

| Group | Columns |
|---|---|
| Identity | `id` INTEGER PK, `uuid` TEXT UNIQUE (stable id across sync), `created_at`, `updated_at` |
| Core | `call`, `time_on`, `time_off`, `freq`, `band`, `freq_rx`, `band_rx`, `mode`, `submode`, `mode_group` (derived, indexed), `rst_sent`, `rst_rcvd`, `tx_pwr` |
| Satellite / propagation | `prop_mode`, `sat_name`, `sat_mode` |
| Worked station | `first_name`, `last_name` (ADIF `NAME` on export, §8.1), `qth`, `address`, `state`, `cnty`, `country`, `dxcc`, `cqz`, `ituz`, `cont`, `gridsquare`, `vucc_grids`, `lat`, `lon`, `iota`, `sota_ref`, `pota_ref`, `wwff_ref`, `sig`, `sig_info`, `skcc`, `email`, `web`, `qsl_via` |
| My station (copied from the station profile at log time) | `station_callsign`, `operator`, `my_gridsquare`, `my_vucc_grids`, `my_dxcc`, `my_cq_zone`, `my_itu_zone`, `my_state`, `my_cnty`, `my_pota_ref`, `my_sota_ref`, `my_wwff_ref`, `my_rig`, `my_antenna`, `station_profile_id` |
| Notes | `comment` (may be sent to services), `notes` (private) |
| Contest | `contest_id`, `contest_session_id`, `stx`, `srx`, `stx_string`, `srx_string` |
| Dupe marking | `dupe_flag` (empty, `DUPE` or `SUSPECT`), `dupe_of` (id of the earlier QSO it collides with) |
| **Paper QSL** | `qsl_sent`, `qslsdate`, `qsl_sent_via`, `qsl_rcvd`, `qslrdate`, `qsl_rcvd_via`, `qslmsg` |
| LoTW | `lotw_qsl_sent`, `lotw_qslsdate`, `lotw_qsl_rcvd`, `lotw_qslrdate` |
| eQSL | `eqsl_qsl_sent`, `eqsl_qslsdate`, `eqsl_qsl_rcvd`, `eqsl_qslrdate`, `eqsl_ag` (Authenticity Guaranteed) |
| QRZ.com | `qrzcom_qso_upload_status`, `qrzcom_qso_upload_date`, `qrzcom_qso_download_status`, `qrzcom_qso_download_date`, `qrz_logid`, `qrz_qsl_rcvd`, `qrz_qslrdate` (exported as QRZ's own `APP_QRZLOG_STATUS` / `APP_QRZLOG_QSLDATE`) |
| HRDLog | `hrdlog_qso_upload_status`, `hrdlog_qso_upload_date` |
| Everything else | `extra` TEXT (JSON object of every other ADIF field, `APP_*` and `USERDEF` included) |

Notes on the model:

- **Lossless ADIF round-trip.** A field without its own column goes to `extra` and is written back out on export. The schema stays small without losing any data.
- **Province is ADIF `STATE`.** For China it holds the ISO 3166-2:CN letter code (`BJ`, `SC`, `XJ`, …), shown with its name (北京 / Beijing). For other countries it holds what the lookup returns, e.g. a US state.
- **Paper and electronic records never share a column.** ADIF `QSL_SENT`/`QSL_RCVD` and their dates are paper only. Every electronic service has its own fields.
- **Edits after upload.** Editing an uploaded QSO sets its QRZ and HRDLog status to `M` (modified, an ADIF enum value), so the next sync re-sends it. LoTW and eQSL do not support in-place edits, so they are flagged in the UI instead.
- **Indexes:** `call`, `time_on`, `band`, `mode_group`, `sat_name`, `dxcc`, `station_callsign`, `contest_session_id`, `dupe_flag`, plus a composite dupe index `(call, band, time_on)`.

### 4.2 Other tables

| Table | Purpose |
|---|---|
| `meta` | `schema_version`, creation info |
| `station_profile` | Call, operator, grid, zones, refs; TQSL station-location name; eQSL QTH nickname; which QRZ logbook key and HRDLog call to use |
| `contest_session` | Contest definition id, name, start/end, Cabrillo categories, sent-exchange template, next serial, station profile |
| `saved_filter` | Named searches (JSON filter spec) |
| `lookup_cache` | Callsign → JSON result, source, fetched_at (TTL in Prefs) |
| `sync_state` | Per-service cursors (LoTW last QSL / last QSO-rx, eQSL RcvdSince; QRZ.com is always fetched whole), the time of the last download and upload per service (the wait between syncs), and how long the last full download took. QSL → LoTW / eQSL → *Download everything again…* resets the cursors |
| `sync_log` | Sync history: service, direction, time, counts, errors. *Clear history…* deletes it per service; `sync_state` (where downloads resume) is separate and kept |

### 4.3 Safety

- Backups go to `backups/` next to the log as `<log>-<UTC time>-<reason>.sqlite`, copied with SQLite's online backup while the log stays open:
  - `open` each time an existing log is opened, skipped when the log is unchanged since the newest backup (the copies are compared page by page, past the 100-byte file header);
  - `import`, `bulk-edit`, `delete`, `merge`, `dupes`, `lookup` and `recompute` right before those changes, the newest 10 kept apart so launches can't push them out;
  - `manual` from File → Back up now and Preferences → Database.
  - Of `open` and `manual`, the newest N are kept (Preferences, default 20). Restoring is opening a backup with File → Open log file.
- All writes happen in transactions.
- Schema migrations are versioned.
- **No undo** (removed in 0.1.5): undoing a download put the log out of step with the services, and one click could delete a whole import. Instead, every change to many QSOs (import, bulk edit, delete, merge, ignore all dupes, look up and fill, recompute DXCC) asks first and saves a backup (§4.3 backups); 0.1.4's undo history is dropped from the file once.

## 5. Modes, mode groups and dupes

ADIF stores `MODE` + `SUBMODE` (e.g. `SSB`/`USB`, `MFSK`/`FT4`). The user can type familiar names; input aliases normalise them:

| Typed | Stored as |
|---|---|
| `USB`, `LSB` | `SSB` + submode |
| `FT4` | `MFSK`/`FT4` |
| `RY` | `RTTY` |
| `PH`, `PHONE` | phone group placeholder |
| `DG`, `DIGI`, `DATA` | data group placeholder |

Every QSO gets a derived **`mode_group`**, following the LoTW/ARRL groups:

| Group | Modes |
|---|---|
| `CW` | CW |
| `PHONE` | SSB (USB/LSB), AM, FM, DIGITALVOICE |
| `DATA` | FT8, FT4, RTTY, PSK, JT65, JS8, OLIVIA, … and the `DIGI`/`DATA` placeholders |
| `IMAGE` | SSTV, FAX, ATV |

### 5.1 How two modes relate

| Relation | Meaning | Examples |
|---|---|---|
| Same | Identical `MODE` + `SUBMODE` | USB–USB, FT8–FT8 |
| Could be the same | One is a more general name for the other | SSB–USB, SSB–LSB, DIGI–FT8, DATA–RTTY, MFSK–FT4 |
| Siblings | Different specific modes in the same group | USB–LSB, FT8–FT4, FM–SSB |
| Unrelated | Different groups | CW–SSB, FT8–CW |

The same relation table drives dupe checks (§5.2), vague mode search (§7) and confirmation matching (§8.2).

### 5.2 Dupe rules (DX mode and ADIF import)

Two QSOs are compared when they have the same call and band, plus the same `sat_name` when `prop_mode = SAT`. Strict is the rule: **only the same mode within 30 seconds is a dupe**. Everything else is at most a warning.

| Mode relation | Time on apart | Result | While logging | ADIF import |
|---|---|---|---|---|
| Same | ≤ 30 s | **Dupe** | Call field red, "DUPE" badge, **Log disabled** | Imported, marked `DUPE` |
| Same | > 30 s, within the warning window | **Suspect** | Amber warning, logging allowed | Imported, marked `SUSPECT` |
| Could be the same, or siblings | Within the warning window | **Suspect** | Amber warning, logging allowed | Imported, marked `SUSPECT` |
| Unrelated, or outside the window | — | — | — | — |

- **Windows:** the block window (30 s) and the warning window (default 24 h) are both set in Prefs.
- **Nothing is filtered out:** USB ≠ LSB and FT8 ≠ FT4. Related modes are never blocked or dropped, only warned about and marked.
- **Import never skips:** every record is imported and every suspicious dupe is marked (§8.1).
- **Marks are stored** in `dupe_flag` / `dupe_of`.
  - Marked rows are highlighted in the log, and a *Suspicious dupes* preset lists them.
  - Actions: *Not a dupe* (clears the mark), *Merge into the other QSO*, *Delete*.
  - *Find duplicates* re-scans the whole log with the same rules.
- **Hard block:** "Log disabled" is absolute by default. A Prefs switch, "Allow logging dupes" (**off** by default), logs the QSO and marks it `DUPE`.

### 5.3 Contest mode

A contest's own rules decide what counts as a dupe, because the score depends on it. The rule is usually once per band, or per band + mode, for the whole contest, so there is no 30-second window. For example, a contest that allows each station once per band on any digital mode treats FT8 followed by FT4 as a real dupe. Built-in definitions follow the published rules. User-defined contests compare modes strictly by default. A dupe logged anyway scores 0 points.

### 5.4 Confirmation matching

Matching downloaded confirmations is a separate job (§8.2). It prefers an exact mode match and falls back to a related mode, because the other station or the service may report SSB where you logged USB.

## 6. Main window

The layout follows the MacLoggerDX workflow, minus the rig panel: entry panel on top, log below. The look is NWOlogger's own modern design, not a copy of MacLoggerDX, BBLogger or N1MM.

**Visual style**

- **Qt Widgets, not QML.** `QTableView` and dense forms handle 100k-row logs better. The modern look comes from the Fusion style plus an app stylesheet built on design tokens (colours, spacing, radii).
- **Themes:** light and dark, following the OS by default. A theme picked in Preferences shows at once (Cancel puts the old one back); icons are drawn in the current theme's colours whenever they are painted, so they follow every switch.
- **Language:** English or 简体中文, switched when you press OK in Preferences: the main window is built again in the new language with the same log file (no new backup), operator, view, tab, filter, sort, selection, scroll position, entry form and Sync results rows (about 0.1–0.3 s). While an upload or download runs, the switch waits for it.
- **Colour:** one accent colour. Status colours appear only where they carry meaning: confirmed green, suspect amber, dupe red.
- **Type:** tabular (monospaced) figures for callsigns, frequencies and times, so columns line up.
- **Icons:** SVG line icons from a permissively licensed set (e.g. Lucide, ISC).
- **Layout:** entry fields grouped into labelled sections (Contact, Station, Location, References, QSL) instead of one dense grid. Validation is inline, next to the field.
- **Keyboard-first:** every action has a shortcut, plus a command palette (⌘K / Ctrl+K).

```
┌ Toolbar: [Log ▾ / Contest]  [Search]  [Sync ▾]  [Import]  [Export ▾]  [Prefs]  UTC 04:20:02  Local 12:20  v0.1.7 ┐
├ Entry panel ── [ DX | Contest ] ─────────────────────────────────────────────────────────────────────────────┤
│ Call [______]  First  Last    │ Time On  2026-10-16 04:19:35   Time Off                                      │
│ Address / City                │ MHz  [145.920] 2M   RX MHz [436.950] 70CM   Mode [FM ]                       │
│ Province / Country            │ Sat [SO-50 ▾]  Sat mode [ ]  Prop [SAT]  Power [25]  RST S [59]  RST R [59]  │
│ Email / Web                   │ Grid  ITU  CQ  DXCC  IOTA  SOTA  POTA  WWFF  SKCC  QSL Via                    │
│ Comment (upload) / (local)    │ ┌ Worked before: similar calls, band × mode matrix, last QSOs, NEW … ┐        │
│ Device: Setup [Sat portable ▾] Power [5]   Rig [IC-705 ▾] Antenna [U7V4 ▾]                                   │
│ [LookUp] [Previous] [TimeOn] [TimeOff] [Log QSO ⏎] [Clear ⎋]  sticky: ☑Freq ☑Mode ☑Sat ☑Device               │
├ Tabs: [ Log | Paper QSL | Sync history ] ────────────────────────────────────────────────────────────────────┤
│ 1434 QSOs · 37 shown · /Users/.../nwolog.sqlite        DXCC 75 wkd / 68 cfm                                   │
│ [Call ?*] [Band ▾] [Mode ▾] [Sat ▾] [Country] [Preset ▾]  [More filters…]  [Clear]                           │
│ Call │ Time On ▾ │ Time Off │ MHz │ Band │ RX MHz │ Band RX │ Mode │ Sat │ Pwr │ RST S │ RST R │ LoTW │ …     │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Entry panel (DX).** The field set follows MacLoggerDX, without rig control. Name is split into First and Last, as in MLDX.
  - **Location** is kept to what is needed: Country, Province, Address (street, city → ADIF `ADDRESS` / `QTH`), CQ zone, ITU zone. County, lat/lon and other location fields are not on the form; they are kept if an import brings them.
  - A summary line under the call shows the last confirmation (e.g. "Confirmed 2025-08-18 on 70CM FM"), plus bearing and distance computed from the two grids.
  - **Times:**
    - Time On stamps itself when you start typing a call, and Time Off follows the clock until you log.
    - If you change Time On by hand, Time Off becomes Time On + 1 min, and keeps following Time On.
    - *Now* next to Time On goes back to the default: Time On is now (held once a call is typed) and Time Off follows the clock again. When editing a saved QSO there is no live clock, so both become now.
    - When you edit an existing QSO, changing Time On keeps its original duration.
    - Changing only Time Off never touches Time On.
    - A Time Off earlier than Time On turns red, and logging is refused with a warning.
  - **Frequencies:** the field is labelled **TX MHz**; RX MHz sits below it.
    - An **= TX** toggle keeps RX equal to TX, for simplex.
    - Picking a satellite turns it off, because satellites are split; clearing the satellite turns it back on.
  - **Mode** is a plain dropdown of *your* modes (Preferences → Modes: tick and drag to order; default = the common ones), then *Other mode…*, which searches every ADIF mode and submode and can add the choice to your list.
  - **Propagation** is a dropdown of TQSL's 16 propagation modes (AS, AUE, AUR, BS, ECH, EME, ES, F2, FAI, ION, IRL, MS, RS, SAT, TEP, TR).
    - It also offers the ADIF-only GWAVE, INTERNET and LOS, marked "not for LoTW". RPT is not offered: the repeater you used goes in *Notes (local)*. A QSO imported with RPT keeps it, and is still not sent to LoTW.
    - The ADIF `SAT_MODE` field is no longer on the form; it is kept when an import brings it.
  - **Propagation comes before Satellite** in the form.
  - **Satellite** is a dropdown of *your* satellites (Preferences → Satellites, "In form" column; until you choose, the ones you worked plus popular ones), then *Other satellite…*, which searches the full LoTW / TQSL list, your added satellites and names found only in your log.
    - Repeaters are not in this list and don't set a propagation: the repeater's name goes in *Notes (local)*.
    - **Satellite without SAT propagation:** the satellite name is kept. An amber chip says "Satellite is set, but propagation is not SAT" and logging asks once. The reverse ("Propagation is SAT, but no satellite") is treated the same way. A satellite with an *empty* propagation is stored as SAT, as ADIF expects.
    - Picking one sets propagation SAT and fills the frequencies and mode from your last QSO on it (else its defaults).
    - Per-service names are handled on upload (§8.2).
  - **Operator** is a field in the Contact section (and in the contest bar). See §6.3.
  - **Device** section (rig, antenna, power), under References & notes:
    - *Rig* (`MY_RIG`) and *Antenna* (`MY_ANTENNA`) are editable dropdowns of your lists (Preferences → Devices); *Power* (`TX_PWR`) moved here from the RST row.
    - A **setup** is a rig + antenna + power you put together in Preferences → Devices. Picking it fills all three; each can still be changed. The Setup dropdown shows the setup the fields equal, or "—" once they differ (`core/devices.py`). A setup without a power leaves the power alone.
    - Empty fields show the station profile's rig, antenna and power in grey, and those are logged.
    - The section is your station, not the contact: it is saved between runs (`device` preference) and also used by contest mode. While a saved QSO is edited it shows that QSO's values, and yours come back afterwards.
    - The other station's rig, antenna and power, the repeater, and your QTH as text, have no fields: they go in *Notes (local)*.
  - **Comments:** *Comment (upload)* is ADIF `COMMENT`, sent to eQSL, QRZ.com and HRDLog (the operator's name, a thank-you). *Notes (local)* is `NOTES`, which never leaves the log (`qso.PRIVATE_COLS`).
  - *Sticky* fields (*Keep*: Freq, Mode, Sat, Device) carry over to the next QSO after Log QSO, which matters for satellite passes. With *Device* off, rig, antenna and power go back to the profile's after each QSO.
  - *Clear* (and Esc, and Cancel while editing) empties the Contact section: TX / RX MHz, Mode (a blank "—" entry), RST, Propagation and Satellite, whatever *Keep* says. The Device section stays. RST stays blank until a mode is chosen; logging without a mode is refused.
  - *Previous* recalls the last QSO's fields.
  - Enter logs the QSO.
  - **Similar calls** sit at the top of Worked before, updated while you type (from two characters):
    - A call matches when it is the typed text, starts with it, contains it, or is one character away (changed, added or dropped). Portable calls match through their base call.
    - **Wildcards:** with `*` or `?` the call field is a pattern with the log search's rules (`*` any run, also none; `?` one character; the whole call, any case), searched in every source: `BA*K` finds BA1PK, BA8BLK and BA1PK/P. One regex runs over the call lists' text (a few ms; at most 500 per source, the header counts them and shows "+" past that), and the first 50 rows are listed. A pattern is no call: no Time On, country fill, lookup or dupe check, and Log QSO / Enter are refused until you pick one.
    - Order: calls in your log (count, last QSO, confirmations; most recent first), then calls not worked that QRZ.com / HamQTH have returned (the lookup cache), then the public call lists, then MASTER.SCP. Within each group, better matches first.
    - **Public call lists** (`core/calllists.py`): QRZ.com's XML API and HamQTH only look up one complete call (no partial or wildcard search), so the internet's call data comes from ARRL's LoTW user list (`lotw-user-activity.csv`, ~236,000 calls with their last upload date) and eQSL's AG member list (`AGMemberList.txt`, ~199,000 calls), about 320,000 calls together. In one real log they hold 76% of the Chinese calls, against 28% for MASTER.SCP. They are downloaded over HTTPS without a login next to the log file (File → Update LoTW and eQSL call lists, and weekly at start), kept as one sorted text with offsets (~5 MB in memory, loaded on a worker thread), and searched by binary search: exact, prefix, contains, and every text one character changed, added or removed (~8 ms per query). A bad download (fewer than 1,000 calls) keeps the old copy. The row shows "uses LoTW, last upload <date>" and / or "eQSL AG member".
    - Click a call to use it. The matrix and last QSOs below still show the typed call.
    - The lists are kept in memory and updated when you log, so typing never queries the log file.
  - Double-clicking a row loads that QSO into the panel for editing (Save / Cancel).
- **Log table.** A `QAbstractTableModel` over SQL with lazy loading (`fetchMore`), so 100k+ QSOs stay fast.
  - Clicking any header sorts with SQL `ORDER BY`. `time_on` breaks ties (latest first). A header click shows the first row of the new order; the selection stays highlighted. New data (an edit, a sync) keeps the row you were looking at on top.
  - **Grid map** (Statistics tab, Ctrl+4; `core/grids.py`, `core/maptiles.py`, `ui/stats/`): every 4-character Maidenhead square on satellite imagery, GridMaster / GridTracker style.
    - A square counts the QSOs whose `gridsquare` (or one of whose `vucc_grids`) is in it. Confirmed (green): the first QSO, by Time On, confirmed by one of the sources you tick (LoTW by default; any mix of eQSL, QRZ.com, paper; remembered); the square shows its grid and that call. Worked only (amber): the first call worked, in italics. The tooltip has the date, satellite or band, source, and how many QSOs and calls.
    - Filters: satellite / one band without satellites / all, mode group, and *From within 200 km of* a grid (default on, from the station profile's grid): VUCC and GridMaster count only QSOs made within 200 km of one place; a QSO without your grid counts as made there. The count line gives confirmed / worked, VUCC progress for the band, and for CONUS GridMaster's 488 (outlined).
    - Region presets: around my grid, China & around, CONUS 488, Europe, Japan, World. Wheel or +/− zooms around the cursor, drag or the arrow keys pan, a double-click zooms in, a click shows the square's QSOs in the log (a Grid "starts with" condition). Fields are labelled when zoomed out, then squares get lines, grid names and calls (a long call shrinks to fit). *Save as PNG*.
    - Imagery (Preferences → Map): *Automatic*, NASA Blue Marble (GIBS, no key, zoom 1–8), Tianditu 天地图 (the official national service, which loads reliably in China and uses the same coordinates as Maidenhead; needs a free developer key, kept in the keychain), or none. Automatic picks Tianditu when NWOlogger runs in Chinese or the system region is China and a key is set, else NASA. Only imagery layers are used; the grid is drawn on top.
    - Tiles (Web Mercator) are fetched on daemon threads, 6 at a time, newest first, only the ones in view and only from the provider's hosts (`net.HOSTS`); a coarser tile stands in while a finer one loads. They are kept in the system cache folder (up to 300 MB, least recently used removed first), so a view seen before opens offline. After 12 failures in a row a provider is no longer asked for the session, and the map says why. The log file gets one line per map session (loaded, from the cache, failed); `NWOLOGGER_OFFLINE` fetches nothing.
  - After a download, the QSOs it confirmed for the first time (LoTW, eQSL or QRZ.com received flag turned to Y; not upload states or filled-in details) become the log's selection and the table scrolls to the first; one click clears it. With Sync everything, each download that finishes adds its own, unless you clicked in the log in between. Rows hidden by the filter are counted in the status bar.
  - Columns can be shown, hidden and reordered from the header context menu.
  - Confirmation columns: LoTW ↑/↓, eQSL ↑/↓, QRZ ↑/↓, HRDLog ↑, Paper sent/rcvd. Each shows a date; an empty cell means not done.
  - Row colour: the call is green when LoTW confirmed the QSO (`lotw_qsl_rcvd` Y or V; eQSL, QRZ.com and paper cards don't count); `SUSPECT` rows have an amber background, `DUPE` rows red text on red. Text is grey while a QSO is not yet uploaded to all four services (`core/uploads.py`, the same rule as Upload pending); the tooltip names the missing ones.
- **Context menu on a selection:**
  - Edit
  - Bulk edit…
  - Delete
  - Export selected
  - Upload selected → LoTW / eQSL / QRZ / HRDLog
  - Mark paper QSL sent… / received…
  - Lookup & fill missing
  - Recompute DXCC
  - Compare with the original… / Not a dupe
- **Paper QSL tab.**
  - A queue of QSOs marked "to send" (`qsl_sent = Q/R`).
  - Actions: set sent date, set received date, set via (Bureau / Direct / Manager).
  - Filters: "received, not yet answered", "sent, awaiting reply".

### 6.1 Bulk edit

Bulk edit follows the BBLogger idea of editing many selected QSOs at once, with NWOlogger's own UI. It is a dialog that applies to the selected QSOs:

- Each field row has a ☐ apply box and an operation:
  - set to value
  - clear
  - find & replace
  - fill from lookup
- A preview lists the count and an old → new diff of the first rows.
- Apply runs in one transaction, after a `bulk-edit` backup. It is refused while a time can't be read (it is never written as empty) or a QSO would end before it starts; *Leave those out* takes those QSOs out of the edit.
- After applying, the affected QSOs are flagged as modified for QRZ and HRDLog re-upload (§4.1).

### 6.2 Dupe compare and review

A QSO can collide with more than one other QSO, so the window shows a **group**.
- **What a group is:** every QSO linked by possible-dupe relations (same call and band, related mode, within the warning window; found transitively, at most 12), oldest first, one column each.
- **Layout:**
  - The column header shows the time and the mark (Dupe / Suspect).
  - Fields that differ are highlighted, and the fields that decide a dupe (call, band, mode, propagation, satellite, time) are bold.
  - The top lists each QSO against the earlier ones, e.g. "#2 dupe of #1: same mode and submode, 15 s apart; #3 possible dupe of #2: related modes, 12 min apart".
- **Editing in the window:** double-click a cell and type, then *Save edits*. All edits are saved in one transaction and the group's marks are recomputed. A Time Off earlier than Time On is refused.
- **Actions** (click a column header to select a QSO):
  - *Not dupes*: marks every QSO in the group `OK`, so later scans leave them alone. *Not dupes: all…* (also Edit → Ignore all marked dupes…) does it for every marked QSO in the log, after a warning and a second question, with a `dupes` backup.
  - *Merge…*: a Result column appears. On each row where the QSOs differ, click the value the merged QSO keeps (Comment / Notes can keep both texts); a column header chooses the QSO that stays. Defaults are the old merge: the kept QSO's value, or the only one there is. Each service's state (uploaded, confirmed) moves with its date and log id from the QSO with the best state; other programs' `APP_…` fields are kept from all (`core/merge.py`). The result is checked like the entry form, a `merge` backup is saved, the others are deleted, and an uploaded QSO whose fields changed is marked for re-upload. The confirmation lists deleted QSOs that are also on QRZ.com / HRDLog, to delete there by hand.
  - *Edit → Merge selected…* opens the same merge for 2–5 selected QSOs with the same call, even when they aren't marked as dupes.
  - *Delete selected*.
  - *Open in entry form*.
  - Merge and delete save a backup first.
- **Opened from:**
  - *Review suspicious dupes…* (⇧⌘D / Ctrl+Shift+D), which steps through every marked QSO with Previous / Next.
  - The right-click *Compare with the original…*.
  - The question after an import or *Find duplicates*.
  - The **Compare…** button next to the DUPE / Possible dupe chip while you type. That view compares the not-yet-logged QSO.

### 6.3 Operators (personal and club stations)

Each station profile is a **personal** or a **club** station.

- **Personal:** the operator is the profile's operator, or else its station callsign.
- **Club:** the **Operator field** (Contact section; also in the contest bar) must be filled in. There is no pop-up.
  - An empty field turns red and the QSO is not logged.
  - The operator is kept while the app runs.
  - On a new UTC day, or after a 3-hour break without logging, the field turns amber once ("check the operator") and a second Enter logs. The break catches a change of operator between shifts.
  - ⇧⌘O / Ctrl+Shift+O jumps to the field.
- **The value** goes into ADIF `OPERATOR`. Cabrillo `OPERATORS` lists every operator of a contest session.

## 7. Search

- **Quick filter bar:** Call, Band, Mode, Sat, Country, and a Preset dropdown with entries such as All, Today, This contest, Not uploaded to LoTW, LoTW confirmed, Paper to send, Suspicious dupes.
- **Advanced search dialog:** rows of *field · operator · value*, combined with AND. Searches can be saved as named filters.

| Operator kind | Operators | Applies to |
|---|---|---|
| Text | is, is not, contains, matches (wildcards), empty, not empty | Any text field |
| Time | before, after, between | Any time field |
| Status | yes / no / date range | Confirmation fields |
| Mode | Vague by default (below); tick *Exact* for a strict match | Mode |

- **Vague mode search (default).** A search finds the mode itself, anything more specific under it, and anything logged with a more general name that could be it (§5.1). Group names match a whole group:

| Search | Finds |
|---|---|
| `SSB` | SSB, USB, LSB |
| `USB` | USB, and SSB logged without a submode |
| `DIGI` or `DATA` | every data mode (FT8, FT4, RTTY, PSK, …) |
| `FT8` | FT8, and QSOs logged only as DIGI/DATA |
| `FT4` | FT4, MFSK without a submode, DIGI/DATA |
| `PHONE` | every phone mode (SSB, AM, FM, digital voice) |

With *Exact* ticked, a search finds only that mode and submode.

- **Wildcards:**
  - `*` matches any run of characters and `?` matches exactly one character.
  - Matching is case-insensitive and anchored, e.g. `BA1*`, `B?1PK`, `*/P`.
  - Search terms are converted to SQLite `GLOB` and `[` is escaped.
  - Text with no wildcards means *contains*.
- **Export filtered** always uses the current filter. **Export selected** uses the current selection.

## 8. ADIF and sync

### 8.1 ADIF (target: ADIF 3.1.8)

- **Reader:**
  - Tag names are case-insensitive, and the header is optional.
  - Accepts `APP_*` and `USERDEF` fields.
  - Handles UTF-8 and Latin-1. Field lengths are tried as bytes first, then as characters, because real-world files disagree.
- **Import:** every record is imported by default, and every suspicious dupe is marked `DUPE` or `SUSPECT` (§5.2). Nothing is skipped silently.
  - The preview shows counts of new, `DUPE` and `SUSPECT` records before anything is written.
  - Optional *merge* mode, off by default: when a record matches an existing QSO, its data goes into that QSO (fill empty fields, update confirmation status) instead of becoming a new row. This is for confirmation files downloaded by hand.
  - A station profile is assigned when `STATION_CALLSIGN` is missing. If only `OPERATOR` is present, it is copied into `STATION_CALLSIGN`, because LoTW needs it.
  - `NAME` is split into first and last at the last space, so exporting it again gives back exactly the same text.
  - Files from MacLoggerDX (`PROGRAMID=MacLoggerDX`) never set the paper QSL from `QSL_SENT` (§8.1.1).
- **Writer:** UTF-8 with header `ADIF_VER`, `PROGRAMID=NWOlogger`, `PROGRAMVERSION`, `CREATED_TIMESTAMP`.
  - Exports all / selected / filtered.
  - `NAME` is written as First + space + Last.
  - Optional profiles strip private fields (`notes`, `email`) for service uploads.

### 8.1.1 MacLoggerDX migration

Import straight from the MacLoggerDX database (`MacLoggerDX.sql`, table `qso_table_v008`), not from its ADIF export. The ADIF export sets `QSL_SENT=Y` on every QSO and leaves out all upload history. MLDX keeps that history as comma-separated text in two columns, which the importer splits into separate fields:

| MLDX column | Text | NWOlogger fields |
|---|---|---|
| `qsl_sent` | `Uploaded to LoTW <date>` | `lotw_qsl_sent=Y`, `lotw_qslsdate` |
| | `Uploaded to eQSL <date>` | `eqsl_qsl_sent=Y`, `eqsl_qslsdate` |
| | `Uploaded to QRZ Logbook <date>`, `Updated in QRZ Logbook <date>` | `qrzcom_qso_upload_status=Y`, latest date |
| | `Uploaded to HRDLOG <date>` | `hrdlog_qso_upload_status=Y`, latest date |
| | `Mail Card <date>` | **paper** `qsl_sent=Y`, `qslsdate` |
| | bare `Y` | ignored |
| `qsl_received` | `LoTW:<yyyymmdd>` | `lotw_qsl_rcvd=Y`, `lotw_qslrdate` |
| | `QRZLogbook:<yyyymmdd>` | `qrz_qsl_rcvd=Y`, `qrz_qslrdate` |
| | `eQSL.cc DownloadInBox:Y` | `eqsl_qsl_rcvd=Y` (MLDX keeps no date) |
| | `Mail Card <date>`, also the typo `Mai Card` | **paper** `qsl_rcvd=Y`, `qslrdate` |

Other columns:

| MLDX column | NWOlogger |
|---|---|
| `qso_start`, `qso_done` (Unix time, UTC) | `time_on`, `time_off` |
| `my_call` | `station_callsign` and `operator` |
| `first_name`, `last_name` | same columns |
| `mode` (`USB`, `FT4`, `C4FM`, …) | ADIF mode + submode (`SSB/USB`, `MFSK/FT4`, `DIGITALVOICE/C4FM`) |
| `satellite` | `sat_name`, with `prop_mode=SAT` |
| `tx_frequency`, `rx_frequency`, `band_tx`, `band_rx` | `freq`, `freq_rx`, `band`, `band_rx` |
| `my_grid`, `my_rig` | `my_gridsquare`, `my_rig`. Each distinct (call, grid) pair is offered as a station profile |
| `street`, `city`, `zip`, `postal_country` | `address`, `qth` |
| `state`, `ext_val_3` | Province (`state`), see below |
| `azimuth`, `elevation`, `distance` | `ANT_AZ`, `ANT_EL`, `DISTANCE` (in `extra`) |
| `ext_val_1`, `ext_val_2`, `ext_val_4` | Empty in your log; kept in `extra` if present |

`ext_val_3` is province text that MLDX took from callsign lookups. The importer handles it as follows:
- **Province comes from MLDX `state`** when it is a recognised province, otherwise from `ext_val_3`.
- **Values are normalised.** For example `新疆`, `Sichuan`, `Nei Mongol Zizhiqu` and `湘` become `XJ`, `SC`, `NM` and `HN`.
- **Unusable text is set aside.** When the two columns conflict, or the text is not a province (`yi`, `在线`, `邮编`, …), the dry-run report lists it and the raw value is kept in `extra`.

**Upload state from confirmations.** MLDX only writes `Uploaded to LoTW` for uploads it made itself, so QSOs that reached LoTW another way (WSJT-X / JTDX, TQSL directly, another logger) look never uploaded: in one real log, 502 of 1,008 LoTW-confirmed QSOs had no upload record, and 18 QRZ.com-confirmed ones. A service only confirms a QSO you uploaded, so `qso.infer_sent` marks a QSO with `LOTW_QSL_RCVD` Y/V as `lotw_qsl_sent=Y`, and a QRZ.com-confirmed one as `qrzcom_qso_upload_status=Y` (an `M`, edited since the upload, is kept). The sent date stays empty until a LoTW download fills it. The same rule applies to ADIF imports. An eQSL inbox card says nothing about your own upload, so eQSL is not inferred. The import preview counts these QSOs and suggests downloading from LoTW and QRZ.com before the first upload, which marks every QSO those services hold.

The migration runs as a dry run first. It reports counts per mapping and lists any text it didn't recognise, and nothing is written until you confirm. The MLDX row id is kept (`APP_MLDX_PK`), so running it again updates QSOs instead of duplicating them.

Both MacLoggerDX files contain other operators' personal data, so they are git-ignored and never committed.

### 8.2 Common sync design

- **Adapter interface:** each service implements `test_credentials()`, `upload(qsos)` and `download(since)`.
  - Work runs in background workers, with a progress dialog and a row in `sync_log`.
  - Nothing is uploaded automatically unless enabled per service. "Upload on log" is offered for QRZ, eQSL and HRDLog (off by default).
  - **Wait between syncs:** after a download (or an upload) that reached a service, the next one in the same direction waits N minutes (Preferences → Services, whole minutes, default 5, at least 1). A click inside the wait gives a Sync results row saying when it can run; a run skipped before any request (e.g. no password) doesn't start a wait; the times are kept in the log file, so a restart doesn't shorten it. QSOs uploaded when logged are never dropped: inside the wait (or while an upload runs) they wait and go out together when it ends. Lookups, call lists, cty.csv and MASTER.SCP are not paced by it.
  - **Summaries lead with what is new:** "2 new confirmations · 1 already in your log · 6 not in your log". LoTW sends its newest confirmation again (its "since" is inclusive, so nothing recorded in the same second is missed), eQSL starts a day early, QRZ.com sends the whole logbook: those records are only "already in your log". The QRZ.com download date is set once, when a QSO first appears there. A Sync results row with new confirmations has *Select them*.
  - *Sync everything* only downloads (LoTW, eQSL, QRZ.com). Uploads are always started by you: Upload pending (asks first) or Upload selected, because a LoTW or eQSL upload can't be taken back and eQSL sends a card to the other station.
  - **Outcomes** go to one non-modal **Sync results** window (QSL → Show sync results), a row per run: Done / Note / Skipped / Failed / Stopped, the details and a short *ref*; a row that a setting fixes has *Open Preferences*. No message box is opened from a finished background job: three downloads finishing together used to stack three app-modal boxes, where only the top one takes a click and the app looked hung. Upload on log only brings the window up for a failure, without taking the keyboard. Records that don't match open the Unmatched list without blocking either.
  - **Checks before a run:** account fields; for LoTW, TQSL and each profile's station location, which must be one TQSL has (`~/.tqsl/station_data`, `%APPDATA%\TrustedQSL\station_data`, or `TQSLDIR`); for eQSL, each profile's QTH nickname. A QSO whose profile lacks one is skipped with a row naming the profile.
  - **Upload state from downloads:** LoTW's report of accepted QSOs (`qso_qsl=no`) sets `lotw_qsl_sent`, and every QSO found in the QRZ.com logbook gets `qrzcom_qso_upload_status=Y` (unless edited since, `M`). eQSL offers only the inbox and HRDLog no download, so what was uploaded there before can't be learned.
- **Matching downloaded confirmations:**
  - Key: same call + band, with `sat_name` when the QSO was via satellite. An exact mode match is preferred; the same mode group is accepted.
  - Time within ±30 min (LoTW's own window). Among candidates, an exact mode match wins, then the nearest time.
  - Records that don't match go to an **Unmatched** list, where they can be added as new QSOs.
- **Satellite names per service.** NWOlogger stores one name per satellite and translates it on upload and when matching downloads.
  - The table is editable in Prefs.
  - LoTW defaults come from the TQSL satellite list.
  - Example: the ISS is `ARISS` for LoTW (TQSL accepts only that) but `ISS` for eQSL (eQSL accepts only that). A QSO stored as `ARISS` is uploaded to eQSL as `ISS`, and eQSL records that say `ISS` match it.
- **Credentials:** kept only in the OS keychain. Every request uses HTTPS and the User-Agent `NWOlogger/<ver> (<call>)`.
  - **Keychain:** only the platform stores are used (macOS Keychain, Windows Credential Manager, SecretService / KWallet). A plaintext, `fail` or `null` keyring backend is refused, and Preferences says so.
  - **Requests** go through `core/net.py`: HTTPS only, to that service's own hosts (lotw.arrl.org, www.eqsl.cc, logbook.qrz.com, xmldata.qrz.com, www.hamqth.com, robot.hrdlog.net), checked before every redirect hop; no URL user info, at most 5 redirects. The eQSL ADIF link must be on www.eqsl.cc (an `http://` link there is upgraded).
  - **Errors** name the service and HTTP status (e.g. "LoTW: HTTP 503 Service Unavailable (the service is down or busy; try again later)"), never the URL. Service reasons (QRZ `REASON`, HRDLog `<error>`, eQSL `Error` lines) are kept, because they say why a QSO was refused.
  - **Redaction** (`net.redact`) runs on every message before a dialog, the status bar, `sync_log` or a log record. URL queries are removed and `password=…`-style pairs are masked. The text is decoded first (HTML entities twice, then %-escapes), so the secrets of that request become `•••` however a server echoed them (`&#39;`, `&apos;`, `%27`, double encoding); the decoded text is what is shown. Secrets under 4 characters are masked as whole tokens only. `sync_log_add` applies it again. Text redaction cannot catch every transformation (a truncated or base64 echo); the real protection is that errors no longer contain the request.
  - **Diagnostic log** (`nwologger/applog.py`): a rotating file (1 MB × 6) in `~/Library/Logs/NWOlogger/` (macOS), `%LOCALAPPDATA%\NWOlogger\Logs` (Windows) or `~/.local/state/nwologger/` (Linux); `NWOLOGGER_LOG_DIR` overrides it, `--debug` adds detail. It records the start (versions, OS), each sync run with its ref, each request as service, method, host + path, status, size and time (never the query), TQSL's flags, location, exit code and last lines, Qt warnings, uncaught exceptions with tracebacks (GUI and worker threads), and, from a watchdog, every thread's stack once when the window has not responded for 15 s. A shorter freeze of 2 s or more is written when the window responds again, with where the GUI thread was half a second into it. Each Preferences save is one line naming what changed (theme and language with their values, lists as counts, account names and secrets by name only); steps on the GUI thread that take over 0.5 s (Preferences OK, a log table refresh, the entry form's checks) are written with their parts, and each sync summary carries its download and apply times. Log lines are always English, whatever the UI language; launch backups are noted (made, or skipped because the log is unchanged). Every line goes through `net.redact` with every secret and account user name read this session (`applog.remember_secrets`) and the secrets in flight, after formatting, so tracebacks and echoed error pages are masked too. The home folder shows as `~`. Help → Show log folder opens it; About → System names the file.
  - **Logs:** `httpx` and `httpcore` are set to WARNING, so an app-wide DEBUG or INFO setup prints no request URLs or header traces. Every one of their loggers, children included (a parent's filter does not see its children's records), masks URL queries, the `Set-Cookie` / `Cookie` / `Authorization` / `Location` headers of httpcore's trace, and the secrets of the requests in flight (`net.request(secrets=…)` registers them while it runs).
  - **The log file never holds a credential.** This is a rule of the write paths, not something checked afterwards: error text is redacted before `sync_log` stores it, and credential-named ADIF tags are dropped on import (below). v0.1.0 (never released) stored an LoTW / eQSL URL with its password in `sync_log` after an HTTP error; 0.1.1–0.1.3 cleaned such files on open and re-scanned the log and every backup with the saved secrets after each Preferences save. 0.1.4 removed that cleanup: it found nothing in real logs, and scanning every backup froze the window for 20–30 s after each Preferences → OK.
  - **Uploads** send only the standard ADIF 3.1.5 fields (`fields.ADIF_STANDARD`) plus the service's own tags (`APP_EQSL_QTH_NICKNAME` for eQSL). Other programs' `APP_*` and `USERDEF` fields stay in the log and in your ADIF exports.
  - **ADIF records** never carry credentials: tags that look like one (`PASSWORD`, `PSWD`, `PASSCODE`, `API_KEY`, `SECRET`, `TOKEN`, `SESSION`, `AUTH`, `EQSL_USER`, `APP_…_CODE`, `APP_…_KEY`) are dropped on import (the preview lists them) and never exported.
  - **Password in the URL:** QRZ.com XML takes the login and lookups by POST, so the password and session key travel in the encrypted body. LoTW (`lotwreport.adi` answers POST with HTTP 405) and eQSL (`DownloadInBox.cfm` rejects a POST login) need the GET query their APIs document: HTTPS encrypts it, but it reaches the services' own logs. Checked with real accounts on 2026-10-16. HamQTH is not checked yet (no account).

### 8.3 LoTW

- **Download:** `GET https://lotw.arrl.org/lotwuser/lotwreport.adi`
  - Parameters: `login`, `password`, `qso_query=1`, `qso_qsl=yes`, `qso_qslsince=<APP_LoTW_LASTQSL>`, `qso_qsldetail=yes`, `qso_mydetail=yes`, `qso_withown=yes`.
  - The response sets `lotw_qsl_rcvd=Y` and `lotw_qslrdate`, and can optionally fill an empty grid, zone or state from the QSL details.
  - A second pass with `qso_qsl=no` and `qso_qsorxsince` marks QSOs that another program uploaded as `lotw_qsl_sent=Y`.
  - A missing `<EOH>` means the query failed.
- **Upload:** LoTW only accepts logs signed by **TQSL**. NWOlogger exports pending QSOs for each station profile to a temp ADIF and runs `tqsl -x -d -u -a compliant -l "<station location>" file.adi`.
  - It then parses the exit code and stderr, and sets `lotw_qsl_sent=Y` and `lotw_qslsdate`.
  - TQSL can still open its own window in batch mode, e.g. for a passphrase-protected certificate. It runs in its own process group with its output drained while it runs; the progress dialog's *Cancel* stops it (the whole group), as does a 15-minute limit. Quitting NWOlogger during an upload asks first, then stops TQSL; closing the progress dialog alone does not. Background work runs on daemon threads, so quitting never waits for a request or for TQSL.
  - TQSL must be installed with a valid certificate. Its path is auto-detected and can be overridden in Prefs.
  - **Propagation mode:** QSOs via repeater (`RPT`) or internet (`INTERNET`) are not sent to LoTW. Other values TQSL does not accept (`GWAVE`, `LOS`) are left out of the upload record.

### 8.4 eQSL

- **Upload:** `POST https://www.eqsl.cc/qslcard/ImportADIF.cfm` with `EQSL_USER`/`EQSL_PSWD`, in chunks of ≤ 250 records (eQSL advises ≤ 1000).
  - Success is parsed from `Result: x out of y records added` and the per-record warnings. Accepted QSOs get `eqsl_qsl_sent=Y`.
  - The QTH nickname comes from the station profile, and is required: eQSL needs it when an account has more than one QTH (DownloadInBox: "if multiple accounts with same callsign"), and a missing one used to end in an unreadable error.
- **Download:** `GET DownloadInBox.cfm` with `UserName`, `Password`, `RcvdSince`, `QTHNickname`. `RcvdSince` is a date, so the next download starts a day before the last one; cards already applied just match again.
  - NWOlogger finds the generated `.adi` link in the returned HTML and fetches it straight away, since the file expires after a few hours.
  - Without a link, the page's visible text (scripts, styles and head left out, `services.page_text`) is written to the log, and its `Error…` line becomes the message. Before v0.1.2 the message was the first 200 characters of the page with only the tags removed, which was eQSL's JavaScript.
  - Matched cards get `eqsl_qsl_rcvd=Y`, `eqsl_qslrdate` and `eqsl_ag`.

### 8.5 QRZ.com Logbook and HRDLog

- **QRZ upload:** `POST https://logbook.qrz.com/api` with `KEY`, `ACTION=INSERT` and one ADIF record per call.
  - Modified QSOs (`M`) are re-sent with `OPTION=REPLACE`.
  - The returned `LOGID` is stored, and uploads are rate-limited.
  - Each QRZ logbook has its own API key, so the key is chosen per station callsign.
- **QRZ download:** `ACTION=FETCH`, `OPTION=MAX:250,AFTERLOGID:<n>,TYPE:ADIF`, paging until fewer than 250 records come back. Always the whole logbook: QRZ.com answers every FETCH with `MODSINCE` with `RESULT=FAIL COUNT=0` (checked with a real logbook on 2026-10-16), which looked like "nothing new".
  - Each record sets `qrzcom_qso_download_status=Y`. When `APP_QRZLOG_STATUS=C`, it also sets `qrz_qsl_rcvd=Y` and `qrz_qslrdate`.
- **HRDLog upload:** `POST https://robot.hrdlog.net/NewEntry.aspx` with `Callsign`, `Code` (upload code), `App`, `ADIFData`.
  - Edits use `Cmd=UPDATE` with an `ADIFKey`.
  - Success sets `hrdlog_qso_upload_status=Y` and its date.
- **HRDLog download:** HRDLog.net has no documented download API, and a 2015 report says the site has no ADIF export.
  - With your HRDLog username and password, NWOlogger could log in to the website and fetch an export, if the logged-in "ADIF utilities" page offers one. This will be checked with your account during M6.
  - If it works, it is best-effort and can break whenever the site changes. Otherwise HRDLog stays upload-only.
  - HRDLog returns no confirmations either way, so a download would only restore your own log.

## 9. Callsign lookup

The lookup runs when the call field loses focus, after a short pause while typing, or when LookUp is pressed. Steps, in order:

1. **`cty.csv`** (country-files.com, bundled, auto-update weekly) gives country, **DXCC entity number**, CQ/ITU zones, continent and lat/lon instantly and offline.
   - Exact-call overrides (`=CALL`) and longest-prefix matching are supported.
   - Portable forms (`/P`, `/M`, `/MM`, `/AM`, `/QRP`, `/<digit>`, `PREFIX/CALL`) are handled.
2. **Cache** (`lookup_cache`, TTL 30 days by default).
3. **QRZ.com XML** (`xmldata.qrz.com/xml/current/`, session key) gives name, address, QTH, state, county, grid, email, QSL via and LoTW/eQSL flags. Full data needs an XML subscription.
4. **HamQTH** fallback: `xml.php` session, valid 1 h. Its API v2 (bearer token) will be checked during implementation.

**Look up and fill** (Edit menu, for the selected QSOs) fills only empty fields (name, QTH, address, state, county, grid, email, web, QSL via, IOTA). Calls in the cache are filled at once; the others are looked up one at a time, at most one a second, in a window with progress (count, current call, found / not found / failed) and *Stop*. Stop finishes the request in flight and fills from everything found so far; every found call is cached at once, so *Look up the other N* only asks for the rest. The same error three times in a row (a wrong password, an expired subscription) ends the run. A `lookup` backup is saved before the fill; the log file gets one line with the counts and the time taken.

**Similar calls** (§6) use your log, the lookup cache and MASTER.SCP. The XML APIs only look up exact calls, so when a call comes back "not found", up to three MASTER.SCP calls one character away (a changed character first) are looked up on QRZ.com / HamQTH. Those found are cached and listed as known; each candidate is asked once per run. This uses lookup quota, and needs MASTER.SCP (File → Update MASTER.SCP).

Lookups fill **empty fields only** and never overwrite what you typed. They run in a background thread, and a result that arrives after the call has changed is thrown away. Fields filled by lookup are tinted until you edit them, so you can tell them from typed ones.

**Province for Chinese calls** (DXCC 318):
1. Use the lookup's state/province, normalised to the ISO 3166-2:CN code (from English, pinyin or Chinese names and abbreviations).
2. If there is none, guess from the call district and the first suffix letter, using the standard allocation in the table below.
   - Guessed values are marked as guesses, because stations that move keep their call.
   - Suffix letters Y–Z are not guessed: your log shows them used in many provinces.

| District | Suffix A–H | I–P | Q–X |
|---|---|---|---|
| 1 | BJ | BJ | BJ |
| 2 | HL | JL | LN |
| 4 | SH | SD | JS |
| 5 | ZJ | JX | FJ |
| 6 | AH | HA | HB |
| 7 | HN | GD | GX |

| District | Suffix A–F | G–L | M–R | S–X |
|---|---|---|---|---|
| 3 | TJ | NM | HE | SX |
| 8 | SC | CQ | GZ | YN |
| 9 | SN | GS | NX | QH |
| 0 | XJ | XZ | — | — |

Checked against your MacLoggerDX log: every district matches, apart from stations that moved.

## 10. Contest mode (N1MM style, with live scoring)

### Setup

The contest is **chosen from a dropdown**, which lists the built-in definitions and your user-defined ones together. Setup also asks for:
- start date and time
- Cabrillo categories (operator, band, power, mode, assisted, transmitter, overlay)
- station profile
- sent exchange

This creates a `contest_session`. Every QSO logged in it carries `contest_id` and the session id.

Built-in definitions, first set:
- CQ WW DX (CW/SSB/RTTY)
- CQ WPX (CW/SSB/RTTY)
- IARU HF
- ARRL DX (CW/SSB)
- ARRL 10 m
- All Asian DX
- CQ WW VHF
- generic "DX serial" and "DX zone" templates

User-defined contests appear in the same dropdown.

### Entry line

`Call | RST S | RST R | <exchange fields from the definition> | band / mode / MHz`

N1MM conventions:
- Typing `14025` in the Call field sets the frequency, and `CW` / `SSB` sets the mode.
- Space jumps to the next useful field, skipping the RSTs.
- Enter on the Call field moves to the exchange; Enter on the exchange logs the QSO (ESM without a keyer).
- Esc or Alt+W wipes the line. Alt+E edits the last QSO.

### Panes

Panes are `QDockWidget`s, so each can be docked or floated N1MM-style:

| Pane | Shows |
|---|---|
| Contest Log | QSOs in this session |
| Check Partial | MASTER.SCP + your own log, matched as you type |
| N+1 | Calls that differ by one character |
| Score Summary | Per band: QSOs / points / mults, total score |
| Multipliers | Needed / worked per band |
| Rate | Last 10 and last 100 QSOs, hourly |

### Rules

- **Serial numbers** auto-increment per session. They are reserved when a QSO is logged; gaps are allowed.
- **Dupe check** is live and uses the definition's rule (§5.3). A dupe disables Log; a suspect only warns.
- **Exchange validation** uses a regex or list per field, for example zones 1–40, numeric serials, or a state/province list.
  - The expected exchange is pre-filled from `cty.csv` (CQ/ITU zone) and from earlier QSOs in your log.

### Scoring engine

Each contest is a declarative JSON definition with:
- exchange fields
- allowed bands and modes
- dupe rule
- QSO points table (same country / same continent / other continent, by mode or band)
- multiplier types (DXCC, CQ zone, ITU zone, WPX prefix, list, exchange value, grid), each counted per band, per band-mode or per contest
- score formula

Contests with quirks add a small Python rule module. Totals are recomputed incrementally as QSOs are logged, and in full after any edit.

User-defined contests use the same JSON, edited through a form. This is the equivalent of N1MM's UDC.

### Output

Cabrillo 3.0 export, with the header filled from the session, and ADIF export of the session (`CONTEST_ID`, `STX`/`SRX`).

## 11. Preferences

| Section | Settings |
|---|---|
| General | Language (English / 简体中文, switched without a restart), date/time display, theme (follows the OS, previewed at once) |
| Station profiles | One or more profiles; each is a personal or a club station (§6.3); double-click makes one the default. The TQSL station location is a dropdown of the locations TQSL has |
| Devices | Your rigs and antennas (add, rename, reorder, or take the ones your log uses), and setups: a name with a rig, an antenna and a power (§6) |
| Modes | Which modes the entry dropdown lists, and their order |
| Satellites | Which satellites the entry dropdown lists ("In form"), satellites you add, and the name sent to each service |
| Logging | Sticky fields, RX = TX default, default RST by mode group, dupe block window (30 s), dupe warning window (24 h), allow-dupes switch, auto-lookup |
| Lookup | QRZ XML user/password, HamQTH user/password, cache TTL, `cty.csv` update |
| Services | LoTW user/password + TQSL path + station location per profile; eQSL user/password/QTH nickname; QRZ API key per logbook; HRDLog call + upload code (+ website login if download works); upload-on-log switches; satellite name map per service |
| Secrets (Lookup, Services) | Saved passwords, codes and keys are never loaded into the form: a blank field keeps the saved one, *Forget* removes it on Save. QRZ API keys are typed in a password field and shown as dots. Renaming an account deletes the old account's entry (moved instead when only the case changed) |
| Database | Path, backup count |

## 12. Milestones

| # | Milestone | Done when |
|---|---|---|
| M0 | Scaffold | `uv run nwologger` opens an empty window showing its version (title, toolbar, About, `--version`); CI runs ruff + pytest on macOS and Windows |
| M1 | Core data | Schema + migrations, field registry, modes/bands, ADIF read/write, with round-trip tests |
| M2 | Log view | Lazy table, sort by any column, column chooser, quick filter + advanced search (wildcards, vague mode), import with dupe marking, MacLoggerDX migration, export all/selected/filtered |
| M3 | DX entry | Entry panel, `cty.csv` resolver, worked-before, dupe rules (30 s block, warning window), sticky fields, satellite list, edit/delete/undo |
| M4 | Bulk edit + paper QSL | Bulk edit dialog with preview + undo; Paper QSL tab |
| M5 | Lookup | QRZ XML + HamQTH + cache, fill-empty-only |
| M6 | Sync | Framework + matcher + satellite name map, then QRZ → eQSL → LoTW (TQSL) → HRDLog upload (and download, if feasible); Unmatched list; sync history |
| M7 | Contest | Setup dropdown, entry line, serials, dupes, Check Partial / N+1, scoring engine + built-ins, score/mults/rate panes, Cabrillo |
| M8 | Release | zh_CN translation complete, PyInstaller builds (.dmg, Windows installer), GitHub Actions release workflow |
| M9 | Integrations (later) | WSJT-X / JTDX UDP auto-logging (port 2237, "QSO Logged" / "Logged ADIF"); N1MM Logger+ UDP contact broadcasts (`contactinfo` / `contactreplace` / `contactdelete`, port 12060) |

Every milestone ships tests for its core logic. UI milestones are checked by running the app.

## 13. Decisions and open questions

### Decided

| Topic | Decision | Where |
|---|---|---|
| HRDLog download | Try a logged-in website download during M6; otherwise upload only | §8.5 |
| Dupes in DX mode | Strict: the same mode within 30 s blocks logging; beyond 30 s, or a related mode, only warns | §5.2 |
| Dupes on import | Import everything and mark every suspicious dupe | §5.2, §8.1 |
| Mode search | Vague by default, *Exact* optional | §7 |
| Built-in contests | The first set listed in §10 is enough for now | §10 |
| Name | First and Last fields, as in MacLoggerDX; joined into ADIF `NAME` on export, split on import | §4.1, §8.1 |
| Satellite names | Per-service name table, applied on upload and on download matching. ISS: `ARISS` for LoTW/TQSL, `ISS` for eQSL | §8.2 |
| UI references | No reference screenshots. NWOlogger gets its own modern design | §6 |
| MacLoggerDX data | Migrated from the database file, with upload history; a bare `Y` in the sent column is ignored; both MLDX files are git-ignored | §8.1.1 |
| Location data | Country, Province, Address, CQ zone, ITU zone are enough; MLDX `ext_val_3` is lookup province text, normalised into Province | §4.1, §6, §8.1.1, §9 |
| WSJT-X / JTDX / N1MM UDP | Later milestone | M9 |
| Time Off | Follows Time On + 1 min when Time On is changed; earlier than Time On is refused | §6 |
| Entry dropdowns | Mode, satellite and propagation are dropdowns; propagation uses TQSL's list; satellites can be added in Prefs | §6 |
| Dupe display | A side-by-side compare / review window | §6.2 |
| Operators | Personal or club station; an Operator field in the form (no pop-up); club must fill it, checked again on a new UTC day or after a 3-hour break | §6.3 |
| Dropdown lists | Modes and satellites listed in the entry form are chosen in Preferences; *Other…* reaches the rest | §6, §11 |
| Satellite vs propagation | A satellite with non-SAT propagation is kept, with a warning and a confirmation | §6 |
| Dupe groups | The compare window shows every QSO of a dupe group, is editable, and merges them field by field (also any 2–5 selected QSOs with the same call); *Not dupes: all…* asks twice | §6.2 |
| Logo and About | The BY1QH logo is the app icon; About lists authors Han Zhang (BI1NWO) and Claude, copyright Han Zhang, credit BY1QH | §14 |
| License | MIT | `LICENSE` |
| Secrets in errors | Errors carry the HTTP status, never the URL; every message is redacted before it is shown, stored or logged; provider reasons are kept | §8.2 |
| Account renamed | Its old keychain entry is deleted (moved when only the case changed) | §11 |
| v0.1.0 leaked passwords | 0.1.1–0.1.3 cleaned them; 0.1.4 removed the cleanup, since nothing writes a credential into the log | §8.2 |
| Profile callsign changed | Once no profile uses the old call, its QRZ.com Logbook key moves to the new call or is removed, as you choose | §11 |
| Similar calls | Log, then QRZ.com / HamQTH cache, then MASTER.SCP; a "not found" call checks up to three one-character-away MASTER.SCP calls online | §6, §9 |
| Clear | Empties the whole form and ignores *Keep*; Power shows the profile default and logs it when left empty | §6 |
| Time On *Now* | Back to the default: both times now, Time Off follows the clock | §6 |
| Value check timing | With the saved secrets: once after an update, after each Preferences save, and from the menu; not at every start | §8.2 |
| What uploads send | Standard ADIF fields plus the service's own tags; no other program's `APP_*` / `USERDEF` fields; no field holding a saved secret | §8.2 |
| Sync everything | Downloads only; uploads are always started by you. QSOs not yet on all four services are grey | §6, §8.2 |

### Open

None right now. New questions will be added here as they come up during implementation.

## 14. Implementation notes (v0.1.0)

Where the code differs from, or adds to, the plan above:

- **Translations** use a JSON catalog (`resources/translations/zh_CN.json`) read by `nwologger.i18n.tr()`, instead of Qt Linguist `.ts/.qm` files.
  - There is no build step, and the catalog is easy to edit.
  - Qt's own dialog strings still come from Qt's `qtbase` translations.
  - Setting `NWOLOGGER_I18N_RECORD=<file>` while running the UI lists every string it asks for.
- **Confirmed by QRZ** is stored in `qrz_qsl_rcvd` / `qrz_qslrdate` and exported as QRZ's own `APP_QRZLOG_STATUS` / `APP_QRZLOG_QSLDATE`.
- **Dupe marks** use `dupe_flag` values `DUPE`, `SUSPECT` and `OK`. `OK` means you said "not a dupe", and later scans leave it alone.
- **Satellite frequencies** come from your own last QSO on that satellite first, then from a few bundled defaults (`sats.json`).
- **Contest definitions** are JSON files: built-ins in `core/contest/defs/`, yours in `Documents/NWOlogger/contests/`.
  - A rules list gives the QSO points; the first matching rule wins.
  - Multipliers are counted per band, band + mode, mode or contest.
  - There are no Python rule modules yet: every built-in contest fits the JSON rules.
- **Contest exchange storage:** RST goes to `rst_sent` / `rst_rcvd` and serials to `stx` / `srx`. Other exchange fields, space-separated, go to `stx_string` / `srx_string`, and are also copied to their ADIF field where one exists (zone → `cqz` / `ituz`, grid → `gridsquare`, state → `state`).
- **MASTER.SCP** is downloaded on request into the log's folder. Check Partial also uses every call in your log.
- **The country file** is checked weekly and downloaded to `Documents/NWOlogger/cty.csv`. The bundled copy is the fallback.
- **The MacLoggerDX importer** also maps the remaining MLDX columns: `azimuth`, `elevation`, `distance`, latitude/longitude and the `ext_val_*` fields go to `extra`. MLDX grid-line pairs (`OM32,OM33`) go to `*_VUCC_GRIDS`.
- **Screenshots:** `tools/screenshots.py` renders `docs/screenshots/` off-screen from made-up QSOs. Test and screenshot runs set `NWOLOGGER_SETTINGS=<file.ini>`, so they never touch your real preferences.
- **Tests and the outside world:** `tests/conftest.py` gives every test an in-memory keychain and a network transport that fails the test on any request. Service tests answer real `httpx` requests through `mock_net`; `tests/test_security.py` checks that fake secrets never appear in errors, dialogs, `sync_log`, the WAL, backups or HTTPX logs.
- **Branding:** the BY1QH logo (`resources/brand/by1qh.png`) is the window and app icon; PyInstaller converts it to `.icns` / `.ico` with Pillow at build time. Help → About (or the version button in the toolbar) shows the authors (Han Zhang BI1NWO, Claude Opus 5.5 Extra High), copyright (Han Zhang), the BY1QH credit, data and software credits, and system info that can be copied into bug reports.
- **Tester sample:** `tests/fixtures/sample.adi` is a synthetic 17-QSO log (satellite pass, HF, one strict and one suspicious dupe) for trying the app; a test keeps it importable.
- **TQSL data:** the propagation list and five extra satellites come from TQSL's current configuration (`config.tq6` v11.34, lotw.arrl.org). RS95S is on LoTW's FAQ list but not yet in that configuration; both sources are kept.
