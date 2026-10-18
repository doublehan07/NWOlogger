"""Network clients for LoTW, eQSL, QRZ.com Logbook and HRDLog.

These functions only talk to the network and parse responses; they never touch the log database, so they can
run on a worker thread. Applying results to the log is done by :mod:`apply` on the GUI thread. Requests go through
:mod:`..net`, so errors never carry a password, and any text from a service is passed through ``net.redact``.
"""

from __future__ import annotations

import html
import logging
import re
from pathlib import Path
from urllib.parse import parse_qsl, urljoin

import httpx

from ... import __version__
from .. import adif, net, timeutil

TIMEOUT = 60.0
log = logging.getLogger("nwologger.sync")


class ServiceError(net.NetError):
    pass


_HIDDEN = re.compile(r"<(script|style|head|noscript|title)\b.*?</\1\s*>", re.I | re.S)
_BREAK = re.compile(r"<br\s*/?>|</?(p|div|li|ul|ol|tr|table|h[1-6]|center|form|hr)\b[^>]*>", re.I)


def page_text(page: str | None) -> str:
    """The visible text of an HTML page, one line per block. Scripts, styles and the page head are left out: an
    error message cut to its first 200 characters must not be JavaScript."""
    t = _BREAK.sub("\n", _HIDDEN.sub(" ", page or ""))
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    lines = (" ".join(ln.split()) for ln in t.splitlines())
    return "\n".join(ln for ln in lines if ln)


def error_line(text: str) -> str | None:
    """The line of a page that reports the problem ("Error: …", "No such …"), if there is one."""
    for ln in text.splitlines():
        if re.search(r"\berror\b|\bno such\b|\bincorrect\b|\binvalid\b|\bnot (found|allowed)\b", ln, re.I):
            return ln[:300]
    return None


# ------------------------------------------------------------------ LoTW

LOTW_REPORT = "https://lotw.arrl.org/lotwuser/lotwreport.adi"


def report_header(text: str) -> dict[str, str]:
    """The plain-text lines LoTW puts before the tags of a report: ``generated`` (its server time, ``2026-10-18
    07:33:33``), ``call`` and the query it answers, upper-case keys (``QSL ONLY``, ``QSO RX SINCE``, and the date or
    call filters when the report was made with one). Nothing after ``<eoh>`` is read."""
    head = re.split(r"<eoh>", text[:20000], maxsplit=1, flags=re.I)[0]
    out: dict[str, str] = {}
    for line in head.splitlines():
        line = line.strip()
        m = re.match(r"Generated at\s+(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", line, re.I)
        if m:
            out["generated"] = m.group(1)
        elif re.match(r"for\s+\S+$", line, re.I):
            out["call"] = line.split()[1].upper()
        elif ":" in line and not line.startswith("<") and not line.lower().startswith("query"):
            key, value = line.split(":", 1)
            value = re.sub(r"\s*\(user supplied value\)\s*$", "", value.strip(), flags=re.I)
            if key.strip() and value:
                out[key.strip().upper()] = value
    return out


def report_is_complete(info: dict[str, str]) -> bool:
    """Whether a report was made without a filter, so its resume values cover everything: no date, call, band,
    mode or entity filter, and RX / QSL since the start."""
    filtered = {k for k in info if k.startswith(("QSO START", "QSO END", "QSO CALL", "QSO BAND", "QSO MODE",
                                                  "QSO DXCC", "QSO OWN", "OWN CALL", "WORKED", "START", "END"))}
    since = [v for k, v in info.items() if k.endswith("SINCE")]
    return not filtered and all(v.startswith("1900-01-01") for v in since)


def report_info(path) -> dict[str, str]:
    """The plain-text header of a report file (:func:`report_header`), from its first bytes; {} without a file."""
    try:
        with open(path, "rb") as fh:
            return report_header(adif.decode(fh.read(20000)))
    except (OSError, TypeError):
        return {}


def read_report(path, *, partial: bool = False) -> tuple[dict, list[dict], dict[str, str]]:
    """A LoTW report file: its tag header, its records and the plain-text header. ``partial`` (a transfer that
    broke) leaves out the record it was writing when it stopped."""
    with open(path, "rb") as fh:
        data = fh.read()
    text = adif.decode(data)
    if not re.search(r"<eoh>", text, re.I):
        page = page_text(text)
        log.warning("LoTW answered without ADIF; the page said: %s", page[:1500])
        msg = error_line(page) or " ".join(page.split())[:200]
        raise ServiceError("LoTW: query failed (check user and password). " + msg)
    header, records = adif.parse(text, complete_only=partial)
    return header, records, report_header(text)


