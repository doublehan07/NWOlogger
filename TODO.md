# TODO

Milestones and design details are in [PLAN.md](PLAN.md). v0.1.0 covers M0–M8; v0.1.1 adds the credential fixes, the real-account tests and the new name, NWOlogger; v0.1.2 adds the Device section, the Sync results window and the diagnostic log; v0.1.3 adds the LoTW / eQSL call lists for Similar calls and clearing sync history per service; v0.1.4 fixes what testing 0.1.3 found; v0.1.5 what testing 0.1.4 found; v0.1.6 adds merging field by field, Look up and fill with progress, and safer bulk edit times; v0.1.7 adds the grid map; v0.1.8 turns it into a globe drawn at the display's resolution and fixes what testing 0.1.7 and the first 0.1.8 build found; v0.1.9 does what the 0.1.8 feedback asked: downloads to kept files that resume, downloaded QSOs added with a possible-duplicates window, resume positions from imports with a Preferences tab, a one-minute dupe window, the map's callsign, place and orbit filters in a window of its own, one Grid field for several grids, station profiles from the log, paper QSL replies, log-file provenance and a clean exit. What is left is below.

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
- [x] TQSL station locations on Windows (`%APPDATA%\TrustedQSL\station_data`) fill the Preferences dropdown

## Next

- [ ] **Label maker:** print QSL card labels from the paper QSL queue (label sheets and label printers, layout templates)
- [ ] Contest: per-transmitter (multi-op) support, a Cabrillo template per contest (column widths), ARRL section lists
- [ ] Measure the per-pixel globe renderer on the Windows test machine at 100% and 250% scaling (it took 106–242 ms per dense scene before; PLAN §15)

## M9 — Integrations (later)

- [ ] WSJT-X / JTDX UDP auto-logging (port 2237, "QSO Logged" / "Logged ADIF")
- [ ] N1MM Logger+ UDP contact broadcasts (`contactinfo` / `contactreplace` / `contactdelete`, port 12060)

## Later / maybe

- [ ] **Statistics: Overview, Awards and Matrix** (sub-tabs next to the grid map): totals per year, band, mode and satellite, unique calls, QSL counts; award cards (DXCC, WAS, WAC, WAZ, WPX, VUCC, QRZ.com awards, China provinces, IOTA) counting confirmations as each sponsor does; the band × mode matrix per entity, zone, province or prefix
- [ ] macOS code signing + notarization (the BY1QH icon is in place)
- [ ] ADX (XML ADIF) import/export
- [ ] Club Log
- [ ] Award progress (DXCC/WAZ/VUCC/WAS/IOTA, China provinces)

## Done in v0.1.9 (2026-10-18)

From the feedback on 0.1.8 (PLAN §16):

