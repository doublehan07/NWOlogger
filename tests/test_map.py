"""The grid map: Maidenhead squares, the first confirming call per square, imagery tiles and their cache."""

from __future__ import annotations

import os
import time

import httpx
import pytest

from nwologger.core import db as dbm
from nwologger.core import grids, maptiles
from nwologger.core import qso as qsomod

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64  # enough of a JPEG for the cache


@pytest.fixture
def db(tmp_path):
    d = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    yield d
    d.close()


def add(db, call, t, grid, *, sat=True, band="2m", my="ON80", **kw):
    q = {"call": call, "time_on": t, "mode": "FM", "band": band, "gridsquare": grid, "my_gridsquare": my}
    if sat:
        q.update(prop_mode="SAT", sat_name="SO-50")
    q.update(kw)
    return db.insert(qsomod.normalize(q))


# ------------------------------------------------------------------ squares and projection
def test_squares_and_projection():
    assert grids.square("on80qw") == "ON80" and grids.square("XX99") is None and grids.square("") is None
    assert grids.squares_of("FN31pr", "FN31,FN41, fn42") == ["FN31", "FN41", "FN42"]
    assert grids.bounds("ON80") == (116.0, 40.0, 118.0, 41.0) and grids.center("ON80") == (40.5, 117.0)
    assert list(grids.squares_in(116.5, 40.2, 117.5, 40.8)) == ["ON80"]
    assert list(grids.fields_in(100, 30, 119, 39)) == ["OM"]
    x, y = grids.to_world(117.0, 40.5, 5)
    lon, lat = grids.from_world(x, y, 5)
    assert abs(lon - 117.0) < 1e-9 and abs(lat - 40.5) < 1e-9
    assert grids.to_world(-180, 0, 0) == (0.0, 128.0)
    us = grids.conus()
    assert len(us) == 488 and "FN31" in us and "CN87" in us and "OM89" not in us
    w, s, e, n = grids.region_box("home", "ON80")
    assert w < 117 < e and s < 40.5 < n


# ------------------------------------------------------------------ what the log says about each square
def test_first_confirming_call_by_the_sources_you_choose(db):
    add(db, "BA1AA", "2026-01-01T10:00:00Z", "OM89")  # worked first, never confirmed
    add(db, "BA1BB", "2026-01-02T10:00:00Z", "OM89", eqsl_qsl_rcvd="Y")
    add(db, "BA1CC", "2026-01-03T10:00:00Z", "OM89", lotw_qsl_rcvd="Y")
    add(db, "BA1DD", "2026-01-04T10:00:00Z", "PM95", qsl_rcvd="Y")
    add(db, "BA1EE", "2026-01-05T10:00:00Z", "ON80", sat=False)  # 2 m, no satellite
    cells = grids.collect(db, band="SAT", home="ON80")
    om = cells["OM89"]
    assert om.qsos == 3 and om.calls == {"BA1AA", "BA1BB", "BA1CC"} and om.first["call"] == "BA1AA"
    assert om.confirmed["call"] == "BA1CC" and om.source == "lotw"  # LoTW is the default
    assert cells["PM95"].status == "worked" and "ON80" not in cells
    both = grids.collect(db, band="SAT", home="ON80", sources=("lotw", "eqsl"))
    assert both["OM89"].confirmed["call"] == "BA1BB" and both["OM89"].source == "eqsl"
    paper = grids.collect(db, band="SAT", home="ON80", sources=("paper",))
    assert paper["PM95"].confirmed["call"] == "BA1DD" and paper["OM89"].status == "worked"
    assert set(grids.collect(db, band="2m", home="ON80")) == {"ON80"}
    assert set(grids.collect(db, home="ON80")) == {"OM89", "PM95", "ON80"}


def test_qsos_from_far_away_and_vucc_grids(db):
    add(db, "BA1AA", "2026-01-01T10:00:00Z", "OM89", my="ON80ca")
    add(db, "BA1BB", "2026-01-02T10:00:00Z", "PM95", my="OL72")  # a trip about 1,500 km south
    add(db, "BA1CC", "2026-01-03T10:00:00Z", "PN11", my=None)  # no grid of yours: counted as home
    add(db, "W1AW", "2026-01-04T10:00:00Z", "FN31", vucc_grids="FN31,FN41", lotw_qsl_rcvd="Y")
    near = grids.collect(db, band="SAT", home="ON80")
    assert set(near) == {"OM89", "PN11", "FN31", "FN41"} and near["FN41"].confirmed["call"] == "W1AW"
    assert "PM95" in grids.collect(db, band="SAT", home="ON80", radius_km=None)


