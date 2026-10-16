# QHlogger — Design Plan

Status: **v0.1.0 implemented** (2026-10-16). Milestones M0–M8 are built; see §14 for where the code differs from
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

- **One source of truth:** the `version` in `pyproject.toml`, read at runtime through `importlib.metadata` and exposed as `qhlogger.__version__`. Versions follow SemVer and start at `0.1.0`. While in `0.x`, a new milestone bumps the minor number and a fix bumps the patch number.
- **Shown in the app** in four places:
  - the window title (`QHlogger v0.1.0 — qhlog.sqlite`)
  - the right end of the toolbar, as in MacLoggerDX
  - Help → About (version, Qt/PySide6 version, DB schema version, data-file path)
  - `qhlogger --version`
- **Sent outward:** the same string goes into ADIF `PROGRAMVERSION`, the service User-Agent (`QHlogger/0.1.0 (BI1NWO)`), and the Cabrillo `CREATED-BY` line.
- **Releases:** a git tag `v0.1.0` triggers the release build. CI fails if the tag and `pyproject.toml` disagree.

Architecture rule: `qhlogger/core/` never imports Qt. All logic lives there: DB, ADIF, search, dupes, DXCC, contest scoring and sync.
The UI layer is thin, so the core is unit-testable and could also back a CLI.

## 3. Repository layout (planned)

