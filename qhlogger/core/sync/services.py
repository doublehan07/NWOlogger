"""Network clients for LoTW, eQSL, QRZ.com Logbook and HRDLog.

These functions only talk to the network and parse responses; they never touch the log database, so they can
run on a worker thread. Applying results to the log is done by :mod:`apply` on the GUI thread. Requests go through
:mod:`..net`, so errors never carry a password, and any text from a service is passed through ``net.redact``.
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qsl, urljoin

import httpx

from ... import __version__
from .. import adif, net

TIMEOUT = 60.0


class ServiceError(net.NetError):
    pass


# ------------------------------------------------------------------ LoTW

LOTW_REPORT = "https://lotw.arrl.org/lotwuser/lotwreport.adi"


def lotw_download(user: str, password: str, *, qsl: bool = True, since: str | None = None,
                  own_call: str | None = None) -> tuple[dict, list[dict]]:
    """Download LoTW confirmations (qsl=True) or your accepted uploads (qsl=False). Raw ADIF records."""
    params = {"login": user, "password": password, "qso_query": "1", "qso_qsl": "yes" if qsl else "no",
              "qso_qsldetail": "yes", "qso_mydetail": "yes", "qso_withown": "yes"}
    if qsl:
        params["qso_qslsince"] = since or "1900-01-01"
    else:
        params["qso_qsorxsince"] = since or "1900-01-01"
    if own_call:
        params["qso_owncall"] = own_call
    r = net.request("lotw", "GET", LOTW_REPORT, params=params, secrets=[password], timeout=300.0)
    text = r.text
    if not re.search(r"<eoh>", text, re.I):
        msg = re.sub(r"<[^>]+>", " ", text)
        msg = net.redact(" ".join(msg.split()), [password])[:200]
        raise ServiceError("LoTW: query failed (check user and password). " + msg)
    return adif.parse(text)


# ------------------------------------------------------------------ eQSL

EQSL_UPLOAD = "https://www.eqsl.cc/qslcard/ImportADIF.cfm"
EQSL_INBOX = "https://www.eqsl.cc/qslcard/DownloadInBox.cfm"


def eqsl_upload(user: str, password: str, records: list[dict[str, str]]) -> tuple[int, int, list[str]]:
    """Upload ADIF records (≤ 250 per call). Returns (added, total, warning/error lines)."""
    head = f"<EQSL_USER:{len(user)}>{user}<EQSL_PSWD:{len(password)}>{password}<EOH>\n"
    body = "\n".join(" ".join(f"<{k}:{len(v)}>{v}" for k, v in rec.items()) + " <EOR>" for rec in records)
    files = {"Filename": ("qhlogger.adi", (head + body).encode("utf-8"), "text/plain")}
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
                  qth_nickname: str | None = None) -> tuple[dict, list[dict]]:
    """Download your eQSL inbox (incoming cards). ``since`` is YYYYMMDD."""
    params = {"UserName": user, "Password": password}
    if since:
        params["RcvdSince"] = since
    if qth_nickname:
        params["QTHNickname"] = qth_nickname
    r = net.request("eqsl", "GET", EQSL_INBOX, params=params, secrets=[password], timeout=TIMEOUT,
                    redirects=True)
    m = re.search(r'href="([^"]+\.adi)"', r.text, re.I)
    if not m:
        msg = net.redact(" ".join(re.sub(r"<[^>]+>", " ", r.text).split()), [password])
        if "no such" in msg.lower() or "error" in msg.lower():
            raise ServiceError("eQSL: " + msg[:200])
        return {}, []
    url = httpx.URL(urljoin(str(r.url), m.group(1)))
    if url.scheme == "http" and url.host in net.HOSTS["eqsl"]:
        url = url.copy_with(scheme="https")  # same host; other hosts are refused by net.request
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


def qrz_fetch(key: str, *, since: str | None = None, call: str | None = None, page: int = 250
              ) -> list[dict[str, str]]:
    """Fetch the whole logbook (or records modified since a date), paging by log id."""
    out: list[dict[str, str]] = []
    after = 0
    while True:
        opt = [f"MAX:{page}", f"AFTERLOGID:{after}", "TYPE:ADIF"]
        if since:
            opt.append(f"MODSINCE:{since}")
        res = qrz_call(key, "FETCH", call, OPTION=",".join(opt))
        if res.get("RESULT") != "OK":
            reason = res.get("REASON", "")
            if "no log entries" in reason.lower() or res.get("COUNT") == "0":
                break
            raise ServiceError("QRZ.com: " + (reason or "fetch failed"))
        _, recs = adif.parse(res.get("ADIF", ""))
        out.extend(recs)
        if len(recs) < page:
            break
        ids = [int(r.get("APP_QRZLOG_LOGID", "0") or 0) for r in recs]
        nxt = max(ids) + 1 if ids else 0
        if nxt <= after:
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
    data = {"Callsign": call, "Code": code, "App": f"QHlogger {__version__}", "ADIFData": text}
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
