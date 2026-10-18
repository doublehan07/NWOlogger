"""Downloaded contacts keep service confirmations when accepted into an empty log."""

from __future__ import annotations

import pytest

from nwologger.core import db as dbm
from nwologger.core import importer, qso, sats, timeutil
from nwologger.core.sync import apply

DOWNLOADS = {
    "lotw": (apply.apply_lotw_qsls, {"QSL_RCVD": "Y", "QSLRDATE": "20261017"},
             {"lotw_qsl_rcvd": "Y", "lotw_qslrdate": "2026-10-17", "lotw_qsl_sent": "Y"}),
    "eqsl": (apply.apply_eqsl, {"QSLRDATE": "20261017", "APP_EQSL_AG": "Y"},
             {"eqsl_qsl_rcvd": "Y", "eqsl_qslrdate": "2026-10-17", "eqsl_ag": "Y"}),
    "qrz": (apply.apply_qrz, {"APP_QRZLOG_STATUS": "C", "APP_QRZLOG_QSLDATE": "20261017",
                            "APP_QRZLOG_LOGID": "123456"},
            {"qrz_qsl_rcvd": "Y", "qrz_qslrdate": "2026-10-17", "qrz_logid": "123456",
             "qrzcom_qso_upload_status": "Y", "qrzcom_qso_download_status": "Y",
             "qrzcom_qso_download_date": "2026-10-18"}),
}


@pytest.fixture
def db(tmp_path):
    d = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    yield d
    d.close()


def record(call="BA1AA", **tags):
    return {"CALL": call, "QSO_DATE": "20261010", "TIME_ON": "151900", "BAND": "2M", "MODE": "FM",
            "STATION_CALLSIGN": "BI1ZZZ", "NAME": "Jian Wu", "GRIDSQUARE": "ON80EB", "NOTES": "Keep this",
            "APP_OTHER_REFERENCE": "original", **tags}


@pytest.mark.parametrize("service", DOWNLOADS)
def test_empty_log_import_keeps_service_status_and_replay_is_idempotent(db, monkeypatch, service):
    monkeypatch.setattr(timeutil, "today_iso", lambda: "2026-10-18")
    download, tags, expected = DOWNLOADS[service]
    rec = record(**tags)
    catalog = sats.load()

    result = download(db, [rec], catalog)

    # Preparing candidates does not add contacts or report confirmations on nonexistent rows.
    assert list(db.iter_all()) == []
    assert (result.total, result.matched, result.changed, result.confirmed) == (1, 0, 0, [])
    assert len(result.unmatched) == 1
    for col, value in expected.items():
        assert result.unmatched[0][col] == value

    imported = importer.run(db, result.unmatched, fill=False)
    assert imported.added == 1 and imported.errors == []
    saved = db.get(imported.ids[0])
    for col, value in expected.items():
        assert saved[col] == value
    assert saved["call"] == "BA1AA"
    assert (saved["first_name"], saved["last_name"]) == ("Jian", "Wu")
    assert saved["gridsquare"] == "ON80eb" and saved["notes"] == "Keep this"
    assert saved["station_callsign"] == "BI1ZZZ"
    assert qso.load_extra(saved)["APP_OTHER_REFERENCE"] == "original"
    assert saved["qsl_rcvd"] is None and saved["qslrdate"] is None
    assert saved["qsl_sent"] is None and saved["qslsdate"] is None
    if service == "eqsl":
        # An inbox card proves receipt, but does not prove the user's contact was uploaded.
        assert saved["eqsl_qsl_sent"] is None and saved["eqsl_qslsdate"] is None

    # Another day's full download neither duplicates contacts nor changes the download date.
    monkeypatch.setattr(timeutil, "today_iso", lambda: "2026-10-19")
    again = download(db, [rec], catalog)
    assert (again.total, again.matched, again.changed, again.confirmed, again.unmatched) == (1, 1, 0, [], [])
    assert db.get(saved["id"]) == saved
    assert len(list(db.iter_all())) == 1


@pytest.mark.parametrize("service", DOWNLOADS)
def test_only_accepted_download_candidates_are_imported(db, service):
    download, tags, expected = DOWNLOADS[service]
    result = download(db, [record("BA1AA", **tags), record("BA1BB", **tags)], None)
    assert list(db.iter_all()) == []
    assert len(result.unmatched) == 2

    imported = importer.run(db, result.unmatched[:1], fill=False)
    assert imported.added == 1
    saved = list(db.iter_all())
    assert [r["call"] for r in saved] == ["BA1AA"]
    confirm_col = next(col for col in expected if col.endswith("_qsl_rcvd"))
    assert saved[0][confirm_col] == "Y"


