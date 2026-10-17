"""SQLite log file: schema, migrations, backups and CRUD."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid as uuidlib
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import fields, timeutil
from . import qso as qsomod
from .fields import BY_COL, FIELDS, SERVICE_KEY_FIELDS
from .net import redact

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

DEFAULT_DIR = Path.home() / "Documents" / "NWOlogger"
DEFAULT_PATH = DEFAULT_DIR / "nwolog.sqlite"

_OTHER_TABLES = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS station_profile (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL, is_default INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS contest_session (
    id INTEGER PRIMARY KEY, contest_def TEXT NOT NULL, name TEXT NOT NULL, started_at TEXT NOT NULL,
    next_serial INTEGER NOT NULL DEFAULT 1, station_profile_id INTEGER, data TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS saved_filter (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, spec TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lookup_cache (
    call TEXT PRIMARY KEY, source TEXT, fetched_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sync_state (service TEXT NOT NULL, key TEXT NOT NULL, value TEXT,
    PRIMARY KEY (service, key));
CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY, service TEXT NOT NULL, direction TEXT NOT NULL, started_at TEXT NOT NULL,
    finished_at TEXT, sent INTEGER DEFAULT 0, received INTEGER DEFAULT 0, matched INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0, message TEXT);
"""
_DROPPED_TABLES = ("undo_log", "undo_batch")  # undo history, until 0.1.4

_INDEXES = [
    ("idx_qso_call", "call"),
    ("idx_qso_time_on", "time_on"),
    ("idx_qso_band", "band"),
    ("idx_qso_mode_group", "mode_group"),
    ("idx_qso_sat_name", "sat_name"),
    ("idx_qso_dxcc", "dxcc"),
    ("idx_qso_station_callsign", "station_callsign"),
    ("idx_qso_contest_session", "contest_session_id"),
    ("idx_qso_dupe_flag", "dupe_flag"),
    ("idx_qso_dupe_key", "call, band, time_on"),
]

# Backups taken right before a change to many QSOs (there is no undo: a backup is the way back).
SAFETY_LABELS = ("import", "bulk-edit", "delete", "merge", "dupes", "lookup", "recompute")
SAFETY_KEEP = 10
QSO_COLS = [f.col for f in FIELDS if f.col != "id"]

ChangeListener = Callable[[str, list[int]], None]


def _same_content(a: Path, b: Path) -> bool:
    """Two backups of the same log state: SQLite's backup copies every page as is, and only the 100-byte file
    header (change counter, library version) may differ."""
    try:
        return a.stat().st_size == b.stat().st_size and a.read_bytes()[100:] == b.read_bytes()[100:]
    except OSError:
        return False


def _qso_table_sql() -> str:
    cols = ["id INTEGER PRIMARY KEY"]
    for f in FIELDS:
        if f.col == "id":
            continue
        decl = f"{f.col} {fields.sql_type(f.kind)}"
        if f.col == "uuid":
            decl += " NOT NULL UNIQUE"
        cols.append(decl)
    return "CREATE TABLE IF NOT EXISTS qso (\n    " + ",\n    ".join(cols) + "\n)"


