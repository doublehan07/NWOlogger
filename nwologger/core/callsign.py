"""Callsign parsing: portable prefixes/suffixes, base call, WPX prefix."""

from __future__ import annotations

import re
from dataclasses import dataclass

CALL_RE = re.compile(r"^[A-Z0-9]{1,4}[0-9][A-Z0-9]*[A-Z]$|^[0-9][A-Z][0-9][A-Z0-9]*[A-Z]$")
# Suffixes that don't change the DXCC entity.
NEUTRAL_SUFFIXES = {"P", "M", "QRP", "QRPP", "A", "B", "LH", "J", "R", "T", "X", "FF", "YOTA", "POTA", "SOTA"}
NO_DXCC_SUFFIXES = {"MM", "AM"}


@dataclass
class ParsedCall:
    full: str
    base: str
    prefix: str | None  # explicit country prefix, e.g. VE3 in VE3/BA1PK or BA1PK/VE3
    suffix: str | None  # P, M, MM, digit, …
    area_digit: str | None  # /4 style call-area change


def clean(call: str | None) -> str:
    return re.sub(r"\s+", "", (call or "").upper())


def looks_like_call(s: str) -> bool:
    return bool(CALL_RE.match(clean(s)))


def parse(call: str) -> ParsedCall:
    c = clean(call)
    parts = [p for p in c.split("/") if p]
    if len(parts) <= 1:
        return ParsedCall(c, c, None, None, None)
    # The base call is the longest part that looks like a callsign (ties: the first such part).
    candidates = [p for p in parts if looks_like_call(p)] or parts
    base = max(candidates, key=len)
    idx = parts.index(base)
    before, after = parts[:idx], parts[idx + 1:]
    prefix = before[-1] if before else None
    suffix = None
    area = None
    for a in after:
        if len(a) == 1 and a.isdigit():
            area = a
        elif a in NEUTRAL_SUFFIXES or a in NO_DXCC_SUFFIXES:
            suffix = a
        elif prefix is None and re.match(r"^[A-Z0-9]{1,4}$", a) and any(ch.isalpha() for ch in a):
            prefix = a  # BA1PK/VE3 style: a country prefix after the call
        else:
            suffix = suffix or a
    return ParsedCall(c, base, prefix, suffix, area)


def dxcc_lookup_string(call: str) -> str | None:
    """The string to look up in the country file, or None for /MM and /AM."""
    p = parse(call)
    if p.suffix in NO_DXCC_SUFFIXES:
        return None
    if p.prefix:
        return p.prefix
    if p.area_digit:
        m = re.match(r"^([A-Z0-9]*?[A-Z])(\d)(.*)$", p.base)
        if m:
            return m.group(1) + p.area_digit + m.group(3)
    return p.base


def wpx_prefix(call: str) -> str | None:
    """CQ WPX prefix: letters and digits up to and including the last digit of the prefix."""
    p = parse(call)
    if p.suffix in NO_DXCC_SUFFIXES:
        return None
    if p.prefix:
        pre = p.prefix
        return pre if pre[-1].isdigit() else pre + "0"
    base = p.base
    m = re.match(r"^(\d?[A-Z]+\d+)", base) or re.match(r"^([A-Z0-9]*?\d+)", base)
    if not m:
        return (base[:2] + "0") if len(base) >= 2 else None
    pre = m.group(1)
    if p.area_digit:
        pre = re.sub(r"\d+$", p.area_digit, pre)
    return pre
