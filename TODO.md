# TODO

Milestones and design details are in [PLAN.md](PLAN.md). v0.1.0 covers M0–M8; v0.1.1 adds the credential fixes, the real-account tests and the new name, NWOlogger; v0.1.2 adds the Device section, the Sync results window and the diagnostic log. What is left is below.

## Needs real accounts

Tried with real accounts on 2026-10-16: downloads and lookups (read-only), and uploads of two fresh QSOs, each sent once to every service.

- [x] LoTW download of confirmations and accepted uploads, all matched to the log; a first full download of a large log takes a few minutes
- [x] LoTW upload via TQSL with a real certificate and station location (2 QSOs, "Success")
- [x] eQSL inbox download with the QTH nickname, matched to the log; the ADIF link is on www.eqsl.cc
- [x] eQSL upload with the QTH nickname (2 QSOs; "1 out of 1 records added" parsed)
- [ ] eQSL upload of a satellite QSO (the ISS name)
- [x] QRZ.com Logbook STATUS and FETCH paging, matched to the log
- [x] QRZ.com Logbook INSERT (2 QSOs; each found once in the logbook afterwards, log id stored)
- [ ] QRZ.com Logbook REPLACE (re-upload of a QSO edited after upload)
- [x] HRDLog upload with a real upload code (2 QSOs)
- [ ] HRDLog: re-upload of an edited QSO (`Cmd=UPDATE`); check whether the logged-in website offers an ADIF export (PLAN §8.5)
- [x] QRZ.com XML lookups, including "not found"
- [ ] HamQTH lookups (no account yet); evaluate HamQTH API v2; check whether it accepts the password by POST
- [x] Password by POST: QRZ.com XML accepts it (login and lookups now use POST); LoTW (HTTP 405) and eQSL do not, so they keep the GET query their APIs document
- [ ] eQSL error text: with a wrong password or QTH nickname, the Sync results row shows eQSL's own `Error…` line (and the log the page text)
- [ ] TQSL with a passphrase-protected certificate: TQSL asks in its own window; *Cancel* stops it; quitting during the upload asks and stops it
- [ ] TQSL station locations on Windows (`%APPDATA%\TrustedQSL\station_data`) fill the Preferences dropdown

## Next

- [ ] **Gridmaster-style grid display:** a map of Maidenhead grid squares coloured by worked / confirmed (VUCC), per band, mode or satellite
- [ ] **Label maker:** print QSL card labels from the paper QSL queue (label sheets and label printers, layout templates)
- [ ] First GitHub Actions run (CI and the release workflow have not run yet)
- [ ] Bulk edit: refuse a Time Off earlier than Time On, as the entry form does
- [ ] Contest: per-transmitter (multi-op) support, a Cabrillo template per contest (column widths), ARRL section lists
- [ ] Bulk "Look up and fill" progress dialog with cancel
- [ ] Find duplicates: merge two QSOs field by field
- [ ] Check the release build (PyInstaller) uses the macOS Keychain / Windows Credential Manager backend

## M9 — Integrations (later)

- [ ] WSJT-X / JTDX UDP auto-logging (port 2237, "QSO Logged" / "Logged ADIF")
- [ ] N1MM Logger+ UDP contact broadcasts (`contactinfo` / `contactreplace` / `contactdelete`, port 12060)

## Later / maybe

- [ ] Statistics view: totals per year, band, mode and satellite, unique calls, QSL counts
- [ ] Import aliases for satellite spellings other programs use (`TEVEL-5`, `InspireSat-7`, `ISS` → `ARISS`) and Yaesu's `FM DN` mode

- [ ] macOS code signing + notarization (the BY1QH icon is in place)
- [ ] ADX (XML ADIF) import/export
- [ ] Club Log
- [ ] Award progress (DXCC/WAZ/VUCC/WAS/IOTA, China provinces)

## Done in v0.1.2 (2026-10-16)