@pytest.mark.parametrize("service", ["lotw", "eqsl"])
def test_service_generic_qsl_fields_do_not_become_paper_qsl(db, service):
    download = DOWNLOADS[service][0]
    rec = record(QSL_RCVD="Y", QSLRDATE="20261017", QSL_SENT="Y", QSLSDATE="20261016")
    result = download(db, [rec], None)
    imported = importer.run(db, result.unmatched, fill=False)
    saved = db.get(imported.ids[0])
    for col in ("qsl_rcvd", "qslrdate", "qsl_sent", "qslsdate"):
        assert saved[col] is None
    assert saved[f"{service}_qsl_rcvd"] == "Y"
    assert saved[f"{service}_qslrdate"] == "2026-10-17"


@pytest.mark.parametrize("status", [None, "N", "R", "I"])
@pytest.mark.parametrize("service", ["lotw", "qrz"])
def test_unconfirmed_download_is_imported_without_confirmation(db, service, status):
    tag = "QSL_RCVD" if service == "lotw" else "APP_QRZLOG_STATUS"
    tags = {tag: status} if status else {}
    result = DOWNLOADS[service][0](db, [record(**tags)], None)
    imported = importer.run(db, result.unmatched, fill=False)
    saved = db.get(imported.ids[0])
    assert saved[f"{service}_qsl_rcvd"] is None
    assert saved[f"{service}_qslrdate"] is None
    uploaded = "lotw_qsl_sent" if service == "lotw" else "qrzcom_qso_upload_status"
    assert saved[uploaded] == "Y"


def test_lotw_confirmation_date_falls_back_to_server_timestamp(db):
    result = apply.apply_lotw_qsls(db, [record(QSL_RCVD="Y", APP_LOTW_RXQSL="2026-10-17 15:19:00")], None)
    imported = importer.run(db, result.unmatched, fill=False)
    saved = db.get(imported.ids[0])
    assert saved["lotw_qsl_rcvd"] == "Y" and saved["lotw_qslrdate"] == "2026-10-17"
    assert saved["qsl_rcvd"] is None and saved["qslrdate"] is None
    assert "APP_LOTW_RXQSL" not in qso.load_extra(saved)


def test_lotw_upload_download_import_is_not_a_confirmation(db):
    result = apply.apply_lotw_uploads(db, [record(APP_LOTW_RXQSO="2026-10-16 12:13:14")], None)
    imported = importer.run(db, result.unmatched, fill=False)
    saved = db.get(imported.ids[0])
    assert saved["lotw_qsl_sent"] == "Y" and saved["lotw_qslsdate"] == "2026-10-16"
    assert saved["lotw_qsl_rcvd"] is None and saved["lotw_qslrdate"] is None


def test_matching_download_preserves_local_details_and_other_extra(db):
    original = qso.from_adif(record(NAME="Local Operator", GRIDSQUARE="OM89aa", NOTES="Local note",
                                   QSL_RCVD="Y", QSLRDATE="20261015", APP_LOCAL_REFERENCE="local"))
    qid = db.insert(original)
    result = apply.apply_lotw_qsls(db, [record(QSL_RCVD="Y", QSLRDATE="20261017")], None)
    assert result.unmatched == [] and result.confirmed == [qid]
    saved = db.get(qid)
    assert (saved["first_name"], saved["last_name"]) == ("Local", "Operator")
    assert saved["gridsquare"] == "OM89aa" and saved["notes"] == "Local note"
    assert saved["qsl_rcvd"] == "Y" and saved["qslrdate"] == "2026-10-15"
    assert qso.load_extra(saved)["APP_LOCAL_REFERENCE"] == "local"
    assert saved["lotw_qsl_rcvd"] == "Y" and saved["lotw_qslrdate"] == "2026-10-17"


