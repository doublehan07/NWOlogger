"""QRZ.com XML and HamQTH callsign lookup. Network code only; caching is done by the caller."""

from __future__ import annotations

import threading
import xml.etree.ElementTree as ET

from ... import __version__
from .. import net
from .. import qso as qsomod

TIMEOUT = 12.0


class LookupError_(net.NetError):
    pass


def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _int(v: str | None) -> int | None:
    try:
        return int(str(v).strip()) if v not in (None, "") else None
    except ValueError:
        return None


class QrzXml:
    URL = "https://xmldata.qrz.com/xml/current/"
    name = "QRZ.com"

    def __init__(self, user: str, password: str):
        self.user, self.password = user, password
        self.key: str | None = None
        self.lock = threading.Lock()

    def secrets(self) -> list[str]:
        return [s for s in (self.password, self.key) if s]

    def _get(self, params: dict) -> ET.Element:
        # POST, so the password and the session key travel in the encrypted body, not in a URL that servers
        # and proxies log (QRZ.com accepts both; checked 2026-10-16).
        r = net.request("qrz_xml", "POST", self.URL, data=params, secrets=self.secrets(), timeout=TIMEOUT)
        return _strip_ns(ET.fromstring(r.content))

    def login(self) -> None:
        root = self._get({"username": self.user, "password": self.password, "agent": f"NWOlogger-{__version__}"})
        sess = root.find("Session")
        key = sess.findtext("Key") if sess is not None else None
        if not key:
            err = sess.findtext("Error") if sess is not None else "no session"
            raise LookupError_(f"QRZ.com: {err}")
        self.key = key

    def lookup(self, call: str) -> dict | None:
        with self.lock:
            for attempt in (0, 1):
                if not self.key:
                    self.login()
                root = self._get({"s": self.key, "callsign": call})
                sess = root.find("Session")
                err = sess.findtext("Error") if sess is not None else None
                cs = root.find("Callsign")
                if cs is not None:
                    return self._map(cs)
                if err and "not found" in err.lower():
                    return None
                if err and attempt == 0 and ("session" in err.lower() or "timeout" in err.lower() or
                                             "invalid" in err.lower()):
                    self.key = None
                    continue
                if err:
                    raise LookupError_(f"QRZ.com: {err}")
                return None
        return None

    @staticmethod
    def _map(cs: ET.Element) -> dict:
        g = cs.findtext
        addr = [x for x in (g("addr1"), " ".join(p for p in (g("addr2"), g("zip")) if p), g("country")) if x]
        return {
            "call": (g("call") or "").upper(),
            "first_name": g("fname"),
            "last_name": g("name"),
            "qth": g("addr2"),
            "address": "\n".join(addr) or None,
            "state": g("state"),
            "cnty": g("county"),
            "country": g("country"),
            "dxcc": _int(g("dxcc")),
            "gridsquare": g("grid"),
            "email": g("email"),
            "web": g("url"),
            "qsl_via": g("qslmgr"),
            "cqz": _int(g("cqzone")),
            "ituz": _int(g("ituzone")),
            "iota": g("iota"),
            "lotw": g("lotw") == "1",
            "eqsl": g("eqsl") == "1",
        }


class HamQth:
    URL = "https://www.hamqth.com/xml.php"
    name = "HamQTH"

    def __init__(self, user: str, password: str):
        self.user, self.password = user, password
        self.sid: str | None = None
        self.lock = threading.Lock()

    def secrets(self) -> list[str]:
        return [s for s in (self.password, self.sid) if s]

    def _get(self, params: dict) -> ET.Element:
        r = net.request("hamqth", "GET", self.URL, params=params, secrets=self.secrets(), timeout=TIMEOUT)
        return _strip_ns(ET.fromstring(r.content))

    def login(self) -> None:
        root = self._get({"u": self.user, "p": self.password})
        s = root.find("session")
        sid = s.findtext("session_id") if s is not None else None
        if not sid:
            raise LookupError_(f"HamQTH: {s.findtext('error') if s is not None else 'login failed'}")
        self.sid = sid

    def lookup(self, call: str) -> dict | None:
        with self.lock:
            for attempt in (0, 1):
                if not self.sid:
                    self.login()
                root = self._get({"id": self.sid, "callsign": call, "prg": "NWOlogger"})
                srch = root.find("search")
                if srch is not None:
                    return self._map(srch)
                s = root.find("session")
                err = s.findtext("error") if s is not None else None
                if err and "not found" in err.lower():
                    return None
                if err and attempt == 0 and ("session" in err.lower() or "expired" in err.lower()):
                    self.sid = None
                    continue
                if err:
                    raise LookupError_(f"HamQTH: {err}")
                return None
        return None

    @staticmethod
    def _map(s: ET.Element) -> dict:
        g = s.findtext
        full = g("adr_name")
        first, last = qsomod.split_name(full) if full else (g("nick"), None)
        street = [x for x in (g("adr_street1"), g("adr_street2"), g("adr_street3")) if x]
        city = " ".join(p for p in (g("adr_city"), g("adr_zip")) if p)
        addr = [*street, *( [city] if city else []), *( [g("adr_country")] if g("adr_country") else [])]
        return {
            "call": (g("callsign") or "").upper(),
            "first_name": first,
            "last_name": last,
            "qth": g("qth") or g("adr_city"),
            "address": "\n".join(addr) or None,
            "state": g("us_state") or g("district") or None,
            "cnty": g("us_county"),
            "country": g("country"),
            "dxcc": _int(g("adif")),
            "gridsquare": g("grid"),
            "email": g("email"),
            "web": g("web"),
            "qsl_via": g("qsl_via"),
            "cqz": _int(g("cq")),
            "ituz": _int(g("itu")),
            "iota": g("iota"),
            "lotw": (g("lotw") or "").upper() == "Y",
            "eqsl": (g("eqsl") or "").upper() == "Y",
        }


def lookup_chain(call: str, clients: list) -> tuple[dict | None, str | None, list[str]]:
    """Try each client in order. Returns (data, source, errors)."""
    errors: list[str] = []
    for c in clients:
        try:
            d = c.lookup(call)
        except (net.NetError, ET.ParseError) as e:
            errors.append(net.redact(str(e), c.secrets()))
            continue
        if d:
            return {k: v for k, v in d.items() if v not in (None, "")}, c.name, errors
    return None, None, errors
