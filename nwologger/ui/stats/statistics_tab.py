"""The Statistics tab. For now it holds the grid map: filters, which confirmations count, region presets, and the map."""

from __future__ import annotations

import math

from PySide6.QtCore import QLocale, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ... import i18n
from ...core import bands, grids, maptiles
from ...core.grids import SOURCE_NAMES
from .gridmap import MAX_ZOOM, MIN_ZOOM, GridMapView

tr = i18n.tr

# VUCC: confirmed squares an award on that band needs.
VUCC_NEEDS = {"SAT": 100, "6m": 100, "2m": 100, "1.25m": 50, "70cm": 50, "33cm": 25, "23cm": 25}
MODE_GROUPS = [("", "Mixed"), ("PHONE", "Phone"), ("CW", "CW"), ("DATA", "Data"), ("IMAGE", "Image")]


def system_region() -> str:
    """Two-letter code of the system's region (``CN``), for choosing the map provider."""
    return QLocale.territoryToCode(QLocale.system().territory()) if hasattr(QLocale, "territoryToCode") else ""


def _saved_view(value, *, planar: bool = False) -> tuple[float, float, float] | None:
    """Read a globe view, converting the previous map's zoom-zero pixel centre when needed."""
    try:
        zoom, x, y = (float(part) for part in str(value).split(","))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(part) for part in (zoom, x, y)) or not MIN_ZOOM <= zoom <= MAX_ZOOM:
        return None
    if planar:
        if not (0 <= x <= 256 and 0 <= y <= 256):
            return None
        x, y = grids.from_world(x, y, 0)
    elif not (-180 <= x <= 180 and -90 <= y <= 90):
        return None
    return zoom, x, y


