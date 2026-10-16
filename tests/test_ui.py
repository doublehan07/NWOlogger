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
