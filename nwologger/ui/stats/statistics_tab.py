"""The Statistics tab. For now it holds the grid map: filters (satellite, band, mode, your callsigns and the
places you operated from), which confirmations count, region presets, the map, and the map in its own window."""

from __future__ import annotations

import logging
import math
import re
from pathlib import Path

from PySide6.QtCore import QLocale, QRect, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import APP_NAME, __version__, i18n
from ...core import bands, grids, maptiles, sats
from ...core.grids import SOURCE_NAMES
from ..widgets import CheckListButton
from .gridmap import MAX_ZOOM, MIN_ZOOM, GlobeSnapshot, GridMapView

tr = i18n.tr
log = logging.getLogger("nwologger.stats")

# VUCC: confirmed squares an award on that band needs.
VUCC_NEEDS = {"SAT": 100, "6m": 100, "2m": 100, "1.25m": 50, "70cm": 50, "33cm": 25, "23cm": 25}
MODE_GROUPS = [("", "Mixed"), ("PHONE", "Phone"), ("CW", "CW"), ("DATA", "Data"), ("IMAGE", "Image")]
# The satellite list's categories: every satellite, each orbit, and Gridmaster's non-geostationary group.
SAT_CATEGORIES = [("", "All satellites"), ("LEO", "LEO · low Earth orbit"), ("MEO", "MEO · medium Earth orbit"),
                  ("HEO", "HEO · highly elliptical orbit"), ("GEO", "GEO · geostationary"),
                  (sats.NON_GEO, "Non-geostationary")]
ALL = "*"  # the saved value for "every callsign" / "every location"
AUTO = "*near"  # the saved value for "the locations within 200 km of my grid"


def png_file_name(calls: list[str], squares: list[str], version: str = __version__) -> str:
    """``BI1NWO at ON80 - NWOlogger 0.1.9.png``: the first call, "(et al)" after it when there are several, and
    the first grid alone (the centre grid, as the caller orders them); a slash in a portable call becomes a hyphen."""
    def part(items: list[str], more: str) -> str:
        clean = [re.sub(r"[^A-Z0-9]+", "-", (x or "").upper()).strip("-") for x in items]
        clean = [x for x in clean if x]
        return clean[0] + (more if len(clean) > 1 else "") if clean else ""

    who, where = part(calls, " (et al)"), part(squares, "")
    head = who + (f" at {where}" if who and where else where)
    return (f"{head} - " if head else "") + f"{APP_NAME} {version}.png"


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


def _saved_rect(value) -> QRect | None:
    """The map window's remembered place, ``x,y,w,h``; None when unreadable or too small to use."""
    try:
        x, y, w, h = (int(float(p)) for p in str(value).split(","))
    except (TypeError, ValueError):
        return None
    return QRect(x, y, w, h) if w >= 300 and h >= 200 else None


class MapPane(QWidget):
    """The controls and the map together: in the tab, or inside a :class:`MapWindow` (Open in a window). Always an
    ordinary child widget: turned into a top-level window and back with window flags, it kept the window's size
    inside the tab on Windows until the main window was resized."""


