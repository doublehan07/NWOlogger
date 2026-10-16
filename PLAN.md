# QHlogger — Design Plan

Status: **draft for review** (2026-10-15). Nothing here is implemented yet.
Open questions are collected at the end; answers will be folded back into this file.

## 1. Scope

A minimal, fast, cross-platform (macOS + Windows) amateur radio logger, modelled on
**MacLoggerDX** for day-to-day logging and on **N1MM Logger+** for contesting.

**In scope**

- One SQLite file holds every QSO. Columns are plain ADIF names, so you can query the file with any SQLite tool.
- ADIF import; export of all, selected, or filtered QSOs.
- Multi-criteria search, with `?` / `*` wildcards on callsigns.
- Sort by any column (satellite name, time on, time off, …).
- Bulk edit of multiple selected QSOs after logging (BBLogger style).
- Separate tracking of electronic confirmations and paper QSL cards. The traditional generic "QSL S / QSL R" columns are dropped.
- Two-way sync with LoTW, eQSL and QRZ.com Logbook; upload to HRDLog.net (see §8.5).
- Callsign lookup: offline `cty.csv` → QRZ.com XML → HamQTH fallback.
- Contest mode, N1MM style, with live dupe checking and live scoring. The contest is chosen from a dropdown.
- English and Simplified Chinese UI.

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
- **Sent outward:** the same string goes into ADIF `PROGRAMVERSION`, the service User-Agent (`QHlogger/0.1.0 (BY1QH)`), and the Cabrillo `CREATED-BY` line.
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
    search.py              # filter spec → SQL WHERE (wildcards)
    dupes.py               # dupe keys and checks (mode-group aware)
    callsign.py            # call normalisation, portable suffixes, WPX prefix
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

- Column names are lower-case ADIF field names.
- Callsigns are stored upper-case.
- Times are **UTC ISO-8601 text with seconds** (`2026-10-10T15:19:00Z`), so the file stays readable with plain SQL.
- Frequencies are stored in MHz as `REAL`.

### 4.1 `qso` (one row per QSO)

| Group | Columns |
|---|---|
| Identity | `id` INTEGER PK, `uuid` TEXT UNIQUE (stable id across sync), `created_at`, `updated_at` |
| Core | `call`, `time_on`, `time_off`, `freq`, `band`, `freq_rx`, `band_rx`, `mode`, `submode`, `mode_group` (derived, indexed), `rst_sent`, `rst_rcvd`, `tx_pwr` |
| Satellite / propagation | `prop_mode`, `sat_name`, `sat_mode` |
| Worked station | `name`, `qth`, `address`, `state`, `cnty`, `country`, `dxcc`, `cqz`, `ituz`, `cont`, `gridsquare`, `vucc_grids`, `lat`, `lon`, `iota`, `sota_ref`, `pota_ref`, `wwff_ref`, `sig`, `sig_info`, `skcc`, `email`, `web`, `qsl_via` |
| My station (copied from the station profile at log time) | `station_callsign`, `operator`, `my_gridsquare`, `my_vucc_grids`, `my_dxcc`, `my_cq_zone`, `my_itu_zone`, `my_state`, `my_cnty`, `my_pota_ref`, `my_sota_ref`, `my_wwff_ref`, `my_rig`, `my_antenna`, `station_profile_id` |
| Notes | `comment` (may be sent to services), `notes` (private) |
| Contest | `contest_id`, `contest_session_id`, `stx`, `srx`, `stx_string`, `srx_string` |
| **Paper QSL** | `qsl_sent`, `qslsdate`, `qsl_sent_via`, `qsl_rcvd`, `qslrdate`, `qsl_rcvd_via`, `qslmsg` |
| LoTW | `lotw_qsl_sent`, `lotw_qslsdate`, `lotw_qsl_rcvd`, `lotw_qslrdate` |
| eQSL | `eqsl_qsl_sent`, `eqsl_qslsdate`, `eqsl_qsl_rcvd`, `eqsl_qslrdate`, `eqsl_ag` (Authenticity Guaranteed) |
| QRZ.com | `qrzcom_qso_upload_status`, `qrzcom_qso_upload_date`, `qrzcom_qso_download_status`, `qrzcom_qso_download_date`, `qrz_logid`, `qrz_confirmed` |
| HRDLog | `hrdlog_qso_upload_status`, `hrdlog_qso_upload_date` |
| Everything else | `extra` TEXT (JSON object of every other ADIF field, `APP_*` and `USERDEF` included) |

Notes on the model:

