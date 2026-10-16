"""Your rigs, antennas and setups (Preferences → Devices).

A setup is a named rig + antenna + power that you put together yourself, e.g. "Sat portable: FT-5DR, U7V4, 5 W".
Picking it in the entry form fills all three; each can still be changed for the QSO. Rig and antenna are logged
as ADIF MY_RIG and MY_ANTENNA, power as TX_PWR.
"""

from __future__ import annotations

import re
from typing import Any

_POWER = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(k?w)?\s*$", re.I)


def power_value(text: Any) -> float | None:
    """Watts from "25", "25W", "25 w", "0.5", "1,5" or "1 kW"; None when empty or not a number."""
    if text is None or text == "":
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = _POWER.match(str(text))
    if not m:
        return None
    w = float(m.group(1).replace(",", "."))
    return w * 1000 if (m.group(2) or "").lower() == "kw" else w


def clean_setups(raw: Any) -> list[dict]:
    """Setups from preferences: each {name, rig, antenna, power}; entries without a name are dropped."""
    out = []
    for s in raw or []:
        if not isinstance(s, dict) or not str(s.get("name") or "").strip():
            continue
        out.append({"name": str(s["name"]).strip(), "rig": str(s.get("rig") or "").strip(),
                    "antenna": str(s.get("antenna") or "").strip(), "power": power_value(s.get("power"))})
    return out


def clean_names(raw: Any) -> list[str]:
    """A rig or antenna list: trimmed, no blanks, no repeats (ignoring case), in your order."""
    out: list[str] = []
    seen: set[str] = set()
    for n in raw or []:
        n = str(n or "").strip()
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def matching(setups: list[dict], rig: str | None, antenna: str | None, power: Any) -> int | None:
    """The index of the first setup that the form's rig, antenna and power equal (a setup without a power
    matches any power), or None."""
    r, a, p = (rig or "").strip().lower(), (antenna or "").strip().lower(), power_value(power)
    for i, s in enumerate(setups):
        if s["rig"].lower() == r and s["antenna"].lower() == a and (s["power"] is None or s["power"] == p):
            return i
    return None