```
qhlogger/
  __main__.py              # `python -m qhlogger`
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

Default file: `~/Documents/QHlogger/qhlog.sqlite`. It can be changed in Prefs, and several log files can be opened, one at a time.

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
| `sync_state` | Per-service cursors (LoTW last QSL / last QSO-rx, eQSL RcvdSince, QRZ MODSINCE / AFTERLOGID) |
| `sync_log` | Sync history: service, direction, time, counts, errors |
| `undo_log` | Before-images for bulk edit, delete and import, so they can be undone |

### 4.3 Safety

- Automatic backup on open (rotating, keeps N copies), plus a backup before every import and bulk edit.
- All writes happen in transactions.
- Schema migrations are versioned.
- Undo is available for bulk edit, delete and import.

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

The layout follows the MacLoggerDX workflow, minus the rig panel: entry panel on top, log below. The look is QHlogger's own modern design, not a copy of MacLoggerDX, BBLogger or N1MM.

**Visual style**

- **Qt Widgets, not QML.** `QTableView` and dense forms handle 100k-row logs better. The modern look comes from the Fusion style plus an app stylesheet built on design tokens (colours, spacing, radii).
- **Themes:** light and dark, following the OS by default.
- **Colour:** one accent colour. Status colours appear only where they carry meaning: confirmed green, suspect amber, dupe red.
- **Type:** tabular (monospaced) figures for callsigns, frequencies and times, so columns line up.
- **Icons:** SVG line icons from a permissively licensed set (e.g. Lucide, ISC).
- **Layout:** entry fields grouped into labelled sections (Contact, Station, Location, References, QSL) instead of one dense grid. Validation is inline, next to the field.
- **Keyboard-first:** every action has a shortcut, plus a command palette (⌘K / Ctrl+K).

```
┌ Toolbar: [Log ▾ / Contest]  [Search]  [Sync ▾]  [Import]  [Export ▾]  [Prefs]  UTC 04:20:02  Local 12:20  v0.1.0 ┐
├ Entry panel ── [ DX | Contest ] ─────────────────────────────────────────────────────────────────────────────┤
│ Call [______]  First  Last    │ Time On  2026-10-16 04:19:35   Time Off                                      │
│ Address / City                │ MHz  [145.920] 2M   RX MHz [436.950] 70CM   Mode [FM ]                       │
│ Province / Country            │ Sat [SO-50 ▾]  Sat mode [ ]  Prop [SAT]  Power [25]  RST S [59]  RST R [59]  │
│ Email / Web                   │ Grid  ITU  CQ  DXCC  IOTA  SOTA  POTA  WWFF  SKCC  QSL Via                    │
│ Comment / Notes               │ ┌ Worked before: band × mode-group matrix, last QSOs, NEW DXCC/BAND/MODE ┐    │
│ [LookUp] [Previous] [TimeOn] [TimeOff] [Log QSO ⏎] [Clear ⎋]  sticky: ☑Freq ☑Mode ☑Sat ☑Pwr                  │
├ Tabs: [ Log | Paper QSL | Sync history ] ────────────────────────────────────────────────────────────────────┤
│ 1434 QSOs · 37 shown · /Users/.../qhlog.sqlite        DXCC 75 wkd / 68 cfm                                   │
│ [Call ?*] [Band ▾] [Mode ▾] [Sat ▾] [Country] [Preset ▾]  [More filters…]  [Clear]                           │
│ Call │ Time On ▾ │ Time Off │ MHz │ Band │ RX MHz │ Band RX │ Mode │ Sat │ Pwr │ RST S │ RST R │ LoTW │ …     │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Entry panel (DX).** The field set follows MacLoggerDX, without rig info. Name is split into First and Last, as in MLDX.
  - **Location** is kept to what is needed: Country, Province, Address (street, city → ADIF `ADDRESS` / `QTH`), CQ zone, ITU zone. County, lat/lon and other location fields are not on the form; they are kept if an import brings them.
  - A summary line under the call shows the last confirmation (e.g. "Confirmed 2025-08-18 on 70CM FM"), plus bearing and distance computed from the two grids.
  - **Times:**
    - Time On stamps itself when you start typing a call, and Time Off follows the clock until you log.
    - If you change Time On (by hand or with *Now*), Time Off becomes Time On + 1 min, and keeps following Time On.
    - When you edit an existing QSO, changing Time On keeps its original duration.
    - Changing only Time Off never touches Time On.
    - A Time Off earlier than Time On turns red, and logging is refused with a warning.
  - **Frequencies:** the field is labelled **TX MHz**; RX MHz sits below it.
    - An **= TX** toggle keeps RX equal to TX, for simplex.
    - Picking a satellite turns it off, because satellites are split; clearing the satellite turns it back on.
  - **Mode** is a plain dropdown of *your* modes (Preferences → Modes: tick and drag to order; default = the common ones), then *Other mode…*, which searches every ADIF mode and submode and can add the choice to your list.
  - **Propagation** is a dropdown of TQSL's 16 propagation modes (AS, AUE, AUR, BS, ECH, EME, ES, F2, FAI, ION, IRL, MS, RS, SAT, TEP, TR).
    - It also offers the ADIF-only GWAVE, INTERNET, LOS and RPT, marked "not for LoTW".
    - The ADIF `SAT_MODE` field is no longer on the form; it is kept when an import brings it.
  - **Propagation comes before Satellite** in the form.
  - **Satellite** is a dropdown of *your* satellites (Preferences → Satellites, "In form" column; until you choose, the ones you worked plus popular ones), then *Other satellite…*, which searches the full LoTW / TQSL list, your added satellites and names found only in your log.
    - **Satellite without SAT propagation:** the satellite name is kept. An amber chip says "Satellite is set, but propagation is not SAT" and logging asks once. The reverse ("Propagation is SAT, but no satellite") is treated the same way. A satellite with an *empty* propagation is stored as SAT, as ADIF expects.
    - Picking one sets propagation SAT and fills the frequencies and mode from your last QSO on it (else its defaults).
    - Per-service names are handled on upload (§8.2).
  - **Operator** is a field in the Contact section (and in the contest bar). See §6.3.
  - *Sticky* fields carry over to the next QSO, which matters for satellite passes.
  - *Previous* recalls the last QSO's fields.
  - Enter logs the QSO, Esc clears the form.
  - Double-clicking a row loads that QSO into the panel for editing (Save / Cancel).
- **Log table.** A `QAbstractTableModel` over SQL with lazy loading (`fetchMore`), so 100k+ QSOs stay fast.
  - Clicking any header sorts with SQL `ORDER BY`. `time_on` breaks ties.
  - Columns can be shown, hidden and reordered from the header context menu.
  - Confirmation columns: LoTW ↑/↓, eQSL ↑/↓, QRZ ↑/↓, HRDLog ↑, Paper sent/rcvd. Each shows a date; an empty cell means not done.
  - Row colour: confirmed (any service or paper), unconfirmed, `SUSPECT` (amber), `DUPE` (red).
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

Bulk edit follows the BBLogger idea of editing many selected QSOs at once, with QHlogger's own UI. It is a dialog that applies to the selected QSOs:

- Each field row has a ☐ apply box and an operation:
  - set to value
  - clear
  - find & replace
  - fill from lookup
- A preview lists the count and an old → new diff of the first rows.
- Apply runs in one transaction and is recorded in `undo_log`, so it can be undone.
- After applying, the affected QSOs are flagged as modified for QRZ and HRDLog re-upload (§4.1).