def lotw_download(user: str, password: str, *, qsl: bool = True, since: str | None = None,
                  own_call: str | None = None, start: str | None = None, end: str | None = None,
                  dest=None, progress=None) -> tuple[dict, list[dict]]:
    """Download LoTW confirmations (qsl=True) or your accepted uploads (qsl=False). Raw ADIF records.

    ``start`` / ``end`` (YYYY-MM-DD) limit the report to QSOs made in that range (LoTW's qso_startdate and
    qso_enddate, both inclusive). With ``dest`` the report is streamed to that file (see :func:`net.download`), the
    file is kept, and a transfer that breaks for good raises :class:`net.PartialDownload` with it."""
    params = {"login": user, "password": password, "qso_query": "1", "qso_qsl": "yes" if qsl else "no",
              "qso_qsldetail": "yes", "qso_mydetail": "yes", "qso_withown": "yes"}
    if qsl:
        params["qso_qslsince"] = since or "1900-01-01"
    else:
        params["qso_qsorxsince"] = since or "1900-01-01"
    if own_call:
        params["qso_owncall"] = own_call
    if start:
        params["qso_startdate"] = start
    if end:
        params["qso_enddate"] = end
    if dest is not None:
        net.download("lotw", "GET", LOTW_REPORT, dest=dest, params=params, secrets=[password], progress=progress)
        header, records, _info = read_report(dest)
        return header, records
    r = net.request("lotw", "GET", LOTW_REPORT, params=params, secrets=[password], timeout=300.0)
    text = r.text
    if not re.search(r"<eoh>", text, re.I):
        page = net.redact(page_text(text), [password])
        log.warning("LoTW answered without ADIF; the page said: %s", page[:1500])
        msg = error_line(page) or " ".join(page.split())[:200]
        raise ServiceError("LoTW: query failed (check user and password). " + msg)
    return adif.parse(text)


def lotw_sync_download(user: str, password: str, *, since_qsl: str | None = None,
                       since_rx: str | None = None, folder=None, run: str | None = None,
                       progress=None) -> tuple[dict, list[dict], dict, list[dict]]:
    """Download confirmations and accepted uploads, sharing the full report on the first sync.

    LoTW's qso_qsl=no report contains all accepted QSOs, including QSL_RCVD, QSLRDATE and
    APP_LOTW_RXQSL for confirmed contacts. Its newest confirmation timestamp is therefore a safe,
    inclusive resume point without downloading the confirmed subset again. Once either cursor exists,
    keep the separate incremental queries: an old upload can gain a new confirmation at any time.
    See https://lotw.arrl.org/lotw-help/developer-query-qsos-qsls/.

    With ``folder`` and ``run`` each report is streamed to a file there (``lotw-<run>-qsl.adi``,
    ``lotw-<run>-qso.adi``), which is kept.
    """
    def dest(part: str):
        return Path(folder) / f"lotw-{run}-{part}.adi" if folder is not None and run else None

    if since_qsl or since_rx:
        h1, r1 = lotw_download(user, password, qsl=True, since=since_qsl, dest=dest("qsl"), progress=progress)
        h2, r2 = lotw_download(user, password, qsl=False, since=since_rx, dest=dest("qso"), progress=progress)
        return h1, r1, h2, r2

    h2, r2 = lotw_download(user, password, qsl=False, dest=dest("qso"), progress=progress)
    r1 = [r for r in r2 if r.get("QSL_RCVD", "").upper() == "Y"]
    if not r1:
        # No confirmation cursor yet: the next sync can safely ask for all confirmations.
        return {}, r1, h2, r2
    received = [timeutil.parse_iso(r.get("APP_LOTW_RXQSL")) for r in r1]
    if any(t is None for t in received):
        # Do not guess from the local clock or advance past a confirmation with no timestamp.
        h1, r1 = lotw_download(user, password, qsl=True, dest=dest("qsl"), progress=progress)
    else:
        last = timeutil.parse_iso(h2.get("APP_LOTW_LASTQSL")) or max(t for t in received if t is not None)
        h1 = {"APP_LOTW_LASTQSL": last.strftime("%Y-%m-%d %H:%M:%S")}
    return h1, r1, h2, r2


