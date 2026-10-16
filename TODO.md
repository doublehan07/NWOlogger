# TODO

Milestones and design details are in [PLAN.md](PLAN.md). Tick items off as they land.

## Now: plan review

- [x] First round of open questions answered (PLAN §13 "Decided")
- [x] MacLoggerDX database and ADIF export analysed (PLAN §8.1.1)
- [x] License: MIT
- [x] `.gitignore` keeps personal log data out of git
- [ ] Remaining open questions (PLAN §13 "Open"): ISS satellite name, MLDX bare `Y`, MLDX `ext_val_3`

## M0 — Scaffold

- [ ] `pyproject.toml` (uv, Python ≥ 3.12, PySide6, httpx, keyring; dev: pytest, pytest-qt, ruff)
- [ ] `qhlogger` package with `python -m qhlogger` / `qhlogger` entry point
- [ ] Single-source version (`pyproject.toml` → `qhlogger.__version__`), starting at 0.1.0
- [ ] Version shown in the window title, toolbar (right side), Help → About and `--version`
- [ ] Modern style base: design tokens, app stylesheet on Fusion, light/dark following the OS, SVG icon set
- [ ] Empty main window: toolbar, entry-panel placeholder, log-tab placeholder
- [ ] GitHub Actions: ruff + pytest on macOS and Windows
- [ ] `CONTRIBUTING` notes (commit style: one sentence; no real log data in fixtures)

## M1 — Core data

- [ ] SQLite schema v1 (`qso`, `station_profile`, `contest_session`, `saved_filter`, `lookup_cache`, `sync_state`, `sync_log`, `undo_log`, `meta`)
- [ ] Migration runner + automatic rotating backups
- [ ] Field registry (ADIF name, column, type, label, group, i18n label)
- [ ] Mode/submode table, mode groups (CW / PHONE / DATA / IMAGE), input aliases (USB → SSB/USB, DIGI → DATA, …)
- [ ] Mode relation table: same / could be the same / siblings / unrelated
- [ ] Band table, freq → band
- [ ] ADIF ADI reader (case-insensitive, byte/char length tolerant, UTF-8/Latin-1, APP_/USERDEF)
- [ ] ADIF writer (3.1.8 header, PROGRAMID/PROGRAMVERSION, NAME from First + Last)
- [ ] Lossless round-trip of unknown fields via `extra`; NAME split/join round-trips exactly
- [ ] Tests: round-trip, mode relations, band edges, malformed files

## M2 — Log view, search, import/export

- [ ] Lazy SQL table model (`fetchMore`) for 100k+ QSOs
- [ ] Sort any column via SQL `ORDER BY` (time_on as tiebreaker)
- [ ] Column chooser + reorder, persisted in QSettings
- [ ] Confirmation columns (LoTW / eQSL / QRZ / HRDLog / Paper)
- [ ] Quick filter bar (Call with `?*`, Band, Mode, Sat, Country, Presets incl. Suspicious dupes)
- [ ] Advanced search dialog (multi-criteria AND) + saved filters
- [ ] Vague mode search by default (SSB → USB/LSB, DIGI → FT8 …), *Exact* option
- [ ] Wildcard → SQLite GLOB conversion with escaping
- [ ] ADIF import: every record imported, suspicious dupes marked `DUPE` / `SUSPECT`, preview counts, optional merge mode
- [ ] OPERATOR → STATION_CALLSIGN fill when missing
- [ ] MacLoggerDX database importer: upload-history text → per-service fields, paper `Mail Card` dates, epoch times, mode/submode, dry-run report, re-runnable via `APP_MLDX_PK`
- [ ] Ignore `QSL_SENT` as paper in MacLoggerDX ADIF files
- [ ] Export all / selected / filtered
- [ ] Status line: total, shown, DB path; DXCC worked/confirmed counter

## M3 — DX entry

