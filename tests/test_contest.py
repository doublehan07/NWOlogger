"""Contest engine tests (synthetic QSOs)."""

from __future__ import annotations

from qhlogger.core.contest import cabrillo
from qhlogger.core.contest.engine import Engine, Me, load_all, store_exchange
from qhlogger.core.contest.scp import Scp

DEFS = load_all()


def q(call, band="20m", mode="CW", t="2026-11-28T00:01:00Z", qid=None, **exch):
    d = {"call": call, "band": band, "mode": mode, "time_on": t, "_exch": exch}
    if qid is not None:
        d["id"] = qid
    return d


ME_CN = Me(call="BI1ZZZ", dxcc=318, cont="AS", cqz=24, ituz=44, grid="ON80")


def test_all_definitions_load():
    for key in ("CQWW-CW", "CQWW-SSB", "CQWW-RTTY", "CQWPX-CW", "IARU-HF", "ARRLDX-CW", "ARRL-10", "AA-CW",
                "CQ-VHF", "DX-SERIAL", "DX-ZONE"):
        assert key in DEFS


def test_cqww_points_mults_dupes():
    e = Engine(DEFS["CQWW-CW"], ME_CN)
    s = e.add(q("JA1AAA", qid=1, rst="599", zone="25"))
    assert s.points == 1 and ("Zones", "25") in s.new_mults and ("Countries", "339") in s.new_mults
    s = e.add(q("K1AAA", qid=2, rst="599", zone="5"))
    assert s.points == 3
    s = e.add(q("BA4AAA", qid=3, rst="599", zone="24"))
    assert s.points == 0  # same country: no points, still a mult
    assert e.add(q("JA1AAA", qid=4, rst="599", zone="25")).dupe
    assert not e.preview(q("JA1AAA", band="40m", rst="599", zone="25")).dupe
    assert e.score == (1 + 3 + 0) * 6


def test_wpx_band_points_and_prefixes():
    e = Engine(DEFS["CQWPX-SSB"], ME_CN)
    assert e.add(q("JA1AAA", band="40m", mode="SSB", qid=1, rst="59", serial="1")).points == 2
    assert e.add(q("DL1AAA", band="40m", mode="SSB", qid=2, rst="59", serial="2")).points == 6
    assert e.add(q("DL2AAA", band="20m", mode="SSB", qid=3, rst="59", serial="3")).points == 3
    assert e.mult_total == 3  # JA1, DL1, DL2


def test_iaru_hq_and_zones():
    e = Engine(DEFS["IARU-HF"], ME_CN)
    assert e.add(q("BY1RX", qid=1, rst="599", zone="CRAC")).points == 1
    assert e.add(q("BA1AAA", qid=2, rst="599", zone="44")).points == 1  # same ITU zone
    assert e.add(q("JA1AAA", qid=3, rst="599", zone="45")).points == 3
    assert e.add(q("DL1AAA", qid=4, rst="599", zone="28")).points == 5
    # Same call on another mode is not a dupe (once per band per mode).
    assert not e.preview(q("DL1AAA", mode="SSB", rst="59", zone="28")).dupe


def test_arrl_dx_as_dx_station():
    e = Engine(DEFS["ARRLDX-CW"], ME_CN)
    s = e.add(q("K1AAA", qid=1, rst="599", exch="MA"))
    assert s.points == 3 and ("States/Provinces", "MA") in s.new_mults
    assert e.add(q("JA1AAA", qid=2, rst="599", exch="100")).points == 0


def test_all_asian_band_points():
    e = Engine(DEFS["AA-CW"], ME_CN)
    assert e.add(q("JA1AAA", band="160m", qid=1, rst="599", age="45")).points == 3
    assert e.add(q("DL1AAA", band="80m", qid=2, rst="599", age="45")).points == 6
    assert e.add(q("BA1AAA", band="20m", qid=3, rst="599", age="45")).points == 0


def test_store_exchange_and_cabrillo(tmp_path):
    cdef = DEFS["CQWW-CW"]
    cols = store_exchange(cdef, {"rst": "599", "zone": "25"}, {"rst": "599", "zone": "24"}, None)
    assert cols["srx_string"] == "25" and cols["stx_string"] == "24" and cols["cqz"] == 25
    row = {"id": 1, "call": "JA1AAA", "band": "20m", "freq": 14.025, "mode": "CW", "time_on": "2026-11-28T00:01:00Z",
           "station_callsign": "BI1ZZZ", **cols}
    path = tmp_path / "log.cbr"
    cabrillo.export(str(path), cdef, {"data": {"categories": {"CATEGORY-OPERATOR": "SINGLE-OP"}}}, [row], ME_CN, 42)
    text = path.read_text()
    assert "CONTEST: CQ-WW-CW" in text and "CLAIMED-SCORE: 42" in text
    line = next(ln for ln in text.splitlines() if ln.startswith("QSO:"))
    assert line.split() == ["QSO:", "14025", "CW", "2026-11-28", "0001", "BI1ZZZ", "599", "24", "JA1AAA", "599", "25"]


def test_scp_partial_and_n_plus_one():
    s = Scp(["BA1AAA", "BA1AAB", "BG8AAA", "JA1AAA"])
    assert s.partial("A1AA")[:2] == ["BA1AAA", "BA1AAB"]
    assert set(s.n_plus_one("BA1AAA")) == {"BA1AAB", "JA1AAA"}
    assert "BA1AAA" in s.n_plus_one("BA1AA")