class StatisticsTab(QWidget):
    show_square = Signal(str)  # a click on a square: show its QSOs in the log

    def __init__(self, db, prefs, parent=None):
        super().__init__(parent)
        self.db, self.prefs = db, prefs
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        row = QHBoxLayout()
        self.band = QComboBox()
        self.band.setToolTip(tr("Satellite QSOs, one band (without satellites), or all"))
        self.mode = QComboBox()
        for code, name in MODE_GROUPS:
            self.mode.addItem(tr(name), code)
        self.near = QCheckBox(tr("From within"))
        self.near.setToolTip(tr("VUCC and GridMaster count only QSOs made within 200 km of one place. A QSO "
                                "without your grid counts as made there."))
        self.radius = QSpinBox()
        self.radius.setRange(10, 1000)
        self.radius.setSuffix(" km")
        self.radius.setValue(int(grids.RADIUS_KM))
        self.home = QLineEdit()
        self.home.setMaxLength(6)
        self.home.setFixedWidth(72)
        self.home.setPlaceholderText(tr("grid"))
        self.region = QComboBox()
        for key, name in grids.REGION_NAMES.items():
            self.region.addItem(tr(name), key)
        save = QPushButton(tr("Save as PNG"))
        save.clicked.connect(self.save_png)
        for w in (QLabel(tr("Band")), self.band, QLabel(tr("Mode")), self.mode, self.near, self.radius,
                  QLabel(tr("of grid")), self.home, QLabel(tr("Region")), self.region):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(save)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel(tr("Confirmed by:")))
        self.source_btns: dict[str, QPushButton] = {}
        for key, name in SOURCE_NAMES.items():
            b = QPushButton(name)
            b.setCheckable(True)
            b.setProperty("flat", True)
            b.setObjectName("SyncBtn")
            b.toggled.connect(self._changed)
            self.source_btns[key] = b
            row2.addWidget(b)
        self.count = QLabel()
        self.count.setProperty("muted", True)
        self.count.setWordWrap(True)
        row2.addSpacing(12)
        row2.addWidget(self.count, 1)
        self.terminator_btn = QPushButton(tr("Day/night"))
        self.terminator_btn.setCheckable(True)
        self.terminator_btn.setObjectName("TerminatorBtn")
        self.terminator_btn.setToolTip(tr(
            "Show the day/night boundary and night shading for the current UTC time; updates automatically."))
        row2.insertWidget(row2.count() - 1, self.terminator_btn)
        lay.addLayout(row2)
        self.map = GridMapView(self)
        self.map.square_clicked.connect(self.show_square)
        lay.addWidget(self.map, 1)
        hint = QLabel(tr("Drag to rotate · Scroll to zoom") + ". " +
                      tr("Double-click or +/− to zoom, arrow keys to rotate, click a square to show its QSOs. "
                         "Green: confirmed, with the first call that confirmed it; amber: worked only, with "
                         "the first call worked (italics)."))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self._dirty = True
        self._shut = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(800)
        self._timer.timeout.connect(self.refresh)
        self._load_settings()
        self.terminator_btn.toggled.connect(self._terminator_changed)
        for w in (self.band, self.mode, self.region):
            w.currentIndexChanged.connect(self._changed)
        self.region.currentIndexChanged.connect(self._region_changed)
        self.near.toggled.connect(self._changed)
        self.radius.valueChanged.connect(self._changed)
        self.home.editingFinished.connect(self._changed)
        db.add_listener(self._on_db_change)

    # ------------------------------------------------------------------ settings
    def _fill_bands(self, keep: str | None) -> None:
        self.band.blockSignals(True)
        self.band.clear()
        self.band.addItem(tr("Satellite"), "SAT")
        self.band.addItem(tr("All bands"), "")
        for b in sorted({b for b in self.db.distinct("band") if b}, key=bands.band_order):
            self.band.addItem(bands.display_band(b), b)
        i = self.band.findData(keep if keep is not None else "SAT")
        self.band.setCurrentIndex(max(0, i))
        self.band.blockSignals(False)

    def _load_settings(self) -> None:
        v = self.prefs.value
        self._fill_bands(v("map/band", "SAT"))
        self.mode.setCurrentIndex(max(0, self.mode.findData(v("map/mode", ""))))
        self.near.setChecked(str(v("map/near", "true")).lower() in ("true", "1"))
        try:
            self.radius.setValue(int(v("map/radius", int(grids.RADIUS_KM))))
        except (TypeError, ValueError):
            pass
        prof = self.db.default_profile() or {}
        self.home.setText((v("map/home", "") or prof.get("my_gridsquare") or "")[:6].upper())
        self.region.setCurrentIndex(max(0, self.region.findData(v("map/region", "home"))))
        chosen = str(v("map/sources", "lotw")).split(",")
        for key, b in self.source_btns.items():
            b.blockSignals(True)
            b.setChecked(key in chosen)
            b.blockSignals(False)
        terminator = str(v("map/terminator", True)).strip().lower() not in ("false", "0", "off", "no")
        self.terminator_btn.setChecked(terminator)
        self.map.set_terminator_enabled(terminator)
        saved = v("map/globe_view")
        state = _saved_view(saved) if saved is not None else _saved_view(v("map/view"), planar=True)
        if state is not None:
            self.map.set_view_state(state)
        else:
            QTimer.singleShot(0, lambda: self.map.fit(grids.region_box(self.region.currentData(), self.home.text())))
        self.apply_provider()

    def save_settings(self) -> None:
        s = self.prefs.set_value
        s("map/band", self.band.currentData())
        s("map/mode", self.mode.currentData())
        s("map/near", self.near.isChecked())
        s("map/radius", self.radius.value())
        s("map/home", self.home.text().strip().upper())
        s("map/region", self.region.currentData())
        s("map/sources", ",".join(self.sources()))
        s("map/terminator", self.terminator_btn.isChecked())
        s("map/globe_view", ",".join(f"{x:.6f}" for x in self.map.view_state()))

    def sources(self) -> list[str]:
        return [k for k, b in self.source_btns.items() if b.isChecked()]

    def apply_provider(self) -> None:
        """The imagery provider from Preferences → Map (and the Tianditu key from the keychain)."""
        key = self.prefs.tianditu_key()
        provider = maptiles.choose(self.prefs.get("map_provider"), language=i18n.lang(), region=system_region(),
                                   has_key=bool(key))
        self.map.set_provider(provider, key if provider is maptiles.TIANDITU else None)

    # ------------------------------------------------------------------ data
    def _on_db_change(self, kind: str, _ids) -> None:
        if kind in ("insert", "delete", "reset", "update"):
            self._dirty = True
            if self.isVisible():
                self._timer.start()

    def _changed(self, *_a) -> None:
        self._dirty = True
        self.refresh()
        self.save_settings()

    def _terminator_changed(self, enabled: bool) -> None:
        self.map.set_terminator_enabled(enabled)
        self.prefs.set_value("map/terminator", enabled)

    def _region_changed(self, *_a) -> None:
        name = self.region.currentData()
        self.map.highlight = grids.conus() if name == "conus" else frozenset()
        self.map.fit(grids.region_box(name, self.home.text()))

    def showEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().showEvent(e)
        if self._dirty:
            self.refresh()

    def hideEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().hideEvent(e)
        if self._shut:
            return  # the window is closing: shutdown() saved everything
        self.save_settings()
        self.map.fetcher.log_summary(self.map.provider)

    def refresh(self) -> None:
        self._dirty = False
        home = self.home.text().strip().upper() or None
        radius = float(self.radius.value()) if self.near.isChecked() and grids.square(home) else None
        sources = self.sources()
        cells = grids.collect(self.db, sources=sources, band=self.band.currentData() or None,
                              mode_group=self.mode.currentData() or None, home=home, radius_km=radius)
        self.map.home, self.map.radius_km = home, radius
        self.map.highlight = grids.conus() if self.region.currentData() == "conus" else frozenset()
        self.map.set_cells(cells)
        confirmed = sum(1 for c in cells.values() if c.confirmed)
        parts = [f"<b>{confirmed:,}</b> " + tr("confirmed") + " (" +
                 (" + ".join(SOURCE_NAMES[s] for s in sources) or tr("nothing ticked")) + ")",
                 f"<b>{len(cells) - confirmed:,}</b> " + tr("worked only")]
        need = VUCC_NEEDS.get(self.band.currentData())
        if need:
            parts.append(f"VUCC {self.band.currentText()}: {confirmed} / {need}")
        if self.region.currentData() == "conus":
            us = grids.conus()
            parts.append(tr("GridMaster:") + f" {sum(1 for s, c in cells.items() if s in us and c.confirmed)} / 488 " +
                         tr("confirmed") + f", {sum(1 for s in cells if s in us)} " + tr("worked"))
        self.count.setText(" · ".join(parts))

    def save_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("Save as PNG"), "grid-map.png", "PNG (*.png)")
        if path:
            self.map.grab().save(path, "PNG")

    def shutdown(self) -> None:
        if self._shut:
            return
        self._shut = True
        self._timer.stop()
        self.save_settings()
        self.db.remove_listener(self._on_db_change)
        self.map.shutdown()