- **Lossless ADIF round-trip.** A field without its own column goes to `extra` and is written back out on export. The schema stays small without losing any data.
- **Paper and electronic records never share a column.** ADIF `QSL_SENT`/`QSL_RCVD` and their dates are paper only. Every electronic service has its own fields.
- **Edits after upload.** Editing an uploaded QSO sets its QRZ and HRDLog status to `M` (modified, an ADIF enum value), so the next sync re-sends it. LoTW and eQSL do not support in-place edits, so they are flagged in the UI instead.
- **Indexes:** `call`, `time_on`, `band`, `mode_group`, `sat_name`, `dxcc`, `station_callsign`, `contest_session_id`, plus a composite dupe index `(call, band, mode_group, time_on)`.

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

**Dupes are compared by mode group, never by raw mode string.** This fixes the reported bug: `SSB` vs `USB`, or `DIGI` vs `FT8`, must count as a dupe.
Dupe key: `call + band + mode_group`, plus `sat_name` when `prop_mode = SAT`, within a scope:

| Context | Scope | Action when a dupe is detected while typing |
|---|---|---|
| Contest mode | The contest definition's rule (once per band, per band+mode, or once per contest) | Call field turns red, "DUPE" badge, **Log is disabled** |
| DX mode | Same key within a time window (see Q2) | Same: Log is disabled |
| Existing log / ADIF import | Same key, time within a tolerance | Shown as "possible duplicate" in the import preview and in a *Find duplicates* tool |

A Prefs switch, "Allow logging dupes", is **off by default**. When it is on, the dupe is logged and flagged; in a contest it scores 0 points.

The same matching code (mode group + band + time tolerance + satellite) is used to match LoTW, eQSL and QRZ confirmations to local QSOs.

## 6. Main window (MacLoggerDX-like, no rig panel)

