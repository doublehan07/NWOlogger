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


def test_club_station_operator_field(win, qtbot):
    from datetime import timedelta

    from qhlogger.core import timeutil

    db = win.db
    pid = db.save_profile({"name": "BY1QQ club", "station_callsign": "BY1QQ", "station_type": "club",
                           "my_gridsquare": "ON80", "is_default": True})
    win.dx.reload_profiles()
    win.dx.profile.setCurrentIndex(win.dx.profile.findData(pid))
    win.dx.op_field.clear()
    log_one(win, qtbot, "BA1AA")
    assert db.count() == 0 and win.dx.op_field.property("opstate") == "bad"  # empty operator: not logged
    win.dx.op_field.setText("BI1XYZ")
    win.dx.commit()
    assert db.count() == 1
    log_one(win, qtbot, "BA1BB")
    assert db.count() == 2  # kept for the session
    # After a break the field asks for a check once; a second Enter logs.
    prof = db.profile(pid)
    win.ops.set(prof, "BI1XYZ", timeutil.now_utc() - timedelta(hours=4))
    log_one(win, qtbot, "BA1CC")
    assert db.count() == 2 and win.dx.op_field.property("opstate") == "check"
    win.dx.commit()
    assert db.count() == 3
    assert {r[0] for r in db.conn.execute("SELECT operator FROM qso")} == {"BI1XYZ"}


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


def test_dupe_group_of_three(win, qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from qhlogger.core import dupes, qso
    from qhlogger.ui.dupe_dialog import DupeCompareDialog, flagged_ids

    db = win.db
    rows = [("2026-10-10T10:00:00Z", "FT8", None), ("2026-10-10T10:00:20Z", "FT8", None),
            ("2026-10-10T10:10:00Z", "FT4", None)]
    ids = [db.insert(qso.normalize({"call": "BA1AA", "time_on": t, "mode": m, "band": "20m", "freq": 14.074}))
           for t, m, _ in rows]
    db.update(ids[2], {"lotw_qsl_rcvd": "Y", "lotw_qslrdate": "2026-10-12"}, track_service=False)
    dupes.mark(db, ids)
    groups = dupes.groups_for(db, flagged_ids(db))
    assert groups == [ids]  # one group holding all three
    dlg = DupeCompareDialog(db, win.prefs, groups=groups, parent=win)
    assert dlg.table.columnCount() == 3
    # Edit a cell and save.
    from qhlogger.ui.dupe_dialog import ROWS

    r = ROWS.index("rst_sent")
    dlg.table.item(r, 1).setText("-05")
    assert dlg.edits and dlg.save_btn.isEnabled()
    dlg._save()
    assert db.get(ids[1])["rst_sent"] == "-05"
    # Keep the first, merge the others into it: the LoTW confirmation moves over.
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dlg.keep_and_merge(0, confirm=False)
    assert db.count() == 1
    kept = db.get(ids[0])
    assert kept["lotw_qsl_rcvd"] == "Y" and kept["dupe_flag"] is None


def test_sat_name_without_sat_propagation(win, qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from qhlogger.ui.widgets import set_combo_data

    p = win.dx
    p.call.setText("BA1AA")
    p._call_edited("BA1AA")
    set_combo_data(p.sat, "ARISS")
    set_combo_data(p.prop, "ES")
    assert p.sat_prop_warning()
    p.w["freq"].setText("145.99")
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.Yes)
    p.commit()
    assert asked and win.db.count() == 1
    row = win.db.get(1)
    assert row["sat_name"] == "ARISS" and row["prop_mode"] == "ES"


def test_custom_mode_list(win, qtbot):
    from qhlogger.ui.widgets import OTHER, fill_mode_combo

    win.prefs.set("mode_list", ["CW", "FT8"])
    fill_mode_combo(win.dx.mode, win.prefs)
    items = [win.dx.mode.itemData(i) for i in range(win.dx.mode.count()) if win.dx.mode.itemData(i)]
    assert items == ["CW", "FT8", OTHER]




def test_time_on_now_returns_to_the_default(win, qtbot, monkeypatch):
    from qhlogger.core import timeutil

    p = win.dx
    p.call.setText("BA1AA")
    p._call_edited("BA1AA")
    p.w["time_on"].set_iso("2026-10-10T15:19:00Z")
    p._time_edited("on")
    assert p.time_off_mode == "follow" and p.w["time_off"].iso() == "2026-10-10T15:20:00Z"
    monkeypatch.setattr(timeutil, "now_iso", lambda: "2026-10-16T08:00:00Z")
    p.stamp("on")
    assert p.w["time_on"].iso() == p.w["time_off"].iso() == "2026-10-16T08:00:00Z"
    assert p.time_off_mode == "live" and p.time_on_locked  # a call is entered: Time On is held
    monkeypatch.setattr(timeutil, "now_iso", lambda: "2026-10-16T08:00:30Z")
    p._tick()
    assert p.w["time_on"].iso() == "2026-10-16T08:00:00Z" and p.w["time_off"].iso() == "2026-10-16T08:00:30Z"
    # Without a call, Time On keeps ticking too, like a fresh form.
    p.clear_form(full=True)
    p.w["time_on"].set_iso("2026-10-10T15:19:00Z")
    p._time_edited("on")
    p.stamp("on")
    monkeypatch.setattr(timeutil, "now_iso", lambda: "2026-10-16T08:01:00Z")
    p._tick()
    assert p.w["time_on"].iso() == p.w["time_off"].iso() == "2026-10-16T08:01:00Z"


def test_clear_empties_the_contact_section_whatever_keep_says(win, qtbot):
    from qhlogger.ui.widgets import combo_value, set_combo_data

    p = win.dx
    for cb in p.sticky.values():
        cb.setChecked(True)
    p.call.setText("BA1AA")
    p._call_edited("BA1AA")
    set_combo_data(p.sat, "SO-50")
    p.rx_sync.setChecked(False)
    p.w["freq"].setText("145.85")
    p.w["freq_rx"].setText("436.795")
    p.mode.setCurrentText("FM")
    for col, v in (("rst_sent", "59"), ("rst_rcvd", "57"), ("tx_pwr", "25")):
        p.w[col].setText(v)
    set_combo_data(p.prop, "SAT")
    p.clear_form(full=True)
    for col in ("freq", "freq_rx", "rst_sent", "rst_rcvd", "tx_pwr"):
        assert p.w[col].text() == "", col
    assert combo_value(p.mode) is None and combo_value(p.prop) is None and combo_value(p.sat) is None
    assert p.call.text() == ""
    # Keep still applies after Log QSO.
    p.call.setText("BA1BB")
    p._call_edited("BA1BB")
    p.w["freq"].setText("14.2")
    p.mode.setCurrentText("USB")
    p.w["tx_pwr"].setText("100")
    p.commit()
    assert win.db.count() == 1
    assert p.w["freq"].text() == "14.2" and combo_value(p.mode) == "USB" and p.w["tx_pwr"].text() == "100"


def test_log_needs_a_mode_and_uses_the_profile_power(win, qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    p = win.dx
    prof = win.db.default_profile()
    prof["tx_pwr"] = 25.0
    win.db.save_profile(prof)
    p.clear_form(full=True)
    assert p.w["tx_pwr"].text() == "" and p.w["tx_pwr"].placeholderText() == "25 W"
    assert p.w["rst_sent"].text() == ""  # no mode, no default report
    p.call.setText("BA1AA")
    p._call_edited("BA1AA")
    p.w["freq"].setText("14.2")
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[1])))
    p.commit()
    assert shown == ["Missing mode"] and win.db.count() == 0
    p.mode.setCurrentText("CW")
    assert p.w["rst_sent"].text() == "599"
    p.commit()
    assert win.db.count() == 1 and win.db.get(1)["tx_pwr"] == 25


