"""Downloads go to a file first: streamed, retried, continued with a Range request, and kept when they break."""

from __future__ import annotations

import httpx
import pytest

from nwologger.core import adif, net
from nwologger.core import db as dbm
from nwologger.core.sync import services

BODY = b"LoTW report\nGenerated at 2026-10-18 07:33:33\nfor bi1nwo\nQuery:\n    QSL ONLY: NO\nQSO RX SINCE: " \
       b"1900-01-01 00:00:00 (user supplied value)\n\n<PROGRAMID:4>LoTW\n<APP_LoTW_LASTQSORX:19>2026-10-16 14:37:32" \
       b"\n<eoh>\n" + b"".join(f"<CALL:5>BA1A{i % 10} <QSO_DATE:8>2026101{i % 10} <TIME_ON:6>10{i:02d}00 <BAND:2>2M "
                              f"<MODE:2>FM <eor>\n".encode() for i in range(40))


class Cut(httpx.SyncByteStream):
    """A response body that breaks after some bytes."""

    def __init__(self, data: bytes, after: int):
        self.data, self.after = data, after

    def __iter__(self):
        yield self.data[:self.after]
        raise httpx.ReadError("connection cut")


def test_a_complete_download_is_written_to_the_file_with_progress(tmp_path, mock_net):
    seen = mock_net(lambda req: httpx.Response(200, content=BODY))
    chunks = []
    n = net.download("lotw", "GET", services.LOTW_REPORT, dest=tmp_path / "d" / "lotw.adi",
                     progress=lambda got, chunk: chunks.append((got, len(chunk))))
    assert n == len(BODY) == (tmp_path / "d" / "lotw.adi").stat().st_size and len(seen) == 1
    assert chunks and chunks[-1][0] == len(BODY)


def test_a_broken_transfer_continues_with_a_range_request_when_the_server_allows(tmp_path, mock_net):
    def answer(req):
        if "range" in req.headers:
            start = int(req.headers["range"].split("=")[1].rstrip("-"))
            return httpx.Response(206, content=BODY[start:], headers={"accept-ranges": "bytes"})
        return httpx.Response(200, stream=Cut(BODY, 500), headers={"accept-ranges": "bytes"})

    seen = mock_net(answer)
    waited = []
    n = net.download("lotw", "GET", services.LOTW_REPORT, dest=tmp_path / "lotw.adi", sleep=waited.append)
    assert n == len(BODY) and (tmp_path / "lotw.adi").read_bytes() == BODY
    assert [r.headers.get("range") for r in seen] == [None, "bytes=500-"] and waited == [5.0]