### 6.2 Dupe compare and review

A QSO can collide with more than one other QSO, so the window shows a **group**.
- **What a group is:** every QSO linked by possible-dupe relations (same call and band, related mode, within the warning window; found transitively, at most 12), oldest first, one column each.
- **Layout:**
  - The column header shows the time and the mark (Dupe / Suspect).
  - Fields that differ are highlighted, and the fields that decide a dupe (call, band, mode, propagation, satellite, time) are bold.
  - The top lists each QSO against the earlier ones, e.g. "#2 dupe of #1: same mode and submode, 15 s apart; #3 possible dupe of #2: related modes, 12 min apart".
- **Editing in the window:** double-click a cell and type, then *Save edits*. All edits are saved as one undo step and the group's marks are recomputed. A Time Off earlier than Time On is refused.
- **Actions** (click a column header to select a QSO):
  - *Not dupes*: marks every QSO in the group `OK`, so later scans leave them alone.
  - *Keep #n, merge others*: copies the fields and confirmations #n is missing from the others, then deletes the others.
  - *Delete selected*.
  - *Open in entry form*.
  - Every action can be undone.
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
- **Writer:** UTF-8 with header `ADIF_VER`, `PROGRAMID=QHlogger`, `PROGRAMVERSION`, `CREATED_TIMESTAMP`.
  - Exports all / selected / filtered.
  - `NAME` is written as First + space + Last.
  - Optional profiles strip private fields (`notes`, `email`) for service uploads.

### 8.1.1 MacLoggerDX migration

Import straight from the MacLoggerDX database (`MacLoggerDX.sql`, table `qso_table_v008`), not from its ADIF export. The ADIF export sets `QSL_SENT=Y` on every QSO and leaves out all upload history. MLDX keeps that history as comma-separated text in two columns, which the importer splits into separate fields:

| MLDX column | Text | QHlogger fields |
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

| MLDX column | QHlogger |
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

The migration runs as a dry run first. It reports counts per mapping and lists any text it didn't recognise, and nothing is written until you confirm. The MLDX row id is kept (`APP_MLDX_PK`), so running it again updates QSOs instead of duplicating them.

Both MacLoggerDX files contain other operators' personal data, so they are git-ignored and never committed.

### 8.2 Common sync design

- **Adapter interface:** each service implements `test_credentials()`, `upload(qsos)` and `download(since)`.
  - Work runs in background workers, with a progress dialog and a row in `sync_log`.
  - Nothing is uploaded automatically unless enabled per service. "Upload on log" is offered for QRZ, eQSL and HRDLog.
