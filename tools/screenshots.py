"""Render README screenshots off-screen from synthetic data only (no real log, no real operators).

    uv run python tools/screenshots.py            # writes docs/screenshots/*.png
"""

from __future__ import annotations

import os
import random
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["NWOLOGGER_OFFLINE"] = "1"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="nwologger-shots-"))
os.environ["NWOLOGGER_SETTINGS"] = str(TMP / "settings.ini")  # never touch your real preferences
app = QApplication(sys.argv)

from nwologger import i18n  # noqa: E402
from nwologger.core import db as dbm  # noqa: E402
from nwologger.core import dupes, importer, qso, secrets  # noqa: E402
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
        else:
            app.quit()

    QTimer.singleShot(300, step)
    app.exec()


if __name__ == "__main__":
    main()