def test_without_ranges_the_retry_starts_the_file_over(tmp_path, mock_net):
    calls = []

    def answer(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(200, stream=Cut(BODY, 700))
        return httpx.Response(200, content=BODY)

    mock_net(answer)
    n = net.download("lotw", "GET", services.LOTW_REPORT, dest=tmp_path / "lotw.adi", sleep=lambda s: None)
    assert n == len(BODY) and (tmp_path / "lotw.adi").read_bytes() == BODY
    assert all("range" not in r.headers for r in calls) and len(calls) == 2


def test_when_the_retries_are_spent_the_partial_file_is_kept_and_reported(tmp_path, mock_net):
    seen = mock_net(lambda req: httpx.Response(200, stream=Cut(BODY, 900)))
    waited = []
    with pytest.raises(net.PartialDownload) as info:
        net.download("lotw", "GET", services.LOTW_REPORT, dest=tmp_path / "lotw.adi", sleep=waited.append)
    assert info.value.received == 900 and info.value.path.stat().st_size == 900
    assert len(seen) == 4 and waited == [5.0, 15.0, 45.0]
    assert "network error" in str(info.value) and "lotwreport" not in str(info.value)
    header, records, head = services.read_report(info.value.path, partial=True)
    assert head["generated"] == "2026-10-18 07:33:33" and header["APP_LOTW_LASTQSORX"] == "2026-10-16 14:37:32"
    assert 0 < len(records) < 40 and all("CALL" in r and "MODE" in r for r in records)
    # the record it was writing when it stopped is left out
    assert len(records) == BODY[:900].lower().count(b"<eor>")


def test_a_refused_login_is_not_retried_and_leaves_no_partial_file(tmp_path, mock_net):
    seen = mock_net(lambda req: httpx.Response(401, text="no"))
    with pytest.raises(net.NetError, match="HTTP 401"):
        net.download("lotw", "GET", services.LOTW_REPORT, dest=tmp_path / "lotw.adi", sleep=lambda s: None)
    assert len(seen) == 1 and not (tmp_path / "lotw.adi").exists()


def test_a_busy_server_is_retried(tmp_path, mock_net):
    calls = []

    def answer(req):
        calls.append(req)
        return httpx.Response(503, text="busy") if len(calls) < 3 else httpx.Response(200, content=BODY)

    mock_net(answer)
    assert net.download("lotw", "GET", services.LOTW_REPORT, dest=tmp_path / "lotw.adi",
                        sleep=lambda s: None) == len(BODY)
    assert len(calls) == 3


def test_complete_only_parsing_drops_a_record_without_eor():
    text = "<CALL:5>BA1AA <QSO_DATE:8>20261010 <TIME_ON:6>101000 <eor>\n<CALL:5>BA1BB <QSO_DATE:8>2026"
    assert len(adif.parse(text)[1]) == 2
    assert [r["CALL"] for r in adif.parse(text, complete_only=True)[1]] == ["BA1AA"]


def test_the_report_header_names_its_time_call_and_query():
    info = services.report_header(BODY.decode())
    assert info == {"generated": "2026-10-18 07:33:33", "call": "BI1NWO", "QSL ONLY": "NO",
                    "QSO RX SINCE": "1900-01-01 00:00:00"}
    assert services.report_is_complete(info)
    assert not services.report_is_complete({**info, "QSO START DATE": "2024-01-01"})
    assert not services.report_is_complete({**info, "QSO RX SINCE": "2026-10-01 00:00:00"})
    assert services.report_header("<CALL:5>BA1AA <eor>") == {}


def test_lotw_download_with_a_date_range_and_a_file(tmp_path, mock_net):
    seen = mock_net(lambda req: httpx.Response(200, content=BODY))
    header, records = services.lotw_download("u", "p", qsl=False, start="2023-01-01", end="2023-12-31",
                                             dest=tmp_path / "lotw-2023.adi")
    params = seen[0].url.params
    assert params["qso_startdate"] == "2023-01-01" and params["qso_enddate"] == "2023-12-31"
    assert params["qso_qsl"] == "no" and params["qso_qsorxsince"] == "1900-01-01"
    assert len(records) == 40 and header["APP_LOTW_LASTQSORX"] == "2026-10-16 14:37:32"
    assert (tmp_path / "lotw-2023.adi").read_bytes() == BODY


def test_download_files_live_beside_the_log_and_are_pruned_by_run(tmp_path):
    db = dbm.LogDB(tmp_path / "log.sqlite", backup_on_open=False)
    try:
        old = db.download_path("lotw", "20260901-120000", "2024")
        done = db.download_path("lotw", "20261018-112513", "2023")
        current = db.download_path("lotw", "20261018-150000", "2022")
        other = db.download_path("eqsl", "20260901-120000", "Home")
        for p in (old, done, current, other):
            p.write_text("x")
        assert done == tmp_path / "downloads" / "log" / "lotw-20261018-112513-2023.adi"  # a folder per log
        assert db.download_run(done.name) == "20261018-112513" and db.download_run("odd.adi") is None
        removed = db.prune_downloads("lotw", keep_runs=["20261018-112513", "20261018-150000", None])
        assert removed == [old] and done.exists() and current.exists() and other.exists()
        assert db.download_path("qrz", "20261018-150000", "VR2/BD1AJF").name == "qrz-20261018-150000-VR2-BD1AJF.adi"
        assert db.first_qso_time() is None
    finally:
        db.close()