- **Matching downloaded confirmations:**
  - Key: same call + band, with `sat_name` when the QSO was via satellite. An exact mode match is preferred; the same mode group is accepted.
  - Time within ±30 min (LoTW's own window). Among candidates, an exact mode match wins, then the nearest time.
  - Records that don't match go to an **Unmatched** list, where they can be added as new QSOs.
- **Satellite names per service.** QHlogger stores one name per satellite and translates it on upload and when matching downloads.
  - The table is editable in Prefs.
  - LoTW defaults come from the TQSL satellite list.
  - Example: the ISS is `ARISS` for LoTW (TQSL accepts only that) but `ISS` for eQSL (eQSL accepts only that). A QSO stored as `ARISS` is uploaded to eQSL as `ISS`, and eQSL records that say `ISS` match it.
- **Credentials:** kept only in the OS keychain. Every request uses HTTPS and the User-Agent `QHlogger/<ver> (<call>)`.

### 8.3 LoTW

- **Download:** `GET https://lotw.arrl.org/lotwuser/lotwreport.adi`
  - Parameters: `login`, `password`, `qso_query=1`, `qso_qsl=yes`, `qso_qslsince=<APP_LoTW_LASTQSL>`, `qso_qsldetail=yes`, `qso_mydetail=yes`, `qso_withown=yes`.
  - The response sets `lotw_qsl_rcvd=Y` and `lotw_qslrdate`, and can optionally fill an empty grid, zone or state from the QSL details.
  - A second pass with `qso_qsl=no` and `qso_qsorxsince` marks QSOs that another program uploaded as `lotw_qsl_sent=Y`.
  - A missing `<EOH>` means the query failed.
- **Upload:** LoTW only accepts logs signed by **TQSL**. QHlogger exports pending QSOs for each station profile to a temp ADIF and runs `tqsl -x -d -u -a compliant -l "<station location>" file.adi`.
  - It then parses the exit code and stderr, and sets `lotw_qsl_sent=Y` and `lotw_qslsdate`.
  - TQSL must be installed with a valid certificate. Its path is auto-detected and can be overridden in Prefs.
  - **Propagation mode:** QSOs via repeater (`RPT`) or internet (`INTERNET`) are not sent to LoTW. Other values TQSL does not accept (`GWAVE`, `LOS`) are left out of the upload record.

### 8.4 eQSL

- **Upload:** `POST https://www.eqsl.cc/qslcard/ImportADIF.cfm` with `EQSL_USER`/`EQSL_PSWD`, in chunks of ≤ 250 records (eQSL advises ≤ 1000).
  - Success is parsed from `Result: x out of y records added` and the per-record warnings. Accepted QSOs get `eqsl_qsl_sent=Y`.
  - The QTH nickname comes from the station profile.
- **Download:** `GET DownloadInBox.cfm` with `UserName`, `Password`, `RcvdSince`, `QTHNickname`.
  - QHlogger finds the generated `.adi` link in the returned HTML and fetches it straight away, since the file expires after a few hours.
  - Matched cards get `eqsl_qsl_rcvd=Y`, `eqsl_qslrdate` and `eqsl_ag`.

### 8.5 QRZ.com Logbook and HRDLog

- **QRZ upload:** `POST https://logbook.qrz.com/api` with `KEY`, `ACTION=INSERT` and one ADIF record per call.
  - Modified QSOs (`M`) are re-sent with `OPTION=REPLACE`.
  - The returned `LOGID` is stored, and uploads are rate-limited.
  - Each QRZ logbook has its own API key, so the key is chosen per station callsign.
- **QRZ download:** `ACTION=FETCH`, `OPTION=MODSINCE:<last>,MAX:250,AFTERLOGID:<n>`, paging until fewer than 250 records come back.
  - Each record sets `qrzcom_qso_download_status=Y`. When `APP_QRZLOG_STATUS=C`, it also sets `qrz_qsl_rcvd=Y` and `qrz_qslrdate`.
- **HRDLog upload:** `POST https://robot.hrdlog.net/NewEntry.aspx` with `Callsign`, `Code` (upload code), `App`, `ADIFData`.
  - Edits use `Cmd=UPDATE` with an `ADIFKey`.
  - Success sets `hrdlog_qso_upload_status=Y` and its date.
- **HRDLog download:** HRDLog.net has no documented download API, and a 2015 report says the site has no ADIF export.
  - With your HRDLog username and password, QHlogger could log in to the website and fetch an export, if the logged-in "ADIF utilities" page offers one. This will be checked with your account during M6.
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
- Esc or Alt+W wipes the line. Ctrl+Z undoes the last QSO. Alt+E edits the last QSO.

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
| General | Language (English / 简体中文), date/time display, theme follows the OS |
| Station profiles | One or more profiles; each is a personal or a club station (§6.3); double-click makes one the default |
| Modes | Which modes the entry dropdown lists, and their order |
| Satellites | Which satellites the entry dropdown lists ("In form"), satellites you add, and the name sent to each service |
| Logging | Sticky fields, RX = TX default, default RST by mode group, dupe block window (30 s), dupe warning window (24 h), allow-dupes switch, auto-lookup |
| Lookup | QRZ XML user/password, HamQTH user/password, cache TTL, `cty.csv` update |
| Services | LoTW user/password + TQSL path + station location per profile; eQSL user/password/QTH nickname; QRZ API key per logbook; HRDLog call + upload code (+ website login if download works); upload-on-log switches; satellite name map per service |
| Database | Path, backup count |

## 12. Milestones

| # | Milestone | Done when |
|---|---|---|
| M0 | Scaffold | `uv run qhlogger` opens an empty window showing its version (title, toolbar, About, `--version`); CI runs ruff + pytest on macOS and Windows |
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
| UI references | No reference screenshots. QHlogger gets its own modern design | §6 |
| MacLoggerDX data | Migrated from the database file, with upload history; a bare `Y` in the sent column is ignored; both MLDX files are git-ignored | §8.1.1 |
| Location data | Country, Province, Address, CQ zone, ITU zone are enough; MLDX `ext_val_3` is lookup province text, normalised into Province | §4.1, §6, §8.1.1, §9 |
| WSJT-X / JTDX / N1MM UDP | Later milestone | M9 |
| Time Off | Follows Time On + 1 min when Time On is changed; earlier than Time On is refused | §6 |
| Entry dropdowns | Mode, satellite and propagation are dropdowns; propagation uses TQSL's list; satellites can be added in Prefs | §6 |
| Dupe display | A side-by-side compare / review window | §6.2 |
| Operators | Personal or club station; an Operator field in the form (no pop-up); club must fill it, checked again on a new UTC day or after a 3-hour break | §6.3 |
| Dropdown lists | Modes and satellites listed in the entry form are chosen in Preferences; *Other…* reaches the rest | §6, §11 |
| Satellite vs propagation | A satellite with non-SAT propagation is kept, with a warning and a confirmation | §6 |
| Dupe groups | The compare window shows every QSO of a dupe group, is editable, and can keep one and merge the others | §6.2 |
| Logo and About | The BY1QH logo is the app icon; About lists authors Han Zhang (BI1NWO) and Claude, copyright Han Zhang, credit BY1QH | §14 |
| License | MIT | `LICENSE` |

### Open

None right now. New questions will be added here as they come up during implementation.

## 14. Implementation notes (v0.1.0)

Where the code differs from, or adds to, the plan above:

- **Translations** use a JSON catalog (`resources/translations/zh_CN.json`) read by `qhlogger.i18n.tr()`, instead of Qt Linguist `.ts/.qm` files.
  - There is no build step, and the catalog is easy to edit.
  - Qt's own dialog strings still come from Qt's `qtbase` translations.
  - Setting `QHLOGGER_I18N_RECORD=<file>` while running the UI lists every string it asks for.
- **Confirmed by QRZ** is stored in `qrz_qsl_rcvd` / `qrz_qslrdate` and exported as QRZ's own `APP_QRZLOG_STATUS` / `APP_QRZLOG_QSLDATE`.
- **Dupe marks** use `dupe_flag` values `DUPE`, `SUSPECT` and `OK`. `OK` means you said "not a dupe", and later scans leave it alone.
- **Satellite frequencies** come from your own last QSO on that satellite first, then from a few bundled defaults (`sats.json`).
- **Contest definitions** are JSON files: built-ins in `core/contest/defs/`, yours in `Documents/QHlogger/contests/`.
  - A rules list gives the QSO points; the first matching rule wins.
  - Multipliers are counted per band, band + mode, mode or contest.
  - There are no Python rule modules yet: every built-in contest fits the JSON rules.
- **Contest exchange storage:** RST goes to `rst_sent` / `rst_rcvd` and serials to `stx` / `srx`. Other exchange fields, space-separated, go to `stx_string` / `srx_string`, and are also copied to their ADIF field where one exists (zone → `cqz` / `ituz`, grid → `gridsquare`, state → `state`).
- **MASTER.SCP** is downloaded on request into the log's folder. Check Partial also uses every call in your log.
- **The country file** is checked weekly and downloaded to `Documents/QHlogger/cty.csv`. The bundled copy is the fallback.
- **The MacLoggerDX importer** also maps the remaining MLDX columns: `azimuth`, `elevation`, `distance`, latitude/longitude and the `ext_val_*` fields go to `extra`. MLDX grid-line pairs (`OM32,OM33`) go to `*_VUCC_GRIDS`.
- **Screenshots:** `tools/screenshots.py` renders `docs/screenshots/` off-screen from made-up QSOs. Test and screenshot runs set `QHLOGGER_SETTINGS=<file.ini>`, so they never touch your real preferences.
- **Branding:** the BY1QH logo (`resources/brand/by1qh.png`) is the window and app icon; PyInstaller converts it to `.icns` / `.ico` with Pillow at build time. Help → About (or the version button in the toolbar) shows the authors (Han Zhang BI1NWO, Claude Opus 5.5 Extra High), copyright (Han Zhang), the BY1QH credit, data and software credits, and system info that can be copied into bug reports.
- **Tester sample:** `tests/fixtures/sample.adi` is a synthetic 17-QSO log (satellite pass, HF, one strict and one suspicious dupe) for trying the app; a test keeps it importable.
- **TQSL data:** the propagation list and five extra satellites come from TQSL's current configuration (`config.tq6` v11.34, lotw.arrl.org). RS95S is on LoTW's FAQ list but not yet in that configuration; both sources are kept.
