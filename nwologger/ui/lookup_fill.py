"""Look up and fill: the selected QSOs' calls on QRZ.com / HamQTH, one at a time, with progress and Stop.

Only empty fields are filled. Calls looked up recently come from the cache at once; the others are asked at most once
a second. Stop keeps everything found so far. The same error three times in a row ends the run, so a wrong password
or an expired subscription fails once, not once per call.
"""

from __future__ import annotations

import logging
import threading
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from .. import i18n
from ..core import regions
from ..core.lookup import clients as lookup_clients

tr = i18n.tr
log = logging.getLogger("nwologger.lookup")

FILL_COLS = ["first_name", "last_name", "qth", "address", "state", "cnty", "gridsquare", "email", "web", "qsl_via",
             "iota"]
PACE = 1.0  # seconds between two lookups, at least
SAME_ERRORS = 3  # the same error this many times in a row stops the run


def fill_changes(q: dict, data: dict) -> dict:
    """The empty fields of ``q`` that ``data`` (a lookup) can fill."""
    out = {c: data[c] for c in FILL_COLS if data.get(c) and not q.get(c)}
    if out.get("state") and q.get("dxcc") == regions.CHINA_DXCC:
        out["state"] = regions.normalize_cn(out["state"]) or out["state"]
    return out


class _Signals(QObject):
    step = Signal(int, str, object, str, str)  # index, call, data (or None), source, error
    done = Signal(bool)  # stopped early


