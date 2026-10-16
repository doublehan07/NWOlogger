"""UI smoke tests (offscreen). All data is synthetic."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings

from qhlogger.core import db as dbm


@pytest.fixture
def win(qtbot, tmp_path):
    from qhlogger.ui import theme
    from qhlogger.ui.main_window import MainWindow
    from qhlogger.ui.prefs import Prefs

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    prefs = Prefs(settings)
    prefs.set("auto_lookup", False)
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    db.save_profile({"name": "BI1ZZZ", "station_callsign": "BI1ZZZ", "operator": "BI1ZZZ",
                     "my_gridsquare": "ON80da", "is_default": True})
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    theme.apply(app, "light")
    w = MainWindow(db, prefs, app)
    qtbot.addWidget(w)
    w.show()
    yield w
    w.shutdown()
    db.close()


def log_one(w, qtbot, call, mode="USB", freq="14.2"):
    p = w.dx
    p.call.setText(call)
    p._call_edited(call)
    p.w["freq"].setText(freq)
    p.mode.setCurrentText(mode)
    p._refresh_checks()
    p.commit()


def test_log_dupe_and_bulk_edit(win, qtbot):
    db = win.db
    log_one(win, qtbot, "BA1AA")
    assert db.count() == 1
    q = db.get(1)
    assert (q["mode"], q["submode"], q["band"], q["station_callsign"]) == ("SSB", "USB", "20m", "BI1ZZZ")
    # Strict dupe within 30 s: blocked.
    win.dx.call.setText("BA1AA")
    win.dx._call_edited("BA1AA")
    win.dx.w["freq"].setText("14.2")
    win.dx.mode.setCurrentText("USB")
    win.dx._refresh_checks()
    assert not win.dx.log_btn.isEnabled()
    win.dx.commit()
    assert db.count() == 1
    # Sibling mode: warning only, logged and marked.
    win.dx.mode.setCurrentText("LSB")
    win.dx._refresh_checks()
    assert win.dx.log_btn.isEnabled()
    win.dx.commit()
    assert db.count() == 2
    assert db.get(2)["dupe_flag"] == "SUSPECT"
    # Bulk edit then undo.
    from qhlogger.ui.bulk_edit import BulkEditDialog

    dlg = BulkEditDialog(db, [1, 2], win)
    row = next(f for f in dlg.fields if f.col == "rst_sent")
    row.value.setText("55")
    dlg._update_preview()
    assert dlg.changed_count == 2
    dlg._apply()
    assert db.get(1)["rst_sent"] == "55"
    win.undo()
    assert db.get(1)["rst_sent"] != "55"


def test_filters_and_export(win, qtbot, tmp_path):
    for c in ("BA1AA", "BG8BB", "BA1CC"):
        log_one(win, qtbot, c, mode="FT8", freq="14.074")
    win.filter.call.setText("BA1*")
    win.filter.emit()
    assert len(win.model.ids) == 2
    win.filter.call.setText("")
    win.filter.mode.setCurrentText("DIGI")
    win.filter.emit()
    assert len(win.model.ids) == 3
    from qhlogger.core import exporter

    out = tmp_path / "out.adi"
    assert exporter.export_ids(win.db, win.model.ids, str(out)) == 3
    assert "<CALL:5>BA1AA" in out.read_text()


def test_time_off_follows_time_on_and_blocks_earlier(win, qtbot):
    p = win.dx
    p.call.setText("BA1AA")
    p._call_edited("BA1AA")
    p.w["time_on"].set_iso("2026-10-10T15:19:00Z")
    p._time_edited("on")
    assert p.w["time_off"].iso() == "2026-10-10T15:20:00Z"
    # Editing only Time Off leaves Time On alone.
    p.w["time_off"].set_iso("2026-10-10T15:25:00Z")
    p._time_edited("off")
    assert p.w["time_on"].iso() == "2026-10-10T15:19:00Z"
    # Time Off earlier than Time On: warned, not logged.
    p.w["time_off"].set_iso("2026-10-10T15:10:00Z")
    p._time_edited("off")
    from PySide6.QtWidgets import QMessageBox

    shown = []
    orig = QMessageBox.warning
    QMessageBox.warning = staticmethod(lambda *a, **k: shown.append(a))
    try:
        p.w["freq"].setText("14.2")
        p.commit()
    finally:
        QMessageBox.warning = orig
    assert shown and win.db.count() == 0


def test_rx_follows_tx_when_synced(win, qtbot):
    p = win.dx
    p.rx_sync.setChecked(True)
    p.w["freq"].setText("7.074")
    assert p.w["freq_rx"].text() == "7.074"
    p.rx_sync.setChecked(False)
    p.w["freq"].setText("7.080")
    assert p.w["freq_rx"].text() == "7.074"


def test_club_station_asks_for_operator(win, qtbot, monkeypatch):
    from qhlogger.ui import operator_dialog

    db = win.db
    pid = db.save_profile({"name": "BY1QQ club", "station_callsign": "BY1QQ", "station_type": "club",
                           "my_gridsquare": "ON80", "is_default": True})
    win.dx.reload_profiles()
    win.dx.profile.setCurrentIndex(win.dx.profile.findData(pid))
    asked = []

    def fake_exec(self):
        asked.append(1)
        self.value = "BI1XYZ"
        return operator_dialog.QDialog.DialogCode.Accepted

    monkeypatch.setattr(operator_dialog.OperatorDialog, "exec", fake_exec)
    log_one(win, qtbot, "BA1AA")
    log_one(win, qtbot, "BA1BB")
    assert len(asked) == 1
    ops = {r[0] for r in db.conn.execute("SELECT operator FROM qso")}
    assert ops == {"BI1XYZ"}


def test_operator_session_rechecks_after_break():
    from datetime import UTC, datetime, timedelta

    from qhlogger.core.operators import OperatorSession

    prof = {"station_callsign": "BY1QQ", "station_type": "club"}
    s = OperatorSession()
    t0 = datetime(2026, 10, 16, 1, 0, tzinfo=UTC)
    assert s.needs_confirmation(prof, t0)
    s.set(prof, "bi1xyz", t0)
    assert s.current(prof) == "BI1XYZ" and not s.needs_confirmation(prof, t0 + timedelta(hours=1))
    assert s.needs_confirmation(prof, t0 + timedelta(hours=4))
    assert s.needs_confirmation(prof, datetime(2026, 10, 17, 0, 10, tzinfo=UTC))
    assert not s.needs_confirmation({"station_callsign": "BI1NWO"}, t0)


def test_lotw_propagation_rules(tmp_path):
    from qhlogger.core import qso, sats
    from qhlogger.core.sync import apply

    d = dbm.LogDB(tmp_path / "p.sqlite", backup_on_open=False)
    d.insert(qso.normalize({"call": "BA1AA", "time_on": "2026-10-10T15:19:00Z", "mode": "FM", "band": "2m",
                            "prop_mode": "RPT"}))
    d.insert(qso.normalize({"call": "BA1BB", "time_on": "2026-10-10T15:20:00Z", "mode": "FM", "band": "2m",
                            "prop_mode": "GWAVE"}))
    pend = apply.pending(d, "lotw")
    assert [q["call"] for q in pend] == ["BA1BB"]
    assert "PROP_MODE" not in apply.upload_record(pend[0], "lotw", sats.load())
    d.close()


def test_dupe_compare_dialog(win, qtbot):
    log_one(win, qtbot, "BA1AA", mode="USB")
    win.dx.mode.setCurrentText("LSB")
    win.dx.call.setText("BA1AA")
    win.dx._call_edited("BA1AA")
    win.dx.w["freq"].setText("14.2")
    win.dx._refresh_checks()
    assert win.dx.compare_btn.isVisibleTo(win.dx)
    win.dx.commit()
    from qhlogger.ui.dupe_dialog import DupeCompareDialog, flagged_ids

    ids = flagged_ids(win.db)
    assert len(ids) == 1
    dlg = DupeCompareDialog(win.db, win.prefs, queue=ids, parent=win)
    assert dlg.table.rowCount() > 10
    dlg._not_dupe()
    assert win.db.get(ids[0])["dupe_flag"] == "OK"