- [x] **Quitting:** deterministic teardown, a crash file from `faulthandler`, the watchdog stopped and joined before Qt goes (it deadlocked when destroyed on its own thread), background tasks dropped only once their thread has ended, and `NWOLOGGER_SELF_TEST=close` in the packaging smoke test
- [x] **Downloads to files that resume:** every service's download streams to `downloads/` next to the log with retries and Range continuation; LoTW in yearly parts applied as they land, eQSL by byte range, QRZ.com page by page; uploads mark each accepted QSO at once; *Download from a date…*
- [x] **Downloaded QSOs are added** instead of asked for every time; a near-duplicate (same call within a minute) waits in the *Possible duplicates* window, which can open the QSO it may duplicate; the same for a LoTW report imported from a file
- [x] **Sync positions:** set from an imported log's confirmations (a day earlier, never later than an existing position) and a LoTW report's headers; a Preferences tab shows them locked, with *Unlock* (danger zone), *Lock and save* and Cancel
- [x] **Dupe warning window:** 1 minute (a stored 24 h becomes 1 min; other values are kept in minutes)
- [x] **Grid map:** satellite categories (LEO, MEO, HEO, GEO, non-geostationary; orbits in `sats.json`), *Callsigns* and *Grids* lists from the log with counts and *Within 200 km*, the counts badge on screen and in the PNG, right or middle button rotation with a left click to select, *Open in a window* with a remembered geometry and full screen inside it, PNG named `<call> at <grid> - NWOlogger <version>.png` (*(et al)* for several calls; the centre grid alone), solid M/N/P field watermarks
- [x] **Several grids on one QSO:** one Grid field reads `OM88, OM89` and writes `GRIDSQUARE` + `VUCC_GRIDS`; shown together in the form, the log, the compare window, Worked before and the station profile
- [x] **Station profiles → Add from my log…**, and the import preview says how many records lack a station callsign (the only ones a profile fills)
- [x] **Paper QSL:** *Mark paper QSL not sent / not received*; *Queue replies to received cards…*
- [x] **Help menu:** Show log file folder, Show backups folder, Show diagnostic log folder; **About:** the BY1QH logo designer, the log's provenance
- [x] **Log files:** application id, user version and creating / last-writing NWOlogger in the file; a newer version's log warns; *Open log file* refuses a foreign SQLite file; *Import* tells a non-MacLoggerDX SQLite file from an empty one
- [x] **Regular backup plan:** every N days (default 7) a scheduled copy into backups/ when the QSOs changed, in a pool of its own (default 8), and a reminder window for a copy on another disk with *Back up to…*; checked a minute after the start and hourly
- [x] **Grid search on the whole grid list:** any grid of a station on a grid line finds it, several grids in any order, a comma or semicolon alone lists the QSOs with several grids; a map click lists squares held only as VUCC grids; "/" in the Call box pinned by a test
- [x] The map's own window keeps the tab's margins; the counts badge in colour and larger in the PNG, whose band carries the file's name; Preferences pages listed down the left side
- [x] **Stale dupe marks:** the review, Find duplicates and a change of the windows rescan the marks first and say how many an earlier rule had left (0.1.8's 24-hour window marked pairs 20 minutes apart)
- [x] **The day/night line over the sea:** satellite imagery's near-black ocean is lifted toward a sea blue on the day side while the tiles are sampled, so the night shade shows the same gradient over water as over land; the bare globe got a lighter sea too
- [x] **CI:** the Windows tests no longer depend on text widths (its off-screen Qt draws every glyph one em wide), the session ends by taking down tasks, the globe's thread pool and top-level widgets while the application exists (Linux had crashed on the way out after every test passed), and `PYTHONFAULTHANDLER` is set so an exit crash prints its stack
- [x] **A minute of blocked clicks** after closing the map window during a PNG export: the export's progress window was window-modal and, moved to the main window, could stay invisible on macOS while blocking it until the 60 s tile deadline; it is non-modal now
- [x] Translations for everything above (简体中文)

## Done in v0.1.8 (2026-10-18)

From testing the first 0.1.8 build (the version stays 0.1.8):

- [x] **macOS disk image:** the app next to an Applications link, with an arrow between, to drag it in (dmgbuild, `packaging/dmg_settings.py`); the installed app ejects the disk image when it starts from Applications
- [x] **Grid map full screen:** *Full screen* (or the system's full-screen key) shows the map alone, with *Save as PNG* in its corner; Esc, its *Exit full screen* button or closing the window brings it back, and a click on a square comes back to show its QSOs
- [x] **Calls the same size:** a square's calls all have one size (a seventh of its width, so an eight-character call fits), so a five-letter call is no larger than a six-letter one and all calls appear at the same zoom; thin (normal weight), up to 10 pt, with names a fifth smaller (8 pt); a longer call is made smaller to fit
- [x] **Save as PNG:** the map as you see it, at twice its size in pixels, named `<operator>-grid-map-NWOlogger.png`, with "NWOlogger" and the version in a band above the map, after its imagery has arrived
- [x] **Imagery after much zooming and dragging:** a view seen before could come back without imagery, because tiles dropped from memory were never asked for again; now they load again (from the disk cache), coarse tiles stay as stand-ins, and a failed tile is retried after 30 s
- [x] **A clear grey line:** the night shade goes from none to full within ±1° of solar altitude, as on PSK Reporter's map, and covers the squares too
- [x] **Space behind the globe:** stars, a faint Milky Way and an atmosphere glow at the limb
- [x] **Asking again while a run goes on:** a second download or upload with the same service shows "another download (upload) is still in progress", starts nothing and brings back its hidden progress window; Sync everything warns once for all still running
- [x] Preferences: the language hint names the *Save* button (it said OK)
- [x] The *Day/night* button looks selected while the night side is shown
- [x] **File → Compact log file:** deleting QSOs never shrinks the SQLite file (and their old bytes stay in its free space); this rebuilds it with VACUUM after a backup and shows the size before and after
- [x] **Importing a LoTW report** (`lotwreport.adi`): its `QSL_RCVD` / `QSLRDATE` are LoTW's confirmation and date, but File → Import wrote them into the paper QSL fields. Now it is applied like a LoTW download by default (confirmations with LoTW's date, uploads marked, new confirmations selected, the QSOs not in the log offered to add), or imported as QSOs into the LoTW fields; paper QSL fields are never touched. A QSO LoTW holds twice, with and without its satellite, now matches each copy

Before that:

- [x] **GitHub Actions:** CI runs ruff and pytest on Linux, macOS and Windows for every push (passing); the release workflow's first run, for the v0.1.8 tag, built a Linux `.tar.gz`, a macOS `.dmg` (Apple silicon) and a Windows installer and ZIP, started each built app off-screen, and put them in a draft pre-release (publish it on GitHub). Actions → Release → *Run workflow* builds them without a release
- [x] **Keychain in the packaged builds:** the built macOS app uses the macOS Keychain and the built Windows app the Windows Credential Manager (`packaging/smoke_test.py`, from the start-up log's new *Keychain:* line); a build without its backend fails the release
- [x] **Windows display scaling:** fit the main window and Preferences to the available desktop, with scrolling to reach clipped controls; checked in English and Chinese up to 250% scaling
- [x] **Download retries and imports:** failed LoTW, eQSL and QRZ.com downloads do not start the download wait; adding downloaded QSOs to an empty log preserves their service confirmations; overlapping import dialogs merge contacts already added by another service
- [x] **First LoTW download:** reuse the full accepted-QSO report for confirmations when its timestamps are complete, with a separate confirmation query when needed; skip matching against an empty log
- [x] **Frequencies:** all 33 ADIF bands in TX/RX and custom contests; explicit Hz/kHz/MHz/GHz units and decimal commas; bare values use MHz in DX entry and kHz in contests
- [x] **Unsaved station profiles:** Preferences Save, Cancel, Esc and window close offer Save / Discard / Cancel; failed profile validation keeps the editor open
- [x] **Spherical grid map:** drag or arrow keys rotate the globe; centered two-letter field names at 8% opacity; current UTC night shading with a remembered on/off button; PNG export and valid legacy-view migration
- [x] **Globe crash and rendering:** exact sampled latitude endpoints avoid rounding beyond ±90°; batched and cached geometry, cached native-font labels, and hidden square/call labels during dragging reduce render work
- [x] **Globe at the display's resolution:** imagery, squares and the night side are computed per pixel on a thread pool, with antialiased square edges; the frame follows the display up to 2× (Retina), the tile level follows the zoom up to the provider's deepest, and lines are one-device-pixel hairlines. A dense view with 1,000 squares takes 16 ms instead of 25 ms on an Apple M4, with full-resolution imagery, and 34 ms at 2× (PLAN §15)
- [x] **Twilight instead of a line:** the night shade fades in from +3° to −12° solar altitude; the orange boundary line is gone
- [x] **Names and calls like GridMaster:** a square's name and first call appear together about half a zoom step inside a whole-country view (none with China fitted) and grow to 11 / 14 pt; no text shadow; worked squares get a light edge so neighbours stay apart
- [x] **Selected rows keep their colour:** a call LoTW confirmed stays green when selected (dupes red, not-yet-uploaded grey)
- [x] **Bulk edit while typing:** the preview for 5,000 QSOs takes 9 ms instead of 135 ms (typing a date no longer stalls); a date it can't read, such as `2026`, is refused instead of written as typed
- [x] **README grid map section** (English and Chinese) with three screenshots; `tools/screenshots.py` renders at 1480 × 900 again (a larger off-screen desktop) and fetches NASA imagery for the map shots
- [x] **Documentation and credits:** English and Chinese READMEs with pre-release notices, installation instructions moved to INSTALLATION.md, and GridmasterMap / GridTracker credits in About and the READMEs
- [x] **Windows x64 distribution:** rebuilt and checked executable architecture, English/Chinese startup and shutdown, and ZIP integrity; latest focused globe regression run passed 235 tests (PLAN §15)

## Done in v0.1.7 (2026-10-17)

- [x] **Grid map** (Statistics tab, Ctrl+4), GridMaster / GridTracker style: every 4-character square on satellite imagery; confirmed squares green with their grid and the first call that confirmed them (LoTW, eQSL, QRZ.com, paper: any mix you tick), worked-only squares amber with the first call worked; satellite / band / all, mode group, only QSOs made within 200 km of your grid (VUCC, GridMaster); region presets incl. CONUS 488; zoom, rotate, click to show the square's QSOs, Save as PNG
- [x] **Map imagery** (Preferences → Map): NASA Blue Marble (no key) or Tianditu 天地图 (free key, kept in the keychain); tiles fetched in the background and kept in the system cache folder (up to 300 MB), so views seen before open offline

## Done in v0.1.6 (2026-10-17)

- [x] **Merge field by field:** in the compare window, *Merge…* adds a Result column; click the value to keep on each row that differs (Comment / Notes can keep both), choose the QSO that stays by its column header. Confirmations and upload states are combined (the best state with its date and log id), other programs' fields kept from all; the result is checked like the entry form; deleted QSOs that are also on QRZ.com / HRDLog are listed to delete there. *Edit → Merge selected…* does the same for 2–5 selected QSOs with the same call
- [x] **Ignore all marked dupes** (Edit menu, and *Not dupes: all…* in the compare window): every DUPE / SUSPECT QSO becomes "not a dupe", after a warning and a second question, with a backup
- [x] **Look up and fill** runs in a window with progress (count, current call, found / not found / failed) and *Stop*, which keeps what was found; at most one lookup a second; the same error three times in a row ends the run; *Look up the other N* continues later
- [x] **Bulk edit** refuses a QSO that would end before it starts and a time it can't read (it used to empty the field), with *Leave those out* for the rest

## Done in v0.1.5 (2026-10-17)

- [x] **No undo any more:** undoing a download put the log out of step with the services (and the next download couldn't repair it), and one click could delete a whole import. Every change to many QSOs (import, bulk edit, delete, merge, look up and fill, recompute DXCC) asks first and saves a backup; the last 10 such backups are kept apart from the launch copies. 0.1.4's undo history is removed from the log file once
- [x] **Downloads say what is new:** "2 new confirmations · 1 already in your log · 6 not in your log" instead of "received / matched / updated" (LoTW sends its newest confirmation again, eQSL the last day, QRZ.com the whole logbook). The QRZ.com download date is set once, so the first download of a UTC day no longer "updates" every QSO. A Sync results row with new confirmations has *Select them*
- [x] **Download everything again…** (QSL → LoTW / eQSL) starts downloads over from your first QSO; *Clear history* says it keeps where downloads resume
- [x] **Wait between syncs** with the same service (Preferences → Services, default 5 min, at least 1): a sooner download or upload is refused with the time it can run; QSOs uploaded when logged wait and go together; kept across restarts
- [x] Wildcards: a match only through a portable part (`4W/JI1AVY` for `JI1*`) ranks after the whole-call matches
- [x] Preferences → OK no longer re-applies an unchanged theme (0.49 s in a real log)

## Done in v0.1.4 (2026-10-17)

- [x] **Preferences → OK is quick again** (was 21–28 s in a real log, every time): the saved-password scan of the log and every backup is gone. It only cleaned up after v0.1.0, which was never released, and it parsed every undo record of every backup. Nothing writes a credential into the log: error text is redacted when stored, credential-named ADIF tags are dropped on import. Uploads no longer re-check each field against the saved secrets either (they send standard ADIF fields only)
- [x] **Language without a restart:** OK builds the window again in the new language (about 0.1–0.3 s) with the same log file, operator, view, tab, filter, sort, selection, scroll position, entry form and Sync results rows; it waits while a sync runs
- [x] **Theme:** previewed as you pick it, *Cancel* puts the old one back; icons are drawn in the current theme's colours, so they no longer stay dark on the dark toolbar after a switch
- [x] **Wildcards in the call field:** `*` and `?` search Similar calls like the log's search box (`BA*K` → BA1PK, BA8BLK, BA1PK/P) in the log, the lookup cache, the LoTW / eQSL lists and MASTER.SCP; a pattern is never looked up, filled in or logged
- [x] **Sorting:** a header click shows the first row of the new order (e.g. the latest QSO for Time On ↓) instead of keeping the old top row somewhere in the middle
- [x] **New confirmations selected:** after a download, the QSOs it confirmed for the first time (LoTW, eQSL, QRZ.com) are selected and shown; one click clears them; Sync everything adds each download's
- [x] **Backups:** no launch copy while the log is unchanged; the last 5 copies before an import or bulk edit are kept apart from the 20
- [x] **QRZ.com key on a callsign change:** once no profile uses the old call, its Logbook key moves to the new call or is removed, as you choose (it was left in the keychain where nothing could reach it)
- [x] **Diagnostic log:** freezes from 2 s with the GUI thread's stack (home folder as `~`), slow steps with their parts, one line per Preferences save naming what changed, sync summaries always in English with download and apply times and new confirmations, launch backups

## Done in v0.1.3 (2026-10-16)

- [x] **Similar calls from the internet's call data:** ARRL's LoTW user list and eQSL's AG member list (about 320,000 calls; 76% of the Chinese calls in a real log, against 28% for MASTER.SCP) are downloaded next to the log, refreshed weekly (File → Update LoTW and eQSL call lists) and searched while you type, also for a missing letter or digit (`BINWO` → `BI1NWO`); rows show the last LoTW upload date and AG membership. QRZ.com and HamQTH can't search part of a call, so this is how their gap is filled
- [x] A call is green only when LoTW confirmed the QSO (eQSL, QRZ.com and paper cards no longer count)
- [x] *Clear history…* in Sync history (and QSL → Clear sync history…) deletes the history of the services you tick, e.g. only QRZ.com; QSOs, their upload and confirmation status, and download positions are kept

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
