"""HTTPS for the account services, and removing secrets from anything shown, stored or logged.

Every request to LoTW, eQSL, QRZ.com, HamQTH and HRDLog goes through :func:`request`: it only talks HTTPS to the
service's own hosts (checked before every redirect hop), and its errors carry the HTTP status, never the URL.
:func:`redact` is the safety net for any other text that may hold a password, key or session token.
"""

from __future__ import annotations

import html
import logging
import re
import threading
import time
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from urllib.parse import quote, quote_plus, unquote

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

log = logging.getLogger("nwologger.net")
# Lookups run for every call you type: their requests are only logged at debug level.
_QUIET = {"qrz_xml", "hamqth"}

_URL_QUERY = re.compile(r"(https?://[^\s'\"<>?#]+)\?[^\s'\"<>#]*", re.I)
_PAIRS = re.compile(r"\b(password|passwd|pswd|eqslpswd|eqsl_pswd|api_?key)=[^&\s'\"<>]+", re.I)
_SHORT = 4  # shorter secrets are only masked as a whole token, so "59" inside "559" stays
# httpcore's DEBUG trace prints response headers as (b'Name', b'value') pairs.
_HEADERS = re.compile(r"""(b['"](?:set-cookie|cookie|authorization|proxy-authorization|location)['"],\s*)"""
                      r"""b(['"])(?:\\.|(?!\2).)*\2""", re.I)


class NetError(Exception):
    """A network or service failure whose message is safe to show and store."""


class BlockedURL(Exception):
    pass


def _mask(text: str, secrets: list[str]) -> str:
    for s in secrets:
        for form in sorted({s, quote(s, safe=""), quote_plus(s), html.escape(s)}, key=len, reverse=True):
            pat = re.escape(form)
            if len(s) < _SHORT:
                pat = rf"(?<![0-9A-Za-z]){pat}(?![0-9A-Za-z])"
            text = re.sub(pat, MASK, text, flags=re.I)
    return text


def decode(text: str) -> str:
    """HTML entities (twice, for double encoding) and %-escapes, as a server may echo text back."""
    for _ in range(2):
        t = html.unescape(text)
        if t == text:
            break
        text = t
    return unquote(text)


def redact(text: str | None, secrets: Iterable[str | None] = ()) -> str:
    """Remove URL queries, the given secret values and password=… pairs. The text is decoded first (HTML
    entities, %-escapes), so an echoed secret is found however it was encoded; the result is that decoded text."""
    if not text:
        return ""
    vals = sorted({s for s in secrets if s}, key=len, reverse=True)
    out = _mask(_URL_QUERY.sub(lambda m: f"{m.group(1)}?{MASK}", str(text)), vals)  # encoded forms, as sent
    out = _mask(_URL_QUERY.sub(lambda m: f"{m.group(1)}?{MASK}", decode(out)), vals)
    return _PAIRS.sub(lambda m: f"{m.group(1)}={MASK}", out)


def leaks(text: str | None, secrets: Iterable[str | None] = ()) -> bool:
    """True when :func:`redact` would hide something in ``text`` (not merely decode it)."""
    return bool(text) and redact(text, secrets) != decode(str(text))


def contains_secret(text: str | None, secrets: Iterable[str | None]) -> bool:
    """True when ``text`` holds one of ``secrets`` in any of the forms :func:`redact` recognises."""
    vals = sorted({s for s in secrets if s}, key=len, reverse=True)
    if not text or not vals:
        return False
    t = str(text)
    return _mask(t, vals) != t or _mask(decode(t), vals) != decode(t)


# Secrets of the requests running right now, so log records written during a request can be masked too.
_active: Counter[str] = Counter()
_active_lock = threading.Lock()


@contextmanager
def _in_flight(secrets: Iterable[str | None]) -> Iterator[None]:
    vals = [s for s in secrets if s]
    with _active_lock:
        _active.update(vals)
    try:
        yield
    finally:
        with _active_lock:
            _active.subtract(vals)
            for s in [s for s, n in _active.items() if n <= 0]:
                del _active[s]


def active_secrets() -> list[str]:
    with _active_lock:
        return list(_active)


def _ua(call: str | None = None) -> dict[str, str]:
    return {"User-Agent": f"NWOlogger/{__version__}" + (f" ({call})" if call else "")}


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


def request(service: str, method: str, url: str, *, secrets: Iterable[str | None] = (), call: str | None = None,
            timeout: float = 60.0, redirects: bool = False, **kw) -> httpx.Response:
    """Send one request; any failure, including a non-2xx status, becomes a :class:`NetError`. ``secrets`` are
    the credentials it carries: log records written meanwhile have them masked."""
    msg = None
    level = logging.DEBUG if service in _QUIET else logging.INFO
    where = _where(url)
    t0 = time.monotonic()
    try:
        with _in_flight(secrets), client(service, call=call, timeout=timeout, redirects=redirects) as c:
            r = c.request(method, url, **kw)
            r.raise_for_status()
            log.log(level, "%s %s %s: HTTP %s, %d bytes, %.1f s", NAMES[service], method, where, r.status_code,
                    len(r.content), time.monotonic() - t0)
            return r
    except (httpx.HTTPError, BlockedURL) as e:
        msg = describe(service, e)
    log.warning("%s %s %s failed after %.1f s: %s", NAMES[service], method, where, time.monotonic() - t0, msg)
    # Raised outside the handler so the original exception, which holds the URL, is not chained.
    raise NetError(msg)


def _where(url: str) -> str:
    """Host and path of a URL for the log: never its query, which may hold a password."""
    try:
        u = httpx.URL(url)
        return f"{u.host}{u.path}"
    except (TypeError, ValueError, httpx.InvalidURL):
        return "?"


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = _HEADERS.sub(lambda m: f"{m.group(1)}b'{MASK}'", record.getMessage())
        record.msg, record.args = redact(msg, active_secrets()), ()
        return True


HTTP_LOGGERS = ("httpx", "httpcore", "httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy",
                "httpcore.socks")


def install_log_filter() -> None:
    """Keep passwords, keys and session cookies out of HTTPX / httpcore logs.

    Both are set to WARNING, so an app-wide DEBUG or INFO setup prints no request URLs or header traces; getting
    them takes a deliberate level on these loggers. Every one of them (a filter on a parent logger does not see
    its children's records) also masks URL queries, headers and the secrets of requests in flight.
    """
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    known = [n for n in logging.root.manager.loggerDict if n.split(".")[0] in ("httpx", "httpcore")]
    for name in {*HTTP_LOGGERS, *known}:
        lg = logging.getLogger(name)
        if not any(isinstance(f, _RedactFilter) for f in lg.filters):
            lg.addFilter(_RedactFilter())


install_log_filter()