- [x] **Device section** in the entry form: rig (`MY_RIG`), antenna (`MY_ANTENNA`) and power (`TX_PWR`, moved from the RST row), from your lists in Preferences → Devices. A *setup* you put together there (rig + antenna + power) fills all three in one pick, and each can still be changed; empty fields log the station profile's. *Keep: Device* replaces *Keep: Power*; *Clear* keeps the Device section; contest QSOs use it too
- [x] *Comment (upload)* (`COMMENT`, sent to eQSL, QRZ.com, HRDLog) and *Notes (local)* (`NOTES`, never uploaded, e.g. the repeater you used, the other station's rig, antenna and power, or your QTH)
- [x] Propagation no longer offers RPT (a repeater goes in *Notes (local)*); QSOs imported with RPT keep it and are still not sent to LoTW
- [x] **MacLoggerDX upload history:** a QSO confirmed by LoTW or QRZ.com is marked uploaded there, also on ADIF import (MLDX only records its own uploads: 502 LoTW-confirmed QSOs had none in a real log); the import preview counts them and suggests downloading first
- [x] **eQSL / TQSL settings checked before a run:** a station profile without an eQSL QTH nickname or a TQSL station location, or with a location TQSL doesn't have, is skipped with a clear row; the TQSL station location is a dropdown of TQSL's own locations
- [x] **Readable service errors:** eQSL and LoTW pages without a result show their `Error…` line, never JavaScript
- [x] **No more stacked message boxes:** every sync outcome is a row in one non-modal Sync results window, with *Open Preferences* where a setting fixes it; the Unmatched list no longer blocks
- [x] **No more hang on quit:** TQSL runs in its own process group and *Cancel* stops it; quitting during a sync asks, then stops TQSL; background work runs on daemon threads, so quitting never waits for it
- [x] **Diagnostic log** (Help → Show log folder): sync runs with a ref, requests by host and path, TQSL results, Qt warnings, uncaught exceptions, and thread stacks when the window stops responding for 15 s; every secret and account user name is masked, also in tracebacks; `--debug` for more detail

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

## Done after second testing (2026-10-16)

- [x] Mode and satellite dropdowns list only your choice (Preferences → Modes, Satellites "In form"); *Other…* searches the rest and can add to your list
- [x] Propagation sits before Satellite; a satellite with non-SAT propagation is kept, with a warning and a confirmation
- [x] Operator is a field in the entry form and the contest bar (no pop-up); club stations must fill it; amber check on a new UTC day or after a 3-hour break
- [x] Double-click a station profile to make it the default
- [x] Dupe compare shows a whole group (3+ QSOs), with editable cells, Save edits, Keep #n + merge others, Delete, Not dupes
- [x] README screenshots from made-up data (`tools/screenshots.py`); test runs no longer touch real preferences

## Done after the credential review (2026-10-16)

- [x] Errors from LoTW, eQSL, QRZ.com, HamQTH and HRDLog carry the HTTP status, never the URL with its password or session key; every message is redacted before a dialog, the status bar, sync history or a log
- [x] Requests only go over HTTPS to each service's own hosts, checked before every redirect; the eQSL ADIF link must be on www.eqsl.cc
- [x] Passwords saved in sync history by v0.1.0 are cleaned once when the log opens (before the open backup); backups that still hold one are listed (File → Check backups for saved passwords…)
- [x] HTTPX request logs are filtered, so turning on INFO logging shows no password
- [x] Preferences: saved secrets are never shown, *Forget* removes one, QRZ API keys are typed in a password field and shown as dots; renaming an account deletes the old keychain entry; deleting the last profile with a callsign removes its QRZ key
- [x] Only the system keychain is used: plaintext or `fail` keyring backends are refused; a failed delete is reported
- [x] ADIF record tags that look like passwords are dropped on import and never exported or uploaded; LoTW upload removes its temporary files
- [x] A failed download or upload no longer leaves the service "busy" until restart
- [x] Tests run with an in-memory keychain and fail on any real network request
- [x] Time On *Now* goes back to the default: both times now, Time Off follows the clock
- [x] *Clear* (Esc) empties the whole Contact section (TX / RX MHz, mode, RST, power, propagation, satellite) whatever *Keep* says; logging needs a mode; an empty Power logs the profile's default
- [x] Similar calls in Worked before: your log first, then calls QRZ.com / HamQTH know, then MASTER.SCP; a call not found online checks up to three one-letter-away MASTER.SCP calls; File → Update MASTER.SCP

## Done after the second credential review (2026-10-16)

- [x] Secrets echoed back encoded (`&#39;`, `&apos;`, `%27`, double encoding) or shorter than 4 characters are masked too
- [x] HTTPX / httpcore logs stay at WARNING even when the app logs at DEBUG; their child loggers mask headers, cookies and the secrets of requests in flight
- [x] The leftover check uses your saved secrets (once after an update, after each Preferences save, and from the menu), so `Wrong code …`-style messages are found; credential tags are removed from imported QSOs and undo history; the cleanup waits for other programs that have the file open, never blocks opening, and the "no backup" message no longer overclaims
- [x] Uploads send only standard ADIF fields plus the service's own tags, and no field holding a saved secret; more credential-like tag names are dropped on import and export
- [x] Status bar messages no longer fire after their window is closed

## Done after the live tests (2026-10-16)

- [x] Upload test with two fresh QSOs, each sent once to QRZ.com, HRDLog, eQSL and LoTW (TQSL): both accepted by all four and checked on the websites; QRZ.com holds each exactly once
- [x] QRZ.com XML sends the password and session key in the request body (POST), not the URL
- [x] QRZ.com downloads always fetch the whole logbook: `MODSINCE` made every download after the first return nothing
- [x] eQSL downloads start a day before the last one, so cards that arrive later the same day aren't missed
- [x] *Sync everything* only downloads; QSOs not yet uploaded to all four services are grey, with the missing services in the tooltip and a *Not uploaded to every service* preset
- [x] Upload pending, the *Not uploaded to …* presets and the grey rows share one rule (`core/uploads.py`)
