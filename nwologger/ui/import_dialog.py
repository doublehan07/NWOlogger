"""Import an ADIF file or a MacLoggerDX database, with a dry-run preview first. A LoTW report is applied as a LoTW
download by default (its QSL fields are LoTW's), or imported as QSOs."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QRadioButton,
    QVBoxLayout,
)

from .. import i18n
from ..core import importer, mldx, sats
from ..core.sync import apply
from .widgets import Section

tr = i18n.tr


class ImportDialog(QDialog):
    def __init__(self, db, prefs, path: str, parent=None):
        super().__init__(parent)
        self.db, self.prefs, self.path = db, prefs, path
        self.result: importer.ImportResult | None = None
        self.lotw_result: apply.ApplyResult | None = None  # a LoTW report applied as a download
        self.is_mldx = Path(path).suffix.lower() in (".sql", ".sqlite", ".db") and mldx.is_mldx_db(path)
        self.setWindowTitle(tr("Import") + f" — {Path(path).name}")
        self.resize(760, 600)
        lay = QVBoxLayout(self)
        self.dropped: list[str] = []
        self.inferred: Counter = Counter()
        self.records: list[dict[str, str]] = []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if self.is_mldx:
                self.qsos, self.report = mldx.read(path)
                self.inferred = self.report.inferred
                self.header = {}
            else:
                with open(path, "rb") as fh:
                    self.header, self.qsos = importer.read_adif(fh.read(), dropped=self.dropped,
                                                                inferred=self.inferred, raw=self.records)
                self.report = None
        finally:
            QApplication.restoreOverrideCursor()
        self.is_lotw = not self.is_mldx and importer.is_lotw_report(self.header, self.records)
        kind = tr("MacLoggerDX database") if self.is_mldx else tr("LoTW report") if self.is_lotw else tr("ADIF file")
        lay.addWidget(QLabel(f"<b>{Path(path).name}</b>  ·  {kind}"))

        counts = Section(tr("Preview"))
        self.counts = QGridLayout()
        counts.add(self.counts)
        lay.addWidget(counts)

        self.as_download = self.as_qsos = None
        if self.is_lotw:
            box = Section(tr("LoTW report"))
            note = QLabel(tr("Its QSL received fields are LoTW's: the confirmation and the date LoTW confirmed it, "
                             "not a paper card."))
            note.setWordWrap(True)
            box.add(note)
            self.as_download = QRadioButton(tr("Apply it to your log as a LoTW download (recommended)"))
            self.as_download.setToolTip(tr("Confirms the QSOs in your log and marks them uploaded to LoTW; the "
                                           "records not in your log are listed afterwards, to add the ones you "
                                           "choose."))
            self.as_download.setChecked(True)
            self.as_qsos = QRadioButton(tr("Import every record as a QSO"))
            for w in (self.as_download, self.as_qsos):
                box.add(w)
            lay.addWidget(box)

        opts = Section(tr("Options"))
        self.opts = opts
        form = QFormLayout()
        self.merge = QCheckBox(tr("Merge records that match an existing QSO instead of adding them"))
        self.merge.setToolTip(tr("For confirmation files: fills empty fields and updates QSL status."))
        self.fill = QCheckBox(tr("Fill missing country, DXCC and zones from the country file"))
        self.fill.setChecked(True)
        self.profile = QComboBox()
        self.profile.addItem(tr("(none)"), None)
        for p in db.profiles():
            self.profile.addItem(p["name"], p["id"])
        if self.profile.count() > 1:
            self.profile.setCurrentIndex(1)
        if self.is_lotw:
            self.merge.setChecked(True)  # importing a confirmation file as QSOs: update the QSOs it matches
        if not self.is_mldx:
            form.addRow(self.merge)
        form.addRow(self.fill)
        form.addRow(tr("Station profile for records without a station call"), self.profile)
        opts.add(form)
        lay.addWidget(opts)

        self.profiles_list = None
        if self.is_mldx and self.report:
            rep = Section(tr("MacLoggerDX history"))
            text = QPlainTextEdit()
            text.setReadOnly(True)
            lines = [f"{k}: {v}" for k, v in sorted(self.report.tokens.items())]
            if self.report.unknown_tokens:
                lines.append("")
                lines.append(tr("Not recognised (kept out):"))
                lines += [f"  {k}: {v}" for k, v in self.report.unknown_tokens.items()]
            if self.report.province_conflicts:
                lines.append("")
                lines.append(tr("Province conflicts (State kept, ext_val_3 saved in extra):"))
                lines += [f"  {c}: State={s!r} ext_val_3={e!r}" for c, s, e in self.report.province_conflicts]
            if self.report.province_unrecognised:
                lines.append("")
                lines.append(tr("Province text not recognised (saved in extra):"))
                lines += [f"  {k!r}: {v}" for k, v in self.report.province_unrecognised.items()]
            text.setPlainText("\n".join(lines))
            text.setMaximumHeight(160)
            rep.add(text)
            existing = {(p.get("station_callsign"), p.get("my_gridsquare")) for p in db.profiles()}
            pairs = [(k, n) for k, n in self.report.profiles.most_common(8) if k not in existing]
            if pairs:
                lab = QLabel(tr("Create station profiles for your most-used locations:"))
                rep.add(lab)
                self.profiles_list = QListWidget()
                self.profiles_list.setMaximumHeight(120)
                for i, ((call, grid), n) in enumerate(pairs):
                    it = QListWidgetItem(f"{call}  ·  {grid or '—'}   ({n} QSOs)")
                    it.setData(Qt.ItemDataRole.UserRole, (call, grid))
                    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    it.setCheckState(Qt.CheckState.Checked if i < 2 else Qt.CheckState.Unchecked)
                    self.profiles_list.addItem(it)
                rep.add(self.profiles_list)
            lay.addWidget(rep)

        lay.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = bb.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText(tr("Import"))
        self.ok.setProperty("primary", True)
        bb.accepted.connect(self._run)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.merge.toggled.connect(self._preview)
        self.fill.toggled.connect(self._preview)
        if self.as_download is not None:
            self.as_download.toggled.connect(self._mode_changed)
            self._mode_changed()
        else:
            self._preview()

    def applies_lotw(self) -> bool:
        """A LoTW report, to be applied as a LoTW download (not imported as QSOs)."""
        return self.as_download is not None and self.as_download.isChecked()

    def _mode_changed(self, *_a) -> None:
        apply_it = self.applies_lotw()
        self.opts.setEnabled(not apply_it)
        self.ok.setText(tr("Apply") if apply_it else tr("Import"))
        self._preview()

    def _catalog(self) -> sats.SatCatalog:
        return sats.load(overrides=self.prefs.get("sat_names"), extra=self.prefs.get("user_sats"))

    def _show_counts(self, items: list[tuple[str, int]], text: str) -> None:
        while self.counts.count():
            w = self.counts.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, (label, n) in enumerate(items):
            big = QLabel(f"{n:,}")
            big.setObjectName("Big")
            lab = QLabel(label)
            lab.setProperty("muted", True)
            self.counts.addWidget(big, 0, i)
            self.counts.addWidget(lab, 1, i)
        note = QLabel(text)
        note.setObjectName("Hint")
        note.setWordWrap(True)
        self.counts.addWidget(note, 2, 0, 1, max(1, len(items)))

    def _preview_lotw(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            res = apply.apply_lotw_report(self.db, self.records, self._catalog(), dry_run=True)
        finally:
            QApplication.restoreOverrideCursor()
        confirmed = sum(1 for r in self.records if (r.get("QSL_RCVD") or "").upper() == "Y")
        self._show_counts(
            [(tr("Records"), res.total), (tr("Confirmed by LoTW"), confirmed), (tr("In your log"), res.matched),
             (tr("New confirmations"), len(res.confirmed)), (tr("Not in your log"), len(res.unmatched))],
            tr("As Download from LoTW does: the QSOs in your log get LoTW's confirmation with the date LoTW "
               "confirmed them and are marked uploaded to LoTW, and empty details LoTW knows (grid, zones, DXCC, "
               "country, state, county, IOTA) are filled in. The records not in your log are listed afterwards, to "
               "add the ones you choose. Where downloads resume is left as it is."))

    def _opts(self) -> dict:
        return {
            "merge": self.merge.isChecked() and not self.is_mldx,
            "fill": self.fill.isChecked(),
            "profile": self.db.profile(self.profile.currentData()),
            "key_tag": mldx.KEY_TAG if self.is_mldx else None,
            "block_s": self.prefs.get("block_seconds"),
            "warn_s": self.prefs.get("warn_hours") * 3600,
        }

    def _preview(self) -> None:
        if self.applies_lotw():
            self._preview_lotw()
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            res = importer.run(self.db, self.qsos, dry_run=True, **self._opts())
        finally:
            QApplication.restoreOverrideCursor()
        while self.counts.count():
            w = self.counts.takeAt(0).widget()
            if w:
                w.deleteLater()
        items = [(tr("Records"), res.total), (tr("New"), res.added), (tr("Marked dupe"), res.dupes),
                 (tr("Marked suspect"), res.suspects)]
        if res.merged:
            items.append((tr("Merged into existing"), res.merged))
        if res.updated or self.is_mldx:
            items.append((tr("Updated (imported before)"), res.updated))
        if res.errors:
            items.append((tr("Errors"), len(res.errors)))
        text = tr("Nothing is skipped: every record is imported and suspicious dupes are marked so you "
                  "can review them (preset “Suspicious dupes”).")
        if self.dropped:
            text += " " + tr("Fields that look like passwords are left out:") + f" {len(self.dropped)} (" + \
                ", ".join(sorted(set(self.dropped))) + ")."
        if self.inferred:
            parts = [f"{n} " + tr(label) for svc, label in (("lotw", "confirmed by LoTW"),
                                                             ("qrz", "confirmed by QRZ.com"))
                     if (n := self.inferred.get(svc))]
            text += "\n\n" + tr("QSOs with a confirmation but no upload record are marked uploaded there "
                                 "(a service only confirms what you uploaded):") + " " + ", ".join(parts) + ". " + \
                tr("Download from LoTW and QRZ.com before your first upload: that marks every other QSO they "
                   "already hold.")
        self._show_counts(items, text)

    def _run(self) -> None:
        if self.applies_lotw():
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self.db.backup("import")
                self.lotw_result = apply.apply_lotw_report(self.db, self.records, self._catalog())
            except Exception as e:  # noqa: BLE001
                QApplication.restoreOverrideCursor()
                QMessageBox.critical(self, tr("Import failed"), str(e))
                return
            QApplication.restoreOverrideCursor()
            self.accept()
            return
        if self.profiles_list is not None:
            for i in range(self.profiles_list.count()):
                it = self.profiles_list.item(i)
                if it.checkState() == Qt.CheckState.Checked:
                    call, grid = it.data(Qt.ItemDataRole.UserRole)
                    self.db.save_profile({"name": f"{call} @ {grid}" if grid else call, "station_callsign": call,
                                          "operator": call, "my_gridsquare": grid})
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            label = tr("Import") + f" {Path(self.path).name}"
            self.result = importer.run(self.db, self.qsos, label=label, **self._opts())
        except Exception as e:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, tr("Import failed"), str(e))
            return
        QApplication.restoreOverrideCursor()
        self.accept()

