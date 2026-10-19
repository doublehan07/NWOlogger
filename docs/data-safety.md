# Data safety in NWOlogger 0.2.0: one folder, one writer, a readable file

How NWOlogger keeps a user's log safe from the program itself, from a second copy of the program, from other
programs and from cloud sync clients, and what it does not try to do. This records the decisions made on
2026-10-19 after the Windows and Surface tests of 0.1.9, and the reasoning behind them, so the next person who
touches this code (or the next version of us) does not re-open settled questions without knowing why they were
settled. The code is in `core/station.py`, `core/lease.py`, `core/db.py`, `core/logcopy.py`, `core/mirror.py`,
`instance.py`, `ui/migrate.py` and `ui/add_log.py`.

## The bottom lines

1. **Never ruin the user's log** through conflicting edits from several copies of NWOlogger or from other programs.
2. **The data must outlive the program.** The log is a plain SQLite file that any SQLite tool can read, today and
   after NWOlogger is abandoned, with nothing encoded privately (the schema is in PLAN §4). If the file is ever
   read-only to others, it must be recoverable in one documented step.
3. **No deadlocks, no missed protection.** Any lock we add must be released by the operating system when the
   process dies, never wait, and sit on exactly one path so that every way of opening a log passes through it.
4. **The diagnostic log must say what happened**, including which process did it.

## What was wrong in 0.1.9

A second NWOlogger started on Windows or Linux (every double-click starts a new process there; macOS brings the
running app to the front by itself) opened a second window on the same log. SQLite kept the file intact, write
transactions queue and a busy writer waits 5 s, but the two windows went stale against each other (change notices
are in-process), a QSO not yet marked uploaded could be sent twice, and the two overwrote each other's download
positions and preferences. The windows did not know of each other.

## What we chose

### One station folder, outside cloud sync

Everything NWOlogger has lives in one folder: the logs (`*.sqlite`), `backups/`, `downloads/<log name>/`,
`settings.ini`, `logs/` (diagnostic log and crash file) and `nwologger.lock`. The folder is the operating system's
application-data location (`~/Library/Application Support/NWOlogger` on macOS, `%LOCALAPPDATA%\NWOlogger` on
Windows, `~/.local/share/nwologger` on Linux), which iCloud Desktop & Documents, OneDrive folder backup and Dropbox
never touch. Nothing is stored outside it: no plist, no registry key, no pointer. The folder is a constant, so none
is needed; `--db <file>` (tests, developers) makes that file's folder the station folder for the run.

Why not Documents, where the log lived up to 0.1.9: the live database with its `-wal` and `-shm` is the one file a
sync client must not copy half-written, and macOS and Windows both sync Documents by default. MacLoggerDX keeps
its log in Documents and even suggests a cloud folder in its FAQ, but on the condition "don't change your log from
more than one location at once", and its classic rollback-journal file is a single file at rest where our WAL log
is not. We keep the convenience a different way: see the mirror below.

Why one folder rather than several: a second instance (if ever allowed, see "roads not taken") would be a second
such folder, and the migration would be trivial. Splitting the live `.sqlite` into Documents and the rest elsewhere
was considered and rejected: it keeps the only dangerous file in the synced place and moves the harmless ones.

### The lease: one writing NWOlogger per folder

`core/lease.py` holds an advisory operating-system lock on `nwologger.lock` in the folder: `fcntl.flock` on Unix,
`msvcrt.locking` on Windows (a byte far beyond the file's text, so the text stays readable). It is taken before
`LogDB` opens a log, never waits (`LeaseHeld` is raised at once), and the OS releases it the moment the process
ends, crash included, so there is never a stale lock to delete by hand. The lock file's contents (pid, host,
version, start and heartbeat times) are only a hint for the "in use by" message. Within one process the lease is
reference counted, so switching logs inside one folder never contends with itself.

The lock is on the file, not on a path string: two processes reaching the folder by different spellings, symlinks,
junctions or letter case open the same file and contend for the same lock.

Why not SQLite's `locking_mode=EXCLUSIVE`, which a reviewer proposed: it would make the file unreadable to every
other program while NWOlogger runs (bottom line 2), it solves a problem SQLite has already solved (file integrity)
rather than the real one (workflow exclusion), and it ties the protection to the database connection so that every
operation needing a second connection has to tiptoe around it.

Why only one lock: a lock each for settings, log folder, cache, keyring and TQSL subprocesses was proposed. None
of those resources can ruin the user's data, and every added lock widens the surface for deadlocks and missed
cases. With everything inside the leased folder, one lock covers them. The map tile cache is shared and its
temporary files carry the pid; the keychain is per user and shared, which is correct under one instance.

### The socket: bringing the running window to the front

`instance.py` keeps the per-user single instance for the double-click case: a starting NWOlogger knocks on a local
socket (Python's `multiprocessing.connection`, a named pipe on Windows, served from a thread of its own so the GUI
loop is never part of the handshake); a running copy is asked to come to the front and the new process quits
within a second. This is the experience; the lease is the guarantee. Both are taken once at start, non-blocking,
in a fixed order (knock, then lease), and neither is held while waiting for the other.

Qt's `QLocalServer`/`QLocalSocket` were tried first and never answered in-process on Windows. On Windows,
`Listener.close()` does not reach the pipe instance a blocked `accept()` holds, so `close()` wakes it with one
connection of its own; the thread sees the stop flag and ends.

### The write guard: other programs read, only NWOlogger writes