```
┌ Toolbar: [Log ▾ / Contest]  [Search]  [Sync ▾]  [Import]  [Export ▾]  [Prefs]  UTC 04:20:02  Local 12:20  v0.1.0 ┐
├ Entry panel ── [ DX | Contest ] ─────────────────────────────────────────────────────────────────────────────┤
│ Call [______]  Name           │ Time On  2026-10-16 04:19:35   Time Off                                      │
│ Address / QTH / County        │ MHz  [145.920] 2M   RX MHz [436.950] 70CM   Mode [FM ]                       │
│ State / Country               │ Sat [SO-50 ▾]  Sat mode [ ]  Prop [SAT]  Power [25]  RST S [59]  RST R [59]  │
│ Email / Web                   │ Grid  ITU  CQ  DXCC  IOTA  SOTA  POTA  WWFF  SKCC  QSL Via                    │
│ Comment / Notes               │ ┌ Worked before: band × mode-group matrix, last QSOs, NEW DXCC/BAND/MODE ┐    │
│ [LookUp] [Previous] [TimeOn] [TimeOff] [Log QSO ⏎] [Clear ⎋]  sticky: ☑Freq ☑Mode ☑Sat ☑Pwr                  │
├ Tabs: [ Log | Paper QSL | Sync history ] ────────────────────────────────────────────────────────────────────┤
│ 1434 QSOs · 37 shown · /Users/.../qhlog.sqlite        DXCC 75 wkd / 68 cfm                                   │
│ [Call ?*] [Band ▾] [Mode ▾] [Sat ▾] [Country] [Preset ▾]  [More filters…]  [Clear]                           │
│ Call │ Time On ▾ │ Time Off │ MHz │ Band │ RX MHz │ Band RX │ Mode │ Sat │ Pwr │ RST S │ RST R │ LoTW │ …     │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Entry panel (DX).** The field set follows the MacLoggerDX screenshot, without rig info.
  - Times stamp automatically. TimeOn/TimeOff buttons re-stamp "now".
  - *Sticky* fields carry over to the next QSO, which matters for satellite passes.
  - The **Sat dropdown** comes from a bundled `sats.json` of LoTW-accepted `SAT_NAME`s. Picking a satellite sets `prop_mode=SAT` and fills its default up/down frequencies and mode.
  - *Previous* recalls the last QSO's fields.
  - Enter logs the QSO, Esc clears the form.
  - Double-clicking a row loads that QSO into the panel for editing (Save / Cancel).
- **Log table.** A `QAbstractTableModel` over SQL with lazy loading (`fetchMore`), so 100k+ QSOs stay fast.
  - Clicking any header sorts with SQL `ORDER BY`. `time_on` breaks ties.
  - Columns can be shown, hidden and reordered from the header context menu.
  - Confirmation columns: LoTW ↑/↓, eQSL ↑/↓, QRZ ↑/↓, HRDLog ↑, Paper sent/rcvd. Each shows a date; an empty cell means not done.
  - Row colour: confirmed (any service or paper), unconfirmed, dupe.
- **Context menu on a selection:**
  - Edit
  - Bulk edit…
  - Delete
  - Export selected
  - Upload selected → LoTW / eQSL / QRZ / HRDLog
  - Mark paper QSL sent… / received…
  - Lookup & fill missing
  - Recompute DXCC
- **Paper QSL tab.**
  - A queue of QSOs marked "to send" (`qsl_sent = Q/R`).
  - Actions: set sent date, set received date, set via (Bureau / Direct / Manager).
  - Filters: "received, not yet answered", "sent, awaiting reply".

### 6.1 Bulk edit (BBLogger style; refined once I see a screenshot, Q5)

Bulk edit is a dialog that applies to the selected QSOs:

- Each field row has a ☐ apply box and an operation:
  - set to value
  - clear
  - find & replace
  - fill from lookup
- A preview lists the count and an old → new diff of the first rows.
- Apply runs in one transaction and is recorded in `undo_log`, so it can be undone.
- After applying, the affected QSOs are flagged as modified for QRZ and HRDLog re-upload (§4.1).

## 7. Search

- **Quick filter bar:** Call, Band, Mode, Sat, Country, and a Preset dropdown with entries such as All, Today, This contest, Not uploaded to LoTW, LoTW confirmed, Paper to send.
- **Advanced search dialog:** rows of *field · operator · value*, combined with AND. Searches can be saved as named filters.

| Operator kind | Operators | Applies to |
|---|---|---|
| Text | is, is not, contains, matches (wildcards), empty, not empty | Any text field |
| Time | before, after, between | Any time field |
| Status | yes / no / date range | Confirmation fields |
| Mode | Matches by group: `SSB` finds USB/LSB, `DIGI` finds every data mode | Mode |

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
- **Import preview:** counts of new / possible duplicates / matches with new confirmation data.
  - Policies: skip, merge (fill empty fields and update QSL status), or import anyway.
  - A station profile is assigned when `STATION_CALLSIGN` is missing.
- **Writer:** UTF-8 with header `ADIF_VER`, `PROGRAMID=QHlogger`, `PROGRAMVERSION`, `CREATED_TIMESTAMP`.
  - Exports all / selected / filtered.
  - Optional profiles strip private fields (`notes`, `email`) for service uploads.
- **MacLoggerDX migration:** the importer will special-case MLDX quirks once I have a sample export (Q6).

### 8.2 Common sync design

- **Adapter interface:** each service implements `test_credentials()`, `upload(qsos)` and `download(since)`.
  - Work runs in background workers, with a progress dialog and a row in `sync_log`.
  - Nothing is uploaded automatically unless enabled per service. "Upload on log" is offered for QRZ, eQSL and HRDLog.
- **Matching downloaded confirmations:**
  - Key: same call + band + mode group, with `sat_name` when the QSO was via satellite.
  - Time within ±30 min (LoTW's own window); the nearest QSO wins.
  - Records that don't match go to an **Unmatched** list, where they can be added as new QSOs.
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
  - Each record sets `qrzcom_qso_download_status=Y`, plus `qrz_confirmed` when `APP_QRZLOG_STATUS=C`.
- **HRDLog upload:** `POST https://robot.hrdlog.net/NewEntry.aspx` with `Callsign`, `Code` (upload code), `App`, `ADIFData`.
  - Edits use `Cmd=UPDATE` with an `ADIFKey`.
  - Success sets `hrdlog_qso_upload_status=Y` and its date.
- **HRDLog download:** **HRDLog.net has no public API for downloading QSOs or confirmations.** Wavelog and other loggers treat it as upload-only. See Q1.

## 9. Callsign lookup

The lookup runs when the call field loses focus, after a short pause while typing, or when LookUp is pressed. Steps, in order:

1. **`cty.csv`** (country-files.com, bundled, auto-update weekly) gives country, **DXCC entity number**, CQ/ITU zones, continent and lat/lon instantly and offline.
   - Exact-call overrides (`=CALL`) and longest-prefix matching are supported.
   - Portable forms (`/P`, `/M`, `/MM`, `/AM`, `/QRP`, `/<digit>`, `PREFIX/CALL`) are handled.