# ------------------------------------------------------------------ providers and the cache
def test_providers_and_choice():
    assert maptiles.NASA.url(5, 26, 12).endswith("/GoogleMapsCompatible_Level8/5/12/26.jpeg")
    assert maptiles.TIANDITU.url(5, 26, 12) is None  # needs a key
    u = maptiles.TIANDITU.url(5, 26, 12, "KEY123")
    assert u.startswith("https://t6.tianditu.gov.cn/img_w/wmts?") and "LAYER=img" in u and "tk=KEY123" in u
    assert "cia" not in u and "ibo" not in u  # imagery only: the grid is drawn on top
    choose = maptiles.choose
    assert choose("auto", language="zh_CN", region="", has_key=True) is maptiles.TIANDITU
    assert choose("auto", language="en", region="CN", has_key=True) is maptiles.TIANDITU
    assert choose("auto", language="zh_CN", region="CN", has_key=False) is maptiles.NASA
    assert choose("auto", language="en", region="US", has_key=True) is maptiles.NASA
    assert choose("tianditu", language="en", region="US", has_key=False) is maptiles.TIANDITU
    assert choose("none", language="zh_CN", region="CN", has_key=True) is None


def test_cache_prune_and_clear(tmp_path):
    for i in range(5):
        p = maptiles.tile_path("nasa", 3, i, 1)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 100)
        t = time.time() - 100 + i
        os.utime(p, (t, t))
    assert str(maptiles.cache_dir()).startswith(str(tmp_path))  # tests never touch your real cache
    assert maptiles.cache_size() == 500
    assert maptiles.prune(limit=300, keep=250) == 3  # the three least recently used
    assert sorted(p.parent.name for p in (maptiles.cache_dir() / "tiles").rglob("*.jpg")) == ["3", "4"]
    assert maptiles.cached("nasa", 3, 4, 1) == b"x" * 100
    assert maptiles.clear() == 200 and maptiles.cache_size() == 0


def _fetch(fetcher, *tiles, timeout=5.0):
    got = []
    fetcher.on_tile = lambda *a: got.append(a)
    for t in tiles:
        fetcher.want(*t)
    unique = len({t[:4] for t in tiles})
    end = time.time() + timeout
    while time.time() < end and fetcher.stats["loaded"] + fetcher.stats["cached"] + fetcher.stats["failed"] < unique:
        time.sleep(0.02)
    return got


def test_fetcher_loads_caches_and_gives_up(monkeypatch, mock_net):
    monkeypatch.delenv("NWOLOGGER_OFFLINE", raising=False)
    seen = mock_net(lambda req: httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"}))
    f = maptiles.Fetcher(lambda *a: None)
    got = _fetch(f, (maptiles.NASA, 4, 12, 6), (maptiles.NASA, 4, 12, 6))  # the same tile twice: asked once
    assert len(seen) == 1 and got[0][:4] == ("nasa", 4, 12, 6) and maptiles.cached("nasa", 4, 12, 6) == JPEG
    f2 = maptiles.Fetcher(lambda *a: None)  # a new session reads the cache, not the network
    _fetch(f2, (maptiles.NASA, 4, 12, 6))
    assert len(seen) == 1 and f2.stats["cached"] == 1
    mock_net(lambda req: httpx.Response(418, text="<html>key?</html>"))
    f3 = maptiles.Fetcher(lambda *a: None)
    _fetch(f3, *[(maptiles.TIANDITU, 6, x, 20, "BADKEY") for x in range(maptiles.GIVE_UP + 3)])
    assert "tianditu" in f3.stopped and "HTTP 418" in f3.stopped["tianditu"]
    assert f3.stats["failed"] >= maptiles.GIVE_UP


def test_fetcher_offline_and_without_key(mock_net):
    seen = mock_net(lambda req: httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"}))
    f = maptiles.Fetcher(lambda *a: None)  # NWOLOGGER_OFFLINE is set for every test
    f.want(maptiles.NASA, 4, 1, 1)
    f2 = maptiles.Fetcher(lambda *a: None)
    f2.offline = False
    f2.want(maptiles.TIANDITU, 4, 1, 1, None)
    time.sleep(0.3)
    assert seen == []