def test_similar_calls_in_worked_before(win, qtbot):
    log_one(win, qtbot, "BA1PK")
    p = win.dx
    win.db.cache_put("BA1PX", "QRZ.com", {"call": "BA1PX"})
    p.note_known("BA1PX", "QRZ.com")
    p._index.set_scp({"BA1PQ", "BA1PK"})
    p.call.setText("BA1P")
    p._call_edited("BA1P")
    p._refresh_checks()
    rows = [(p.similar.item(r, 0).text(), p.similar.item(r, 1).text()) for r in range(p.similar.rowCount())]
    assert [c for c, _ in rows] == ["BA1PK", "BA1PX", "BA1PQ"]
    assert rows[0][1].startswith("1×") and "QRZ.com" in rows[1][1] and "MASTER.SCP" in rows[2][1]
    p._pick_similar(1, 0)
    assert p.call.text() == "BA1PX"


def test_unknown_call_checks_one_edit_candidates_online(win, qtbot, monkeypatch):
    from qhlogger.core.lookup import clients as lookup_clients

    class FakeClient:
        name = "QRZ.com"

        def secrets(self):
            return []

    win._lookup_clients = [FakeClient()]
    asked = []

    def chain(call, _clients):
        asked.append(call)
        return ({"call": call, "first_name": "Li"}, "QRZ.com", []) if call == "BA1PZ" else (None, None, [])

    monkeypatch.setattr(lookup_clients, "lookup_chain", chain)
    win.dx._index.set_scp({"BA1PQ", "BA1PZ", "BA1PWX", "BA1PY", "BA1AA"})
    win.dx.call.setText("BA1PW")
    win.dx._call_edited("BA1PW")
    win.lookup("BA1PW")
    qtbot.waitUntil(lambda: "BA1PZ" in win.dx._index.known)
    assert asked[0] == "BA1PW" and sorted(asked[1:]) == ["BA1PQ", "BA1PY", "BA1PZ"]
    assert win.db.cache_get("BA1PZ", 30)
    # Each candidate is asked once per run: the next "not found" checks the one left.
    win.lookup("BA1PW")
    qtbot.waitUntil(lambda: "BA1PWX" in asked)
    assert asked.count("BA1PQ") == 1


def test_saved_password_check(win, qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    info, boxes = [], []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda _p, _t, text, *a, **k: info.append(text)))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: boxes.append(self.text()) or 0)
    win.check_secrets("menu")
    assert info and "changed since can't be recognised" in info[-1] and not boxes
    # A saved HRDLog code in an old history message and in a backup: patterns can't see it, the value can.
    win.prefs.set("hrdlog_call", "BI1ZZZ")
    win.prefs.set_secret("hrdlog_code", "FAKE_HRD_CODE")
    win.db.conn.execute("INSERT INTO sync_log(service, direction, started_at, errors, message) VALUES "
                        "('HRDLog', 'upload', '2026-10-10T00:00:00Z', 1, 'Wrong code FAKE_HRD_CODE')")
    old = win.db.backup("manual")
    win.check_secrets("prefs")
    assert boxes and old.name in boxes[0] and ": 1." in boxes[0]
    assert win.db.sync_history()[0]["message"] == "Wrong code •••"
    # After that, a start does not read the keychain again.
    monkeypatch.setattr(win.prefs, "saved_secrets", lambda *_a: pytest.fail("keychain read at start"))
    win.check_secrets("start")