class LogDB:
    """One open log file. Not thread-safe: use it from one thread (the GUI thread)."""

    def __init__(self, path: str | os.PathLike, *, backup_on_open: bool = True, backup_keep: int = 20):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists() and self.path.stat().st_size > 0
        self.backup_keep = backup_keep
        self.conn = sqlite3.connect(str(self.path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._listeners: list[ChangeListener] = []
        self._in_batch = False
        self._pending: list[tuple[str, list[int]]] = []
        if existed and backup_on_open:
            self.backup("open")
        self._migrate()

    # ------------------------------------------------------------------ setup
    def _migrate(self) -> None:
        c = self.conn
        c.executescript(_OTHER_TABLES)
        c.execute(_qso_table_sql())
        existing = {r["name"] for r in c.execute("PRAGMA table_info(qso)")}
        for f in FIELDS:
            if f.col not in existing:
                c.execute(f"ALTER TABLE qso ADD COLUMN {f.col} {fields.sql_type(f.kind)}")
        for name, cols in _INDEXES:
            c.execute(f"CREATE INDEX IF NOT EXISTS {name} ON qso ({cols})")
        old = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")} & set(_DROPPED_TABLES)
        if old:
            for t in _DROPPED_TABLES:
                c.execute(f"DROP TABLE IF EXISTS {t}")
            c.execute("VACUUM")  # give the space of the old undo history back
            log.info("Removed the undo history of NWOlogger 0.1.4 and earlier from the log file")
        if self.meta("schema_version") is None:
            self.set_meta("schema_version", str(SCHEMA_VERSION))
            self.set_meta("created_at", timeutil.now_iso())

    def close(self) -> None:
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        self.conn.close()

    # ------------------------------------------------------------------ meta
    def meta(self, key: str) -> str | None:
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def set_meta(self, key: str, value: str | None) -> None:
        self.conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, value))

    # ------------------------------------------------------------------ backups
    @property
    def backup_dir(self) -> Path:
        return self.path.parent / "backups"

    def backups(self) -> list[Path]:
        """This log's backups, oldest first."""
        return sorted(self.backup_dir.glob(f"{self.path.stem}-*.sqlite"))

    def _label(self, p: Path) -> str:
        """``nwolog-20261016-135423-309488-bulk-edit.sqlite`` → ``bulk-edit``."""
        return p.stem[len(self.path.stem) + 1:].split("-", 3)[-1]

    def backup(self, label: str = "manual") -> Path | None:
        """Copy the log file to ``backups/``. A launch copy (``open``) is not taken again while the log is unchanged
        since the newest backup: that one is returned. Copies taken right before an import or a bulk edit are kept
        apart (the newest :data:`SAFETY_KEEP`), so launches can't push them out; of the others the newest
        ``backup_keep`` are kept."""
        if not self.path.exists():
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        before = self.backups()
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        dest = self.backup_dir / f"{self.path.stem}-{stamp}-{label}.sqlite"
        conn = getattr(self, "conn", None)
        src = conn if conn is not None else sqlite3.connect(str(self.path))
        out = sqlite3.connect(str(dest))
        try:
            with out:
                src.backup(out)
        finally:
            out.close()
            if conn is None:
                src.close()
        if label == "open" and before and _same_content(before[-1], dest):
            dest.unlink(missing_ok=True)
            log.info("No launch backup: the log is unchanged since %s", before[-1].name)
            return before[-1]
        log.info("Backup made: %s", dest.name)
        safety, other = [], []
        for p in self.backups():
            (safety if self._label(p) in SAFETY_LABELS else other).append(p)
        for old in safety[:-SAFETY_KEEP] + other[:-self.backup_keep]:
            try:
                old.unlink()
            except OSError:
                pass
        return dest

    # ------------------------------------------------------------------ listeners
    def add_listener(self, fn: ChangeListener) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: ChangeListener) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _notify(self, kind: str, ids: list[int]) -> None:
        if self._in_batch:
            self._pending.append((kind, ids))
            return
        for fn in list(self._listeners):
            fn(kind, ids)

    # ------------------------------------------------------------------ batches
    @contextmanager
    def batch(self, label: str) -> Iterator[None]:
        """Group writes into one transaction (all or nothing), with one change notice per kind at the end.
        ``label`` names the change in the diagnostic log (with --debug)."""
        if self._in_batch:  # nested: join the outer batch
            yield
            return
        self.conn.execute("BEGIN")
        self._in_batch = True
        try:
            yield
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            self._pending.clear()
            raise
        finally:
            self._in_batch = False
        log.debug("Change: %s", label)
        pending, self._pending = self._pending, []
        merged: dict[str, list[int]] = {}
        for kind, ids in pending:
            merged.setdefault(kind, []).extend(ids)
        for kind, ids in merged.items():
            self._notify(kind, ids)

    # ------------------------------------------------------------------ QSO CRUD
    def _row_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {k: row[k] for k in row.keys()}

    def get(self, qso_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM qso WHERE id=?", (qso_id,)).fetchone()
        return self._row_dict(r) if r else None

    def get_many(self, ids: Iterable[int]) -> dict[int, dict]:
        ids = list(ids)
        out: dict[int, dict] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = f"SELECT * FROM qso WHERE id IN ({', '.join('?' * len(chunk))})"
            for r in self.conn.execute(q, chunk):
                out[r["id"]] = self._row_dict(r)
        return out

    def insert(self, qso: dict, *, normalized: bool = False) -> int:
        q = dict(qso) if normalized else qsomod.normalize(qso)
        q.pop("id", None)
        now = timeutil.now_iso()
        q.setdefault("uuid", str(uuidlib.uuid4()))
        if not q.get("uuid"):
            q["uuid"] = str(uuidlib.uuid4())
        q["created_at"] = q.get("created_at") or now
        q["updated_at"] = now
        cols = [c for c in q if c in BY_COL and c != "id"]
        cur = self.conn.execute(
            f"INSERT INTO qso ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [q[c] for c in cols])
        qid = cur.lastrowid
        assert qid is not None
        self._notify("insert", [qid])
        return qid

    def insert_many(self, qsos: Iterable[dict], label: str = "Insert") -> list[int]:
        ids: list[int] = []
        with self.batch(label):
            for q in qsos:
                ids.append(self.insert(q))
        return ids

    def update(self, qso_id: int, changes: dict, *, track_service: bool = True, normalized: bool = False) -> bool:
        """Apply ``changes`` to one QSO. Returns True if anything changed."""
        before = self.get(qso_id)
        if before is None:
            return False
        ch = dict(changes) if normalized else _normalize_changes(before, changes)
        ch.pop("id", None)
        ch.pop("uuid", None)
        diff = {k: v for k, v in ch.items() if k in BY_COL and before.get(k) != v}
        if not diff:
            return False
        if track_service and set(diff) & SERVICE_KEY_FIELDS:
            if before.get("qrzcom_qso_upload_status") == "Y" and "qrzcom_qso_upload_status" not in diff:
                diff["qrzcom_qso_upload_status"] = "M"
            if before.get("hrdlog_qso_upload_status") == "Y" and "hrdlog_qso_upload_status" not in diff:
                diff["hrdlog_qso_upload_status"] = "M"
        diff["updated_at"] = timeutil.now_iso()
        sets = ", ".join(f"{k}=?" for k in diff)
        self.conn.execute(f"UPDATE qso SET {sets} WHERE id=?", [*diff.values(), qso_id])
        self._notify("update", [qso_id])
        return True

    def update_many(self, ids: Iterable[int], changes: dict | Callable[[dict], dict], label: str = "Edit",
                    *, track_service: bool = True) -> int:
        """Apply the same changes (or a per-QSO function) to many QSOs in one transaction."""
        n = 0
        ids = list(ids)
        rows = self.get_many(ids)
        with self.batch(label):
            for qid in ids:
                row = rows.get(qid)
                if row is None:
                    continue
                ch = changes(row) if callable(changes) else changes
                if ch and self.update(qid, ch, track_service=track_service):
                    n += 1
        return n

    def delete(self, ids: Iterable[int], label: str = "Delete", *, backup: bool = True) -> int:
        """Delete QSOs. A ``delete`` backup is saved first (there is no undo); ``backup=False`` when the caller has
        just saved its own."""
        ids = list(ids)
        rows = self.get_many(ids)
        if rows and backup:
            self.backup("delete")
        with self.batch(label):
            for qid in ids:
                if qid in rows:
                    self.conn.execute("DELETE FROM qso WHERE id=?", (qid,))
            # Clear references from other QSOs' dupe marks.
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                ph = ", ".join("?" * len(chunk))
                for r in self.conn.execute(f"SELECT id FROM qso WHERE dupe_of IN ({ph})", chunk).fetchall():
                    self.conn.execute("UPDATE qso SET dupe_flag=NULL, dupe_of=NULL WHERE id=?", (r["id"],))
            self._notify("delete", [i for i in ids if i in rows])
        return len(rows)

    # ------------------------------------------------------------------ queries
    def count(self, where: str = "", params: Iterable[Any] = ()) -> int:
        sql = "SELECT COUNT(*) FROM qso" + (f" WHERE {where}" if where else "")
        return self.conn.execute(sql, list(params)).fetchone()[0]

    def select_ids(self, where: str = "", params: Iterable[Any] = (),
                   order: list[tuple[str, bool]] | None = None) -> list[int]:
        """IDs matching ``where``, sorted by ``order`` [(column, descending)]. NULLs sort last."""
        order = order or [("time_on", True)]
        parts = []
        for col, desc in order:
            if col not in BY_COL:
                continue
            expr = f"{col} COLLATE NOCASE" if BY_COL[col].kind in ("text", "flag") else col
            parts.append(f"({col} IS NULL OR {col} = '')")
            parts.append(f"{expr} {'DESC' if desc else 'ASC'}")
        if not any(c == "time_on" for c, _ in order):
            parts.append("time_on DESC")
        parts.append("id DESC")
        sql = "SELECT id FROM qso" + (f" WHERE {where}" if where else "") + " ORDER BY " + ", ".join(parts)
        return [r[0] for r in self.conn.execute(sql, list(params))]

    def iter_all(self, where: str = "", params: Iterable[Any] = ()) -> Iterator[dict]:
        sql = "SELECT * FROM qso" + (f" WHERE {where}" if where else "") + " ORDER BY time_on, id"
        for r in self.conn.execute(sql, list(params)):
            yield self._row_dict(r)

    def distinct(self, col: str, limit: int = 500) -> list[str]:
        if col not in BY_COL:
            return []
        rows = self.conn.execute(
            f"SELECT {col}, COUNT(*) n FROM qso WHERE {col} IS NOT NULL AND {col} != '' "
            f"GROUP BY {col} ORDER BY n DESC LIMIT ?", (limit,))
        return [str(r[0]) for r in rows]

    def last_qso(self, where: str = "", params: Iterable[Any] = ()) -> dict | None:
        sql = "SELECT * FROM qso" + (f" WHERE {where}" if where else "") + " ORDER BY time_on DESC, id DESC LIMIT 1"
        r = self.conn.execute(sql, list(params)).fetchone()
        return self._row_dict(r) if r else None

    def worked_before(self, call: str, limit: int = 200) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM qso WHERE call=? ORDER BY time_on DESC LIMIT ?",
                                 (call.upper(), limit))
        return [self._row_dict(r) for r in rows]

    def find_by_extra(self, tag: str, value: str) -> int | None:
        r = self.conn.execute("SELECT id FROM qso WHERE json_extract(extra, ?) = ?", (f"$.{tag}", value)).fetchone()
        return r[0] if r else None

    # ------------------------------------------------------------------ station profiles
    def profiles(self) -> list[dict]:
        out = []
        for r in self.conn.execute("SELECT * FROM station_profile ORDER BY is_default DESC, name"):
            d = json.loads(r["data"])
            d.update(id=r["id"], name=r["name"], is_default=bool(r["is_default"]))
            out.append(d)
        return out

    def profile(self, pid: int | None) -> dict | None:
        if pid is None:
            return None
        for p in self.profiles():
            if p["id"] == pid:
                return p
        return None

    def default_profile(self) -> dict | None:
        ps = self.profiles()
        return ps[0] if ps else None

    def save_profile(self, profile: dict) -> int:
        data = {k: v for k, v in profile.items() if k not in ("id", "name", "is_default")}
        name = profile.get("name") or profile.get("station_callsign") or "Station"
        if profile.get("is_default"):
            self.conn.execute("UPDATE station_profile SET is_default=0")
        if profile.get("id"):
            self.conn.execute("UPDATE station_profile SET name=?, data=?, is_default=? WHERE id=?",
                              (name, json.dumps(data), int(bool(profile.get("is_default"))), profile["id"]))
            pid = int(profile["id"])
        else:
            first = self.conn.execute("SELECT COUNT(*) FROM station_profile").fetchone()[0] == 0
            cur = self.conn.execute("INSERT INTO station_profile(name, data, is_default) VALUES(?, ?, ?)",
                                    (name, json.dumps(data), int(bool(profile.get("is_default")) or first)))
            pid = int(cur.lastrowid or 0)
        self._notify("profiles", [])
        return pid

    def delete_profile(self, pid: int) -> None:
        self.conn.execute("DELETE FROM station_profile WHERE id=?", (pid,))
        self._notify("profiles", [])

    # ------------------------------------------------------------------ saved filters
    def saved_filters(self) -> dict[str, dict]:
        return {r["name"]: json.loads(r["spec"]) for r in self.conn.execute("SELECT * FROM saved_filter ORDER BY name")}

    def save_filter(self, name: str, spec: dict) -> None:
        self.conn.execute("INSERT INTO saved_filter(name, spec) VALUES(?, ?) "
                          "ON CONFLICT(name) DO UPDATE SET spec=excluded.spec", (name, json.dumps(spec)))

    def delete_filter(self, name: str) -> None:
        self.conn.execute("DELETE FROM saved_filter WHERE name=?", (name,))

    # ------------------------------------------------------------------ lookup cache
    def cache_get(self, call: str, max_age_days: float) -> dict | None:
        r = self.conn.execute("SELECT * FROM lookup_cache WHERE call=?", (call.upper(),)).fetchone()
        if not r:
            return None
        age = timeutil.seconds_between(r["fetched_at"], timeutil.now_iso())
        if age > max_age_days * 86400:
            return None
        d = json.loads(r["data"])
        d["_source"] = r["source"]
        return d

    def calls_last_worked(self) -> dict[str, str]:
        """Every call in the log with the time of its last QSO."""
        return {r[0]: r[1] or "" for r in self.conn.execute(
            "SELECT call, MAX(time_on) FROM qso WHERE call IS NOT NULL AND call != '' GROUP BY call")}

    def cached_calls(self) -> dict[str, str]:
        """Every call QRZ.com / HamQTH has returned, with the source name (any age)."""
        return {r["call"]: r["source"] or "" for r in self.conn.execute("SELECT call, source FROM lookup_cache")}

    def call_summaries(self, calls: Iterable[str]) -> dict[str, dict]:
        """For each call in the log: QSO count, the last QSO (time, band, mode) and which confirmations exist."""
        calls = [c.upper() for c in calls]
        if not calls:
            return {}
        marks = ",".join("?" * len(calls))
        sql = (
            "SELECT * FROM (SELECT call, time_on, band, mode, submode, sat_name, "
            "ROW_NUMBER() OVER (PARTITION BY call ORDER BY time_on DESC, id DESC) rn, "
            "COUNT(*) OVER (PARTITION BY call) n, "
            "MAX(lotw_qsl_rcvd = 'Y') OVER (PARTITION BY call) cl, MAX(eqsl_qsl_rcvd = 'Y') OVER (PARTITION BY call) ce, "
            "MAX(qrz_qsl_rcvd = 'Y') OVER (PARTITION BY call) cq, MAX(qsl_rcvd = 'Y') OVER (PARTITION BY call) cp "
            f"FROM qso WHERE call IN ({marks})) WHERE rn = 1")
        out = {}
        for r in self.conn.execute(sql, calls):
            d = dict(r)
            d["confirmed"] = "".join(letter for col, letter in (("cl", "L"), ("ce", "E"), ("cq", "Q"), ("cp", "P"))
                                     if d.get(col))
            out[d["call"]] = d
        return out

    def cache_put(self, call: str, source: str, data: dict) -> None:
        self.conn.execute("INSERT OR REPLACE INTO lookup_cache(call, source, fetched_at, data) VALUES(?, ?, ?, ?)",
                          (call.upper(), source, timeutil.now_iso(), json.dumps(data, ensure_ascii=False)))

    # ------------------------------------------------------------------ sync state / log
    def sync_get(self, service: str, key: str) -> str | None:
        r = self.conn.execute("SELECT value FROM sync_state WHERE service=? AND key=?", (service, key)).fetchone()
        return r["value"] if r else None

    def sync_set(self, service: str, key: str, value: str | None) -> None:
        self.conn.execute("INSERT OR REPLACE INTO sync_state(service, key, value) VALUES(?, ?, ?)",
                          (service, key, value))

    def sync_log_add(self, service: str, direction: str, started_at: str, *, sent: int = 0, received: int = 0,
                     matched: int = 0, errors: int = 0, message: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO sync_log(service, direction, started_at, finished_at, sent, received, matched, errors, "
            "message) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (service, direction, started_at, timeutil.now_iso(), sent, received, matched, errors, redact(message)))
        self._notify("sync_log", [])
        return int(cur.lastrowid or 0)

    def sync_history(self, limit: int = 200) -> list[dict]:
        return [self._row_dict(r) for r in self.conn.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT ?",
                                                              (limit,))]

    def sync_history_counts(self) -> dict[str, int]:
        """Rows of sync history per service name (LoTW, eQSL, QRZ.com, HRDLog)."""
        return {r[0]: r[1] for r in self.conn.execute(
            "SELECT service, COUNT(*) FROM sync_log GROUP BY service ORDER BY service")}

    def clear_sync_history(self, services: Iterable[str]) -> int:
        """Delete the sync history of these services. Only the history: QSOs, their upload and confirmation
        status, and where the next download starts are not touched."""
        names = sorted(set(services))
        if not names:
            return 0
        cur = self.conn.execute(f"DELETE FROM sync_log WHERE service IN ({','.join('?' * len(names))})", names)
        self._notify("sync_log", [])
        return cur.rowcount

    # ------------------------------------------------------------------ contest sessions
    def contest_sessions(self) -> list[dict]:
        out = []
        for r in self.conn.execute("SELECT * FROM contest_session ORDER BY id DESC"):
            d = self._row_dict(r)
            d["data"] = json.loads(d["data"] or "{}")
            out.append(d)
        return out

    def contest_session(self, sid: int) -> dict | None:
        for s in self.contest_sessions():
            if s["id"] == sid:
                return s
        return None

    def create_contest_session(self, contest_def: str, name: str, started_at: str, data: dict,
                               station_profile_id: int | None) -> int:
        cur = self.conn.execute(
            "INSERT INTO contest_session(contest_def, name, started_at, next_serial, station_profile_id, data) "
            "VALUES(?, ?, ?, 1, ?, ?)", (contest_def, name, started_at, station_profile_id, json.dumps(data)))
        self._notify("contest", [])
        return int(cur.lastrowid or 0)

    def update_contest_session(self, sid: int, **kw: Any) -> None:
        if "data" in kw:
            kw["data"] = json.dumps(kw["data"])
        sets = ", ".join(f"{k}=?" for k in kw)
        self.conn.execute(f"UPDATE contest_session SET {sets} WHERE id=?", [*kw.values(), sid])
        self._notify("contest", [])

    def delete_contest_session(self, sid: int) -> None:
        self.conn.execute("DELETE FROM contest_session WHERE id=?", (sid,))
        self._notify("contest", [])


def _normalize_changes(before: dict, changes: dict) -> dict:
    """Normalise a partial update in the context of the existing row."""
    merged = dict(before)
    merged.update(changes)
    keys = set(changes)
    if "freq" in keys and "band" not in keys:
        merged["band"] = None
    if "freq_rx" in keys and "band_rx" not in keys:
        merged["band_rx"] = None
    if "mode" in keys and "submode" not in keys:
        merged["submode"] = None
    norm = qsomod.normalize(merged)
    touched = set(keys)
    if "freq" in keys or "band" in keys:
        touched.add("band")
    if "freq_rx" in keys or "band_rx" in keys:
        touched.add("band_rx")
    if "mode" in keys or "submode" in keys:
        touched |= {"mode", "submode", "mode_group"}
    if "sat_name" in keys:
        touched.add("prop_mode")
    return {k: norm.get(k) for k in touched if k in BY_COL}
