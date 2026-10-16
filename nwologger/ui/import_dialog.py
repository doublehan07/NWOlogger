"""Import an ADIF file or a MacLoggerDX database, with a dry-run preview first."""

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
    QVBoxLayout,
)

from .. import i18n
from ..core import importer, mldx
from .widgets import Section

tr = i18n.tr


class ImportDialog(QDialog):
    def __init__(self, db, prefs, path: str, parent=None):
        super().__init__(parent)
        self.db, self.prefs, self.path = db, prefs, path
        self.result: importer.ImportResult | None = None
        self.is_mldx = Path(path).suffix.lower() in (".sql", ".sqlite", ".db") and mldx.is_mldx_db(path)
        self.setWindowTitle(tr("Import") + f" — {Path(path).name}")
        self.resize(760, 600)
        lay = QVBoxLayout(self)
        kind = tr("MacLoggerDX database") if self.is_mldx else tr("ADIF file")
        title = QLabel(f"<b>{Path(path).name}</b>  ·  {kind}")
        lay.addWidget(title)
        self.dropped: list[str] = []
        self.inferred: Counter = Counter()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if self.is_mldx:
                self.qsos, self.report = mldx.read(path)
                self.inferred = self.report.inferred
                self.header = {}
            else:
                with open(path, "rb") as fh:
                    self.header, self.qsos = importer.read_adif(fh.read(), dropped=self.dropped,
                                                                inferred=self.inferred)
                self.report = None
        finally:
            QApplication.restoreOverrideCursor()

        counts = Section(tr("Preview"))
        self.counts = QGridLayout()
        counts.add(self.counts)
        lay.addWidget(counts)

        opts = Section(tr("Options"))
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
        self._preview()

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
        for i, (label, n) in enumerate(items):
            big = QLabel(str(n))
            big.setObjectName("Big")
            lab = QLabel(label)
            lab.setProperty("muted", True)
            self.counts.addWidget(big, 0, i)
            self.counts.addWidget(lab, 1, i)
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
        note = QLabel(text)
        note.setObjectName("Hint")
        note.setWordWrap(True)
        self.counts.addWidget(note, 2, 0, 1, max(1, len(items)))

    def _run(self) -> None:
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

