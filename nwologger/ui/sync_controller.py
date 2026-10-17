"""Upload / download with LoTW, eQSL, QRZ.com and HRDLog. Network runs on worker threads; DB on the GUI thread.

Outcomes go to one non-modal Sync results window, never to message boxes: results that arrive together (Sync
everything starts three downloads) would otherwise stack modal boxes, and only the top one takes a click.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import timedelta

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, applog, i18n
from ..core import adif, importer, net, sats, timeutil
from ..core.sync import apply, services, tqsl
from . import theme
from .widgets import fmt, mode_text, run_async

tr = i18n.tr
log = logging.getLogger("nwologger.sync")

# A message for Sync results, built with a translate function: tr for the window, as is (English) for the log file.
Text = str | Callable[[Callable[[str], str]], str]


def _as_is(text: str) -> str:
    return text


def _duration(t, seconds: float) -> str:
    """78 → "1 min 18 s" (with t translating the units)."""
    m, s = divmod(int(round(seconds)), 60)
    return (f"{m} " + t("min") + (f" {s} " + t("sec") if s else "")) if m else f"{s} " + t("sec")

RESULT_LABELS = {"ok": "Done", "info": "Note", "skipped": "Skipped", "failed": "Failed", "cancelled": "Stopped"}
DIRECTIONS = {"up": "upload", "down": "download"}


class UnmatchedDialog(QDialog):
    def __init__(self, service: str, qsos: list[dict], parent=None):
        super().__init__(parent)
        self.qsos = qsos
        self.setWindowTitle(tr("Not found in your log") + f" — {service}")
        self.resize(720, 460)
        lay = QVBoxLayout(self)
        lab = QLabel(tr("These records did not match any QSO in your log. Select the ones to add."))
        lab.setWordWrap(True)
        lay.addWidget(lab)
        self.table = QTableWidget(len(qsos), 5)
        self.table.setHorizontalHeaderLabels([tr("Call"), tr("Time On"), tr("Band"), tr("Mode"), tr("Sat")])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for i, q in enumerate(qsos):
            for j, v in enumerate([q.get("call"), fmt("time_on", q.get("time_on")), fmt("band", q.get("band")),
                                   mode_text(q), q.get("sat_name") or ""]):
                self.table.setItem(i, j, QTableWidgetItem(v or ""))
        lay.addWidget(self.table, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Close)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Add selected to log"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def selected(self) -> list[dict]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.qsos[r] for r in rows]


class SyncResults(QDialog):
    """Every upload and download outcome, newest first, in a window that never blocks the rest of NWOlogger."""

    prefs_requested = Signal(str)
    history_requested = Signal()
    select_requested = Signal(list)  # the QSO ids of the chosen row (its new confirmations)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Sync results"))
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(780, 320)
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr("Time (UTC)"), tr("Service"), tr("Result"), tr("Details")])
        hh = self.table.horizontalHeader()
        for c in range(3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._selected)
        self.table.cellDoubleClicked.connect(lambda *_a: self._fix())
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.fix_btn = QPushButton(tr("Open Preferences"))
        self.fix_btn.setEnabled(False)
        self.fix_btn.setToolTip(tr("Opens the Preferences page that fixes the selected result"))
        self.fix_btn.clicked.connect(self._fix)
        self.select_btn = QPushButton(tr("Select them"))
        self.select_btn.setEnabled(False)
        self.select_btn.setToolTip(tr("Select the QSOs this download confirmed for the first time in the log"))
        self.select_btn.clicked.connect(self._select_ids)
        hist = QPushButton(tr("Show sync history"))
        hist.clicked.connect(self.history_requested)
        clear = QPushButton(tr("Clear"))
        clear.clicked.connect(self.clear)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.hide)
        for w in (self.fix_btn, self.select_btn, hist):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(clear)
        row.addWidget(close)
        lay.addLayout(row)
        self.fixes: list[str | None] = []
        self.ids: list[list[int] | None] = []
        self.entries: list[tuple] = []  # newest first: (time, service, kind, text, fix, ref, ids)

    def add(self, service: str, kind: str, text: str, *, fix: str | None = None, ref: str | None = None,
            at: str | None = None, ids: list[int] | None = None) -> None:
        at = at or timeutil.now_utc().strftime("%H:%M:%S")
        self.entries.insert(0, (at, service, kind, text, fix, ref, ids))
        self.ids.insert(0, ids)
        self.table.insertRow(0)
        detail = text + (f"  ·  ref {ref}" if ref else "")
        cells = [at, service, tr(RESULT_LABELS.get(kind, kind)), detail]
        t = theme.tokens()
        color = {"ok": t.ok, "failed": t.bad, "skipped": t.warn, "cancelled": t.warn}.get(kind)
        for c, v in enumerate(cells):
            it = QTableWidgetItem(v)
            if c == 3:
                it.setToolTip(detail)
            if c == 2 and color:
                it.setForeground(QColor(color))
            self.table.setItem(0, c, it)
        self.fixes.insert(0, fix)
        self.table.resizeRowToContents(0)
        self.table.selectRow(0)

    def clear(self) -> None:
        self.table.setRowCount(0)
        self.fixes = []
        self.ids = []
        self.entries = []
        self.fix_btn.setEnabled(False)
        self.select_btn.setEnabled(False)

    def replay(self, entries: list[tuple]) -> None:
        """The rows of an earlier window (before a language switch), oldest added first."""
        for at, service, kind, text, fix, ref, ids in reversed(entries):
            self.add(service, kind, text, fix=fix, ref=ref, at=at, ids=ids)

    def texts(self) -> list[str]:
        return [self.table.item(r, 3).text() for r in range(self.table.rowCount())]

    def _current_fix(self) -> str | None:
        r = self.table.currentRow()
        return self.fixes[r] if 0 <= r < len(self.fixes) else None

    def _current_ids(self) -> list[int] | None:
        r = self.table.currentRow()
        return self.ids[r] if 0 <= r < len(self.ids) else None

    def _selected(self) -> None:
        self.fix_btn.setEnabled(bool(self._current_fix()))
        self.select_btn.setEnabled(bool(self._current_ids()))

    def _select_ids(self) -> None:
        ids = self._current_ids()
        if ids:
            self.select_requested.emit(list(ids))

    def _fix(self) -> None:
        page = self._current_fix()
        if page:
            self.prefs_requested.emit(page)


class ClearHistoryDialog(QDialog):
    """Choose which services' sync history to delete, e.g. only QRZ.com."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle(tr("Clear sync history"))
        lay = QVBoxLayout(self)
        lab = QLabel(tr("Delete the sync history of:"))
        lay.addWidget(lab)
        self.boxes: dict[str, QCheckBox] = {}
        counts = db.sync_history_counts()
        for name in [*apply.SERVICES.values(), *sorted(set(counts) - set(apply.SERVICES.values()))]:
            n = counts.get(name, 0)
            cb = QCheckBox(f"{name}  ({n} {tr('entries')})")
            cb.setEnabled(n > 0)
            cb.toggled.connect(self._update)
            self.boxes[name] = cb
            lay.addWidget(cb)
        hint = QLabel(tr("Only the history is deleted. Your QSOs, their upload and confirmation status, and where "
                         "the next download starts stay as they are: downloads continue from where they stopped. To "
                         "start over, use QSL → LoTW (or eQSL) → Download everything again…"))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = bb.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText(tr("Clear"))
        self.ok.setProperty("danger", True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._update()

    def chosen(self) -> list[str]:
        return [name for name, cb in self.boxes.items() if cb.isChecked()]

    def _update(self) -> None:
        self.ok.setEnabled(bool(self.chosen()))


def clear_history(db, parent) -> int:
    """Ask which services, confirm, and delete their sync history. Returns the rows deleted."""
    dlg = ClearHistoryDialog(db, parent)
    if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.chosen():
        return 0
    names = dlg.chosen()
    counts = db.sync_history_counts()
    what = ", ".join(f"{n} ({counts.get(n, 0)})" for n in names)
    if QMessageBox.question(parent, tr("Clear sync history"), tr("Delete the sync history of these services?") +
                            f"\n\n{what}\n\n" + tr("This can't be undone.")) != QMessageBox.StandardButton.Yes:
        return 0
    n = db.clear_sync_history(names)
    log.info("Sync history cleared for %s: %d rows", ", ".join(names), n)
    return n


class SyncController(QObject):
    message = Signal(str)
    prefs_requested = Signal(str)
    history_requested = Signal()
    idle = Signal()  # the last running upload or download has finished
    confirmed = Signal(str, list)  # service, the QSO ids a download confirmed for the first time
    select_requested = Signal(list)  # Sync results → Select them

    def __init__(self, db, prefs, parent: QWidget):
        super().__init__(parent)
        self.db, self.prefs, self.win = db, prefs, parent
        self.busy: set[str] = set()
        self.jobs: dict[str, tqsl.Job] = {}
        self.results: SyncResults | None = None
        self.queued: dict[str, set[int]] = {}  # service → QSOs uploaded when logged, waiting for the gap
        self._queue_timers: dict[str, QTimer] = {}

    def catalog(self) -> sats.SatCatalog:
        return sats.load(overrides=self.prefs.get("sat_names"), extra=self.prefs.get("user_sats"))

    # ------------------------------------------------------------------ outcomes
    def _window(self) -> SyncResults:
        if self.results is None:
            self.results = SyncResults(self.win)
            self.results.prefs_requested.connect(self.prefs_requested)
            self.results.select_requested.connect(self.select_requested)
            self.results.history_requested.connect(self.history_requested)
        return self.results

    def report(self, service: str, kind: str, text: Text, *, fix: str | None = None, ref: str | None = None,
               quiet: bool = False, secrets=(), log_detail: str = "", ids: list[int] | None = None) -> None:
        """One outcome: a row in Sync results, the status bar and the log file. ``quiet`` (upload on log) only
        brings the window up for a failure, and then without taking the keyboard from the entry form. The log
        file gets the text in English (when ``text`` is built with a translate function) and ``log_detail``.
        ``ids``: QSOs the row can select again (new confirmations)."""
        name = apply.SERVICES.get(service, service)
        english = self._safe(text(_as_is) if callable(text) else text, secrets)
        text = self._safe(text(tr), secrets) if callable(text) else english
        level = logging.WARNING if kind in ("failed", "skipped", "cancelled") else logging.INFO
        log.log(level, "%s %s%s: %s%s", name, RESULT_LABELS.get(kind, kind).lower(),
                f" (ref {ref})" if ref else "", english, f"; {log_detail}" if log_detail else "")
        w = self._window()
        w.add(name, kind, text, fix=fix, ref=ref, ids=ids or None)
        if not quiet:
            w.show()
            w.raise_()
            w.activateWindow()
        elif kind == "failed" and not w.isVisible():
            w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            w.show()
            w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.message.emit(f"{name}: {text.splitlines()[0] if text else tr(RESULT_LABELS.get(kind, kind))}")

    def running(self) -> list[str]:
        """What is still running, e.g. ["LoTW upload"], for the question when you quit."""
        out = []
        for key in sorted(self.busy):
            svc, d = key[:-2] if key.endswith("up") else key[:-4], "up" if key.endswith("up") else "down"
            out.append(f"{apply.SERVICES.get(svc, svc)} {tr(DIRECTIONS[d])}")
        return out

    def waiting(self) -> list[str]:
        """Uploads waiting for the gap, e.g. ["QRZ.com upload (2 QSOs waiting)"], for the question when you quit."""
        return [f"{apply.SERVICES.get(s, s)} {tr('upload')} ({len(ids)} {tr('QSOs waiting')})"
                for s, ids in sorted(self.queued.items()) if ids]

    def stop_timers(self) -> None:
        for t in self._queue_timers.values():
            t.stop()

    def cancel_all(self) -> None:
        """Stop every TQSL run and waiting upload now (quitting); network requests end with their timeout on a
        daemon thread. Waiting QSOs stay pending: Upload pending sends them later."""
        self.stop_timers()
        for key, job in list(self.jobs.items()):
            log.warning("Stopping %s (NWOlogger is quitting)", key)
            job.stop()

    # ------------------------------------------------------------------ helpers
    def _need(self, service: str, *pairs: tuple[str, str | None], quiet: bool = False) -> bool:
        missing = [label for label, v in pairs if not v]
        if missing:
            self.report(service, "skipped", lambda t: t("Set these in Preferences → Services first:") + " " +
                        ", ".join(t(m) for m in missing), fix="Services", quiet=quiet)
            return False
        return True

    def _progress(self, text: str, cancel: str | None = None) -> QProgressDialog:
        dlg = QProgressDialog(text, cancel or tr("Hide"), 0, 0, self.win)
        dlg.setWindowTitle("NWOlogger")
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        dlg.setMinimumDuration(300)
        dlg.show()
        return dlg

    @staticmethod
    def _safe(msg: str, secrets=()) -> str:
        """Every message shown or stored goes through here: no URL query, password, key or upload code."""
        return net.redact(msg, secrets)

    def _secret(self, key: str) -> str | None:
        v = self.prefs.secret(key)
        applog.remember_secrets([v])
        return v

    def _account(self, key: str) -> str | None:
        """An account user name: part of your credentials, so the log file masks it like a password."""
        v = self.prefs.get(key)
        applog.remember_secrets([v])
        return v

    def _log(self, service: str, direction: str, started: str, secrets=(), **kw) -> None:
        if "message" in kw:
            kw["message"] = self._safe(kw["message"], secrets)
        self.db.sync_log_add(apply.SERVICES[service], direction, started, **kw)

    # ------------------------------------------------------------------ pacing
    def gap_seconds(self) -> float:
        return 60.0 * max(1, int(self.prefs.get("sync_gap_minutes") or 1))

    def wait_left(self, service: str, direction: str) -> float:
        """Seconds until the next run in this direction may reach this service (0: now). The time of the last run
        is kept in the log file, so quitting doesn't shorten the wait."""
        last = timeutil.parse_iso(self.db.sync_get(service, f"last_{direction}"))
        if last is None:
            return 0.0
        return max(0.0, self.gap_seconds() - (timeutil.now_utc() - last).total_seconds())

    def _too_soon(self, service: str, direction: str) -> bool:
        left = self.wait_left(service, direction)
        if left <= 0:
            return False
        ago, at = self.gap_seconds() - left, (timeutil.now_utc() + timedelta(seconds=left)).strftime("%H:%M:%S")
        last = "Too soon after the last download" if direction == "download" else "Too soon after the last upload"
        self.report(service, "skipped", lambda t: t(last) + f" ({_duration(t, ago)} " + t("ago") + "). " +
                    t("The next one is possible at") + f" {at} UTC.", fix="Services")
        return True

    def _queue(self, service: str, ids: list[int]) -> None:
        """Upload on log inside the gap (or while an upload runs): the QSOs wait and go together when it ends."""
        q = self.queued.setdefault(service, set())
        q.update(ids)
        timer = self._queue_timers.get(service)
        if timer is None:
            timer = self._queue_timers[service] = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda s=service: self._flush(s))
        if not timer.isActive():
            timer.start(int(max(1.0, self.wait_left(service, "upload")) * 1000) + 500)
        at = (timeutil.now_utc() + timedelta(milliseconds=timer.remainingTime())).strftime("%H:%M:%S")
        log.info("%s upload: %d QSOs wait until %s UTC", apply.SERVICES[service], len(q), at)
        self.message.emit(f"{apply.SERVICES[service]}: {len(q)} " + tr("QSOs wait for the next upload at") +
                          f" {at} UTC")

    def _flush(self, service: str) -> None:
        ids = sorted(self.queued.pop(service, set()))
        if ids:
            self.upload(service, ids, quiet=True)

    def _start(self, service: str, direction: str, what: str) -> str:
        ref = applog.new_ref()
        self.db.sync_set(service, f"last_{direction}", timeutil.now_iso())
        self.busy.add(service + ("down" if direction == "download" else "up"))
        log.info("%s %s started (ref %s): %s", apply.SERVICES[service], direction, ref, what)
        return ref

    def _end(self, service: str, direction: str) -> None:
        key = service + ("down" if direction == "download" else "up")
        self.busy.discard(key)
        self.jobs.pop(key, None)
        if not self.busy:
            self.idle.emit()

    def _fail(self, service: str, direction: str, started: str, dlg, msg: str, secrets=(), *,
              ref: str | None = None, quiet: bool = False) -> None:
        dlg.close()
        self._end(service, direction)
        msg = self._safe(msg, secrets)
        self._log(service, direction, started, errors=1, message=msg)
        low = msg.lower()
        fix = "Station profiles" if "nickname" in low or "station location" in low else \
            "Services" if any(w in low for w in ("password", "user", "http 401", "http 403", "key")) else None
        self.report(service, "failed", msg, fix=fix, ref=ref, quiet=quiet)

    def _profile_for(self, q: dict) -> dict | None:
        if q.get("station_profile_id"):
            p = self.db.profile(q["station_profile_id"])
            if p:
                return p
        for p in self.db.profiles():
            if p.get("station_callsign") and p["station_callsign"] == q.get("station_callsign"):
                return p
        return self.db.default_profile()

    def _show_unmatched(self, service: str, res: apply.ApplyResult) -> None:
        """Opened without blocking: other results keep arriving while you choose."""
        if not res.unmatched:
            return
        dlg = UnmatchedDialog(apply.SERVICES[service], res.unmatched, self.win)
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        dlg.accepted.connect(lambda: self._add_unmatched(service, dlg.selected()))
        dlg.finished.connect(dlg.deleteLater)
        dlg.show()

    def _add_unmatched(self, service: str, chosen: list[dict]) -> None:
        if chosen:
            r = importer.run(self.db, chosen, label=tr("Add from") + f" {apply.SERVICES[service]}")
            self.message.emit(tr("Added") + f" {r.added} " + tr("QSOs"))

    # ------------------------------------------------------------------ upload
    def upload(self, service: str, ids: list[int] | None = None, *, quiet: bool = False) -> None:
        key = service + "up"
        if quiet and ids and (key in self.busy or self.wait_left(service, "upload") > 0):
            self._queue(service, ids)  # upload on log: never dropped, sent when the gap ends
            return
        if key in self.busy:
            if not quiet:
                self.message.emit(f"{apply.SERVICES[service]}: " + tr("an upload is already running"))
            return
        if self._too_soon(service, "upload"):
            return
        qsos = apply.pending(self.db, service, ids)
        if not qsos:
            if not quiet:
                self.report(service, "info", lambda t: t("Nothing to upload."))
            return
        if not quiet and len(qsos) > 1 and not ids:
            if QMessageBox.question(self.win, apply.SERVICES[service],
                                    f"{tr('Upload')} {len(qsos)} {tr('QSOs to')} {apply.SERVICES[service]}?"
                                    ) != QMessageBox.StandardButton.Yes:
                return
        getattr(self, f"_upload_{service}")(qsos, quiet)

    def _upload_lotw(self, qsos: list[dict], quiet: bool) -> None:
        path = tqsl.find_tqsl(self.prefs.get("tqsl_path"))
        if not path:
            self.report("lotw", "skipped", lambda t: t("TQSL was not found. Install TrustedQSL from arrl.org, or "
                                                       "set its location in Preferences → Services."),
                        fix="Services", quiet=quiet)
            return
        known = tqsl.station_locations()
        groups: dict[str, list[dict]] = defaultdict(list)
        missing_loc, unknown_loc = set(), set()
        for q in qsos:
            p = self._profile_for(q) or {}
            name = p.get("name") or q.get("station_callsign") or "?"
            loc = p.get("lotw_location")
            if not loc:
                missing_loc.add(name)
            elif known is not None and loc not in known:
                unknown_loc.add(f"{name} ({loc})")
            else:
                groups[loc].append(q)
        if missing_loc:
            self.report("lotw", "skipped", lambda t: t("Set the TQSL station location in these station profiles:") +
                        " " + ", ".join(sorted(missing_loc)), fix="Station profiles", quiet=quiet)
        if unknown_loc:
            self.report("lotw", "skipped", lambda t: t("TQSL has no station location with this name:") + " " +
                        ", ".join(sorted(unknown_loc)) + ". " + t("TQSL has:") + " " +
                        (", ".join(known) if known else t("no station locations")), fix="Station profiles",
                        quiet=quiet)
        if not groups:
            return
        cat = self.catalog()
        tmpdir = tempfile.mkdtemp(prefix="nwologger-lotw-")
        jobs = []
        for loc, qs in groups.items():
            recs = [apply.upload_record(q, "lotw", cat) for q in qs]
            fn = os.path.join(tmpdir, f"lotw-{abs(hash(loc))}.adi")
            adif.write_file(fn, recs, program_version=__version__)
            jobs.append((loc, fn, [q["id"] for q in qs]))
        started = timeutil.now_iso()
        ref = self._start("lotw", "upload", f"{sum(len(j[2]) for j in jobs)} QSOs, {len(jobs)} station location(s)")
        job = self.jobs["lotwup"] = tqsl.Job()
        dlg = self._progress(tr("Signing and uploading to LoTW with TQSL…") + "\n\n" +
                             tr("If TQSL asks for something, such as your certificate passphrase, answer it in "
                                "the TQSL window. Cancel stops TQSL."))
        stop = QPushButton(tr("Cancel"))
        dlg.setCancelButton(stop)
        stop.clicked.connect(job.stop)  # only the button: closing the dialog (e.g. on quit) must not stop TQSL

        def work():
            out = []
            try:
                for loc, fn, ids in jobs:
                    if job.stopped.is_set():
                        out.append((loc, ids, tqsl.CANCELLED, ""))
                        continue
                    code, text = tqsl.sign_and_upload(path, fn, loc, job=job)
                    out.append((loc, ids, code, self._safe(text)))
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
            return out

        def done(results):
            dlg.close()
            self._end("lotw", "upload")
            sent = 0
            for loc, ids, code, text in results:
                if code in tqsl.SENT_CODES:
                    apply.mark_sent(self.db, "lotw", ids)
                    sent += len(ids)
                kind = "ok" if code in tqsl.SENT_CODES else "cancelled" if code == tqsl.CANCELLED else "failed"
                tail = "\n" + "\n".join(text.splitlines()[-4:]) if kind == "failed" and text else ""
                n = len(ids)
                self.report("lotw", kind, lambda t, loc=loc, code=code, n=n, tail=tail:
                            f"{loc}: {tqsl.describe(code)} ({n} " + t("QSOs") + ")" + tail, ref=ref, quiet=quiet)
            errors = sum(1 for r in results if r[2] not in tqsl.SENT_CODES)
            self._log("lotw", "upload", started, sent=sent, errors=errors,
                      message="; ".join(f"{loc}: {tqsl.describe(code)}" for loc, _i, code, _t in results))
            self.message.emit(f"LoTW: {sent} " + tr("QSOs uploaded"))

        run_async(work, on_done=done,
                  on_error=lambda m: self._fail("lotw", "upload", started, dlg, m, ref=ref, quiet=quiet))

    def _upload_eqsl(self, qsos: list[dict], quiet: bool) -> None:
        user, pw = self._account("eqsl_user"), self._secret("eqsl_password")
        if not self._need("eqsl", ("eQSL user", user), ("eQSL password", pw), quiet=quiet):
            return
        ready, missing = [], set()
        for q in qsos:
            p = self._profile_for(q) or {}
            if p.get("eqsl_qth_nickname"):
                ready.append((q, p))
            else:
                missing.add(p.get("name") or q.get("station_callsign") or "?")
        if missing:
            self.report("eqsl", "skipped", lambda t: t("Set the eQSL QTH nickname in these station profiles:") +
                        " " + ", ".join(sorted(missing)), fix="Station profiles", quiet=quiet)
        if not ready:
            return
        cat = self.catalog()
        recs = [(q["id"], q.get("call"), apply.upload_record(q, "eqsl", cat, p)) for q, p in ready]
        chunks = [recs[i:i + 250] for i in range(0, len(recs), 250)]
        started = timeutil.now_iso()
        ref = self._start("eqsl", "upload", f"{len(recs)} QSOs")
        dlg = self._progress(tr("Uploading to eQSL…"))

        def work():
            out = []
            for ch in chunks:
                added, total, lines = services.eqsl_upload(user, pw, [r for _, _, r in ch])
                out.append((ch, added, total, lines))
            return out

        def done(results):
            dlg.close()
            self._end("eqsl", "upload")
            ok_ids, bad, problems = [], [], []
            for ch, _added, _total, lines in results:
                bad_calls = set()
                for ln in lines:
                    if ln.lower().startswith("error") or ("bad record" in ln.lower() and "duplicate" not in ln.lower()):
                        problems.append(ln)
                        for _, call, _ in ch:
                            if call and call in ln:
                                bad_calls.add(call)
                for qid, call, _ in ch:
                    (bad if call in bad_calls else ok_ids).append(qid)
            apply.mark_sent(self.db, "eqsl", ok_ids)
            self._log("eqsl", "upload", started, sent=len(ok_ids), errors=len(bad), secrets=[pw],
                      message="; ".join(problems)[:300])
            self.report("eqsl", "failed" if bad else "ok", lambda t: f"{len(ok_ids)} " + t("QSOs uploaded") +
                        (f", {len(bad)} " + t("rejected") + "\n" + "\n".join(problems[:3]) if bad else ""),
                        ref=ref, quiet=quiet, secrets=[pw])

        run_async(work, on_done=done, on_error=lambda m: self._fail("eqsl", "upload", started, dlg, m, [pw],
                                                                     ref=ref, quiet=quiet))

    def _upload_qrz(self, qsos: list[dict], quiet: bool) -> None:
        by_call: dict[str, list[dict]] = defaultdict(list)
        for q in qsos:
            by_call[(q.get("station_callsign") or "").upper()].append(q)
        keys = {c: self.prefs.qrz_api_key(c) for c in by_call}
        applog.remember_secrets(keys.values())
        missing = [c or "?" for c, k in keys.items() if not k]
        if missing:
            self.report("qrz", "skipped", lambda t: t("Add the QRZ Logbook API key for these station calls in "
                                                      "Preferences → Services:") + " " + ", ".join(missing),
                        fix="Services", quiet=quiet)
            if all(not k for k in keys.values()):
                return
        cat = self.catalog()
        jobs = [(q["id"], c, keys[c], apply.upload_record(q, "qrz", cat),
                 q.get("qrzcom_qso_upload_status") == "M") for c, qs in by_call.items() if keys.get(c) for q in qs]
        started = timeutil.now_iso()
        ref = self._start("qrz", "upload", f"{len(jobs)} QSOs")
        dlg = self._progress(tr("Uploading to QRZ.com Logbook…"))

        def work():
            out = []
            for qid, call, key, rec, replace in jobs:
                try:
                    ok, logid, reason = services.qrz_insert(key, rec, replace=replace, call=call)
                except Exception as e:  # noqa: BLE001
                    ok, logid, reason = False, None, str(e)
                out.append((qid, ok, logid, self._safe(reason, [key])))
            return out

        def done(results):
            dlg.close()
            self._end("qrz", "upload")
            ok = [r for r in results if r[1]]
            bad = [r for r in results if not r[1]]
            apply.mark_sent(self.db, "qrz", [r[0] for r in ok], logids={r[0]: r[2] for r in ok})
            reasons = "; ".join(sorted({r[3] for r in bad}))[:300]
            self._log("qrz", "upload", started, sent=len(ok), errors=len(bad), message=reasons)
            def text(t):
                return f"{len(ok)} " + t("QSOs uploaded") + (f"\n{len(bad)} " + t("failed") + ": " + reasons
                                                             if bad else "")

            self.report("qrz", "failed" if bad else "ok", text, ref=ref, quiet=quiet, secrets=list(keys.values()))

        run_async(work, on_done=done, on_error=lambda m: self._fail("qrz", "upload", started, dlg, m,
                                                                     list(keys.values()), ref=ref, quiet=quiet))

    def _upload_hrdlog(self, qsos: list[dict], quiet: bool) -> None:
        call, code = self._account("hrdlog_call"), self._secret("hrdlog_code")
        if not self._need("hrdlog", ("HRDLog callsign", call), ("HRDLog upload code", code), quiet=quiet):
            return
        cat = self.catalog()
        jobs = []
        for q in qsos:
            rec = apply.upload_record(q, "hrdlog", cat)
            key = None
            if q.get("hrdlog_qso_upload_status") == "M":
                on = timeutil.iso_to_adif(q.get("time_on"))
                key = f"{q.get('call')}{on[0]}{on[1][:4]}" if on else None
            jobs.append((q["id"], rec, key))
        started = timeutil.now_iso()
        ref = self._start("hrdlog", "upload", f"{len(jobs)} QSOs")
        dlg = self._progress(tr("Uploading to HRDLog…"))

        def work():
            out = []
            for qid, rec, key in jobs:
                try:
                    ok, msg = services.hrdlog_upload(call, code, rec, update_key=key)
                except Exception as e:  # noqa: BLE001
                    ok, msg = False, str(e)
                out.append((qid, ok, self._safe(msg, [code])))
            return out

        def done(results):
            dlg.close()
            self._end("hrdlog", "upload")
            ok = [r[0] for r in results if r[1]]
            bad = [r for r in results if not r[1]]
            apply.mark_sent(self.db, "hrdlog", ok)
            reasons = "; ".join(sorted({r[2] for r in bad}))[:300]
            self._log("hrdlog", "upload", started, sent=len(ok), errors=len(bad), message=reasons)
            def text(t):
                return f"{len(ok)} " + t("QSOs uploaded") + (f"\n{len(bad)} " + t("failed") + ": " + reasons
                                                             if bad else "")

            self.report("hrdlog", "failed" if bad else "ok", text, ref=ref, quiet=quiet, secrets=[code])

        run_async(work, on_done=done, on_error=lambda m: self._fail("hrdlog", "upload", started, dlg, m, [code],
                                                                     ref=ref, quiet=quiet))

    # ------------------------------------------------------------------ download
    def download(self, service: str) -> None:
        if service == "hrdlog":
            self.report("hrdlog", "info", lambda t: t("HRDLog.net has no public download API, so NWOlogger can "
                                                      "only upload to it. (It sends no confirmations back.)"))
            return
        if service + "down" in self.busy:
            self.message.emit(f"{apply.SERVICES[service]}: " + tr("a download is already running"))
            return
        if self._too_soon(service, "download"):
            return
        getattr(self, f"_download_{service}")()

    RESUME_KEYS = {"lotw": ("last_qsl", "last_qsorx"), "eqsl": ("rcvd_since",)}

    def download_all_again(self, service: str) -> None:
        """Forget where downloads resume, so the next one starts from your first QSO (QRZ.com always does)."""
        name = apply.SERVICES[service]
        took = self.db.sync_get(service, "full_seconds")
        if QMessageBox.question(
                self.win, tr("Download everything again"),
                f"{name}: " + tr("download everything again? The next download starts from your first QSO instead "
                                 "of where the last one stopped.") + " " +
                (tr("Last time this took") + f" {_duration(tr, float(took))}." if took else
                 tr("For a large log this can take several minutes."))) != QMessageBox.StandardButton.Yes:
            return
        for k in self.RESUME_KEYS[service]:
            self.db.sync_set(service, k, None)
        log.info("%s: download positions reset (download everything again)", name)
        self.download(service)

    def _download_lotw(self) -> None:
        user, pw = self._account("lotw_user"), self._secret("lotw_password")
        if not self._need("lotw", ("LoTW user", user), ("LoTW password", pw)):
            return
        since_qsl = self.db.sync_get("lotw", "last_qsl")
        since_rx = self.db.sync_get("lotw", "last_qsorx")
        started = timeutil.now_iso()
        ref = self._start("lotw", "download", f"confirmations since {since_qsl or 'the start'}, uploads since "
                                              f"{since_rx or 'the start'}")
        dlg = self._progress(tr("Downloading from LoTW…"))
        t0 = time.monotonic()

        def work():
            h1, r1 = services.lotw_download(user, pw, qsl=True, since=since_qsl)
            h2, r2 = services.lotw_download(user, pw, qsl=False, since=since_rx)
            return h1, r1, h2, r2

        def done(res):
            dlg.close()
            self._end("lotw", "download")
            h1, r1, h2, r2 = res
            t1 = time.monotonic()
            if not since_qsl and not since_rx:
                self.db.sync_set("lotw", "full_seconds", str(round(t1 - t0)))
            cat = self.catalog()
            up = apply.apply_lotw_uploads(self.db, r2, cat)
            a = apply.apply_lotw_qsls(self.db, r1, cat)
            timing = f"download {t1 - t0:.1f} s, apply {time.monotonic() - t1:.2f} s"
            if h1.get("APP_LOTW_LASTQSL"):
                self.db.sync_set("lotw", "last_qsl", h1["APP_LOTW_LASTQSL"])
            if h2.get("APP_LOTW_LASTQSORX"):
                self.db.sync_set("lotw", "last_qsorx", h2["APP_LOTW_LASTQSORX"])
            self._log("lotw", "download", started, received=a.total, matched=a.matched,
                      message=apply.summary(_as_is, a, new_one="new confirmation", new_many="new confirmations",
                                            none="No new confirmations", uploaded=up))
            log.info("LoTW download (ref %s): upload states %s", ref, apply.counts(up))
            self.report("lotw", "ok", lambda t: apply.summary(t, a, new_one="new confirmation",
                                                              new_many="new confirmations",
                                                              none="No new confirmations", uploaded=up),
                        ref=ref, ids=a.confirmed, log_detail=f"{apply.counts(a)}; {timing}")
            self.confirmed.emit("lotw", a.confirmed)
            self._show_unmatched("lotw", a)

        run_async(work, on_done=done, on_error=lambda m: self._fail("lotw", "download", started, dlg, m, [pw],
                                                                     ref=ref))

    def _download_eqsl(self) -> None:
        user, pw = self._account("eqsl_user"), self._secret("eqsl_password")
        if not self._need("eqsl", ("eQSL user", user), ("eQSL password", pw)):
            return
        profiles = self.db.profiles()
        nicks = sorted({p["eqsl_qth_nickname"] for p in profiles if p.get("eqsl_qth_nickname")})
        if not nicks:
            self.report("eqsl", "skipped", lambda t: t("Set the eQSL QTH nickname in your station profile (as "
                                                       "shown on eQSL.cc under My Profile). eQSL needs it when your "
                                                       "account has more than one QTH."), fix="Station profiles")
            return
        since = self.db.sync_get("eqsl", "rcvd_since")
        started = timeutil.now_iso()
        ref = self._start("eqsl", "download", f"{len(nicks)} QTH nickname(s), since {since or 'the start'}")
        dlg = self._progress(tr("Downloading from eQSL…"))
        t0 = time.monotonic()

        def work():
            out = []
            for n in nicks:
                _, recs = services.eqsl_download(user, pw, since=since, qth_nickname=n)
                out.extend(recs)
            return out

        def done(recs):
            dlg.close()
            self._end("eqsl", "download")
            t1 = time.monotonic()
            if not since:
                self.db.sync_set("eqsl", "full_seconds", str(round(t1 - t0)))
            a = apply.apply_eqsl(self.db, recs, self.catalog())
            timing = f"download {t1 - t0:.1f} s, apply {time.monotonic() - t1:.2f} s"
            # RcvdSince is a date: start the next download a day early, so cards that arrive later today aren't
            # missed (cards already applied just match again).
            self.db.sync_set("eqsl", "rcvd_since", (timeutil.now_utc() - timedelta(days=1)).strftime("%Y%m%d"))
            self._log("eqsl", "download", started, received=a.total, matched=a.matched,
                      message=apply.summary(_as_is, a, new_one="new card", new_many="new cards", none="No new cards"))
            self.report("eqsl", "ok", lambda t: apply.summary(t, a, new_one="new card", new_many="new cards",
                                                              none="No new cards"),
                        ref=ref, ids=a.confirmed, log_detail=f"{apply.counts(a)}; {timing}")
            self.confirmed.emit("eqsl", a.confirmed)
            self._show_unmatched("eqsl", a)

        run_async(work, on_done=done, on_error=lambda m: self._fail("eqsl", "download", started, dlg, m, [pw],
                                                                     ref=ref))

    def _download_qrz(self) -> None:
        calls = sorted({p.get("station_callsign") for p in self.db.profiles() if p.get("station_callsign")})
        keys = {c: self.prefs.qrz_api_key(c) for c in calls}
        keys = {c: k for c, k in keys.items() if k}
        applog.remember_secrets(keys.values())
        if not keys:
            self.report("qrz", "skipped", lambda t: t("Add a QRZ Logbook API key in Preferences → Services."),
                        fix="Services")
            return
        started = timeutil.now_iso()
        ref = self._start("qrz", "download", f"{len(keys)} logbook(s)")
        dlg = self._progress(tr("Downloading from QRZ.com Logbook…"))
        t0 = time.monotonic()

        def work():
            out = []
            for c, k in keys.items():
                out.extend(services.qrz_fetch(k, call=c))
            return out

        def done(recs):
            dlg.close()
            self._end("qrz", "download")
            t1 = time.monotonic()
            a = apply.apply_qrz(self.db, recs, self.catalog())
            timing = f"download {t1 - t0:.1f} s, apply {time.monotonic() - t1:.2f} s"
            self._log("qrz", "download", started, received=a.total, matched=a.matched,
                      message=apply.summary(_as_is, a, new_one="new confirmation", new_many="new confirmations",
                                            none="No new confirmations"))
            self.report("qrz", "ok", lambda t: apply.summary(t, a, new_one="new confirmation",
                                                             new_many="new confirmations",
                                                             none="No new confirmations"),
                        ref=ref, ids=a.confirmed, log_detail=f"{apply.counts(a)}; {timing}")
            self.confirmed.emit("qrz", a.confirmed)
            self._show_unmatched("qrz", a)

        run_async(work, on_done=done, on_error=lambda m: self._fail("qrz", "download", started, dlg, m,
                                                                     list(keys.values()), ref=ref))

    def upload_on_log(self, qid: int) -> None:
        on = self.prefs.get("upload_on_log") or {}
        for svc in ("qrz", "eqsl", "hrdlog"):
            if on.get(svc):
                self.upload(svc, [qid], quiet=True)