- [ ] Entry panel in grouped sections (Contact, Station, Location, References, QSL), no rig panel
- [ ] First and Last name fields
- [ ] TimeOn/TimeOff auto + buttons, UTC/local clock
- [ ] Sticky fields (freq, mode, sat, power)
- [ ] Satellite dropdown from `sats.json` (sets PROP_MODE=SAT, freqs, bands, mode); free-text names allowed
- [ ] `cty.csv` resolver (exact calls, longest prefix, portable suffixes) + bundled file + weekly update
- [ ] Worked-before box (band × mode-group matrix, NEW DXCC/BAND/MODE badges)
- [ ] **Dupe rules: same mode within 30 s disables Log; same mode later, or a related mode (SSB/USB, USB/LSB, DIGI/FT8, FT8/FT4), only warns; marks stored**
- [ ] Prefs: block window (30 s), warning window (24 h), allow-dupes switch (off)
- [ ] Edit an existing QSO in the panel; delete; undo
- [ ] Previous (recall last QSO)
- [ ] Command palette (⌘K / Ctrl+K) and shortcuts for every action

## M4 — Bulk edit and paper QSL

- [ ] Bulk edit dialog: per-field set / clear / replace / fill from lookup
- [ ] Diff preview, single transaction, undo
- [ ] Mark QRZ/HRDLog status `M` after editing uploaded QSOs
- [ ] Paper QSL tab: to-send queue, mark sent/received with date + via
- [ ] Find duplicates tool (`DUPE` / `SUSPECT`) with Not a dupe / Merge / Delete actions

## M5 — Callsign lookup

- [ ] QRZ.com XML client (session key, re-login on expiry; fname/name → First/Last)
- [ ] HamQTH client (v1 session; evaluate v2 API)
- [ ] Lookup cache with TTL
- [ ] Fill empty fields only; discard stale results
- [ ] Credentials via keyring

## M6 — Sync

- [ ] Adapter interface, background workers, progress UI, sync history
- [ ] Confirmation matcher (call + band + sat, ±30 min; exact mode preferred, related mode accepted) + Unmatched list
- [ ] Satellite name map per service (defaults from TQSL list; e.g. ISS ↔ ARISS), editable in Prefs
- [ ] QRZ Logbook: INSERT / REPLACE upload, FETCH download with paging
- [ ] eQSL: ImportADIF upload (chunked), DownloadInBox download
- [ ] LoTW: lotwreport.adi download (QSL + QSO-rx passes), TQSL CLI upload, exit-code handling
- [ ] HRDLog: NewEntry upload + UPDATE
- [ ] HRDLog: check the logged-in website for an ADIF export; add best-effort download if it exists
- [ ] Upload-on-log switches (QRZ, eQSL, HRDLog)

## M7 — Contest mode

- [ ] Contest definition JSON schema + loader + validation
- [ ] Contest setup dialog with contest dropdown (built-ins + user-defined)
- [ ] Contest entry line (N1MM keys: Space, Enter-ESM, Esc/Alt+W, freq/mode typed in Call field)
- [ ] Serial numbers per session
- [ ] Live dupe check per definition rule; dupe disables Log, suspect warns
- [ ] Check Partial (MASTER.SCP + own log) and N+1 panes
- [ ] Scoring engine: points table, multiplier types, per-band/mode scopes, live totals
- [ ] Score summary, multipliers and rate panes (dockable / floatable)
- [ ] Built-in definitions: CQ WW DX, CQ WPX, IARU HF, ARRL DX, ARRL 10 m, All Asian DX, CQ WW VHF, DX serial / DX zone templates
- [ ] User-defined contest editor
- [ ] Cabrillo 3.0 export; session ADIF export

## M8 — i18n and release

- [ ] All UI strings via `tr()`; `pyside6-lupdate` workflow
- [ ] Simplified Chinese translation (`zh_CN`)
- [ ] Language switch in Prefs
- [ ] PyInstaller builds: macOS `.app`/`.dmg`, Windows installer
- [ ] Release workflow on `v*` tags (tag must match `pyproject.toml` version)

## M9 — Integrations (later)

- [ ] WSJT-X / JTDX UDP auto-logging (port 2237, "QSO Logged" / "Logged ADIF")
- [ ] N1MM Logger+ UDP contact broadcasts (`contactinfo` / `contactreplace` / `contactdelete`, port 12060)

## Later / maybe

- [ ] ADX (XML ADIF) import/export
- [ ] Club Log
- [ ] Paper QSL label printing
- [ ] Award progress (DXCC/WAZ/VUCC/WAS/IOTA)
- [ ] macOS code signing + notarization
