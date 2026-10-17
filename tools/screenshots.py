"""Render README screenshots off-screen from synthetic data only (no real log, no real operators).

    uv run python tools/screenshots.py            # writes docs/screenshots/*.png
    uv run python tools/screenshots.py --offline  # the grid map without satellite imagery

The grid map shots fetch NASA Blue Marble tiles (public, no key) into a temporary cache; nothing else goes online.
"""

from __future__ import annotations

import os
import random
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ["NWOLOGGER_OFFLINE"] = "1"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="nwologger-shots-"))
# An off-screen desktop large enough for the 1480 × 900 window, which fits itself to the available desktop.
(TMP / "screen.json").write_text('{"screens": [{"name": "shots", "x": 0, "y": 0, "width": 2560, "height": 1600, '
                                 '"logicalDpi": 96, "logicalBaseDpi": 96, "dpr": 1}]}')
os.environ.setdefault("QT_QPA_PLATFORM", f"offscreen:configfile={TMP / 'screen.json'}")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

os.environ["NWOLOGGER_SETTINGS"] = str(TMP / "settings.ini")  # never touch your real preferences
os.environ["NWOLOGGER_CACHE_DIR"] = str(TMP / "cache")  # nor your map tile cache
IMAGERY = "--offline" not in sys.argv
app = QApplication(sys.argv)

from nwologger import i18n  # noqa: E402
from nwologger.core import db as dbm  # noqa: E402
from nwologger.core import dupes, geo, grids, importer, qso, secrets  # noqa: E402
from nwologger.ui import theme  # noqa: E402
from nwologger.ui.main_window import MainWindow  # noqa: E402
from nwologger.ui.prefs import Prefs  # noqa: E402
from nwologger.ui.widgets import set_combo_data  # noqa: E402

_fake_keychain: dict = {}  # never read or write the real keychain
secrets._keyring = lambda: type("FakeKeyring", (), {
    "errors": __import__("keyring").errors,
    "get_password": staticmethod(lambda s, a: _fake_keychain.get((s, a))),
    "set_password": staticmethod(lambda s, a, v: _fake_keychain.__setitem__((s, a), v)),
    "delete_password": staticmethod(lambda s, a: _fake_keychain.pop((s, a), None)),
})

PREFIXES = ["BA1", "BG2", "BH4", "BD7", "BI8", "JA1", "JH3", "HL2", "DS5", "UA9", "DL1", "DK3", "F5", "G4", "I2",
            "K1", "W6", "N7", "VE3", "VK2", "ZL1", "PY2", "LU5", "OH2", "SM5", "OK1", "SP9", "YB1", "9M2", "HS0"]


def fake_calls(n: int, rng: random.Random) -> list[str]:
    """Obviously synthetic calls: real prefixes with TS? suffixes."""
    out = set()
    while len(out) < n:
        out.add(rng.choice(PREFIXES) + "TS" + rng.choice("ABCDEFGHJKLMNPRSUVWXYZ"))
    return sorted(out)


