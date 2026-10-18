"""Upload / download with LoTW, eQSL, QRZ.com and HRDLog. Network runs on worker threads; DB on the GUI thread.

Outcomes go to one non-modal Sync results window, never to message boxes: results that arrive together (Sync
everything starts three downloads) would otherwise stack modal boxes, and only the top one takes a click.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
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
from .sync_dialogs import FromDateDialog, NearDuplicatesDialog
from .widgets import run_async

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
    edit_requested = Signal(int)  # Possible duplicates → Edit my QSO

    def __init__(self, db, prefs, parent: QWidget):
        super().__init__(parent)
        self.db, self.prefs, self.win = db, prefs, parent
        self.busy: set[str] = set()
        self.progress: dict[str, QProgressDialog] = {}  # busy key → its progress window, shown or hidden
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

    def _progress(self, key: str, text: str, cancel: str | None = None) -> QProgressDialog:
        """The progress window of the run ``key`` (e.g. "lotwdown"); Hide only hides it."""
        dlg = QProgressDialog(text, cancel or tr("Hide"), 0, 0, self.win)
        dlg.setWindowTitle("NWOlogger")
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        dlg.setMinimumDuration(300)
        dlg.show()
        self.progress[key] = dlg
        return dlg

    def _still_running(self, names: list[str], direction: str) -> None:
        """Asked again while a run is going: say so, skip the request, and show the running one's progress window
        again (it may have been hidden)."""
        key = "down" if direction == "download" else "up"
        log.info("%s %s asked for again while one is still running: skipped",
                 ", ".join(apply.SERVICES[s] for s in names), direction)
        QMessageBox.warning(self.win, "NWOlogger", ", ".join(apply.SERVICES[s] for s in names) + ": " + tr(
            "another download is still in progress." if key == "down" else "another upload is still in progress.") +
            "\n\n" + tr("This request is skipped. The progress window of the one still running is shown again."))
        for service in names:
            dlg = self.progress.get(service + key)
            if dlg is not None:
                dlg.show()
                dlg.raise_()
                dlg.activateWindow()

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
        if direction != "download":
            self.db.sync_set(service, f"last_{direction}", timeutil.now_iso())
        self.busy.add(service + ("down" if direction == "download" else "up"))
        log.info("%s %s started (ref %s): %s", apply.SERVICES[service], direction, ref, what)
        return ref

    def _end(self, service: str, direction: str) -> None:
        key = service + ("down" if direction == "download" else "up")
        self.busy.discard(key)
        self.jobs.pop(key, None)
        self.progress.pop(key, None)
        if not self.busy:
            self.idle.emit()

    def _fail(self, service: str, direction: str, started: str, dlg, msg: str, secrets=(), *,
              ref: str | None = None, quiet: bool = False) -> None:
        self._close(dlg)
        self._end(service, direction)
        msg = self._safe(msg, secrets)
        self._log(service, direction, started, errors=1, message=msg)
        low = msg.lower()
        fix = "Station profiles" if "nickname" in low or "station location" in low else \
            "Services" if any(w in low for w in ("password", "user", "http 401", "http 403", "key")) else None
        self.report(service, "failed", msg, fix=fix, ref=ref, quiet=quiet)

    def _apply_download(self, service: str, started: str, dlg, save: Callable, *, ref: str, secrets=()):
        """Commit downloaded states and resume positions before starting the successful-download guard."""
        try:
            with self.db.batch(f"{apply.SERVICES[service]} download"):
                result = save()
                self.db.sync_set(service, "last_download", started)
        except Exception as e:  # noqa: BLE001 - a failed local apply must also be retryable
            self._fail(service, "download", started, dlg, str(e), secrets, ref=ref)
            return None
        self._close(dlg)
        self._end(service, "download")
        return result

    def _profile_for(self, q: dict) -> dict | None:
        if q.get("station_profile_id"):
            p = self.db.profile(q["station_profile_id"])
            if p:
                return p
        for p in self.db.profiles():
            if p.get("station_callsign") and p["station_callsign"] == q.get("station_callsign"):
                return p
        return self.db.default_profile()

    def lotw_report_applied(self, name: str, res: apply.ApplyResult) -> None:
        """A LoTW report imported from a file and applied as a download: its summary in Sync results, its new
        confirmations selected, and the records not in the log added (or held as possible duplicates)."""
        added, held = self._settle_unmatched("lotw", res.unmatched)

        def text(t) -> str:
            return f"{name}: " + apply.summary(t, res, new_one="new confirmation", new_many="new confirmations",
                                               none="No new confirmations", added=len(added), held=held)

        self.report("lotw", "ok", text, ids=res.confirmed + added,
                    log_detail=f"LoTW report imported; {apply.counts(res)}; added {len(added)}, held {held}")
        self.confirmed.emit("lotw", res.confirmed)

    # ------------------------------------------------------------------ records a download could not match
    def _settle_unmatched(self, service: str, unmatched: list[dict], *, ref: str | None = None
                          ) -> tuple[list[int], int]:
        """Records a download could not match: added to the log after a backup, except those within a minute of a
        QSO of the same call (a near-duplicate: the same contact logged with another band, mode or satellite),
        which wait in a window for your decision. Returns (ids added, number held)."""
        if not unmatched:
            return [], 0
        fresh, held = [], []
        for q in unmatched:
            near = importer.near_qsos(self.db, q)
            if near:
                held.append((q, near[0]))
            else:
                fresh.append(q)
        added = self._add_records(service, fresh) if fresh else []
        if held:
            self._show_near_duplicates(service, held, added=len(added))
        if added or held:
            log.info("%s%s: %d records added to the log, %d held as possible duplicates", apply.SERVICES[service],
                     f" (ref {ref})" if ref else "", len(added), len(held))
        return added, len(held)

    def _add_records(self, service: str, recs: list[dict]) -> list[int]:
        """Add downloaded records as QSOs; a backup is saved first. A record that matches a QSO meanwhile (added
        through another service's window) updates it instead. The records are sorted before anything is inserted,
        so two distinct downloaded QSOs within the matcher's time tolerance are still added separately. Returns
        the ids of the QSOs added."""
        if not recs:
            return []
        existing, missing = [], []
        for q in recs:
            (missing if importer.find_match(self.db, q) is None else existing).append(q)
        label = tr("Add from") + f" {apply.SERVICES[service]}"
        windows = {"block_s": self.prefs.get("block_seconds"), "warn_s": self.prefs.warn_seconds()}
        if existing:
            importer.run(self.db, existing, merge=True, label=label, **windows)
        if not missing:
            return []
        return importer.run(self.db, missing, label=label, backup=not existing, **windows).ids

    def _added_later(self, service: str, recs: list[dict]) -> None:
        """Records ticked in the Possible duplicates window."""
        if not recs:
            return
        ids = self._add_records(service, recs)
        text = tr("Added") + f" {len(ids)} " + tr("QSOs")
        if len(recs) - len(ids):
            text += f" · {len(recs) - len(ids)} " + tr("QSOs updated")
        self.message.emit(text)
        if ids:
            self.select_requested.emit(ids)

    def _show_near_duplicates(self, service: str, held: list[tuple[dict, dict]], *, added: int = 0) -> None:
        """Opened without blocking: other results keep arriving while you choose."""
        dlg = NearDuplicatesDialog(self.db, apply.SERVICES[service], held, self.win, added=added)
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        dlg.add_requested.connect(lambda recs, s=service: self._added_later(s, recs))
        dlg.edit_requested.connect(self.edit_requested)
        dlg.message.connect(self.message)
        dlg.finished.connect(dlg.deleteLater)
        dlg.show()

    # ------------------------------------------------------------------ upload
    def upload(self, service: str, ids: list[int] | None = None, *, quiet: bool = False) -> None:
        key = service + "up"
        if quiet and ids and (key in self.busy or self.wait_left(service, "upload") > 0):
            self._queue(service, ids)  # upload on log: never dropped, sent when the gap ends
            return
        if key in self.busy:
            if not quiet:
                self._still_running([service], "upload")
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
        dlg = self._progress("lotwup", tr("Signing and uploading to LoTW with TQSL…") + "\n\n" +
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
            self._close(dlg)
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
        dlg, stop = self._upload_progress("eqslup", tr("Uploading to eQSL…"), len(recs))
        marks = _Marks(self.db, "eqsl")
        state = {"ok": [], "bad": [], "problems": [], "stopped": False}

        def work(event):
            for ch in chunks:
                if stop.is_set():
                    return "stopped"
                added, total, lines = services.eqsl_upload(user, pw, [r for _, _, r in ch])
                event((ch, added, total, lines))
            return "done"

        def chunk_done(result):
            ch, _added, _total, lines = result
            bad_calls = set()
            for ln in lines:
                if ln.lower().startswith("error") or ("bad record" in ln.lower() and "duplicate" not in ln.lower()):
                    state["problems"].append(ln)
                    for _, call, _ in ch:
                        if call and call in ln:
                            bad_calls.add(call)
            for qid, call, _ in ch:
                (state["bad"] if call in bad_calls else state["ok"]).append(qid)
            marks.add([qid for qid, call, _ in ch if call not in bad_calls])
            marks.flush()  # a chunk the service accepted is marked at once: a broken connection keeps it
            self._say(dlg, tr("Uploading to eQSL…") + f"\n{len(state['ok']) + len(state['bad']):,} / {len(recs):,}")

        def finish(outcome):
            self._close(dlg)
            self._end("eqsl", "upload")
            ok, bad, problems = state["ok"], state["bad"], state["problems"]
            left = len(recs) - len(ok) - len(bad)
            self._log("eqsl", "upload", started, sent=len(ok), errors=len(bad), secrets=[pw],
                      message=("; ".join(problems)[:300]) if problems else
                      (f"stopped with {left} QSOs still to send" if outcome == "stopped" else ""))
            kind = "failed" if bad else "cancelled" if outcome == "stopped" else "ok"
            self.report("eqsl", kind, lambda t: f"{len(ok)} " + t("QSOs uploaded") +
                        (f", {len(bad)} " + t("rejected") + "\n" + "\n".join(problems[:3]) if bad else "") +
                        (f" · {left} " + t("still to send (Upload pending sends them)") if left else ""),
                        ref=ref, quiet=quiet, secrets=[pw])

        def failed(msg):
            marks.flush()
            self._close(dlg)
            self._end("eqsl", "upload")
            sent = len(state["ok"])
            self._log("eqsl", "upload", started, sent=sent, errors=1, secrets=[pw],
                      message=f"{msg}; {sent} QSOs marked as uploaded before it")
            self.report("eqsl", "failed", lambda t: self._safe(msg, [pw]) + f" · {sent} " +
                        t("QSOs were accepted before it and are marked as uploaded; Upload pending sends the rest."),
                        fix="Services" if "password" in msg.lower() else None, ref=ref, quiet=quiet)

        run_async(work, on_done=finish, on_event=chunk_done, on_error=failed)

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
        self._upload_one_by_one("qrz", jobs, quiet, secrets=list(keys.values()),
                                text=tr("Uploading to QRZ.com Logbook…"),
                                send=lambda job: services.qrz_insert(job[2], job[3], replace=job[4], call=job[1]))

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

        def send(job):
            ok, msg = services.hrdlog_upload(call, code, job[1], update_key=job[2])
            return ok, None, msg

        self._upload_one_by_one("hrdlog", jobs, quiet, secrets=[code], text=tr("Uploading to HRDLog…"), send=send)

    def _upload_one_by_one(self, service: str, jobs: list, quiet: bool, *, secrets: list, text: str,
                           send: Callable) -> None:
        """QRZ.com and HRDLog take one record per request. Each QSO is marked as uploaded as soon as the service
        accepts it (in small batches, so the log table is not refreshed for every record), so a connection lost
        half-way keeps what was sent and *Stop* keeps it too; the rest stays pending."""
        started = timeutil.now_iso()
        ref = self._start(service, "upload", f"{len(jobs)} QSOs")
        dlg, stop = self._upload_progress(service + "up", text, len(jobs))
        marks = _Marks(self.db, service)
        state = {"ok": 0, "bad": [], "stopped": False}

        def work(event):
            for job in jobs:
                if stop.is_set():
                    return "stopped"
                try:
                    ok, logid, reason = send(job)
                except Exception as e:  # noqa: BLE001 - one record's failure is reported with it
                    ok, logid, reason = False, None, str(e)
                event((job[0], ok, logid, self._safe(reason or "", secrets)))
            return "done"

        def one_done(result):
            qid, ok, logid, reason = result
            if ok:
                state["ok"] += 1
                marks.add([qid], logid=logid)
            else:
                state["bad"].append(reason)
            if marks.pending >= 25:
                marks.flush()
            self._say(dlg, text + f"\n{state['ok'] + len(state['bad']):,} / {len(jobs):,}")

        def finish(outcome):
            marks.flush()
            self._close(dlg)
            self._end(service, "upload")
            ok, bad = state["ok"], state["bad"]
            left = len(jobs) - ok - len(bad)
            reasons = "; ".join(sorted({r for r in bad if r}))[:300]
            self._log(service, "upload", started, sent=ok, errors=len(bad),
                      message=reasons or (f"stopped with {left} QSOs still to send" if outcome == "stopped" else ""))
            kind = "failed" if bad else "cancelled" if outcome == "stopped" else "ok"
            self.report(service, kind, lambda t: f"{ok} " + t("QSOs uploaded") +
                        (f"\n{len(bad)} " + t("failed") + ": " + reasons if bad else "") +
                        (f" · {left} " + t("still to send (Upload pending sends them)") if left else ""),
                        ref=ref, quiet=quiet, secrets=secrets)

        def failed(msg):
            marks.flush()
            self._close(dlg)
            self._end(service, "upload")
            self._log(service, "upload", started, sent=state["ok"], errors=1,
                      message=f"{msg}; {state['ok']} QSOs marked as uploaded before it")
            self.report(service, "failed", lambda t: self._safe(msg, secrets) + f" · {state['ok']} " +
                        t("QSOs were accepted before it and are marked as uploaded; Upload pending sends the rest."),
                        ref=ref, quiet=quiet)

        run_async(work, on_done=finish, on_event=one_done, on_error=failed)

    def _upload_progress(self, key: str, text: str, count: int) -> tuple[QProgressDialog, threading.Event]:
        """An upload's progress window with a Stop button, which keeps what was sent, and the event it sets."""
        stop = threading.Event()
        dlg = self._progress(key, text + f"\n0 / {count:,}", cancel=tr("Stop"))
        if hasattr(dlg, "canceled"):
            dlg.canceled.connect(stop.set)
        return dlg, stop

    @staticmethod
    def _close(dlg) -> None:
        """Close a progress window that may already be gone (a run ending after its window's parent was deleted)."""
        try:
            dlg.close()
        except RuntimeError:
            pass

    @staticmethod
    def _say(dlg, text: str) -> None:
        if hasattr(dlg, "setLabelText"):
            dlg.setLabelText(text)

    # ------------------------------------------------------------------ download
    def download(self, service: str) -> None:
        self.downloads([service])

    def downloads(self, names: list[str]) -> None:
        """Start these downloads (Sync everything asks for three); the ones still running get one warning."""
        running = [s for s in names if s + "down" in self.busy]
        for service in names:
            if service in running:
                continue
            if service == "hrdlog":
                self.report("hrdlog", "info", lambda t: t("HRDLog.net has no public download API, so NWOlogger "
                                                          "can only upload to it. (It sends no confirmations back.)"))
            elif not self._too_soon(service, "download"):
                getattr(self, f"_download_{service}")()
        if running:
            self._still_running(running, "download")

    RESUME_KEYS = {"lotw": ("last_qsl", "last_qsorx"), "eqsl": ("rcvd_since",)}
    LOTW_PARTS_KEYS = ("run", "parts_floor", "parts_start", "parts_done", "parts_since_rx", "parts_since_qsl")
    QRZ_RESUME = "after_logid:"  # + the station call: the log id an unfinished QRZ.com run continues from

    @staticmethod
    def new_run() -> str:
        """The id of a download run, which names its files in downloads/."""
        return timeutil.now_utc().strftime("%Y%m%d-%H%M%S")

    def reset_positions(self, service: str) -> None:
        """Forget where this service's downloads resume, and any run of it left unfinished."""
        for k in self.RESUME_KEYS.get(service, ()):
            self.db.sync_set(service, k, None)
        if service == "lotw":
            for k in self.LOTW_PARTS_KEYS:
                self.db.sync_set("lotw", k, None)
        if service == "qrz":
            for k in self.db.sync_keys("qrz", self.QRZ_RESUME):
                self.db.sync_set("qrz", k, None)

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
        self.reset_positions(service)
        log.info("%s: download positions reset (download everything again)", name)
        self.download(service)

    def download_from_date(self, service: str = "lotw") -> None:
        """QSL → LoTW → Download from a date…: a first download limited to QSOs made on or after a day, in yearly
        parts from this year back to it; the resume positions are set at its end like a full download's."""
        dlg = FromDateDialog(self.win)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        day = dlg.date()
        self.reset_positions(service)
        self.db.sync_set("lotw", "parts_floor", str(day.year))
        self.db.sync_set("lotw", "parts_start", day.isoformat())
        log.info("LoTW: download positions reset; the next download asks for QSOs made from %s on", day.isoformat())
        self.download(service)

    def lotw_floor_year(self) -> int | None:
        """The earliest year a first LoTW download asks for: the year of your first QSO (None for an empty log)."""
        first = self.db.first_qso_time()
        return int(first[:4]) if first else None

    def _download_lotw(self) -> None:
        user, pw = self._account("lotw_user"), self._secret("lotw_password")
        if not self._need("lotw", ("LoTW user", user), ("LoTW password", pw)):
            return
        since_qsl = self.db.sync_get("lotw", "last_qsl")
        since_rx = self.db.sync_get("lotw", "last_qsorx")
        unfinished = self.db.sync_get("lotw", "run")
        floor = self.db.sync_get("lotw", "parts_floor") or self.lotw_floor_year()
        if unfinished or (not since_qsl and not since_rx and floor is not None):
            self._download_lotw_parts(user, pw, int(floor) if floor is not None else timeutil.now_utc().year)
        else:
            self._download_lotw_once(user, pw, since_qsl, since_rx)

    def _apply_lotw_reports(self, h1, r1, h2, r2) -> tuple[apply.ApplyResult, apply.ApplyResult]:
        """Apply the accepted-QSO report (upload states) and the confirmations, and move the positions to LoTW's
        header values. Upload states only update existing contacts: on an empty log the records not in the log
        already carry their uploaded flag."""
        cat = self.catalog()
        up = apply.apply_lotw_uploads(self.db, r2, cat) if self.db.count() else apply.ApplyResult()
        a = apply.apply_lotw_qsls(self.db, r1, cat)
        if h1.get("APP_LOTW_LASTQSL"):
            self.db.sync_set("lotw", "last_qsl", h1["APP_LOTW_LASTQSL"])
        if h2.get("APP_LOTW_LASTQSORX"):
            self.db.sync_set("lotw", "last_qsorx", h2["APP_LOTW_LASTQSORX"])
        return up, a

    def _finish_lotw(self, a: apply.ApplyResult, up: apply.ApplyResult, started: str, ref: str, timing: str) -> None:
        added, held = self._settle_unmatched("lotw", a.unmatched, ref=ref)

        def text(t) -> str:
            return apply.summary(t, a, new_one="new confirmation", new_many="new confirmations",
                                 none="No new confirmations", uploaded=up, added=len(added), held=held)

        self._log("lotw", "download", started, received=a.total, matched=a.matched, message=text(_as_is))
        log.info("LoTW download (ref %s): upload states %s", ref, apply.counts(up))
        self.report("lotw", "ok", text, ref=ref, ids=a.confirmed + added, log_detail=f"{apply.counts(a)}; {timing}")
        self.confirmed.emit("lotw", a.confirmed)

    def _download_lotw_once(self, user: str, pw: str, since_qsl: str | None, since_rx: str | None) -> None:
        """One request for confirmations and one for uploads (an incremental download, or the first one on an
        empty log, which has no year to go back to), streamed to files in downloads/ and applied together."""
        started = timeutil.now_iso()
        run = self.new_run()
        ref = self._start("lotw", "download", f"confirmations since {since_qsl or 'the start'}, uploads since "
                                              f"{since_rx or 'the start'}")
        dlg = self._progress("lotwdown", tr("Downloading from LoTW…"))
        t0 = time.monotonic()

        def work(event):
            return services.lotw_sync_download(
                user, pw, since_qsl=since_qsl, since_rx=since_rx, folder=self.db.downloads_dir, run=run,
                progress=lambda got, _chunk: event(got))

        def done(res):
            h1, r1, h2, r2 = res
            t1 = time.monotonic()

            def save():
                if not since_qsl and not since_rx:
                    self.db.sync_set("lotw", "full_seconds", str(round(t1 - t0)))
                self.db.sync_set("lotw", "last_run", run)
                return self._apply_lotw_reports(h1, r1, h2, r2)

            saved = self._apply_download("lotw", started, dlg, save, ref=ref, secrets=[pw])
            if saved is None:
                return
            up, a = saved
            self._finish_lotw(a, up, started, ref, f"download {t1 - t0:.1f} s, apply {time.monotonic() - t1:.2f} s")
            self.db.prune_downloads("lotw", keep_runs=[run])

        run_async(work, on_done=done, on_error=lambda m: self._fail("lotw", "download", started, dlg, m, [pw], ref=ref),
                  on_event=lambda got: self._say(dlg, tr("Downloading from LoTW…") + f"\n{got / 1e6:.1f} MB"))

    def _download_lotw_parts(self, user: str, pw: str, floor: int) -> None:
        """A first download in yearly parts, newest year first, each applied as it arrives and recorded, so a
        broken connection or a quit loses one part at most and the next download continues with the years still
        missing. One short request at the end fetches what LoTW received while the parts ran and sets the
        positions, which until then stay unset: the run can always be resumed."""
        db = self.db
        run = db.sync_get("lotw", "run") or self.new_run()
        start_date = db.sync_get("lotw", "parts_start")
        done_years = {int(y) for y in (db.sync_get("lotw", "parts_done") or "").split(",") if y.strip()}
        this_year = timeutil.now_utc().year
        years = [y for y in range(this_year, floor - 1, -1) if y not in done_years]
        db.sync_set("lotw", "run", run)
        db.sync_set("lotw", "parts_floor", str(floor))
        started = timeutil.now_iso()
        ref = self._start("lotw", "download", f"yearly parts: {len(years)} to fetch, back to {floor}"
                                              f"{' from ' + start_date if start_date else ''}, {len(done_years)} done")
        dlg = self._progress("lotwdown", tr("Downloading from LoTW…"))
        t0 = time.monotonic()
        totals = _Totals()
        state = {"year": None, "records": 0}

        def work(event):
            for i, year in enumerate(years):
                dest = db.download_path("lotw", run, str(year))
                start = start_date if start_date and year == floor else f"{year}-01-01"
                partial = None
                try:
                    header, records = services.lotw_download(
                        user, pw, qsl=False, start=start, end=f"{year}-12-31", dest=dest,
                        progress=lambda got, chunk, year=year, i=i: event(
                            {"kind": "bytes", "year": year, "index": i, "bytes": got,
                             "records": chunk.lower().count(b"<eor>")}))
                except net.PartialDownload as e:
                    partial = str(e)
                    try:
                        header, records, _info = services.read_report(e.path, partial=True)
                    except services.ServiceError:
                        header, records = {}, []
                event({"kind": "part", "year": year, "index": i, "total": len(years), "header": header,
                       "records": records, "info": services.report_info(dest), "partial": partial})
                if partial:
                    raise services.ServiceError(partial)
            return len(years)

        def on_event(ev):
            if ev["kind"] == "bytes":
                if ev["year"] != state["year"]:
                    state.update(year=ev["year"], records=0)
                state["records"] += ev["records"]
                self._say(dlg, tr("Downloading from LoTW…") + f"\n{ev['year']} · " + tr("part") +
                          f" {ev['index'] + 1} / {len(years)} · {totals.records + state['records']:,} " +
                          tr("records") + f" · {(totals.bytes + ev['bytes']) / 1e6:.1f} MB")
            elif ev["kind"] == "part":
                self._lotw_part_arrived(ev, run, done_years, totals, ref)

        def stopped(msg):
            if totals.broken:
                self._fail("lotw", "download", started, dlg, totals.broken, [pw], ref=ref)
                return
            if not done_years and not totals.records:  # nothing arrived: a plain failure, as before
                self._fail("lotw", "download", started, dlg, msg, [pw], ref=ref)
                return
            self._close(dlg)
            self._end("lotw", "download")
            next_year = next((y for y in years if y not in done_years), None)
            added, held = self._settle_unmatched("lotw", totals.unmatched, ref=ref)
            res = totals.result()
            done_text = ", ".join(str(y) for y in sorted(done_years, reverse=True))
            self._log("lotw", "download", started, received=res.total, matched=res.matched, errors=1,
                      message=f"{msg}; stopped after {len(done_years)} parts ({done_text or 'none'})")

            def text(t) -> str:
                return self._safe(msg, [pw]) + ". " + t("Years done:") + " " + (done_text or t("none")) + " · " + \
                    apply.summary(t, res, new_one="new confirmation", new_many="new confirmations",
                                  none="No new confirmations", added=len(added), held=held) + \
                    (". " + t("Download from LoTW continues with") + f" {next_year}." if next_year else "")

            self.report("lotw", "cancelled", text, ref=ref, ids=res.confirmed + added)
            self.confirmed.emit("lotw", res.confirmed)
            db.prune_downloads("lotw", keep_runs=[run, db.sync_get("lotw", "last_run")])

        def fetched(_n):
            if totals.broken:  # a part could not be applied: the run ends as a failure, to be retried
                self._fail("lotw", "download", started, dlg, totals.broken, [pw], ref=ref)
                return
            self._lotw_close_parts(user, pw, run, started, ref, dlg, totals, t0)

        run_async(work, on_done=fetched, on_event=on_event, on_error=stopped)

    def _lotw_part_arrived(self, ev: dict, run: str, done_years: set[int], totals: _Totals, ref: str) -> None:
        """One yearly part is in: apply it (every record is a QSO LoTW holds, a confirmed one with LoTW's date),
        record the year as done, and take the closing request's starting points from the first (newest) part."""
        year, records = ev["year"], ev["records"]
        if totals.broken:
            return
        try:
            with self.db.batch(f"LoTW part {year}"):
                res = apply.apply_lotw_report(self.db, records, self.catalog())
                if not ev["partial"]:
                    done_years.add(year)
                    self.db.sync_set("lotw", "parts_done", ",".join(str(y) for y in sorted(done_years, reverse=True)))
                    if self.db.sync_get("lotw", "parts_since_rx") is None:
                        header, info = ev["header"], ev["info"]
                        since_rx = header.get("APP_LOTW_LASTQSORX") or info.get("generated")
                        stamps = [timeutil.parse_iso(r.get("APP_LOTW_RXQSL")) for r in records
                                  if (r.get("QSL_RCVD") or "").upper() == "Y"]
                        stamps = [s for s in stamps if s is not None]
                        since_qsl = max(stamps).strftime("%Y-%m-%d %H:%M:%S") if stamps else info.get("generated")
                        if since_rx:
                            self.db.sync_set("lotw", "parts_since_rx", since_rx)
                        if since_qsl:
                            self.db.sync_set("lotw", "parts_since_qsl", since_qsl)
        except Exception as e:  # noqa: BLE001 - a part that can't be applied ends the run as a failure
            log.exception("LoTW part %s (ref %s) could not be applied", year, ref)
            totals.broken = str(e)
            return
        totals.add(res, records=len(records))
        log.info("LoTW part %s (ref %s): %s%s", year, ref, apply.counts(res), " (partial)" if ev["partial"] else "")

    def _lotw_close_parts(self, user: str, pw: str, run: str, started: str, ref: str, dlg, totals: _Totals,
                          t0: float) -> None:
        """Every part is in: one short request for what LoTW received while they ran, then the positions."""
        since_rx = self.db.sync_get("lotw", "parts_since_rx")
        since_qsl = self.db.sync_get("lotw", "parts_since_qsl") or since_rx
        since_rx = since_rx or since_qsl
        self._say(dlg, tr("Downloading from LoTW…") + "\n" + tr("Last step: what LoTW received while the parts ran"))

        def work():
            return services.lotw_sync_download(user, pw, since_qsl=since_qsl, since_rx=since_rx,
                                               folder=self.db.downloads_dir, run=run)

        def done(res):
            h1, r1, h2, r2 = res
            t1 = time.monotonic()

            def save():
                up, a = self._apply_lotw_reports(h1, r1, h2, r2)
                if since_qsl and not self.db.sync_get("lotw", "last_qsl"):
                    self.db.sync_set("lotw", "last_qsl", since_qsl)
                if since_rx and not self.db.sync_get("lotw", "last_qsorx"):
                    self.db.sync_set("lotw", "last_qsorx", since_rx)
                for k in self.LOTW_PARTS_KEYS:
                    self.db.sync_set("lotw", k, None)
                self.db.sync_set("lotw", "full_seconds", str(round(t1 - t0)))
                self.db.sync_set("lotw", "last_run", run)
                return up, a

            saved = self._apply_download("lotw", started, dlg, save, ref=ref, secrets=[pw])
            if saved is None:
                return
            up, a = saved
            totals.add(a)
            self._finish_lotw(totals.result(), up, started, ref, f"download {t1 - t0:.1f} s in parts")
            self.db.prune_downloads("lotw", keep_runs=[run])

        def failed(msg):
            self._close(dlg)
            self._end("lotw", "download")
            res = totals.result()
            added, held = self._settle_unmatched("lotw", res.unmatched, ref=ref)
            self._log("lotw", "download", started, received=res.total, matched=res.matched, errors=1,
                      message=f"{msg}; every part is applied, the last step is still to do")
            self.report("lotw", "cancelled", lambda t: self._safe(msg, [pw]) + ". " + apply.summary(
                t, res, new_one="new confirmation", new_many="new confirmations", none="No new confirmations",
                added=len(added), held=held) + ". " + t("Every part is applied; Download from LoTW does the last "
                                                        "step."), ref=ref, ids=res.confirmed + added)
            self.confirmed.emit("lotw", res.confirmed)

        run_async(work, on_done=done, on_error=failed)

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
        run = self.new_run()
        ref = self._start("eqsl", "download", f"{len(nicks)} QTH nickname(s), since {since or 'the start'}")
        dlg = self._progress("eqsldown", tr("Downloading from eQSL…"))
        t0 = time.monotonic()

        def work(event):
            out, partial, got_one = [], [], False
            for n in nicks:
                dest = self.db.download_path("eqsl", run, n)
                try:
                    _, recs = services.eqsl_download(user, pw, since=since, qth_nickname=n, dest=dest,
                                                     progress=lambda got, _c, n=n: event((n, got)))
                    got_one = True
                except net.PartialDownload as e:
                    with open(e.path, "rb") as fh:
                        _, recs = adif.parse(fh.read(), complete_only=True)
                    partial.append(f"{n}: {e}")
                except net.NetError as e:  # one nickname failed for good: the others still count
                    partial.append(f"{n}: {e}")
                    recs = []
                out.extend(recs)
            if partial and not got_one and not out:
                raise services.ServiceError("; ".join(partial))
            return out, partial

        def done(res):
            recs, partial = res
            t1 = time.monotonic()

            def save():
                if not since:
                    self.db.sync_set("eqsl", "full_seconds", str(round(t1 - t0)))
                a = apply.apply_eqsl(self.db, recs, self.catalog())
                if not partial:
                    # RcvdSince is a date: overlap a day to include cards arriving later today.
                    self.db.sync_set("eqsl", "rcvd_since",
                                     (timeutil.now_utc() - timedelta(days=1)).strftime("%Y%m%d"))
                    self.db.sync_set("eqsl", "last_run", run)
                return a

            if partial:
                # What arrived is applied; the position stays, so the next download fetches the rest.
                try:
                    with self.db.batch("eQSL download (partial)"):
                        a = save()
                except Exception as e:  # noqa: BLE001
                    self._fail("eqsl", "download", started, dlg, str(e), [pw], ref=ref)
                    return
                self._close(dlg)
                self._end("eqsl", "download")
            else:
                a = self._apply_download("eqsl", started, dlg, save, ref=ref, secrets=[pw])
                if a is None:
                    return
            added, held = self._settle_unmatched("eqsl", a.unmatched, ref=ref)
            timing = f"download {t1 - t0:.1f} s, apply {time.monotonic() - t1:.2f} s"
            msg = "; ".join(self._safe(p, [pw]) for p in partial)

            def text(t) -> str:
                return (msg + ". " + t("The cards that arrived are in your log; the next download fetches the rest.")
                        + " · " if partial else "") + apply.summary(
                    t, a, new_one="new card", new_many="new cards", none="No new cards", added=len(added), held=held)

            self._log("eqsl", "download", started, received=a.total, matched=a.matched, errors=len(partial),
                      message=text(_as_is))
            self.report("eqsl", "cancelled" if partial else "ok", text, ref=ref, ids=a.confirmed + added,
                        log_detail=f"{apply.counts(a)}; {timing}")
            self.confirmed.emit("eqsl", a.confirmed)
            self.db.prune_downloads("eqsl", keep_runs=[run] if not partial else [run, self.db.sync_get("eqsl", "last_run")])

        run_async(work, on_done=done, on_error=lambda m: self._fail("eqsl", "download", started, dlg, m, [pw], ref=ref),
                  on_event=lambda ev: self._say(dlg, tr("Downloading from eQSL…") + f"\n{ev[0]} · {ev[1] / 1e6:.1f} MB"))

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
        run = self.new_run()
        resume = {c: int(self.db.sync_get("qrz", self.QRZ_RESUME + c) or 0) for c in keys}
        ref = self._start("qrz", "download", f"{len(keys)} logbook(s)" +
                          (", continuing " + ", ".join(f"{c} from log id {a}" for c, a in resume.items() if a)
                           if any(resume.values()) else ""))
        dlg = self._progress("qrzdown", tr("Downloading from QRZ.com Logbook…"))
        t0 = time.monotonic()
        totals = _Totals()
        state = {"pages": 0}

        def work(event):
            for c, k in keys.items():
                dest = self.db.download_path("qrz", run, c)

                def on_page(after, recs, text, c=c, dest=dest):
                    with open(dest, "a", encoding="utf-8") as fh:
                        fh.write(text + "\n")  # every page received stays on disk, whatever happens next
                    event({"call": c, "after": after, "records": recs})

                services.qrz_fetch(k, call=c, after=resume[c], on_page=on_page)
            return True

        def on_event(ev):
            if totals.broken:
                return
            try:
                with self.db.batch("QRZ.com page"):
                    a = apply.apply_qrz(self.db, ev["records"], self.catalog())
                    self.db.sync_set("qrz", self.QRZ_RESUME + ev["call"], str(ev["after"]))
            except Exception as e:  # noqa: BLE001 - a page that can't be applied ends the run as a failure
                log.exception("QRZ.com page for %s (ref %s) could not be applied", ev["call"], ref)
                totals.broken = str(e)
                return
            totals.add(a, records=len(ev["records"]))
            state["pages"] += 1
            self._say(dlg, tr("Downloading from QRZ.com Logbook…") + f"\n{ev['call']} · {totals.records:,} " +
                      tr("records") + f" · {state['pages']} " + tr("pages"))

        def done(_ok):
            t1 = time.monotonic()
            if totals.broken:
                self._fail("qrz", "download", started, dlg, totals.broken, list(keys.values()), ref=ref)
                return

            def save():
                for c in keys:
                    self.db.sync_set("qrz", self.QRZ_RESUME + c, None)  # the run is complete: the next starts over
                self.db.sync_set("qrz", "last_run", run)
                return totals.result()

            a = self._apply_download("qrz", started, dlg, save, ref=ref, secrets=list(keys.values()))
            if a is None:
                return
            added, held = self._settle_unmatched("qrz", a.unmatched, ref=ref)
            timing = f"download {t1 - t0:.1f} s"

            def text(t) -> str:
                return apply.summary(t, a, new_one="new confirmation", new_many="new confirmations",
                                     none="No new confirmations", added=len(added), held=held)

            self._log("qrz", "download", started, received=a.total, matched=a.matched, message=text(_as_is))
            self.report("qrz", "ok", text, ref=ref, ids=a.confirmed + added, log_detail=f"{apply.counts(a)}; {timing}")
            self.confirmed.emit("qrz", a.confirmed)
            self.db.prune_downloads("qrz", keep_runs=[run])

        def stopped(msg):
            if not state["pages"]:  # nothing arrived: a plain failure, as before
                self._fail("qrz", "download", started, dlg, msg, list(keys.values()), ref=ref)
                return
            self._close(dlg)
            self._end("qrz", "download")
            res = totals.result()
            added, held = self._settle_unmatched("qrz", res.unmatched, ref=ref)
            where = ", ".join(f"{c} {a}" for c in keys if (a := self.db.sync_get("qrz", self.QRZ_RESUME + c)))
            self._log("qrz", "download", started, received=res.total, matched=res.matched, errors=1,
                      message=f"{msg}; {state['pages']} pages applied; continues from log id {where or '0'}")
            self.report("qrz", "cancelled", lambda t: self._safe(msg, list(keys.values())) + f". {state['pages']} " +
                        t("pages are applied") + " · " + apply.summary(
                            t, res, new_one="new confirmation", new_many="new confirmations",
                            none="No new confirmations", added=len(added), held=held) + ". " +
                        t("Download from QRZ.com continues from log id") + f" {where or 0}.",
                        ref=ref, ids=res.confirmed + added)
            self.confirmed.emit("qrz", res.confirmed)
            self.db.prune_downloads("qrz", keep_runs=[run, self.db.sync_get("qrz", "last_run")])

        run_async(work, on_done=done, on_event=on_event, on_error=stopped)

    def upload_on_log(self, qid: int) -> None:
        on = self.prefs.get("upload_on_log") or {}
        for svc in ("qrz", "eqsl", "hrdlog"):
            if on.get(svc):
                self.upload(svc, [qid], quiet=True)


class _Totals:
    """What a download in parts or pages has applied so far."""

    def __init__(self) -> None:
        self.total = self.matched = self.changed = 0
        self.confirmed: list[int] = []
        self.unmatched: list[dict] = []
        self.records = 0
        self.bytes = 0
        self.broken: str | None = None  # a part or page could not be applied: the run ends as a failure

    def add(self, res: apply.ApplyResult, *, records: int = 0) -> None:
        self.total += res.total
        self.matched += res.matched
        self.changed += res.changed
        self.confirmed.extend(res.confirmed)
        self.unmatched.extend(res.unmatched)
        self.records += records

    def result(self) -> apply.ApplyResult:
        return apply.ApplyResult(total=self.total, matched=self.matched, changed=self.changed,
                                 unmatched=list(self.unmatched), confirmed=list(self.confirmed))


class _Marks:
    """QSOs a service accepted, marked as uploaded in small batches while the upload goes on."""

    def __init__(self, db, service: str) -> None:
        self.db, self.service = db, service
        self.ids: list[int] = []
        self.logids: dict[int, str] = {}

    @property
    def pending(self) -> int:
        return len(self.ids)

    def add(self, ids: list[int], *, logid: str | None = None) -> None:
        self.ids.extend(ids)
        if logid:
            for qid in ids:
                self.logids[qid] = logid

    def flush(self) -> None:
        if self.ids:
            apply.mark_sent(self.db, self.service, list(self.ids), logids=self.logids or None)
            self.ids, self.logids = [], {}
