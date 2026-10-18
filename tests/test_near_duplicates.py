"""Records a download could not match: added by themselves, unless a QSO of the same call lies within a minute,
which waits in the Possible duplicates window."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QCheckBox, QDialog, QWidget

from nwologger.core import db as dbm
from nwologger.core import importer
from nwologger.core import qso as qsomod
from nwologger.ui import sync_controller
from nwologger.ui.prefs import Prefs
from nwologger.ui.sync_dialogs import NearDuplicatesDialog


def q(call, time_on, **fields):
    return qsomod.normalize({"call": call, "time_on": time_on, "band": "2m", "mode": "FM", "prop_mode": "SAT",
                             "sat_name": "SO-50", **fields})


@pytest.fixture
def log(tmp_path):
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    yield db
    db.close()


def test_near_qsos_finds_the_same_call_within_a_minute_whatever_the_band(log):
    mine = log.insert(q("BI1XYZ", "2026-03-14T03:12:00Z"))
    log.insert(q("BI1XYZ", "2026-03-14T04:00:00Z"))  # another contact, later
    near = importer.near_qsos(log, q("BI1XYZ", "2026-03-14T03:12:40Z", band="70cm", mode="SSB", sat_name="RS-44"))
    assert [n["id"] for n in near] == [mine]
    assert importer.near_qsos(log, q("BI1XYZ", "2026-03-14T03:13:30Z")) == []
    assert importer.near_qsos(log, q("BG2ABC", "2026-03-14T03:12:10Z")) == []


def test_settle_adds_fresh_records_and_holds_near_duplicates(qtbot, log, tmp_path, monkeypatch):
    prefs = Prefs(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    parent = QWidget()
    qtbot.addWidget(parent)
    ctrl = sync_controller.SyncController(log, prefs, parent)
    mine = log.insert(q("BI1XYZ", "2026-03-14T03:12:00Z"))
    held = []
    monkeypatch.setattr(ctrl, "_show_near_duplicates", lambda svc, pairs, **k: held.append((svc, pairs, k)))
    near = q("BI1XYZ", "2026-03-14T03:12:30Z", band="70cm", lotw_qsl_sent="Y")
    fresh = [q("BA4TSS", "2025-11-02T11:40:00Z", lotw_qsl_sent="Y", lotw_qsl_rcvd="Y", lotw_qslrdate="2025-11-05"),
             q("BH1ABC", "2025-11-03T11:40:00Z", lotw_qsl_sent="Y")]
    added, n_held = ctrl._settle_unmatched("lotw", [near, *fresh])
    assert n_held == 1 and len(added) == 2 and log.count() == 3
    assert held == [("lotw", [(near, log.get(mine))], {"added": 2})]
    a = log.get(added[0])
    assert (a["call"], a["lotw_qsl_rcvd"], a["lotw_qslrdate"]) == ("BA4TSS", "Y", "2025-11-05")
    assert log.get(mine)["band"] == "2m"  # the near-duplicate changed nothing
    assert ctrl._settle_unmatched("lotw", []) == ([], 0)


def test_the_window_adds_only_the_ticked_records_and_applies_after_a_fix(qtbot, log):
    mine = log.insert(q("BI1XYZ", "2026-03-14T03:12:00Z"))
    other = log.insert(q("BG2ABC", "2025-11-02T11:40:00Z"))
    rec = q("BI1XYZ", "2026-03-14T03:12:10Z", band="70cm", lotw_qsl_sent="Y", lotw_qsl_rcvd="Y",
            lotw_qslrdate="2026-03-20")
    rec2 = q("BG2ABC", "2025-11-02T11:40:00Z", sat_name="PO-101", lotw_qsl_sent="Y")
    dlg = NearDuplicatesDialog(log, "LoTW", [(rec, log.get(mine)), (rec2, log.get(other))], added=23)
    qtbot.addWidget(dlg)
    assert dlg.table.rowCount() == 2 and dlg.ticked() == []
    assert dlg.table.item(0, 1).font().bold() and "70CM" in dlg.table.item(0, 1).text().upper()
    assert "Band" in dlg.table.item(0, 1).toolTip() and "Sat" in dlg.table.item(1, 1).toolTip()
    added, messages = [], []
    dlg.add_requested.connect(added.append)
    dlg.message.connect(messages.append)
    dlg.table.cellWidget(1, 0).findChild(QCheckBox, "add").setChecked(True)
    dlg._add()
    assert added == [[rec2]] and dlg.table.rowCount() == 1 and dlg.pairs == [(rec, log.get(mine))]
    # nothing matches yet, so Check again keeps the row
    assert dlg.check_again() == 0 and dlg.table.rowCount() == 1
    # fix my QSO: the band was wrong; the record now matches and is applied to it
    log.update(mine, {"band": "70cm"}, track_service=False)
    assert dlg.check_again() == 1
    q1 = log.get(mine)
    assert (q1["lotw_qsl_rcvd"], q1["lotw_qslrdate"], q1["lotw_qsl_sent"]) == ("Y", "2026-03-20", "Y")
    assert messages == ["1 records applied to your QSOs"] and dlg.result() == QDialog.DialogCode.Accepted
    assert log.count() == 2  # nothing was added by the fix