2. **Cache** (`lookup_cache`, TTL 30 days by default).
3. **QRZ.com XML** (`xmldata.qrz.com/xml/current/`, session key) gives name, address, QTH, state, county, grid, email, QSL via and LoTW/eQSL flags. Full data needs an XML subscription.
4. **HamQTH** fallback: `xml.php` session, valid 1 h. Its API v2 (bearer token) will be checked during implementation.

Lookups fill **empty fields only** and never overwrite what you typed. They run in a background thread, and a result that arrives after the call has changed is thrown away.

## 10. Contest mode (N1MM style, with live scoring)

### Setup

The contest is **chosen from a dropdown**, which lists the built-in definitions and your user-defined ones together. Setup also asks for:
- start date and time
- Cabrillo categories (operator, band, power, mode, assisted, transmitter, overlay)
- station profile
- sent exchange

This creates a `contest_session`. Every QSO logged in it carries `contest_id` and the session id.

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
- **Dupe check** is live and uses the definition's rule and mode groups (§5). A dupe disables Log.
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
| Station profiles | One or more profiles |
| Logging | Sticky fields, default RST by mode group, dupe window, allow-dupes switch, auto-lookup |
| Lookup | QRZ XML user/password, HamQTH user/password, cache TTL, `cty.csv` update |
| Services | LoTW user/password + TQSL path + station location per profile; eQSL user/password/QTH nickname; QRZ API key per logbook; HRDLog call + upload code; upload-on-log switches |
| Database | Path, backup count |

## 12. Milestones

| # | Milestone | Done when |
|---|---|---|
| M0 | Scaffold | `uv run qhlogger` opens an empty window showing its version (title, toolbar, About, `--version`); CI runs ruff + pytest on macOS and Windows |
| M1 | Core data | Schema + migrations, field registry, modes/bands, ADIF read/write, with round-trip tests |
| M2 | Log view | Lazy table, sort by any column, column chooser, quick filter + advanced search with wildcards, import preview, export all/selected/filtered |
| M3 | DX entry | Entry panel, `cty.csv` resolver, worked-before, mode-group dupe block, sticky fields, satellite list, edit/delete/undo |
| M4 | Bulk edit + paper QSL | Bulk edit dialog with preview + undo; Paper QSL tab |
| M5 | Lookup | QRZ XML + HamQTH + cache, fill-empty-only |
| M6 | Sync | Framework + matcher, then QRZ → eQSL → LoTW (TQSL) → HRDLog upload; Unmatched list; sync history |
| M7 | Contest | Setup dropdown, entry line, serials, dupes, Check Partial / N+1, scoring engine + built-ins, score/mults/rate panes, Cabrillo |
| M8 | Release | zh_CN translation complete, PyInstaller builds (.dmg, Windows installer), GitHub Actions release workflow |

Every milestone ships tests for its core logic. UI milestones are checked by running the app.

## 13. Open questions

1. **HRDLog download.** HRDLog.net exposes no public download API, so HRDLog would be upload-only. Is that acceptable? Its operator has privately shared API docs with other developers, so we could ask.
2. **Dupe window in DX mode.** Outside contests, what counts as a dupe for the same call + band + mode group (+ satellite)? Options: within 10 min, within one satellite pass (~20 min), the same UTC day, or never. Should "Log disabled" be absolute, or overridable with a modifier key such as Ctrl+Enter?
3. **Built-in contests for the dropdown.** Proposed first set:
   - CQ WW DX (CW/SSB/RTTY)
   - CQ WPX (CW/SSB/RTTY)
   - IARU HF
   - ARRL DX (CW/SSB)
   - ARRL 10 m
   - All Asian DX
   - CQ WW VHF
   - generic "DX serial" and "DX zone" templates
   - user-defined

   Which contests do you actually operate, Chinese domestic or satellite events included?
4. **Name field.** MacLoggerDX splits First / Last, but ADIF has only `NAME`. Plan: a single Name field. OK?
5. **Screenshots wanted:** BBLogger's multi-select edit dialog; MacLoggerDX's Contest tab, Search Log window and QSL tab; your preferred N1MM entry-window layout.
6. **MacLoggerDX ADIF sample.** A short export (one satellite QSO, one LoTW-confirmed, one eQSL-uploaded) would let me map MLDX-specific fields for migrating your 1434 QSOs.
7. **WSJT-X / JTDX auto-logging** over UDP (no rig needed): include it in a later milestone?
8. **License** for the GitHub repo (e.g. GPL-3.0, MIT)?