# ------------------------------------------------------------------ eQSL

EQSL_UPLOAD = "https://www.eqsl.cc/qslcard/ImportADIF.cfm"
EQSL_INBOX = "https://www.eqsl.cc/qslcard/DownloadInBox.cfm"


def eqsl_upload(user: str, password: str, records: list[dict[str, str]]) -> tuple[int, int, list[str]]:
    """Upload ADIF records (≤ 250 per call). Returns (added, total, warning/error lines)."""
    head = f"<EQSL_USER:{len(user)}>{user}<EQSL_PSWD:{len(password)}>{password}<EOH>\n"
    body = "\n".join(" ".join(f"<{k}:{len(v)}>{v}" for k, v in rec.items()) + " <EOR>" for rec in records)
    files = {"Filename": ("nwologger.adi", (head + body).encode("utf-8"), "text/plain")}
    data = {"EQSLUSER": user, "EQSLPSWD": password}
    r = net.request("eqsl", "POST", EQSL_UPLOAD, data=data, files=files, secrets=[password], timeout=TIMEOUT)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", r.text))
    m = re.search(r"Result:\s*(\d+)\s+out of\s+(\d+)\s+records added", text, re.I)
    lines = [net.redact(ln.strip(), [password]) for ln in text.splitlines()
             if re.match(r"\s*(Warning|Error)", ln, re.I)]
    if not m:
        err = next((ln for ln in lines if ln.lower().startswith("error")), None)
        raise ServiceError("eQSL: " + (err or "unexpected response"))
    return int(m.group(1)), int(m.group(2)), lines


def eqsl_download(user: str, password: str, *, since: str | None = None,
                  qth_nickname: str | None = None, dest=None, progress=None) -> tuple[dict, list[dict]]:
    """Download your eQSL inbox (incoming cards). ``since`` is YYYYMMDD. With ``dest`` the generated ADIF file is
    streamed to that file and kept; a transfer that breaks for good raises :class:`net.PartialDownload`."""
    params = {"UserName": user, "Password": password}
    if since:
        params["RcvdSince"] = since
    if qth_nickname:
        params["QTHNickname"] = qth_nickname
    r = net.request("eqsl", "GET", EQSL_INBOX, params=params, secrets=[password], timeout=TIMEOUT,
                    redirects=True)
    m = re.search(r'href="([^"]+\.adi)"', r.text, re.I)
    if not m:
        page = net.redact(page_text(r.text), [password])
        log.warning("eQSL inbox page has no ADIF link%s; the page said: %s",
                    f" (QTH nickname {qth_nickname!r})" if qth_nickname else " (no QTH nickname)", page[:1500])
        err = error_line(page)
        if err:
            hint = "" if qth_nickname else " If your eQSL account has more than one QTH, set the eQSL QTH " \
                                            "nickname in the station profile."
            raise ServiceError("eQSL: " + err + hint)
        return {}, []
    url = httpx.URL(urljoin(str(r.url), m.group(1)))
    if url.scheme == "http" and url.host in net.HOSTS["eqsl"]:
        url = url.copy_with(scheme="https")  # same host; other hosts are refused by net.request
    if dest is not None:
        net.download("eqsl", "GET", str(url), dest=dest, secrets=[password], redirects=True, progress=progress)
        with open(dest, "rb") as fh:
            return adif.parse(fh.read())
    f = net.request("eqsl", "GET", str(url), secrets=[password], timeout=TIMEOUT, redirects=True)
    return adif.parse(f.content)


# ------------------------------------------------------------------ QRZ.com Logbook

QRZ_API = "https://logbook.qrz.com/api"


def _qrz_parse(text: str) -> dict[str, str]:
    """QRZ responses are name=value pairs; the ADIF value is HTML-escaped and may contain '&'."""
    out: dict[str, str] = {}
    adif_at = text.find("ADIF=")
    head = text if adif_at < 0 else text[:adif_at]
    for k, v in parse_qsl(head, keep_blank_values=True):
        out[k.upper()] = v
    if adif_at >= 0:
        out["ADIF"] = html.unescape(text[adif_at + 5:])
    return out


