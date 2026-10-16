"""HTTPS for the account services, and removing secrets from anything shown, stored or logged.

Every request to LoTW, eQSL, QRZ.com, HamQTH and HRDLog goes through :func:`request`: it only talks HTTPS to the
service's own hosts (checked before every redirect hop), and its errors carry the HTTP status, never the URL.
:func:`redact` is the safety net for any other text that may hold a password, key or session token.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Iterable
from urllib.parse import quote, quote_plus

import httpx

from .. import __version__

MASK = "•••"

NAMES = {"lotw": "LoTW", "eqsl": "eQSL", "qrz": "QRZ.com", "qrz_xml": "QRZ.com", "hamqth": "HamQTH",
         "hrdlog": "HRDLog"}
HOSTS = {
    "lotw": {"lotw.arrl.org"},
    "eqsl": {"www.eqsl.cc", "eqsl.cc"},
    "qrz": {"logbook.qrz.com"},
    "qrz_xml": {"xmldata.qrz.com"},
    "hamqth": {"www.hamqth.com"},
    "hrdlog": {"robot.hrdlog.net"},
}

# Tests put an httpx.MockTransport here; None means the real network.
transport: httpx.BaseTransport | None = None

_URL_QUERY = re.compile(r"(https?://[^\s'\"<>?#]+)\?[^\s'\"<>#]*", re.I)
_PAIRS = re.compile(r"\b(password|passwd|pswd|eqslpswd|eqsl_pswd|api_?key)=[^&\s'\"<>]+", re.I)
_MIN_SECRET = 4  # shorter values would blank out ordinary words


class NetError(Exception):
    """A network or service failure whose message is safe to show and store."""


class BlockedURL(Exception):
    pass


def redact(text: str | None, secrets: Iterable[str | None] = ()) -> str:
    """Remove URL queries, the given secret values (raw, URL- or HTML-encoded) and password=… pairs."""
    if not text:
        return ""
    out = _URL_QUERY.sub(lambda m: f"{m.group(1)}?{MASK}", str(text))
    forms = set()
    for s in secrets:
        if s and len(s) >= _MIN_SECRET:
            forms.update({s, quote(s, safe=""), quote_plus(s), html.escape(s)})
    for form in sorted(forms, key=len, reverse=True):
        out = re.sub(re.escape(form), MASK, out, flags=re.I)
    return _PAIRS.sub(lambda m: f"{m.group(1)}={MASK}", out)


def _ua(call: str | None = None) -> dict[str, str]:
    return {"User-Agent": f"QHlogger/{__version__}" + (f" ({call})" if call else "")}


def client(service: str, *, call: str | None = None, timeout: float = 60.0, redirects: bool = False
           ) -> httpx.Client:
    """An HTTPS client that refuses any request (redirects included) to a host outside the service's list."""
    allowed = HOSTS[service]

    def check(req: httpx.Request) -> None:
        u = req.url
        if u.scheme != "https" or u.host not in allowed or u.userinfo or u.port not in (None, 443):
            raise BlockedURL(f"{NAMES[service]}: refused to contact {u.scheme}://{u.host} (not an expected "
                             "HTTPS address of the service)")

    return httpx.Client(timeout=timeout, headers=_ua(call), follow_redirects=redirects, max_redirects=5,
                        event_hooks={"request": [check]}, transport=transport)


_HINTS = {401: "check the user name and password", 403: "check the user name and password",
          404: "the service address has changed", 429: "too many requests; try again later"}


def describe(service: str, e: Exception) -> str:
    """A message for a failed request that names the problem but never the URL or its parameters."""
    name = NAMES.get(service, service)
    if isinstance(e, httpx.HTTPStatusError):
        r = e.response
        hint = _HINTS.get(r.status_code) or ("the service is down or busy; try again later"
                                             if r.status_code >= 500 else "")
        return f"{name}: HTTP {r.status_code} {r.reason_phrase}" + (f" ({hint})" if hint else "")
    if isinstance(e, BlockedURL):
        return str(e)
    if isinstance(e, httpx.TooManyRedirects):
        return f"{name}: too many redirects"
    if isinstance(e, httpx.TimeoutException):
        return f"{name}: the server did not answer in time"
    return f"{name}: network error ({type(e).__name__})"


def request(service: str, method: str, url: str, *, call: str | None = None, timeout: float = 60.0,
            redirects: bool = False, **kw) -> httpx.Response:
    """Send one request; any failure, including a non-2xx status, becomes a :class:`NetError`."""
    msg = None
    try:
        with client(service, call=call, timeout=timeout, redirects=redirects) as c:
            r = c.request(method, url, **kw)
            r.raise_for_status()
            return r
    except (httpx.HTTPError, BlockedURL) as e:
        msg = describe(service, e)
    # Raised outside the handler so the original exception, which holds the URL, is not chained.
    raise NetError(msg)


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg, record.args = redact(record.getMessage()), ()
        return True


def install_log_filter() -> None:
    """HTTPX logs every request URL at INFO; make sure those records never carry a password."""
    for name in ("httpx", "httpcore"):
        lg = logging.getLogger(name)
        if not any(isinstance(f, _RedactFilter) for f in lg.filters):
            lg.addFilter(_RedactFilter())


install_log_filter()