class MapWindow(QWidget):
    """A window of its own for the pane, sized to the screen's available area the first time and remembered
    afterwards; its close button and Esc hand the pane back to the tab."""

    closing = Signal()  # the window's close button: the pane goes back into its tab
    escape = Signal()

    def __init__(self):
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("NWOlogger · " + tr("Grid map"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)  # the tab's margins

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        event.ignore()  # the tab takes the pane back and disposes of this window
        self.closing.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape:
            self.escape.emit()
            return
        super().keyPressEvent(event)


class StatisticsTab(QWidget):
    show_square = Signal(str)  # a click on a square: show its QSOs in the log

    def __init__(self, db, prefs, parent=None):
        super().__init__(parent)
        self.db, self.prefs = db, prefs
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(8, 6, 8, 6)
        self.pane = MapPane()
        self._window: MapWindow | None = None  # the pane's own window while Open in a window is on
        lay = QVBoxLayout(self.pane)
        lay.setContentsMargins(0, 0, 0, 0)
        self._lay.addWidget(self.pane, 1)
        row = QHBoxLayout()
        self.sat = QComboBox()
        self.sat.setToolTip(tr("One satellite, or an orbit: LEO, MEO (IO-117), HEO (AO-10, AO-13, AO-40), GEO "
                               "(QO-100), or every satellite but the geostationary ones. The list holds every "
                               "satellite NWOlogger knows, worked or not. Edit nwologger/resources/sats.json to "
                               "change an orbit."))
        self.band = QComboBox()
        self.band.setToolTip(tr("Satellite QSOs, one band (without satellites), or all"))
        self.mode = QComboBox()
        for code, name in MODE_GROUPS:
            self.mode.addItem(tr(name), code)
        self.calls = CheckListButton(tr("Callsigns"))
        self.calls.setToolTip(tr("The station callsigns in your log. Tick several when you hold more than one: the "
                                 "squares worked under all of them count together."))
        self.calls.add_quick(tr("All"), lambda: self.calls.set_checked(self.calls.keys()))
        self.calls.add_quick(tr("None"), lambda: self.calls.set_checked([]))
        self.places = CheckListButton(tr("Grids"))
        self.places.setToolTip(tr("The places your log says you operated from, with their distance from your own "
                                  "grid. VUCC and GridMaster count only QSOs made within 200 km of one place: "
                                  "Within 200 km ticks those."))
        self.near_btn = self.places.add_quick(tr("Within 200 km"), self.tick_near)
        self.places.add_quick(tr("All"), lambda: self.places.set_checked(self.places.keys()))
        self.places.add_quick(tr("None"), lambda: self.places.set_checked([]))
        self.home = QLineEdit()
        self.home.setMaxLength(6)
        self.home.setFixedWidth(self.home.fontMetrics().horizontalAdvance("MM00MM") + 24)
        self.home.setPlaceholderText(tr("grid"))
        self.home.setToolTip(tr("Your own grid: the white square on the map, the centre of Around my grid, and "
                                "what Within 200 km measures from."))
        self.region = QComboBox()
        for key, name in grids.REGION_NAMES.items():
            self.region.addItem(tr(name), key)
        save = QPushButton(tr("Save as PNG"))
        save.setToolTip(tr("Save the map as you see it, at twice its size in pixels"))
        save.clicked.connect(self.save_png)
        self.window_btn = QPushButton(tr("Open in a window"))
        self.window_btn.setToolTip(tr("The map and its controls in a resizable window of their own; its size and "
                                      "place are remembered. The system's full-screen button fills the screen."))
        self.window_btn.clicked.connect(self.toggle_window)
        for combo, chars in ((self.sat, 12), (self.band, 6), (self.mode, 5), (self.region, 8)):
            combo.setMinimumContentsLength(chars)  # they prefer their widest entry but give way on a narrow screen
        # First row: which QSOs count. Second row: the view (own grid, region, what confirms, day and night).
        for w in (QLabel(tr("Satellite")), self.sat, QLabel(tr("Band")), self.band, QLabel(tr("Mode")), self.mode,
                  self.calls, self.places):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(self.window_btn)
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
        self.terminator_btn = QPushButton(tr("Day/night"))
        self.terminator_btn.setCheckable(True)
        self.terminator_btn.setObjectName("TerminatorBtn")
        self.terminator_btn.setToolTip(tr(
            "Shade the night side for the current UTC time, with a clear edge along the grey line; updates "
            "automatically."))
        row2.addSpacing(12)
        row2.addWidget(self.terminator_btn)  # next to the sources, so a narrow screen keeps it in view
        row2.addSpacing(12)
        for w in (QLabel(tr("Own grid")), self.home, QLabel(tr("Region")), self.region):
            row2.addWidget(w)
        row2.addStretch(1)
        lay.addLayout(row2)
        self.map = GridMapView(self.pane)
        self.map.square_clicked.connect(self._square_clicked)
        self.map.full_screen_toggled.connect(self.toggle_full_screen)
        self.map.save_requested.connect(self.save_png)
        lay.addWidget(self.map, 1)
        # One line at the usual window width: a second line takes 15 px from the map, which at a whole-country
        # view can be the difference between names and calls shown or not.
        hint = QLabel(tr("Rotate: right-drag (Ctrl + drag on a trackpad) or arrow keys · Zoom: scroll, double-click, "
                         "+/− · Click a square for its QSOs · Green: confirmed, amber: worked only, each with its "
                         "first call (italics: worked only)."))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.away = QLabel(tr("The map is open in its own window."))
        self.away.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.away.setProperty("muted", True)
        self.back_btn = QPushButton(tr("Bring it back"))
        self.back_btn.clicked.connect(self.bring_back)
        self._away_box = QWidget()
        away_lay = QVBoxLayout(self._away_box)
        away_lay.addStretch(1)
        away_lay.addWidget(self.away)
        away_lay.addWidget(self.back_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        away_lay.addStretch(1)
        self._away_box.hide()
        self._lay.addWidget(self._away_box, 1)
        self._dirty = True
        self._shut = False
        self._snapshot: GlobeSnapshot | None = None
        self._progress: QProgressDialog | None = None  # the running export's
        self._counts: dict[str, int] = {}  # QSOs per location key, for the PNG name's order
        self._call_counts: dict[str, int] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(800)
        self._timer.timeout.connect(self._db_changed_later)
        self._load_settings()
        self.terminator_btn.toggled.connect(self._terminator_changed)
        for w in (self.sat, self.band, self.mode, self.region):
            w.currentIndexChanged.connect(self._changed)
        self.band.currentIndexChanged.connect(self._band_changed)
        self.region.currentIndexChanged.connect(self._region_changed)
        self.region.activated.connect(self._region_changed)  # picking the current region again refits it too
        self.calls.changed.connect(self._calls_changed)
        self.places.changed.connect(self._changed)
        self.home.editingFinished.connect(self._home_changed)
        db.add_listener(self._on_db_change)

    # ------------------------------------------------------------------ settings and lists
    def catalog(self) -> sats.SatCatalog:
        return sats.load(overrides=self.prefs.get("sat_names"), extra=self.prefs.get("user_sats"))

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

    def _fill_sats(self, keep: str | None) -> None:
        """Categories first, then every satellite of the list (worked or not) and any name only in the log, each
        with its QSO count."""
        counts = {r[0]: r[1] for r in self.db.conn.execute(
            "SELECT UPPER(sat_name), COUNT(*) FROM qso WHERE sat_name IS NOT NULL AND sat_name != '' GROUP BY 1")}
        cat = self.catalog()
        self.sat.blockSignals(True)
        self.sat.clear()
        total = sum(counts.values())
        for code, name in SAT_CATEGORIES:
            n = total if code == "" else sum(counts.get(s, 0) for s in cat.names_in(code)) + (
                sum(c for s, c in counts.items() if cat.get(s) is None) if code in ("LEO", sats.NON_GEO) else 0)
            self.sat.addItem(f"{tr(name)} ({n:,})", code)
        self.sat.insertSeparator(self.sat.count())
        names = [s.name for s in cat.sats]
        names += sorted(n for n in counts if cat.get(n) is None)
        worked = [n for n in names if counts.get(n.upper())]
        rest = [n for n in names if not counts.get(n.upper())]
        for n in sorted(worked, key=lambda n: -counts[n.upper()]) + rest:
            c = counts.get(n.upper(), 0)
            self.sat.addItem(f"{n} ({c:,})" if c else n, "sat:" + n.upper())
        i = self.sat.findData(keep if keep is not None else "")
        self.sat.setCurrentIndex(max(0, i))
        self.sat.blockSignals(False)

    def _fill_calls(self, keep: list[str] | None) -> None:
        stations = grids.my_stations(self.db)
        self._call_counts = dict(stations)
        chosen = {c.upper() for c in keep} if keep is not None else None
        self.calls.set_items([(c, f"{c}  ·  {n:,} " + tr("QSOs"), chosen is None or c in chosen)
                              for c, n in stations])

    def _fill_places(self, keep: list[str] | None) -> None:
        """The locations of the ticked callsigns; ``keep`` None means the 200 km default."""
        home = self.home.text().strip().upper() or None
        rows = grids.my_locations(self.db, self.calls.checked() or None)
        self._counts = dict(rows)
        if keep is None:
            chosen = set(grids.within([k for k, _n in rows], home)) if grids.square(home) else {k for k, _n in rows}
        else:
            chosen = {k.upper() for k in keep}
        items = []
        for key, n in rows:
            d = grids.distance_from(home, key)
            where = f"{d:,.0f} km" if d is not None else "—"
            items.append((key, f"{key}  ·  {where}  ·  {n:,} " + tr("QSOs"), key in chosen))
        self.places.set_items(items)
        self.near_btn.setEnabled(bool(grids.square(home)))
        self.near_btn.setText(tr("Within 200 km of") + f" {grids.square(home)}" if grids.square(home)
                              else tr("Within 200 km"))

    def _load_settings(self) -> None:
        v = self.prefs.value
        self._fill_bands(v("map/band", "SAT"))
        self._fill_sats(v("map/sat", ""))
        self.mode.setCurrentIndex(max(0, self.mode.findData(v("map/mode", ""))))
        prof = self.db.default_profile() or {}
        self.home.setText((v("map/home", "") or prof.get("my_gridsquare") or "")[:6].upper())
        self.region.setCurrentIndex(max(0, self.region.findData(v("map/region", "home"))))
        calls = v("map/calls", ALL)
        self._fill_calls(None if calls in (ALL, None, "") else str(calls).split(","))
        places = v("map/grids", AUTO)
        self._fill_places(None if places in (AUTO, None) else [p for p in str(places).split(",") if p]
                          if places != "" else [])
        chosen = str(v("map/sources", "lotw")).split(",")
        for key, b in self.source_btns.items():
            b.blockSignals(True)
            b.setChecked(key in chosen)
            b.blockSignals(False)
        terminator = str(v("map/terminator", True)).strip().lower() not in ("false", "0", "off", "no")
        self.terminator_btn.setChecked(terminator)
        self.map.set_terminator_enabled(terminator)
        self.sat.setEnabled(self.band.currentData() == "SAT")
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
        s("map/sat", self.sat.currentData() or "")
        s("map/mode", self.mode.currentData())
        s("map/home", self.home.text().strip().upper())
        s("map/region", self.region.currentData())
        s("map/sources", ",".join(self.sources()))
        s("map/terminator", self.terminator_btn.isChecked())
        s("map/globe_view", ",".join(f"{x:.6f}" for x in self.map.view_state()))
        calls = self.calls.checked()
        s("map/calls", ALL if len(calls) == len(self.calls.keys()) else ",".join(calls))
        s("map/grids", ",".join(self.places.checked()))
        if self._window is not None and not self._window.isFullScreen():
            g = self._window.geometry()
            s("map/window_geometry", f"{g.x()},{g.y()},{g.width()},{g.height()}")

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
            if self.isVisible() or self.in_window():
                self._timer.start()

    def _db_changed_later(self) -> None:
        """New QSOs may bring new callsigns, places or satellites: the lists are rebuilt. The selection keeps its
        meaning: a Callsigns list that had everything ticked ticks a new call too, so the QSOs under it show at
        once; a Grids list on its 200 km default applies that rule to a new place; an explicit choice stays as it
        was, the new entries unticked."""
        self._timer.stop()
        all_calls = len(self.calls.checked()) == len(self.calls.keys())
        self._fill_calls(None if all_calls else self.calls.checked())
        keys, checked = self.places.keys(), set(self.places.checked())
        home = self.home.text().strip().upper()
        near = set(grids.within(keys, home)) if grids.square(home) else set(keys)
        if checked == set(keys):
            keep = [k for k, _n in grids.my_locations(self.db, self.calls.checked() or None)]  # all, new ones too
        elif checked == near:
            keep = None  # the 200 km default, applied to the new places as well
        else:
            keep = sorted(checked)
        self._fill_places(keep)
        self._fill_sats(self.sat.currentData())
        self.refresh()

    def _changed(self, *_a) -> None:
        self._dirty = True
        self.refresh()
        self.save_settings()

    def _band_changed(self, *_a) -> None:
        self.sat.setEnabled(self.band.currentData() == "SAT")

    def _calls_changed(self) -> None:
        self._fill_places(self.places.checked())  # the places of the ticked calls
        self._changed()

    def _home_changed(self) -> None:
        self.home.setText(self.home.text().strip().upper())
        self._fill_places(self.places.checked())  # the distances follow the own grid
        self._changed()

    def tick_near(self) -> None:
        """Tick the places within 200 km of the own grid: VUCC's and GridMaster's rule."""
        home = self.home.text().strip().upper()
        if grids.square(home):
            self.places.set_checked(grids.within(self.places.keys(), home))

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
            self._db_changed_later()  # QSOs logged while the tab was hidden: the lists as well as the squares

    def hideEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().hideEvent(e)
        if self._shut:
            return  # the window is closing: shutdown() saved everything
        self.save_settings()
        self.map.fetcher.log_summary(self.map.provider)

    def sat_filter(self) -> set[str] | None:
        """The satellite names the view counts, or None for every satellite (and for a band view)."""
        if self.band.currentData() != "SAT":
            return None
        code = self.sat.currentData() or ""
        if code == "":
            return None
        if code.startswith("sat:"):
            return {code[4:]}
        cat = self.catalog()
        names = cat.names_in(code)
        if code in ("LEO", sats.NON_GEO):  # a satellite only the log knows counts as LEO
            names |= {n for n in self._call_counts_sats() if cat.get(n) is None}
        return names

    def _call_counts_sats(self) -> set[str]:
        return {r[0] for r in self.db.conn.execute(
            "SELECT DISTINCT UPPER(sat_name) FROM qso WHERE sat_name IS NOT NULL AND sat_name != ''")}

    def refresh(self) -> None:
        self._dirty = False
        home = self.home.text().strip().upper() or None
        sources = self.sources()
        calls = self.calls.checked()
        places = self.places.checked()
        cells = grids.collect(self.db, sources=sources, band=self.band.currentData() or None,
                              mode_group=self.mode.currentData() or None, radius_km=None,
                              sat_names=self.sat_filter(),
                              station_calls=calls if len(calls) < len(self.calls.keys()) else None,
                              my_grids=places if len(places) < len(self.places.keys()) else None)
        # The dashed ring shows the 200 km of VUCC and GridMaster around the own grid (what Within 200 km ticks);
        # the places ticked in Grids decide what counts, not the ring.
        self.map.home, self.map.radius_km = home, grids.RADIUS_KM if grids.square(home) else None
        self.map.highlight = grids.conus() if self.region.currentData() == "conus" else frozenset()
        self.map.set_cells(cells)
        self.map.set_badge(self.summary(cells, sources))
        self.map.update()

    def summary(self, cells: dict[str, grids.Square], sources: list[str]) -> list[tuple[str, str]]:
        """The counts drawn in the map's corner (and in the saved PNG), as ``(text, role)`` runs for
        :meth:`GridMapView.set_badge`: the confirmed count in green, the worked-only count in amber, the VUCC or
        GridMaster progress in bold, the words in white."""
        confirmed = sum(1 for c in cells.values() if c.confirmed)
        runs = [(f"{confirmed:,}", "ok"),
                (" " + tr("confirmed") + " (" + (" + ".join(SOURCE_NAMES[s] for s in sources) or tr("nothing ticked"))
                 + ")", ""),
                (" · ", ""), (f"{len(cells) - confirmed:,}", "warn"), (" " + tr("worked only"), "")]
        need = VUCC_NEEDS.get(self.band.currentData())
        if need:
            runs += [(" · ", ""), (f"VUCC {self.band.currentText()}: ", ""), (f"{confirmed} / {need}", "strong")]
        if self.region.currentData() == "conus":
            us = grids.conus()
            runs += [(" · ", ""), (tr("GridMaster:") + " ", ""),
                     (f"{sum(1 for s, c in cells.items() if s in us and c.confirmed)} / 488", "strong"),
                     (" " + tr("confirmed") + f", {sum(1 for s in cells if s in us)} " + tr("worked"), "")]
        return runs

    # ------------------------------------------------------------------ the map in its own window
    @property
    def map_window(self) -> MapWindow | None:
        """The pane's own window while Open in a window is on."""
        return self._window

    def in_window(self) -> bool:
        return self._window is not None

    def is_full_screen(self) -> bool:
        return self._window is not None and self._window.isFullScreen()

    def toggle_window(self) -> None:
        """The controls and the map in a resizable window of their own, sized to the screen's available area the
        first time and remembered afterwards; or back into the tab. The pane moves between the tab and the window
        as an ordinary child widget, never becoming a window itself."""
        if self.in_window():
            self.bring_back()
            return
        win = self._window = MapWindow()
        win.closing.connect(self.bring_back)
        win.escape.connect(self._escape)
        self._lay.removeWidget(self.pane)
        win.layout().addWidget(self.pane)
        rect = _saved_rect(self.prefs.value("map/window_geometry"))
        screen = self.screen()
        if rect is not None and (screen is None or screen.availableVirtualGeometry().intersects(rect)):
            win.setGeometry(rect)
        elif screen is not None:
            win.setGeometry(screen.availableGeometry())
        self._away_box.show()
        self.window_btn.setText(tr("Bring it back"))
        win.show()
        win.raise_()
        win.activateWindow()
        self.map.setFocus()
        self._show_progress()

    def bring_back(self) -> None:
        win = self._window
        if win is None:
            return
        if not win.isFullScreen():
            g = win.geometry()
            self.prefs.set_value("map/window_geometry", f"{g.x()},{g.y()},{g.width()},{g.height()}")
        else:
            win.showNormal()
        self.map.set_full_screen(False)
        self._window = None
        win.layout().removeWidget(self.pane)
        self._lay.insertWidget(0, self.pane, 1)
        self.pane.show()
        self._lay.activate()  # laid out at the tab's size now, not at the next resize
        win.hide()
        win.deleteLater()
        self._away_box.hide()
        self.window_btn.setText(tr("Open in a window"))
        self.window().activateWindow()
        self.map.setFocus()
        self._show_progress()

    def toggle_full_screen(self) -> None:
        """The full-screen key (F11, the Mac shortcut) or the map's corner button: the pane's window fills the
        screen, or goes back to its size; from the tab it opens the window first."""
        if not self.in_window():
            self.toggle_window()
        win = self._window
        if win.isFullScreen():
            win.showNormal()
            self.map.set_full_screen(False)
        else:
            win.showFullScreen()
            self.map.set_full_screen(True)

    def _escape(self) -> None:
        """Esc in the window: leave full screen first, then bring the map back into its tab."""
        if self.is_full_screen():
            self.toggle_full_screen()
        else:
            self.bring_back()

    def _square_clicked(self, sq: str) -> None:
        self.show_square.emit(sq)  # the QSOs are shown in the log; the map stays where it is
        if self.in_window():
            self.window().raise_()
            self.window().activateWindow()

    # ------------------------------------------------------------------ PNG
    def png_name(self) -> str:
        """The file name of a saved map: the ticked callsigns (most QSOs first) and the centre grid, the own grid
        when it is among the ticked places, else the place with most QSOs, as :func:`png_file_name` writes them;
        the default profile's call and the own grid when the lists are empty."""
        calls = sorted(self.calls.checked(), key=lambda c: -self._call_counts.get(c, 0))
        own = grids.square(self.home.text()) or ""
        squares: list[str] = []
        # the own grid's place first, then most QSOs first, then the name
        for key in sorted(self.places.checked(), key=lambda k: (not (own and k.startswith(own)),
                                                                -self._counts.get(k, 0), k)):
            sq = grids.square(key.split(",")[0])
            if sq and sq not in squares:
                squares.append(sq)
        if not calls:
            prof = self.db.default_profile() or {}
            calls = [(prof.get("operator") or prof.get("station_callsign") or "").strip()]
        if not squares:
            squares = [grids.square(self.home.text()) or ""]
        return png_file_name(calls, squares)

    def _dialog_owner(self) -> QWidget:
        """Where dialogs about the map open: on the map's own window while it has one."""
        return self._window if self._window is not None else self

    def _show_progress(self) -> None:
        """Bring the running export's progress window to the front, on the window that shows the map. It is never
        modal: a modal sheet moved from the map's own window to the main window (the window closed during an
        export) could stay invisible on macOS while still blocking every click until the export's 60 s deadline."""
        progress = self._progress
        if progress is None:
            return
        owner = self._dialog_owner()
        if progress.parentWidget() is not owner:
            progress.hide()
            progress.setParent(owner, progress.windowFlags())
        progress.setWindowModality(Qt.WindowModality.NonModal)
        progress.show()
        progress.raise_()
        progress.activateWindow()

    def save_png(self) -> None:
        """The map as you see it, at twice its size, once its imagery is in (a moment for finer tiles). The dialogs
        open on the map's own window when it has one. Asked again while one runs: its progress comes forward."""
        if self._snapshot is not None:
            self._show_progress()
            return
        owner = self._dialog_owner()
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation) or str(Path.home())
        path, _ = QFileDialog.getSaveFileName(owner, tr("Save as PNG"), str(Path(folder) / self.png_name()),
                                              "PNG (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        progress = self._progress = QProgressDialog(tr("Drawing the map…"), tr("Cancel"), 0, 0, owner)
        progress.setWindowTitle(tr("Save as PNG"))
        progress.setWindowModality(Qt.WindowModality.NonModal)  # the export draws a copy: nothing needs blocking
        progress.setMinimumDuration(500)
        progress.setValue(0)  # without a value Qt waits four seconds before showing it
        view = self.map.export_view()
        view.caption = Path(self.png_name()).stem  # the band above the map carries the file's own name
        snapshot = self._snapshot = GlobeSnapshot(view, parent=self)

        def done() -> None:
            self._snapshot = self._progress = None
            progress.close()
            progress.deleteLater()

        def finished(image: QImage) -> None:
            done()
            if image.convertToFormat(QImage.Format.Format_RGB32).save(path, "PNG"):
                log.info("Grid map saved as a %d × %d PNG", image.width(), image.height())
            else:
                QMessageBox.warning(self._dialog_owner(), tr("Save as PNG"),
                                    tr("Could not save the image:") + f"\n{path}")

        def cancelled() -> None:
            if self._snapshot is snapshot:
                done()
                snapshot.cancel()

        snapshot.finished.connect(finished)
        progress.canceled.connect(cancelled)
        snapshot.start()

    def shutdown(self) -> None:
        if self._shut:
            return
        self._shut = True
        self._timer.stop()
        if self._snapshot is not None:
            self._snapshot.cancel()
            self._snapshot = None
        if self._progress is not None:
            self._progress.close()
            self._progress = None
        if self.in_window():
            self.bring_back()
        self.save_settings()
        self.db.remove_listener(self._on_db_change)
        self.map.shutdown()