def qrz_call(key: str, action: str, call: str | None = None, **params: str) -> dict[str, str]:
    data = {"KEY": key, "ACTION": action, **params}
    r = net.request("qrz", "POST", QRZ_API, data=data, secrets=[key], timeout=TIMEOUT, call=call)
    res = _qrz_parse(r.text)
    if "REASON" in res:
        res["REASON"] = net.redact(res["REASON"], [key])
    return res


def qrz_insert(key: str, record: dict[str, str], *, replace: bool = False, call: str | None = None
               ) -> tuple[bool, str | None, str]:
    text = " ".join(f"<{k}:{len(v)}>{v}" for k, v in record.items()) + " <EOR>"
    params = {"ADIF": text}
    if replace:
        params["OPTION"] = "REPLACE"
    res = qrz_call(key, "INSERT", call, **params)
    ok = res.get("RESULT") in ("OK", "REPLACE")
    reason = res.get("REASON", "")
    if not ok and "duplicate" in reason.lower():
        return True, res.get("LOGID"), reason  # already in the QRZ logbook
    return ok, res.get("LOGID"), reason


def qrz_fetch(key: str, *, call: str | None = None, page: int = 250, after: int = 0,
              on_page=None) -> list[dict[str, str]]:
    """Fetch the whole logbook, paging by log id.

    Always the whole logbook: QRZ.com answers every FETCH with MODSINCE with RESULT=FAIL COUNT=0 (checked with a
    real logbook on 2026-10-16), which would look like "nothing new". Matching the same record again changes
    nothing, so a full fetch is safe; it is about one request per 250 QSOs. ``after`` continues an earlier run
    from a log id; ``on_page(next_after, records, adif_text)`` is called for every page as it arrives, so a
    caller can keep and apply each page before the next one is asked for."""
    out: list[dict[str, str]] = []
    while True:
        res = qrz_call(key, "FETCH", call, OPTION=",".join([f"MAX:{page}", f"AFTERLOGID:{after}", "TYPE:ADIF"]))
        if res.get("RESULT") != "OK":
            reason = res.get("REASON", "")
            if "no log entries" in reason.lower() or res.get("COUNT") == "0":
                break  # past the last record (or an empty logbook)
            raise ServiceError("QRZ.com: " + (reason or "fetch failed"))
        text = res.get("ADIF", "")
        _, recs = adif.parse(text)
        out.extend(recs)
        ids = [int(r.get("APP_QRZLOG_LOGID", "0") or 0) for r in recs]
        nxt = max(ids) + 1 if ids else 0
        if on_page is not None:
            on_page(max(nxt, after), recs, text)
        if len(recs) < page or nxt <= after:
            break
        after = nxt
    return out


def qrz_status(key: str, call: str | None = None) -> dict[str, str]:
    res = qrz_call(key, "STATUS", call)
    if res.get("RESULT") != "OK":
        raise ServiceError("QRZ.com: " + res.get("REASON", "status failed"))
    return res


# ------------------------------------------------------------------ HRDLog

HRDLOG_URL = "https://robot.hrdlog.net/NewEntry.aspx"


def hrdlog_upload(call: str, code: str, record: dict[str, str], *, update_key: str | None = None
                  ) -> tuple[bool, str]:
    text = " ".join(f"<{k}:{len(v)}>{v}" for k, v in record.items()) + " <EOR>"
    data = {"Callsign": call, "Code": code, "App": f"NWOlogger {__version__}", "ADIFData": text}
    if update_key:
        data["Cmd"] = "UPDATE"
        data["ADIFKey"] = update_key
    r = net.request("hrdlog", "POST", HRDLOG_URL, data=data, secrets=[code], timeout=TIMEOUT, call=call)
    body = r.text
    err = re.search(r"<error>(.*?)</error>", body, re.I | re.S)
    if err:
        return False, net.redact(html.unescape(err.group(1)).strip(), [code])
    if re.search(r"<(insert|update)>\s*[1-9]", body, re.I) or "ok" in body.lower():
        return True, ""
    return False, net.redact(" ".join(re.sub(r"<[^>]+>", " ", body).split()), [code])[:200] or "unexpected response"