Every table carries `BEFORE INSERT/UPDATE/DELETE` triggers (`nwo_guard_*`) whose body calls `nwologger_writer()`,
a SQL function only NWOlogger registers on its connections (`core/db.py`). A write by any other program, a SQLite
browser, a script, another logger, an older NWOlogger, fails while the statement is compiled with "no such
function" and changes nothing; `SELECT` fires no trigger, so reading is untouched. Measured on a real log: 24
triggers over 8 tables are 2.5 KB of SQL and one 4 KB page.

Recovery, any one of: `DROP TRIGGER` from any SQLite tool (triggers cannot block DDL):

    SELECT 'DROP TRIGGER "' || name || '";' FROM sqlite_master WHERE type='trigger' AND name LIKE 'nwo_guard_%';

or File → Save a copy… with "for other programs", which writes a copy without the guard; or `sqlite3 log .dump`.
This is how the data outlives the program while the live file is protected from accidental edits.

### The version gate: a lower version never edits a higher version's log

From 0.2.0 on, a log whose schema (`PRAGMA user_version`, or `meta.schema_version`) is newer than the running
version's `SCHEMA_VERSION` is not opened at all: no launch backup, no migration, not one byte written, a message
naming the writing version with a link to the download page. 0.1.9 only warned and wrote anyway; 0.1.8 checked
nothing. Shipped programs cannot be changed, but the guard above already stops 0.1.x writing a 0.2.0 log, and
users were told during the pre-release that older versions must not be used on it.

### Adding a log by copy, never opening in place

A log from elsewhere (a friend's file, a backup, another computer) is copied into the station folder with SQLite's
online backup API (`core/logcopy.py`), which reads the source through SQLite, replays its `-wal` and writes a
complete file in steps that drive a progress bar. The copy goes to `<name>.part-<pid>` and takes its real name
only after `PRAGMA quick_check` passes; Cancel or a failure deletes the partial file, and partials of dead pids are
swept at start. There is no resume: a restarted copy starts over, because a half-written target is not a valid
database and the cost of restarting a local copy is seconds. The switch to the new log happens only after a
complete copy, so a failure leaves the current log open and `settings.ini` unchanged. The original file is never
written to. "Open log file" chooses among the folder's logs; a file picked elsewhere is added by copy.

### The backup copy in Documents

`Documents/NWOlogger/<log name>.sqlite` is one complete copy of the current log (`core/mirror.py`), written with
the backup API to a temporary name and renamed into place, so a cloud sync client only ever sees a whole file.
It is written on the hourly check when the log changed since the last copy, once more at exit, and right after a
migration or an added log; at most an hour behind. It carries the guard like every log of ours (a backup is not
for editing) and a `README.txt` saying what it is, where the live log is, how to drop the guard and how to
restore (File → Add log file…). Up to 0.1.9 the live log itself lived here, so the place users know still holds
"their log", now as the copy that is safe to sync.

Known wrinkles, accepted: two computers on one iCloud account write the same mirror name and the sync client may
keep conflicting copies (the live logs on each computer are unaffected; the README says "one computer at a time, at
your own risk otherwise"); iCloud and OneDrive upload whole files, so a very large log active for hours uploads
itself a few times a day.

### Idle checkpoint and process ids

`wal_checkpoint(TRUNCATE)` runs two seconds after the last change, so the `.sqlite` on disk is complete whenever
the app is idle. Every diagnostic log line carries the process id.

## The migration (first start of 0.2.0)

Ask ("Migrate" or "Quit", no "continue the old way"); copy the 0.1.x log (the one the old preferences pointed
at, else `Documents/NWOlogger/nwolog.sqlite`) into the station folder with the progress bar; add the guard; move
`backups/` and `downloads/` (an instant rename on the same volume); move the preferences into `settings.ini` and
clear the system store; write the first backup copy back into Documents, replacing the original; tell the user
that older versions can no longer edit the log.

## Roads not taken, kept for the record

- **B, share everything, one writes, the others read:** a second instance opens the same log read-only and
  refreshes by polling `PRAGMA data_version`. A real feature (a live view on a second screen), not a protection,
  and only as good as the completeness of "read-only" across every write path. Deferred until someone asks.
- **C, independent instances (`--instance <folder>`):** each folder its own log, settings, diagnostics and lock,
  keychain entries namespaced per instance, cache shared. The layout chosen makes this a cheap addition later.
  Not built: most "several stations" needs are station profiles in one log.
- **Refusing to run in cloud-synced folders:** rejected as locking existing users out; the mirror design serves the
  same want.
- **Squashing the 0.2.0 commits:** rejected; this project commits one sentence at a time and pushes plainly.

## How a second launch is handled, route by route

| Route | What stops it | What the user sees |
|---|---|---|
| Same user double-clicks, or command line without arguments | the socket knock | the existing window comes to the front; the new process exits 0 |
| Same user, `--db` naming another log | the socket knock | the window comes to the front with a message naming its open log |
| Another OS user, another settings file, a path alias, an older NWOlogger | the lease (an OS lock on the file, not on a string); an old version also meets the guard | "this log is in use by another NWOlogger: pid, started, version"; nothing opens |
| The first instance crashed or was killed | nothing: the OS released the lock; a stale socket file is removed | opens normally; SQLite replays the `-wal`; the old pid in the lock file is history |
| Another computer through a synced folder | nothing can; the lock file's heartbeat is a hint | "seems to be in use on <computer> since …; continue?" |
