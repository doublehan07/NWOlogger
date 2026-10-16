"""Upload / download with LoTW, eQSL, QRZ.com and HRDLog. Network runs on worker threads; DB on the GUI thread."""

from __future__ import annotations

import os
import tempfile
from collections import defaultdict

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, i18n
from ..core import adif, importer, sats, timeutil
from ..core.sync import apply, services, tqsl
from .widgets import fmt, mode_text, run_async

tr = i18n.tr


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


class SyncController(QObject):
    message = Signal(str)

    def __init__(self, db, prefs, parent: QWidget):
        super().__init__(parent)
        self.db, self.prefs, self.win = db, prefs, parent
        self.busy: set[str] = set()

    def catalog(self) -> sats.SatCatalog:
        return sats.load(overrides=self.prefs.get("sat_names"))

    # ------------------------------------------------------------------ helpers
    def _need(self, *pairs: tuple[str, str | None], quiet: bool = False) -> bool:
        missing = [label for label, v in pairs if not v]
        if missing and quiet:
            self.message.emit(tr("Upload skipped: set the account in Preferences → Services."))
            return False
        if missing:
            QMessageBox.information(self.win, tr("Account needed"),
                                    tr("Set these in Preferences → Services first:") + "\n• " + "\n• ".join(missing))
            return False
        return True

    def _progress(self, text: str) -> QProgressDialog:
        dlg = QProgressDialog(text, tr("Hide"), 0, 0, self.win)
        dlg.setWindowTitle("QHlogger")
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        dlg.setMinimumDuration(300)
        dlg.show()
        return dlg

    def _log(self, service: str, direction: str, started: str, **kw) -> None:
        self.db.sync_log_add(apply.SERVICES[service], direction, started, **kw)

    def _fail(self, service: str, direction: str, started: str, dlg, msg: str) -> None:
        dlg.close()
        self.busy.discard(service + direction)
        self._log(service, direction, started, errors=1, message=msg)
        QMessageBox.warning(self.win, apply.SERVICES[service], msg)

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
        if not res.unmatched:
            return
        dlg = UnmatchedDialog(apply.SERVICES[service], res.unmatched, self.win)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            chosen = dlg.selected()
            if chosen:
                r = importer.run(self.db, chosen, label=tr("Add from") + f" {apply.SERVICES[service]}")
                self.message.emit(tr("Added") + f" {r.added} " + tr("QSOs"))

    # ------------------------------------------------------------------ upload
    def upload(self, service: str, ids: list[int] | None = None, *, quiet: bool = False) -> None:
        key = service + "up"
        if key in self.busy:
            return
        qsos = apply.pending(self.db, service, ids)
        if not qsos:
            if not quiet:
                QMessageBox.information(self.win, apply.SERVICES[service], tr("Nothing to upload."))
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
            QMessageBox.warning(self.win, "LoTW", tr("TQSL was not found. Install TrustedQSL from arrl.org, or set "
                                                     "its location in Preferences → Services."))
            return
        groups: dict[str, list[dict]] = defaultdict(list)
        missing_loc = set()
        for q in qsos:
            p = self._profile_for(q) or {}
            loc = p.get("lotw_location")
            if not loc:
                missing_loc.add(p.get("name") or q.get("station_callsign") or "?")
                continue
            groups[loc].append(q)
        if missing_loc:
            QMessageBox.warning(self.win, "LoTW", tr("Set the TQSL station location in these station profiles:")
                                + "\n• " + "\n• ".join(sorted(missing_loc)))
            if not groups:
                return
        cat = self.catalog()
        tmpdir = tempfile.mkdtemp(prefix="qhlogger-lotw-")
        jobs = []
        for loc, qs in groups.items():
            recs = [apply.upload_record(q, "lotw", cat) for q in qs]
            fn = os.path.join(tmpdir, f"lotw-{abs(hash(loc))}.adi")
            adif.write_file(fn, recs, program_version=__version__)
            jobs.append((loc, fn, [q["id"] for q in qs]))
        started = timeutil.now_iso()
        self.busy.add("lotwup")
        dlg = self._progress(tr("Signing and uploading to LoTW with TQSL…"))

        def work():
            out = []
            for loc, fn, ids in jobs:
                code, text = tqsl.sign_and_upload(path, fn, loc)
                out.append((loc, ids, code, text))
            return out

        def done(results):
            dlg.close()
            self.busy.discard("lotwup")
            sent = 0
            msgs = []
            for loc, ids, code, text in results:
                if code in tqsl.SENT_CODES:
                    apply.mark_sent(self.db, "lotw", ids)
                    sent += len(ids)
                msgs.append(f"{loc}: {tqsl.describe(code)}" + (f"\n{text[-400:]}" if code not in (0,) and text else ""))
            errors = sum(1 for r in results if r[2] not in tqsl.SENT_CODES)
            self._log("lotw", "upload", started, sent=sent, errors=errors, message="; ".join(m.split("\n")[0]
                                                                                           for m in msgs))
            if not quiet or errors:
                QMessageBox.information(self.win, "LoTW", "\n\n".join(msgs))
            self.message.emit(f"LoTW: {sent} " + tr("QSOs uploaded"))

        run_async(work, on_done=done, on_error=lambda m: self._fail("lotw", "upload", started, dlg, m))

    def _upload_eqsl(self, qsos: list[dict], quiet: bool) -> None:
        user, pw = self.prefs.get("eqsl_user"), self.prefs.secret("eqsl_password")
        if not self._need((tr("eQSL user"), user), (tr("eQSL password"), pw), quiet=quiet):
            return
        cat = self.catalog()
        recs = [(q["id"], q.get("call"), apply.upload_record(q, "eqsl", cat, self._profile_for(q))) for q in qsos]
        chunks = [recs[i:i + 250] for i in range(0, len(recs), 250)]
        started = timeutil.now_iso()
        self.busy.add("eqslup")
        dlg = self._progress(tr("Uploading to eQSL…"))

        def work():
            out = []
            for ch in chunks:
                added, total, lines = services.eqsl_upload(user, pw, [r for _, _, r in ch])
                out.append((ch, added, total, lines))
            return out

        def done(results):
            dlg.close()
            self.busy.discard("eqslup")
            ok_ids, bad = [], []
            for ch, _added, _total, lines in results:
                bad_calls = set()
                for ln in lines:
                    if ln.lower().startswith("error") or ("bad record" in ln.lower() and "duplicate" not in ln.lower()):
                        for _, call, _ in ch:
                            if call and call in ln:
                                bad_calls.add(call)
                for qid, call, _ in ch:
                    (bad if call in bad_calls else ok_ids).append(qid)
            apply.mark_sent(self.db, "eqsl", ok_ids)
            self._log("eqsl", "upload", started, sent=len(ok_ids), errors=len(bad))
            if not quiet or bad:
                QMessageBox.information(self.win, "eQSL", f"{len(ok_ids)} " + tr("QSOs uploaded") +
                                        (f", {len(bad)} " + tr("rejected") if bad else ""))
            self.message.emit(f"eQSL: {len(ok_ids)} " + tr("QSOs uploaded"))

        run_async(work, on_done=done, on_error=lambda m: self._fail("eqsl", "upload", started, dlg, m))

    def _upload_qrz(self, qsos: list[dict], quiet: bool) -> None:
        by_call: dict[str, list[dict]] = defaultdict(list)
        for q in qsos:
            by_call[(q.get("station_callsign") or "").upper()].append(q)
        keys = {c: self.prefs.qrz_api_key(c) for c in by_call}
        missing = [c or "?" for c, k in keys.items() if not k]
        if missing and quiet:
            if all(not k for k in keys.values()):
                return
        elif missing:
            QMessageBox.information(self.win, "QRZ.com", tr("Add the QRZ Logbook API key for these station calls in "
                                                            "Preferences → Services:") + "\n• " + "\n• ".join(missing))
            if all(not k for k in keys.values()):
                return
        cat = self.catalog()
        jobs = [(q["id"], c, keys[c], apply.upload_record(q, "qrz", cat),
                 q.get("qrzcom_qso_upload_status") == "M") for c, qs in by_call.items() if keys.get(c) for q in qs]
        started = timeutil.now_iso()
        self.busy.add("qrzup")
        dlg = self._progress(tr("Uploading to QRZ.com Logbook…"))

        def work():
            out = []
            for qid, call, key, rec, replace in jobs:
                try:
                    ok, logid, reason = services.qrz_insert(key, rec, replace=replace, call=call)
                except Exception as e:  # noqa: BLE001
                    ok, logid, reason = False, None, str(e)
                out.append((qid, ok, logid, reason))
            return out

        def done(results):
            dlg.close()
            self.busy.discard("qrzup")
            ok = [r for r in results if r[1]]
            bad = [r for r in results if not r[1]]
            apply.mark_sent(self.db, "qrz", [r[0] for r in ok], logids={r[0]: r[2] for r in ok})
            self._log("qrz", "upload", started, sent=len(ok), errors=len(bad),
                      message="; ".join({r[3] for r in bad})[:300])
            if not quiet or bad:
                QMessageBox.information(self.win, "QRZ.com", f"{len(ok)} " + tr("QSOs uploaded") +
                                        (f"\n{len(bad)} " + tr("failed") + ": " + "; ".join({r[3] for r in bad})[:300]
                                         if bad else ""))
            self.message.emit(f"QRZ.com: {len(ok)} " + tr("QSOs uploaded"))

        run_async(work, on_done=done, on_error=lambda m: self._fail("qrz", "upload", started, dlg, m))

    def _upload_hrdlog(self, qsos: list[dict], quiet: bool) -> None:
        call, code = self.prefs.get("hrdlog_call"), self.prefs.secret("hrdlog_code")
        if not self._need((tr("HRDLog callsign"), call), (tr("HRDLog upload code"), code), quiet=quiet):
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
        self.busy.add("hrdlogup")
        dlg = self._progress(tr("Uploading to HRDLog…"))

        def work():
            out = []
            for qid, rec, key in jobs:
                try:
                    ok, msg = services.hrdlog_upload(call, code, rec, update_key=key)
                except Exception as e:  # noqa: BLE001
                    ok, msg = False, str(e)
                out.append((qid, ok, msg))
            return out

        def done(results):
            dlg.close()
            self.busy.discard("hrdlogup")
            ok = [r[0] for r in results if r[1]]
            bad = [r for r in results if not r[1]]
            apply.mark_sent(self.db, "hrdlog", ok)
            self._log("hrdlog", "upload", started, sent=len(ok), errors=len(bad),
                      message="; ".join({r[2] for r in bad})[:300])
            if not quiet or bad:
                QMessageBox.information(self.win, "HRDLog", f"{len(ok)} " + tr("QSOs uploaded") +
                                        (f"\n{len(bad)} " + tr("failed") + ": " + "; ".join({r[2] for r in bad})[:300]
                                         if bad else ""))
            self.message.emit(f"HRDLog: {len(ok)} " + tr("QSOs uploaded"))

        run_async(work, on_done=done, on_error=lambda m: self._fail("hrdlog", "upload", started, dlg, m))

    # ------------------------------------------------------------------ download
    def download(self, service: str) -> None:
        if service == "hrdlog":
            QMessageBox.information(self.win, "HRDLog", tr("HRDLog.net has no public download API, so QHlogger can "
                                                           "only upload to it. (It sends no confirmations back.)"))
            return
        if service + "down" in self.busy:
            return
        getattr(self, f"_download_{service}")()

    def _download_lotw(self) -> None:
        user, pw = self.prefs.get("lotw_user"), self.prefs.secret("lotw_password")
        if not self._need((tr("LoTW user"), user), (tr("LoTW password"), pw)):
            return
        since_qsl = self.db.sync_get("lotw", "last_qsl")
        since_rx = self.db.sync_get("lotw", "last_qsorx")
        started = timeutil.now_iso()
        self.busy.add("lotwdown")
        dlg = self._progress(tr("Downloading from LoTW…"))

        def work():
            h1, r1 = services.lotw_download(user, pw, qsl=True, since=since_qsl)
            h2, r2 = services.lotw_download(user, pw, qsl=False, since=since_rx)
            return h1, r1, h2, r2

        def done(res):
            dlg.close()
            self.busy.discard("lotwdown")
            h1, r1, h2, r2 = res
            cat = self.catalog()
            up = apply.apply_lotw_uploads(self.db, r2, cat)
            a = apply.apply_lotw_qsls(self.db, r1, cat)
            if h1.get("APP_LOTW_LASTQSL"):
                self.db.sync_set("lotw", "last_qsl", h1["APP_LOTW_LASTQSL"])
            if h2.get("APP_LOTW_LASTQSORX"):
                self.db.sync_set("lotw", "last_qsorx", h2["APP_LOTW_LASTQSORX"])
            self._log("lotw", "download", started, received=a.total, matched=a.matched,
                      message=f"{a.changed} confirmations updated; {up.changed} upload states updated")
            QMessageBox.information(self.win, "LoTW",
                                    f"{a.total} " + tr("confirmations received") + f", {a.matched} " + tr("matched") +
                                    f", {a.changed} " + tr("updated") + f".\n{up.changed} " +
                                    tr("QSOs marked as uploaded."))
            self._show_unmatched("lotw", a)

        run_async(work, on_done=done, on_error=lambda m: self._fail("lotw", "download", started, dlg, m))

    def _download_eqsl(self) -> None:
        user, pw = self.prefs.get("eqsl_user"), self.prefs.secret("eqsl_password")
        if not self._need((tr("eQSL user"), user), (tr("eQSL password"), pw)):
            return
        since = self.db.sync_get("eqsl", "rcvd_since")
        nicks = {p.get("eqsl_qth_nickname") for p in self.db.profiles() if p.get("eqsl_qth_nickname")} or {None}
        started = timeutil.now_iso()
        self.busy.add("eqsldown")
        dlg = self._progress(tr("Downloading from eQSL…"))

        def work():
            out = []
            for n in nicks:
                _, recs = services.eqsl_download(user, pw, since=since, qth_nickname=n)
                out.extend(recs)
            return out

        def done(recs):
            dlg.close()
            self.busy.discard("eqsldown")
            a = apply.apply_eqsl(self.db, recs, self.catalog())
            self.db.sync_set("eqsl", "rcvd_since", timeutil.now_utc().strftime("%Y%m%d"))
            self._log("eqsl", "download", started, received=a.total, matched=a.matched,
                      message=f"{a.changed} updated")
            QMessageBox.information(self.win, "eQSL", f"{a.total} " + tr("cards received") + f", {a.matched} " +
                                    tr("matched") + f", {a.changed} " + tr("updated") + ".")
            self._show_unmatched("eqsl", a)

        run_async(work, on_done=done, on_error=lambda m: self._fail("eqsl", "download", started, dlg, m))

    def _download_qrz(self) -> None:
        calls = sorted({p.get("station_callsign") for p in self.db.profiles() if p.get("station_callsign")})
        keys = {c: self.prefs.qrz_api_key(c) for c in calls}
        keys = {c: k for c, k in keys.items() if k}
        if not keys:
            QMessageBox.information(self.win, "QRZ.com", tr("Add a QRZ Logbook API key in Preferences → Services."))
            return
        since = {c: self.db.sync_get("qrz", f"modsince:{c}") for c in keys}
        started = timeutil.now_iso()
        self.busy.add("qrzdown")
        dlg = self._progress(tr("Downloading from QRZ.com Logbook…"))

        def work():
            out = []
            for c, k in keys.items():
                out.extend(services.qrz_fetch(k, since=since[c], call=c))
            return out

        def done(recs):
            dlg.close()
            self.busy.discard("qrzdown")
            a = apply.apply_qrz(self.db, recs, self.catalog())
            for c in keys:
                self.db.sync_set("qrz", f"modsince:{c}", timeutil.today_iso())
            self._log("qrz", "download", started, received=a.total, matched=a.matched,
                      message=f"{a.changed} updated")
            QMessageBox.information(self.win, "QRZ.com", f"{a.total} " + tr("records received") + f", {a.matched} " +
                                    tr("matched") + f", {a.changed} " + tr("updated") + ".")
            self._show_unmatched("qrz", a)

        run_async(work, on_done=done, on_error=lambda m: self._fail("qrz", "download", started, dlg, m))

    def upload_on_log(self, qid: int) -> None:
        on = self.prefs.get("upload_on_log") or {}
        for svc in ("qrz", "eqsl", "hrdlog"):
            if on.get(svc):
                self.upload(svc, [qid], quiet=True)

