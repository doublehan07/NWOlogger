"""The Grid search runs on a QSO's whole grid list (GRIDSQUARE and VUCC_GRIDS), so a station on a grid line is found by
any of its grids, in any order; a comma or semicolon alone lists the QSOs with several grids; and "/" in the Call box
lists portable calls."""

from __future__ import annotations

import pytest

from nwologger.core import qso as qsomod
from nwologger.core import search
from nwologger.core.db import LogDB

QSOS = {  # name: call, grid, VUCC grids, my grid, my VUCC grids
    "line": ("BA1AA", "OM88da", "OM88,OM89", "ON80da", None),
    "single89": ("BA1BB", "OM89aa", None, "ON80da", None),
    "four": ("BA1CC", "ON31", "ON31,ON32,ON41,ON42", "ON80da", None),
    "plain": ("BA1DD", "PM95", None, "ON80da", None),
    "portable": ("VR2/BD1AJF", "OL72", None, "OL72", None),
    "myline": ("BA1EE", "PM96", None, "ON80", "ON80,ON70"),
    "nogrid": ("BA1FF", None, None, None, None),
}


@pytest.fixture
def log(tmp_path):
    db = LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    ids = {}
    for i, (name, (call, grid, vucc, mine, myvucc)) in enumerate(QSOS.items()):
        ids[name] = db.insert(qsomod.normalize({"call": call, "time_on": f"2026-01-{i + 1:02d}T10:00:00Z", "mode": "FM",
                                                "band": "2m", "gridsquare": grid, "vucc_grids": vucc,
                                                "my_gridsquare": mine, "my_vucc_grids": myvucc}))
    yield db, ids
    db.close()


def found(db, spec) -> set[int]:
    sql, params = search.build_where(spec)
    return {r[0] for r in db.conn.execute("SELECT id FROM qso" + (f" WHERE {sql}" if sql else ""), params)}


def grid(db, value, op="contains", field="gridsquare"):
    return found(db, {"conditions": [{"field": field, "op": op, "value": value}]})


def test_a_separator_alone_lists_the_qsos_with_several_grids(log):
    db, ids = log
    several = {ids["line"], ids["four"]}
    assert grid(db, ",") == several and grid(db, ";") == several and grid(db, " , ") == several
    assert grid(db, "/") == set()  # "/" and a space are not grid separators
    assert grid(db, " ") == set(ids.values())  # only whitespace: no condition at all


def test_any_grid_of_a_station_on_a_line_finds_it(log):
    db, ids = log
    assert grid(db, "OM88") == {ids["line"]}
    assert grid(db, "OM89") == {ids["line"], ids["single89"]}  # OM89aa contains OM89 too
    assert grid(db, "OM89, OM88") == {ids["line"]}  # any order
    assert grid(db, "om88;om89") == {ids["line"]}
    assert grid(db, "ON42") == {ids["four"]} and grid(db, "ON4") == {ids["four"]}
    assert grid(db, "OM89", op="is") == {ids["line"]}  # exactly OM89: not OM89aa
    assert grid(db, "OM89aa", op="is") == {ids["single89"]}
    assert grid(db, "ON42", op="starts") == {ids["four"]}  # the map's click: a square held only as a VUCC grid
    assert grid(db, "OM8?", op="matches") == {ids["line"]}  # a whole grid of the list; OM89aa is longer
    assert grid(db, "OM8*", op="matches") == {ids["line"], ids["single89"]}
    assert grid(db, "OM89", op="is_not") == set(ids.values()) - {ids["line"]}
    assert grid(db, "", op="empty") == {ids["nogrid"]}
    assert grid(db, "", op="not_empty") == set(ids.values()) - {ids["nogrid"]}


def test_my_grid_and_the_call_box(log):
    db, ids = log
    assert grid(db, "ON70", field="my_gridsquare") == {ids["myline"]}
    assert grid(db, "ON80", field="my_gridsquare") == set(ids.values()) - {ids["portable"], ids["nogrid"]}
    assert found(db, {"quick": {"call": "/"}}) == {ids["portable"]}  # portable calls, as today
    assert found(db, {"conditions": [{"field": "call", "op": "contains", "value": "/"}]}) == {ids["portable"]}
