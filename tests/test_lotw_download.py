"""LoTW full and incremental sync requests against canned ADIF reports."""

from __future__ import annotations

import httpx
import pytest

from nwologger.core import adif, net
from nwologger.core.sync import services


def report(records, **header):
    fields = " ".join(f"<{k}:{len(v)}>{v}" for k, v in header.items())
    return f"LoTW report\n{fields}<EOH>\n" + adif.dumps(records, program_version="test").split("<EOH>", 1)[1]


def record(call, **fields):
    return {"CALL": call, "QSO_DATE": "20261010", "TIME_ON": "152100", "BAND": "2M", "MODE": "FM",
            "STATION_CALLSIGN": "BI1NWO", "APP_LOTW_RXQSO": "2026-10-10 16:00:00", **fields}


def test_full_sync_uses_one_report_with_confirmation_details(mock_net):
    confirmed = record("BA1AA", QSL_RCVD="Y", QSLRDATE="20261011", APP_LOTW_RXQSL="2026-10-11 01:02:03")
    unconfirmed = record("BA1BB", QSL_RCVD="N")
    seen = mock_net(lambda req: httpx.Response(200, text=report(
        [confirmed, unconfirmed], APP_LOTW_LASTQSORX="2026-10-10 16:00:00")))

    h1, r1, h2, r2 = services.lotw_sync_download("u", "p")

    assert len(seen) == 1
    params = seen[0].url.params
    assert params["qso_qsl"] == "no" and params["qso_qsorxsince"] == "1900-01-01"
    assert params["qso_qsldetail"] == params["qso_mydetail"] == params["qso_withown"] == "yes"
    assert h1 == {"APP_LOTW_LASTQSL": "2026-10-11 01:02:03"}
    assert r1 == [confirmed] and r2 == [confirmed, unconfirmed]
    assert h2 == {"APP_LOTW_LASTQSORX": "2026-10-10 16:00:00"}


def test_full_sync_confirmation_cursor_uses_newest_timestamp_and_keeps_ties(mock_net):
    recs = [record("BA1AA", QSL_RCVD="y", APP_LOTW_RXQSL="2026-10-11T01:02:03Z"),
            record("BA1BB", QSL_RCVD="Y", APP_LOTW_RXQSL="2026-10-12 01:02:03"),
            record("BA1CC", QSL_RCVD="Y", APP_LOTW_RXQSL="2026-10-12T01:02:03Z")]
    seen = mock_net(lambda req: httpx.Response(200, text=report(recs)))

    h1, r1, _, _ = services.lotw_sync_download("u", "p")
    assert h1["APP_LOTW_LASTQSL"] == "2026-10-12 01:02:03" and r1 == recs

    services.lotw_sync_download("u", "p", since_qsl=h1["APP_LOTW_LASTQSL"],
                                since_rx="2026-10-10 16:00:00")
    # LoTW's since query is inclusive: keep this timestamp so equally timed confirmations are rechecked.
    assert seen[1].url.params["qso_qslsince"] == "2026-10-12 01:02:03"


def test_full_sync_accepts_a_valid_server_confirmation_cursor(mock_net):
    recs = [record("BA1AA", QSL_RCVD="Y", APP_LOTW_RXQSL="2026-10-11 01:02:03")]
    mock_net(lambda req: httpx.Response(200, text=report(
        recs, APP_LOTW_LASTQSL="2026-10-12T01:02:03Z", APP_LOTW_LASTQSORX="2026-10-10 16:00:00")))

    h1, r1, _, _ = services.lotw_sync_download("u", "p")
    assert h1 == {"APP_LOTW_LASTQSL": "2026-10-12 01:02:03"} and r1 == recs


