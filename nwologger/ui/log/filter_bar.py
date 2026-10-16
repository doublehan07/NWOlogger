"""Quick filter bar: call (wildcards), band, mode (vague by default), sat, country, preset."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QPushButton, QWidget

from ... import i18n
from ...core import bands, modes, search
from .. import theme

tr = i18n.tr


class FilterBar(QWidget):
    changed = Signal(dict)
    advanced_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(6)
        self.call = QLineEdit()
        self.call.setPlaceholderText(tr("Call  (? *)"))
        self.call.setClearButtonEnabled(True)
        self.call.setProperty("mono", True)
        self.call.addAction(theme.icon("search", theme.tokens().muted), QLineEdit.ActionPosition.LeadingPosition)
        self.call.setMinimumWidth(130)
        self.band = QComboBox()
        self.band.addItem(tr("Any band"), "")
        for b in bands.BAND_NAMES:
            self.band.addItem(bands.display_band(b), b)
        self.mode = QComboBox()
        self.mode.setEditable(True)
        self.mode.lineEdit().setPlaceholderText(tr("Any mode"))
        self.mode.addItems(["", "PHONE", "DATA", *modes.COMMON_MODES[:-2]])
        self.mode.setMinimumWidth(120)
        self.exact = QCheckBox(tr("Exact"))
        self.exact.setToolTip(tr("Exact mode match. Off: SSB also finds USB/LSB, DIGI finds FT8, …"))
        self.sat = QComboBox()
        self.sat.setEditable(True)
        self.sat.lineEdit().setPlaceholderText(tr("Any sat"))
        self.sat.setMinimumWidth(110)
        self.country = QLineEdit()
        self.country.setPlaceholderText(tr("Country"))
        self.country.setClearButtonEnabled(True)
        self.preset = QComboBox()
        for key, label in search.PRESETS.items():
            self.preset.addItem(tr(label), key)
        self.more = QPushButton(tr("More filters…"))
        self.more.setIcon(theme.icon("filter"))
        self.more.clicked.connect(self.advanced_requested)
        self.clear_btn = QPushButton(tr("Clear"))
        self.clear_btn.setProperty("flat", True)
        self.clear_btn.clicked.connect(self.clear)
        for w in (self.call, self.band, self.mode, self.exact, self.sat, self.country, self.preset, self.more,
                  self.clear_btn):
            lay.addWidget(w)
        lay.addStretch(1)
        self.conditions: list[dict] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self.emit)
        self.call.textChanged.connect(self._timer.start)
        self.country.textChanged.connect(self._timer.start)
        self.mode.currentTextChanged.connect(self._timer.start)
        self.sat.currentTextChanged.connect(self._timer.start)
        self.band.currentIndexChanged.connect(self.emit)
        self.preset.currentIndexChanged.connect(self.emit)
        self.exact.toggled.connect(self.emit)

    def set_sats(self, names: list[str]) -> None:
        cur = self.sat.currentText()
        self.sat.blockSignals(True)
        self.sat.clear()
        self.sat.addItems(["", *names])
        self.sat.setCurrentText(cur)
        self.sat.blockSignals(False)

    def spec(self) -> dict:
        quick = {
            "call": self.call.text().strip(),
            "band": self.band.currentData() or "",
            "mode": self.mode.currentText().strip(),
            "sat": self.sat.currentText().strip(),
            "country": self.country.text().strip(),
            "preset": self.preset.currentData() or "",
        }
        return {"quick": {k: v for k, v in quick.items() if v}, "conditions": list(self.conditions),
                "mode_exact": self.exact.isChecked()}

    def set_spec(self, spec: dict) -> None:
        q = spec.get("quick") or {}
        for w in (self.call, self.country, self.mode, self.sat, self.band, self.preset, self.exact):
            w.blockSignals(True)
        self.call.setText(q.get("call", ""))
        self.country.setText(q.get("country", ""))
        self.mode.setCurrentText(q.get("mode", ""))
        self.sat.setCurrentText(q.get("sat", ""))
        i = self.band.findData(q.get("band", ""))
        self.band.setCurrentIndex(max(0, i))
        i = self.preset.findData(q.get("preset", "all") or "all")
        self.preset.setCurrentIndex(max(0, i))
        self.exact.setChecked(bool(spec.get("mode_exact")))
        self.conditions = list(spec.get("conditions") or [])
        for w in (self.call, self.country, self.mode, self.sat, self.band, self.preset, self.exact):
            w.blockSignals(False)
        self.emit()

    def set_preset(self, key: str) -> None:
        i = self.preset.findData(key)
        if i >= 0:
            self.preset.setCurrentIndex(i)

    def set_conditions(self, conds: list[dict]) -> None:
        self.conditions = conds
        self.emit()

    def clear(self) -> None:
        self.set_spec({})

    def emit(self) -> None:
        n = len(self.conditions)
        self.more.setText(tr("More filters…") + (f" ({n})" if n else ""))
        self.more.setProperty("primary", bool(n))
        theme.repolish(self.more)
        self.changed.emit(self.spec())