def test_overlapping_service_imports_merge_confirmations_and_preserve_local_edits(db, qtbot, tmp_path,
                                                                                monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QWidget

    from nwologger.ui.prefs import Prefs
    from nwologger.ui.sync_controller import SyncController

    monkeypatch.setattr(timeutil, "today_iso", lambda: "2026-10-18")
    parent = QWidget()
    qtbot.addWidget(parent)
    prefs = Prefs(QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    controller = SyncController(db, prefs, parent)
    messages = []
    controller.message.connect(messages.append)

    # All services finish against the empty log before any import dialog is accepted.
    candidates = {service: download(db, [record(**tags)], None).unmatched
                  for service, (download, tags, _expected) in DOWNLOADS.items()}
    assert all(len(chosen) == 1 for chosen in candidates.values())
    assert list(db.iter_all()) == []

    changes_before = db.conn.total_changes
    for service in DOWNLOADS:
        controller._added_later(service, [])
    assert db.conn.total_changes == changes_before
    assert list(db.iter_all()) == [] and messages == []

    controller._added_later("lotw", candidates["lotw"])
    qid = list(db.iter_all())[0]["id"]
    extra = qso.load_extra(db.get(qid))
    extra["APP_LOCAL_REFERENCE"] = "added while dialogs are open"
    db.update(qid, {"first_name": "Local", "last_name": "Operator", "notes": "Typed locally",
                    "gridsquare": "OM89aa", "extra": qso.dump_extra(extra)}, track_service=False)

    # The remaining dialogs contain stale copies of the original contact details.
    controller._added_later("eqsl", candidates["eqsl"])
    controller._added_later("qrz", candidates["qrz"])

    saved = list(db.iter_all())
    assert len(saved) == 1 and saved[0]["id"] == qid
    saved = saved[0]
    for _download, _tags, expected in DOWNLOADS.values():
        for col, value in expected.items():
            assert saved[col] == value
    assert (saved["first_name"], saved["last_name"]) == ("Local", "Operator")
    assert saved["notes"] == "Typed locally" and saved["gridsquare"] == "OM89aa"
    assert qso.load_extra(saved) == {"APP_OTHER_REFERENCE": "original",
                                    "APP_LOCAL_REFERENCE": "added while dialogs are open"}
    assert all(saved[col] is None for col in ("qsl_rcvd", "qslrdate", "qsl_sent", "qslsdate"))
    assert len(messages) == 3


@pytest.mark.parametrize("initial_state", ["empty", "unrelated", "mixed"])
def test_service_import_preserves_distinct_contacts_within_match_window(db, qtbot, tmp_path, initial_state):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QWidget

    from nwologger.ui.prefs import Prefs
    from nwologger.ui.sync_controller import SyncController

    parent = QWidget()
    qtbot.addWidget(parent)
    prefs = Prefs(QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    controller = SyncController(db, prefs, parent)
    records = [record(TIME_ON="151900", QSL_RCVD="Y", QSLRDATE="20261017"),
               record(TIME_ON="152900", QSL_RCVD="Y", QSLRDATE="20261018")]
    if initial_state == "mixed":
        records.append(record("BA1BB", TIME_ON="161900", QSL_RCVD="Y", QSLRDATE="20261016"))
    candidates = apply.apply_lotw_qsls(db, records, None).unmatched
    assert len(candidates) == len(records)

    existing_id = None
    if initial_state != "empty":
        # In the mixed case another open import dialog added a third contact while this one waited.
        call = "BA1BB" if initial_state == "mixed" else "BA1CC"
        existing_id = db.insert(qso.from_adif(record(call, TIME_ON="161900", NOTES="Typed locally",
                                                    NAME="Local Operator", GRIDSQUARE="OM89aa")))

    controller._added_later("lotw", candidates)

    rows = list(db.iter_all())
    assert len(rows) == (2 if initial_state == "empty" else 3)
    same_call = sorted((r for r in rows if r["call"] == "BA1AA"), key=lambda r: r["time_on"])
    assert [(r["time_on"], r["lotw_qslrdate"]) for r in same_call] == [
        ("2026-10-10T15:19:00Z", "2026-10-17"), ("2026-10-10T15:29:00Z", "2026-10-18")]
    assert all(r["lotw_qsl_rcvd"] == "Y" and r["lotw_qsl_sent"] == "Y" for r in same_call)
    assert all(r["qsl_rcvd"] is None and r["qslrdate"] is None for r in same_call)
    if existing_id is not None:
        saved = db.get(existing_id)
        assert saved["notes"] == "Typed locally" and saved["gridsquare"] == "OM89aa"
        assert (saved["first_name"], saved["last_name"]) == ("Local", "Operator")
        if initial_state == "mixed":
            assert saved["lotw_qsl_rcvd"] == "Y" and saved["lotw_qslrdate"] == "2026-10-16"
        else:
            assert saved["lotw_qsl_rcvd"] is None