def build_db(path: Path) -> dbm.LogDB:
    db = dbm.LogDB(path, backup_on_open=False)
    with open(ROOT / "tests" / "fixtures" / "sample.adi", "rb") as fh:
        _h, qsos = importer.read_adif(fh.read())
    importer.run(db, qsos)
    rng = random.Random(7)
    t0 = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
    plan = [("20m", 14.074, "FT8"), ("20m", 14.250, "USB"), ("40m", 7.025, "CW"), ("15m", 21.074, "FT8"),
            ("2m", 145.85, "FM"), ("10m", 28.480, "USB"), ("30m", 10.136, "FT8")]
    rows = []
    for i, call in enumerate(fake_calls(60, rng)):
        band, f, mode = rng.choice(plan)
        q = {"call": call, "time_on": (t0 + timedelta(minutes=17 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "band": band,
             "freq": f, "freq_rx": f, "mode": mode, "rst_sent": "-10" if mode == "FT8" else "59",
             "rst_rcvd": "-08" if mode == "FT8" else "59", "station_callsign": "BY1QH", "operator": "BI1NWO",
             "my_gridsquare": "ON80da", "tx_pwr": 50}
        if mode == "FM":
            q.update(prop_mode="SAT", sat_name="SO-50", freq=145.85, freq_rx=436.795, band_rx="70cm")
        if rng.random() < 0.55:
            q.update(lotw_qsl_sent="Y", lotw_qslsdate="2026-10-15")
            if rng.random() < 0.6:
                q.update(lotw_qsl_rcvd="Y", lotw_qslrdate="2026-10-16")
        rows.append(qso.normalize(q))
    # A group of three possible dupes for the compare window.
    for t, m in (("2026-10-16T08:00:00Z", "FT8"), ("2026-10-16T08:00:15Z", "FT8"), ("2026-10-16T08:12:00Z", "FT4")):
        rows.append(qso.normalize({"call": "JA1TSQ", "time_on": t, "band": "20m", "freq": 14.074, "mode": m,
                                   "rst_sent": "-12", "rst_rcvd": "-09", "station_callsign": "BY1QH",
                                   "operator": "BI1NWO", "my_gridsquare": "ON80da"}))
    importer.run(db, rows)
    db.save_profile({"name": "BY1QH club", "station_callsign": "BY1QH", "station_type": "club",
                     "my_gridsquare": "ON80da", "my_cq_zone": 24, "my_itu_zone": 44, "my_dxcc": 318,
                     "tx_pwr": 50, "is_default": True})
    dupes.rescan(db)
    return db


# The grid map's clock: evening in East Asia, so the twilight crosses the views.
MAP_TIME = datetime(2026, 10, 17, 9, 40, tzinfo=UTC)


def add_grid_qsos(db: dbm.LogDB) -> None:
    """Made-up satellite QSOs from ON80 to squares within a low-orbit footprint, and HF FT8 QSOs around the world."""
    rng = random.Random(11)
    home = geo.grid_to_latlon("ON80da")
    letters = "ABCDEFGHJKLMNPRSUVWXYZ"

    def call_for(lat: float, lon: float) -> str:
        if lon > 129 and lat > 30:
            prefix = rng.choice(["JA1", "JH3", "JR6", "JE7"])
        elif 124 <= lon <= 130 and 33 <= lat <= 39:
            prefix = rng.choice(["HL2", "DS5"])
        elif lat > 42 and lon < 120:
            prefix = rng.choice(["JT1", "UA0", "UN7"])
        else:
            prefix = rng.choice(["BA1", "BG2", "BH4", "BD7", "BI8", "BG6", "BD5", "BA4", "BH1", "BG9"])
        return prefix + "TS" + rng.choice(letters)

    def qso_at(sq: str, when: datetime, sat: bool) -> dict:
        lat, lon = grids.center(sq)
        q = {"call": call_for(lat, lon) if sat else rng.choice(PREFIXES) + "TS" + rng.choice(letters),
             "time_on": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "gridsquare": sq + rng.choice("abcdefgh") +
             rng.choice("abcdefgh"), "my_gridsquare": "ON80da", "station_callsign": "BY1QH", "operator": "BI1NWO",
             "lotw_qsl_sent": "Y"}
        if sat:
            q.update(band="2m", freq=145.85, band_rx="70cm", freq_rx=436.795, mode="FM", prop_mode="SAT",
                     sat_name=rng.choice(["SO-50", "RS-44", "AO-91", "ISS", "PO-101", "IO-117"]))
        else:
            q.update(band="20m", freq=14.074, mode="FT8")
        if rng.random() < 0.6:
            q.update(lotw_qsl_rcvd="Y", lotw_qslrdate="2026-10-16")
        return qso.normalize(q)

    rows, when = [], datetime(2025, 3, 1, 12, 0, tzinfo=UTC)
    for sq in grids.squares_in(72, 15, 146, 55):
        distance = geo.distance_km(home, grids.center(sq))
        chance = 0.8 if distance < 900 else 0.55 if distance < 1800 else 0.3 if distance < 2600 else 0
        if rng.random() < chance:
            when += timedelta(hours=7)
            rows.append(qso_at(sq, when, sat=True))
    for west, south, east, north, count in ((-10, 36, 40, 60, 60), (-125, 28, -70, 50, 45), (-75, -35, -40, 5, 18),
                                            (10, -30, 40, 10, 14), (115, -38, 153, -18, 16), (60, 10, 100, 45, 25)):
        for _ in range(count):
            lon, lat = rng.uniform(west, east), rng.uniform(south, north)
            when += timedelta(hours=5)
            rows.append(qso_at(next(grids.squares_in(lon, lat, lon, lat)), when, sat=False))
    importer.run(db, rows)


def wait_for_tiles(view, then, *, scale: float = 1.0, deadline: float = 45.0) -> None:
    """Paint the view until no tile has arrived for two seconds (the cache and NASA answer quickly), then go on."""
    import time

    from PySide6.QtGui import QImage, QPainter

    start, state = time.monotonic(), {"revision": -1, "since": time.monotonic()}

    def poll() -> None:
        image = QImage(view.size() * scale, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(scale)
        painter = QPainter(image)
        view._paint_scene(painter)  # asks for the tiles in view
        painter.end()
        now = time.monotonic()
        if view._tile_revision != state["revision"]:
            state["revision"], state["since"] = view._tile_revision, now
        if not IMAGERY or now - state["since"] > 2.0 or now - start > deadline:
            then()
        else:
            QTimer.singleShot(250, poll)

    poll()


def save_jpeg(widget, name: str) -> None:
    """Satellite imagery is a photograph: as a JPEG it is a fifth of the PNG's size."""
    app.processEvents()
    widget.grab().toImage().save(str(OUT / f"{name}.jpg"), "JPEG", 90)
    print("wrote", OUT / f"{name}.jpg")


def save_map(view, name: str, scale: float = 2.0) -> None:
    """The globe alone at twice the resolution, so names and calls stay readable."""
    from PySide6.QtGui import QImage, QPainter

    image = QImage(view.size() * scale, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(scale)
    painter = QPainter(image)
    view._paint_scene(painter)
    painter.end()
    image.convertToFormat(QImage.Format.Format_RGB32).save(str(OUT / f"{name}.jpg"), "JPEG", 88)
    print("wrote", OUT / f"{name}.jpg")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    db = build_db(TMP / "nwolog.sqlite")
    prefs = Prefs()
    prefs.set("rigs", ["FT-991A", "IC-705", "IC-9700"])
    prefs.set("antennas", ["Inverted V", "Yagi 2m/70cm", "Vertical"])
    prefs.set("setups", [{"name": "Home HF", "rig": "FT-991A", "antenna": "Inverted V", "power": 100},
                         {"name": "Sat portable", "rig": "IC-705", "antenna": "Yagi 2m/70cm", "power": 5},
                         {"name": "VHF base", "rig": "IC-9700", "antenna": "Vertical", "power": 25}])
    state = {"step": 0}
    shots: list = []

    def make_window(mode: str, lang: str = "en") -> MainWindow:
        i18n.load(lang)
        theme.apply(app, mode)
        w = MainWindow(db, prefs, app)
        w.resize(1480, 900)
        w.show()
        w.ops.set(db.default_profile(), "BI1NWO")
        w.dx._update_operator()
        return w

    def save(widget, name: str) -> None:
        app.processEvents()
        widget.grab().save(str(OUT / f"{name}.png"))
        print("wrote", OUT / f"{name}.png")

    def fill_entry(w: MainWindow) -> None:
        p = w.dx
        p.call.setText("JA1TSQ")
        p._call_edited("JA1TSQ")
        p.w["freq"].setText("14.074")
        set_combo_data(p.mode, "FT8")
        p.w["time_on"].set_iso("2026-10-16T08:12:20Z")
        p._time_edited("on")
        p.setup.setCurrentIndex(1)
        p._setup_picked(1)
        p.w["comment"].setText("Tnx QSO 73")
        p._refresh_checks()

    def step():
        s = state["step"]
        state["step"] += 1
        if s == 0:
            w = make_window("light")
            fill_entry(w)
            shots.append(w)
            QTimer.singleShot(500, lambda: (save(w, "main"), step()))
        elif s == 1:
            from nwologger.ui.dupe_dialog import DupeCompareDialog, flagged_ids

            w = shots[-1]
            groups = dupes.groups_for(db, flagged_ids(db))
            g = next((g for g in groups if len(g) >= 3), groups[0])
            d = DupeCompareDialog(db, prefs, groups=[g], parent=w)
            d.resize(1000, 760)
            d.show()
            QTimer.singleShot(400, lambda: (save(d, "compare"), d.close(), step()))
        elif s == 2:
            from nwologger.ui.prefs_dialog import PrefsDialog

            w = shots[-1]
            d = PrefsDialog(db, prefs, w, page="Modes")
            d.show()
            QTimer.singleShot(400, lambda: (save(d, "prefs-modes"), d.close(), step()))
        elif s == 3:
            from nwologger.ui.prefs_dialog import PrefsDialog

            w = shots[-1]
            d = PrefsDialog(db, prefs, w, page="Devices")
            d.show()
            QTimer.singleShot(400, lambda: (save(d, "prefs-devices"), d.close(), step()))
        elif s == 4:
            w = shots[-1]
            sid = db.create_contest_session("CQWW-CW", "CQ WW DX Contest (CW) 2026", "2026-11-28T00:00:00Z",
                                            {"sent": {"rst": "599", "zone": "24"},
                                             "categories": {"CATEGORY-OPERATOR": "SINGLE-OP"}},
                                            db.default_profile()["id"])
            prefs.set("current_contest", sid)
            w.contest.reload_sessions(select=sid)
            w.set_view(1)
            c = w.contest
            c.op_field.setText("BI1NWO")
            c.freq.setText("14025")
            c._freq_set()
            for call, z in (("JA1TST", "25"), ("K1TST", "5"), ("DL1TST", "14"), ("VK2TST", "30"), ("UA9TST", "17"),
                            ("PY2TST", "11"), ("ZL1TST", "32")):
                c.call.setText(call)
                c._call_edited(call)
                c.fields["zone"].setText(z)
                c.refresh_checks()
                c.commit()
            c.call.setText("JA1TS")
            c._call_edited("JA1TS")
            c.refresh_checks()
            QTimer.singleShot(500, lambda: (save(w, "contest"), w.set_view(0), w.close(), step()))
        elif s == 5:
            w = make_window("dark")
            fill_entry(w)
            shots.append(w)
            QTimer.singleShot(500, lambda: (save(w, "dark"), w.close(), step()))
        elif s == 6:
            w = make_window("light", "zh_CN")
            fill_entry(w)
            shots.append(w)
            QTimer.singleShot(500, lambda: (save(w, "zh_CN"), w.close(), step()))
        elif s == 7:
            from nwologger.ui.stats import gridmap
            from nwologger.ui.stats.statistics_tab import StatisticsTab

            add_grid_qsos(db)  # only now, so the other screenshots stay as they were
            gridmap._utc_now = lambda: MAP_TIME
            i18n.load("en")
            theme.apply(app, "light")
            tab = StatisticsTab(db, prefs)  # the Statistics tab on its own, so the map gets the room
            tab.resize(1400, 860)
            tab.show()
            shots.append(tab)
            tab.map.fetcher.offline = not IMAGERY
            tab.region.setCurrentIndex(tab.region.findData("china"))
            app.processEvents()
            tab.map.zoom += 0.5  # half a step inside the whole country: names and calls appear
            tab.map.update()
            wait_for_tiles(tab.map, lambda: (save_jpeg(tab, "gridmap"), step()))
        elif s == 8:
            m = shots[-1].map
            m.devicePixelRatioF = lambda: 2.0
            m.set_view_state((6.3, 116.5, 39.5))
            wait_for_tiles(m, lambda: (save_map(m, "gridmap-detail"), step()), scale=2.0)
        elif s == 9:
            tab = shots[-1]
            tab.band.setCurrentIndex(tab.band.findData(""))  # all bands: the HF squares around the world too
            m = tab.map
            m.set_view_state((2.9, 60.0, 30.0))
            wait_for_tiles(m, lambda: (save_map(m, "gridmap-world"), tab.shutdown(), tab.close(), step()), scale=2.0)
        else:
            app.quit()

    QTimer.singleShot(300, step)
    app.exec()


if __name__ == "__main__":
    main()