class LookupFillDialog(QDialog):
    finished_fill = Signal(int)  # QSOs filled

    def __init__(self, win, ids: list[int], *, pace: float = PACE):
        super().__init__(win)
        self.win, self.db = win, win.db
        self.ids = list(ids)
        self.pace = pace
        self.setWindowTitle(tr("Look up and fill"))
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(520, 190)
        rows = self.db.get_many(self.ids)
        self.rows = rows
        calls = sorted({q["call"] for q in rows.values() if q.get("call")})
        ttl = win.prefs.get("lookup_ttl_days")
        self.found: dict[str, dict] = {}
        for c in calls:
            d = self.db.cache_get(c, ttl)
            if d:
                d.pop("_source", None)
                self.found[c] = d
        self.cached = len(self.found)
        self.todo = [c for c in calls if c not in self.found]
        self.clients = win.lookup_clients() if self.todo else []
        self.not_found = self.failed = 0
        self.asked = 0
        self.errors: list[str] = []
        self.stop_event = threading.Event()
        self.started = time.monotonic()
        self.running = False

        lay = QVBoxLayout(self)
        self.head = QLabel()
        self.head.setWordWrap(True)
        lay.addWidget(self.head)
        self.bar = QProgressBar()
        self.bar.setRange(0, max(1, len(self.todo)))
        self.bar.setTextVisible(False)
        lay.addWidget(self.bar)
        self.counts = QLabel()
        lay.addWidget(self.counts)
        self.detail = QLabel()
        self.detail.setProperty("muted", True)
        self.detail.setWordWrap(True)
        lay.addWidget(self.detail)
        row = QHBoxLayout()
        row.addStretch(1)
        self.again_btn = QPushButton()
        self.again_btn.setVisible(False)
        self.again_btn.clicked.connect(self._again)
        self.hide_btn = QPushButton(tr("Hide"))
        self.hide_btn.clicked.connect(self.hide)
        self.stop_btn = QPushButton(tr("Stop"))
        self.stop_btn.setProperty("danger", True)
        self.stop_btn.clicked.connect(self.stop)
        for b in (self.again_btn, self.hide_btn, self.stop_btn):
            row.addWidget(b)
        lay.addLayout(row)
        self.sig = _Signals()
        self.sig.step.connect(self._step)
        self.sig.done.connect(self._done)

    # ------------------------------------------------------------------ running
    def start(self) -> None:
        if self.todo and not self.clients:
            self.detail.setText(tr("Set a QRZ.com or HamQTH account in Preferences → Lookup.") + " " +
                                tr("Only calls in the cache can be filled."))
            self.todo = []
        names = ", ".join(c.name for c in self.clients) or "QRZ.com / HamQTH"
        self.head.setText(tr("Looking up") + f" <b>{len(self.todo):,}</b> " + tr("calls on") + f" {names}…" +
                          (f" <span style='color:gray'>({self.cached:,} " + tr("filled from the cache") + ")</span>"
                           if self.cached else ""))
        self._counts("")
        self.show()
        if not self.todo:
            self._done(False)
            return
        self.running = True
        threading.Thread(target=self._work, name="lookup-fill", daemon=True).start()

    def _work(self) -> None:
        last_err, same = "", 0
        stopped = False
        for i, call in enumerate(self.todo):
            if self.stop_event.is_set():
                stopped = True
                break
            t0 = time.monotonic()
            data, source, errors = lookup_clients.lookup_chain(call, self.clients)
            err = errors[0] if errors and not data else ""
            same = same + 1 if err and err == last_err else (1 if err else 0)
            last_err = err
            try:
                self.sig.step.emit(i, call, data, source or "", err)
            except RuntimeError:  # the window was closed
                return
            if same >= SAME_ERRORS:
                stopped = True
                break
            wait = self.pace - (time.monotonic() - t0)
            if wait > 0 and i + 1 < len(self.todo) and self.stop_event.wait(wait):
                stopped = True
                break
        try:
            self.sig.done.emit(stopped)
        except RuntimeError:
            pass

    def stop(self) -> None:
        """Finish the lookup in flight, then fill from everything found so far."""
        self.stop_event.set()
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText(tr("Stopping…"))

    def _step(self, i: int, call: str, data, source: str, err: str) -> None:
        self.asked = i + 1
        if data:
            self.found[call] = data
            self.db.cache_put(call, source, data)
            self.win.dx.note_known(call, source)
        elif err:
            self.failed += 1
            if err not in self.errors:
                self.errors.append(err)
        else:
            self.not_found += 1
        self.bar.setValue(self.asked)
        self._counts(call)

    def _counts(self, call: str) -> None:
        left = len(self.todo) - self.asked
        eta = f" · {tr('about')} {max(1, round(left * max(self.pace, 0.5) / 60))} {tr('min left')}" if left > 1 else ""
        self.counts.setText(f"<b>{self.asked:,}</b> / {len(self.todo):,}" + (f" · {tr('now')} {call}" if call else "") +
                            f" · {tr('found')} <b>{len(self.found) - self.cached:,}</b> · {tr('not found')} "
                            f"<b>{self.not_found:,}</b> · {tr('failed')} <b>{self.failed:,}</b>{eta}")

    def _done(self, stopped: bool) -> None:
        """Fill the QSOs of every call found (one transaction, after a backup) and show what happened."""
        self.running = False
        changes = {qid: fill_changes(q, self.found[q["call"]]) for qid, q in self.rows.items()
                   if q.get("call") in self.found}
        changes = {qid: ch for qid, ch in changes.items() if ch}
        n = 0
        if changes:
            self.db.backup("lookup")
            n = self.db.update_many(list(changes), lambda q: changes.get(q["id"], {}), label=tr("Look up and fill"))
        calls_used = len({self.rows[qid]["call"] for qid in changes})
        rest = len(self.todo) - self.asked
        same_err = self.failed >= SAME_ERRORS and len(self.errors) == 1 and rest > 0
        mins = (time.monotonic() - self.started) / 60
        how = "stopped after the same error" if same_err else "stopped by you" if stopped else "finished"
        log.info("Look up and fill: %d calls, %d cached, %d found, %d not found, %d failed; %s after %.1f min",
                 len(self.todo) + self.cached, self.cached, len(self.found) - self.cached, self.not_found,
                 self.failed, how, mins)
        self.setWindowTitle(tr("Look up and fill") + (" — " + tr("stopped") if stopped else ""))
        self.head.setText(f"<b>{tr('Filled')} {n:,} {tr('QSOs')}</b> " + tr("from") + f" {calls_used:,} " +
                          tr("calls") + ". " + tr("A backup of the log was saved first.") if n else
                          f"<b>{tr('Nothing to fill')}</b>: " + tr("the calls found have no new details for these QSOs."))
        parts = []
        if stopped:
            parts.append(tr("Stopped after") + f" {self.asked:,} " + tr("of") + f" {len(self.todo):,} " + tr("calls"))
        parts.append(f"{len(self.found) - self.cached:,} " + tr("found"))
        if self.not_found:
            parts.append(f"{self.not_found:,} " + tr("not found on QRZ.com / HamQTH"))
        if self.failed:
            parts.append(f"{self.failed:,} " + tr("failed") + (f": {self.errors[0]}" if self.errors else ""))
        self.detail.setText(" · ".join(parts))
        self.bar.setValue(self.bar.maximum() if not stopped else self.asked)
        self.stop_btn.setVisible(False)
        self.hide_btn.setText(tr("Close"))
        if rest > 0:
            self.again_btn.setText(tr("Look up the other") + f" {rest:,}")
            self.again_btn.setVisible(True)
        self.finished_fill.emit(n)

    def _again(self) -> None:
        """A new run for the same QSOs: the calls found are in the cache now, so only the rest are asked."""
        self.close()
        self.win.lookup_fill_selected(self.ids)