@pytest.mark.parametrize("missing", [None, "", "not a timestamp", "2026-02-30 01:02:03"])
def test_full_sync_falls_back_when_any_confirmation_timestamp_is_missing_or_invalid(mock_net, missing):
    incomplete = record("BA1AA", QSL_RCVD="Y", **({"APP_LOTW_RXQSL": missing} if missing else {}))
    full = [record("BA1BB", QSL_RCVD="Y", APP_LOTW_RXQSL="2026-10-12 01:02:03"), incomplete]
    confirmations = [record("BA1AA", QSL_RCVD="Y", QSLRDATE="20261013",
                            APP_LOTW_RXQSL="2026-10-13 01:02:03")]

    def answer(req):
        if req.url.params["qso_qsl"] == "no":
            return httpx.Response(200, text=report(full, APP_LOTW_LASTQSORX="2026-10-10 16:00:00"))
        return httpx.Response(200, text=report(confirmations, APP_LOTW_LASTQSL="2026-10-13 01:02:03"))

    seen = mock_net(answer)
    h1, r1, h2, r2 = services.lotw_sync_download("u", "p")
    assert [r.url.params["qso_qsl"] for r in seen] == ["no", "yes"]
    assert seen[1].url.params["qso_qslsince"] == "1900-01-01"
    assert h1["APP_LOTW_LASTQSL"] == "2026-10-13 01:02:03" and r1 == confirmations
    assert h2["APP_LOTW_LASTQSORX"] == "2026-10-10 16:00:00" and r2 == full


@pytest.mark.parametrize("recs", [[], [record("BA1AA", QSL_RCVD="N")]])
def test_full_sync_with_no_confirmations_does_not_guess_confirmation_cursor(mock_net, recs):
    seen = mock_net(lambda req: httpx.Response(200, text=report(recs)))
    h1, r1, _, r2 = services.lotw_sync_download("u", "p")
    assert len(seen) == 1 and h1 == {} and r1 == [] and r2 == recs


@pytest.mark.parametrize(("since_qsl", "since_rx"), [
    ("2026-10-11 01:02:03", "2026-10-10 16:00:00"),
    (None, "2026-10-10 16:00:00"),
    ("2026-10-11 01:02:03", None),
])
def test_incremental_sync_keeps_both_independent_queries(mock_net, since_qsl, since_rx):
    confirmed = record("BA1AA", QSL_RCVD="Y", APP_LOTW_RXQSL="2026-10-12 01:02:03")
    uploaded = record("BA1BB", QSL_RCVD="N", APP_LOTW_RXQSO="2026-10-13 01:02:03")

    def answer(req):
        if req.url.params["qso_qsl"] == "yes":
            return httpx.Response(200, text=report([confirmed], APP_LOTW_LASTQSL="2026-10-12 01:02:03"))
        return httpx.Response(200, text=report([uploaded], APP_LOTW_LASTQSORX="2026-10-13 01:02:03"))

    seen = mock_net(answer)
    h1, r1, h2, r2 = services.lotw_sync_download("u", "p", since_qsl=since_qsl, since_rx=since_rx)
    assert [r.url.params["qso_qsl"] for r in seen] == ["yes", "no"]
    assert seen[0].url.params["qso_qslsince"] == (since_qsl or "1900-01-01")
    assert seen[1].url.params["qso_qsorxsince"] == (since_rx or "1900-01-01")
    assert h1["APP_LOTW_LASTQSL"] == "2026-10-12 01:02:03" and r1 == [confirmed]
    assert h2["APP_LOTW_LASTQSORX"] == "2026-10-13 01:02:03" and r2 == [uploaded]


@pytest.mark.parametrize("status", [503, 429])
def test_full_sync_propagates_server_errors(mock_net, status):
    seen = mock_net(lambda req: httpx.Response(status, text="Unavailable"))
    with pytest.raises(net.NetError, match=f"HTTP {status}"):
        services.lotw_sync_download("u", "p")
    assert len(seen) == 1


def test_full_sync_propagates_failed_confirmation_fallback(mock_net):
    def answer(req):
        if req.url.params["qso_qsl"] == "no":
            return httpx.Response(200, text=report([record("BA1AA", QSL_RCVD="Y")]))
        return httpx.Response(503, text="Unavailable")

    seen = mock_net(answer)
    with pytest.raises(net.NetError, match="HTTP 503"):
        services.lotw_sync_download("u", "p")
    assert len(seen) == 2
