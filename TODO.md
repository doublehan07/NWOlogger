# TODO

Milestones and design details are in [PLAN.md](PLAN.md). Tick items off as they land.

## Now: plan review

- [ ] Review PLAN.md and answer the open questions (§13)
- [ ] Collect reference screenshots: BBLogger bulk edit; MacLoggerDX Contest tab, Search Log and QSL tab; N1MM entry window
- [ ] Get a MacLoggerDX ADIF sample export for migration mapping
- [ ] Choose a license

## M0 — Scaffold

- [ ] `pyproject.toml` (uv, Python ≥ 3.12, PySide6, httpx, keyring; dev: pytest, pytest-qt, ruff)
- [ ] `qhlogger` package with `python -m qhlogger` / `qhlogger` entry point
- [ ] Single-source version (`pyproject.toml` → `qhlogger.__version__`), starting at 0.1.0
- [ ] Version shown in the window title, toolbar (right side), Help → About and `--version`
- [ ] Empty main window: toolbar, entry-panel placeholder, log-tab placeholder
- [ ] GitHub Actions: ruff + pytest on macOS and Windows
- [ ] `.gitignore`, `CONTRIBUTING` notes (commit style: one sentence)

## M1 — Core data

- [ ] SQLite schema v1 (`qso`, `station_profile`, `contest_session`, `saved_filter`, `lookup_cache`, `sync_state`, `sync_log`, `undo_log`, `meta`)
- [ ] Migration runner + automatic rotating backups
- [ ] Field registry (ADIF name, column, type, label, group, i18n label)
- [ ] Mode/submode table, mode groups (CW / PHONE / DATA / IMAGE), input aliases (USB → SSB/USB, DIGI → DATA, …)
- [ ] Band table, freq → band
- [ ] ADIF ADI reader (case-insensitive, byte/char length tolerant, UTF-8/Latin-1, APP_/USERDEF)
- [ ] ADIF writer (3.1.8 header, PROGRAMID/PROGRAMVERSION)
- [ ] Lossless round-trip of unknown fields via `extra`
- [ ] Tests: round-trip, mode groups, band edges, malformed files

## M2 — Log view, search, import/export

- [ ] Lazy SQL table model (`fetchMore`) for 100k+ QSOs
- [ ] Sort any column via SQL `ORDER BY` (time_on as tiebreaker)
- [ ] Column chooser + reorder, persisted in QSettings
- [ ] Confirmation columns (LoTW / eQSL / QRZ / HRDLog / Paper)
- [ ] Quick filter bar (Call with `?*`, Band, Mode, Sat, Country, Presets)
- [ ] Advanced search dialog (multi-criteria AND; mode filter by exact mode, parent mode or whole group) + saved filters
- [ ] Wildcard → SQLite GLOB conversion with escaping
- [ ] Import preview (new / possible dupe / merge) with policies
- [ ] Export all / selected / filtered
- [ ] Status line: total, shown, DB path; DXCC worked/confirmed counter

## M3 — DX entry

- [ ] Entry panel matching the MacLoggerDX layout (no rig panel)
- [ ] TimeOn/TimeOff auto + buttons, UTC/local clock
- [ ] Sticky fields (freq, mode, sat, power)
- [ ] Satellite dropdown from `sats.json` (sets PROP_MODE=SAT, freqs, bands, mode)
- [ ] `cty.csv` resolver (exact calls, longest prefix, portable suffixes) + bundled file + weekly update
- [ ] Worked-before box (band × mode-group matrix, NEW DXCC/BAND/MODE badges)
- [ ] **Dupe check: same mode + submode disables Log; same group but different mode (SSB/USB, USB/LSB, DIGI/FT8, FT8/FT4) only warns**
- [ ] Edit an existing QSO in the panel; delete; undo
- [ ] Previous (recall last QSO)

## M4 — Bulk edit and paper QSL

- [ ] Bulk edit dialog: per-field set / clear / replace / fill from lookup
- [ ] Diff preview, single transaction, undo
- [ ] Mark QRZ/HRDLog status `M` after editing uploaded QSOs
- [ ] Paper QSL tab: to-send queue, mark sent/received with date + via
- [ ] Find duplicates tool (strict dupes and possible dupes listed separately)

## M5 — Callsign lookup

- [ ] QRZ.com XML client (session key, re-login on expiry)
- [ ] HamQTH client (v1 session; evaluate v2 API)
- [ ] Lookup cache with TTL
- [ ] Fill empty fields only; discard stale results
- [ ] Credentials via keyring

## M6 — Sync

- [ ] Adapter interface, background workers, progress UI, sync history
- [ ] Confirmation matcher (call + band + sat, ±30 min; exact mode preferred, same group accepted) + Unmatched list
- [ ] QRZ Logbook: INSERT / REPLACE upload, FETCH download with paging
- [ ] eQSL: ImportADIF upload (chunked), DownloadInBox download
- [ ] LoTW: lotwreport.adi download (QSL + QSO-rx passes), TQSL CLI upload, exit-code handling
- [ ] HRDLog: NewEntry upload + UPDATE
- [ ] Upload-on-log switches (QRZ, eQSL, HRDLog)

## M7 — Contest mode

- [ ] Contest definition JSON schema + loader + validation
- [ ] Contest setup dialog with contest dropdown (built-ins + user-defined)
- [ ] Contest entry line (N1MM keys: Space, Enter-ESM, Esc/Alt+W, freq/mode typed in Call field)
- [ ] Serial numbers per session
- [ ] Live dupe check per definition rule; dupe disables Log, possible dupe warns
- [ ] Check Partial (MASTER.SCP + own log) and N+1 panes
- [ ] Scoring engine: points table, multiplier types, per-band/mode scopes, live totals
- [ ] Score summary, multipliers and rate panes (dockable / floatable)
- [ ] Built-in definitions (list to be confirmed, see PLAN §13 Q3)
- [ ] User-defined contest editor
- [ ] Cabrillo 3.0 export; session ADIF export

## M8 — i18n and release

- [ ] All UI strings via `tr()`; `pyside6-lupdate` workflow
- [ ] Simplified Chinese translation (`zh_CN`)
- [ ] Language switch in Prefs
- [ ] PyInstaller builds: macOS `.app`/`.dmg`, Windows installer
- [ ] Release workflow on `v*` tags (tag must match `pyproject.toml` version)

## Later / maybe

- [ ] WSJT-X / JTDX UDP auto-logging
- [ ] ADX (XML ADIF) import/export
- [ ] Club Log
- [ ] Paper QSL label printing
- [ ] Award progress (DXCC/WAZ/VUCC/WAS/IOTA)
- [ ] macOS code signing + notarization
