# TODO

Milestones and design details are in [PLAN.md](PLAN.md). v0.1.0 covers M0–M8; what is left is below.

## Needs real accounts (written, tested against canned responses only)

- [ ] LoTW: download confirmations and upload via TQSL with a real certificate and station location
- [ ] eQSL: upload and inbox download (incl. the ISS name and QTH nicknames)
- [ ] QRZ.com Logbook: INSERT / REPLACE upload and FETCH paging with a real API key
- [ ] HRDLog: upload with a real upload code; check whether the logged-in website offers an ADIF export (PLAN §8.5)
- [ ] QRZ.com XML and HamQTH lookups with real accounts; evaluate HamQTH API v2

## Next

- [ ] macOS code signing + notarization (the BY1QH icon is in place)
- [ ] First GitHub Actions run (CI and the release workflow have not run yet)
- [ ] Screenshots for the README, made from `tests/fixtures/sample.adi`
- [ ] Contest entry line: show and change the operator there too (today it shares the DX panel's operator)
- [ ] Bulk edit: refuse a Time Off earlier than Time On, as the entry form does
- [ ] Contest: per-transmitter (multi-op) support, a Cabrillo template per contest (column widths), ARRL section lists
- [ ] Bulk "Look up and fill" progress dialog with cancel
- [ ] Find duplicates: merge two QSOs field by field

## M9 — Integrations (later)

- [ ] WSJT-X / JTDX UDP auto-logging (port 2237, "QSO Logged" / "Logged ADIF")
- [ ] N1MM Logger+ UDP contact broadcasts (`contactinfo` / `contactreplace` / `contactdelete`, port 12060)

## Later / maybe

- [ ] ADX (XML ADIF) import/export
- [ ] Club Log
- [ ] Paper QSL label printing
- [ ] Award progress (DXCC/WAZ/VUCC/WAS/IOTA, China provinces)

## Done in v0.1.0

- [x] M0 Scaffold: uv project, version in title / toolbar / About / `--version`, modern theme (light/dark), CI workflow
- [x] M1 Core data: SQLite schema from the field registry, migrations, backups, undo; modes, mode relations, bands; ADIF reader/writer with lossless round-trip
- [x] M2 Log view: lazy table, sort any column, column chooser, quick filter + advanced search (wildcards, vague/exact mode, saved searches), ADIF import with dupe marking and merge mode, MacLoggerDX importer, export all / selected / filtered
- [x] M3 DX entry: grouped sections, auto times, sticky fields, satellite memory, country file + China province guess, worked-before matrix, strict dupe rules (30 s block / 24 h warning), edit and delete
- [x] M4 Bulk edit with preview and undo; Paper QSL tab; Find duplicates; Not a dupe
- [x] M5 Lookup: QRZ.com XML, HamQTH fallback, cache, fill-empty-only with tinted fields
- [x] M6 Sync: LoTW (TQSL), eQSL, QRZ.com Logbook, HRDLog upload; confirmation matching; satellite names per service; unmatched list; sync history; upload on log
- [x] M7 Contest: setup with contest dropdown, N1MM entry keys, serials, live dupes and score, Check Partial / N+1 / Score / Multipliers / Rate docks, MASTER.SCP download, Cabrillo export, user-defined contests
- [x] M8 Simplified Chinese UI; PyInstaller spec, Inno Setup script, release workflow
- [x] Country file (cty.csv) update from country-files.com, checked weekly

## Done after first testing (2026-10-16)

- [x] Time Off follows Time On + 1 min when Time On is changed (an edited QSO keeps its duration); changing Time Off alone never moves Time On; Time Off earlier than Time On is refused with a warning
- [x] "MHz" is "TX MHz"; an "= TX" toggle keeps RX MHz equal to TX (off for satellites)
- [x] Mode is a dropdown; Propagation is a dropdown of TQSL's 16 modes (+ ADIF-only, marked not for LoTW); Satellite is a dropdown, with your own satellites added in Preferences → Satellites
- [x] LoTW upload leaves out repeater / internet QSOs and drops propagation values TQSL rejects
- [x] Side-by-side dupe compare / review window (Not a dupe, Merge, Delete, Edit, Previous / Next)
- [x] Operators: personal vs club station profiles; club asks at the first QSO, again on a new UTC day or after a 3-hour break; Cabrillo OPERATORS from the log
- [x] BY1QH logo as the app icon (`resources/brand/`); new About page (authors, copyright, credits, system info)
- [x] README: how to test, file tree, links to PLAN and TODO; synthetic `tests/fixtures/sample.adi`
