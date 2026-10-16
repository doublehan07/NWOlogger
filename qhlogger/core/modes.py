"""ADIF modes and submodes, mode groups, input aliases, and how two modes relate.

Relations (see PLAN §5.1):

* ``SAME``       identical MODE + SUBMODE (USB–USB)
* ``COULD_BE``   one is a more general name for the other (SSB–USB, DATA–FT8, MFSK–FT4)
* ``SIBLING``    different specific modes in the same group (USB–LSB, FT8–FT4, FM–SSB)
* ``UNRELATED``  different groups (CW–SSB)
"""

from __future__ import annotations

from enum import IntEnum

# ADIF 3.1.x modes → submodes.
MODES: dict[str, list[str]] = {
    "AM": [],
    "ARDOP": [],
    "ATV": [],
    "CHIP": ["CHIP64", "CHIP128"],
    "CLO": [],
    "CONTESTI": [],
    "CW": ["PCW"],
    "DIGITALVOICE": ["C4FM", "DMR", "DSTAR", "FREEDV", "M17"],
    "DOMINO": ["DOM-M", "DOM4", "DOM5", "DOM8", "DOM11", "DOM16", "DOM22", "DOM44", "DOM88", "DOMINOEX", "DOMINOF"],
    "DYNAMIC": ["VARA HF", "VARA SATELLITE", "VARA FM 1200", "VARA FM 9600"],
    "FAX": [],
    "FM": [],
    "FSK441": [],
    "FT8": [],
    "HELL": ["FMHELL", "FSKH105", "FSKH245", "FSKHELL", "HELL80", "HELLX5", "HELLX9", "HFSK", "PSKHELL", "SLOWHELL"],
    "ISCAT": ["ISCAT-A", "ISCAT-B"],
    "JT4": ["JT4A", "JT4B", "JT4C", "JT4D", "JT4E", "JT4F", "JT4G"],
    "JT9": ["JT9-1", "JT9-2", "JT9-5", "JT9-10", "JT9-30", "JT9A", "JT9B", "JT9C", "JT9D", "JT9E",
            "JT9E FAST", "JT9F", "JT9F FAST", "JT9G", "JT9G FAST", "JT9H", "JT9H FAST"],
    "JT44": [],
    "JT65": ["JT65A", "JT65B", "JT65B2", "JT65C", "JT65C2"],
    "MFSK": ["FSQCALL", "FST4", "FST4W", "FT4", "JS8", "JTMS", "MFSK4", "MFSK8", "MFSK11", "MFSK16", "MFSK22",
             "MFSK31", "MFSK32", "MFSK64", "MFSK64L", "MFSK128", "MFSK128L", "Q65"],
    "MSK144": [],
    "MT63": [],
    "OLIVIA": ["OLIVIA 4/125", "OLIVIA 4/250", "OLIVIA 8/250", "OLIVIA 8/500", "OLIVIA 16/500",
               "OLIVIA 16/1000", "OLIVIA 32/1000"],
    "OPERA": ["OPERA-BEACON", "OPERA-QSO"],
    "PAC": ["PAC2", "PAC3", "PAC4"],
    "PAX": ["PAX2"],
    "PKT": [],
    "PSK": ["8PSK125", "8PSK125F", "8PSK125FL", "8PSK250", "8PSK250F", "8PSK250FL", "8PSK500", "8PSK500F",
            "8PSK1000", "8PSK1000F", "8PSK1200F", "FSK31", "PSK10", "PSK31", "PSK63", "PSK63F", "PSK63RC4",
            "PSK63RC5", "PSK63RC10", "PSK63RC20", "PSK63RC32", "PSK125", "PSK125C12", "PSK125R", "PSK125RC10",
            "PSK125RC12", "PSK125RC16", "PSK125RC4", "PSK125RC5", "PSK250", "PSK250C6", "PSK250R", "PSK250RC2",
            "PSK250RC3", "PSK250RC5", "PSK250RC6", "PSK250RC7", "PSK500", "PSK500C2", "PSK500C4", "PSK500R",
            "PSK500RC2", "PSK500RC3", "PSK500RC4", "PSK800C2", "PSK800RC2", "PSK1000", "PSK1000C2", "PSK1000R",
            "PSK1000RC2", "PSKAM10", "PSKAM31", "PSKAM50", "PSKFEC31", "QPSK31", "QPSK63", "QPSK125",
            "QPSK250", "QPSK500", "SIM31"],
    "PSK2K": [],
    "Q15": [],
    "QRA64": ["QRA64A", "QRA64B", "QRA64C", "QRA64D", "QRA64E"],
    "ROS": ["ROS-EME", "ROS-HF", "ROS-MF"],
    "RTTY": ["ASCI"],
    "RTTYM": [],
    "SSB": ["LSB", "USB"],
    "SSTV": [],
    "T10": [],
    "THOR": ["THOR-M", "THOR4", "THOR5", "THOR8", "THOR11", "THOR16", "THOR22", "THOR25X4", "THOR50X1",
             "THOR50X2", "THOR100"],
    "THRB": ["THRBX", "THRBX1", "THRBX2", "THRBX4", "THROB1", "THROB2", "THROB4"],
    "TOR": ["AMTORFEC", "GTOR", "NAVTEX", "SITORB"],
    "V4": [],
    "VOI": [],
    "WINMOR": [],
    "WSPR": [],
}

SUBMODE_PARENT: dict[str, str] = {sub: mode for mode, subs in MODES.items() for sub in subs}

# Group placeholders: a QSO logged only as "data" / "phone" / "image".
PLACEHOLDERS = {"DATA": "DATA", "PHONE": "PHONE", "IMAGE": "IMAGE"}

ALIASES: dict[str, str] = {
    "DIGI": "DATA",
    "DG": "DATA",
    "DIG": "DATA",
    "PH": "PHONE",
    "RY": "RTTY",
    "IMG": "IMAGE",
}

GROUPS = ("CW", "PHONE", "DATA", "IMAGE")
_PHONE = {"AM", "FM", "SSB", "DIGITALVOICE"}
_IMAGE = {"ATV", "FAX", "SSTV"}

# Modes offered first in dropdowns.
COMMON_MODES = ["SSB", "USB", "LSB", "CW", "FM", "AM", "FT8", "FT4", "RTTY", "PSK31", "JS8", "SSTV", "C4FM",
                "DSTAR", "DMR", "DATA", "PHONE"]


class Relation(IntEnum):
    UNRELATED = 0
    SIBLING = 1
    COULD_BE = 2
    SAME = 3


def normalize(text: str | None, submode: str | None = None) -> tuple[str | None, str | None]:
    """Turn what the user typed (or an ADIF MODE/SUBMODE pair) into ADIF (MODE, SUBMODE)."""
    if not text:
        return None, None
    t = " ".join(text.strip().upper().split())
    sub = " ".join(submode.strip().upper().split()) if submode else None
    t = ALIASES.get(t, t)
    if sub:
        sub = ALIASES.get(sub, sub)
    if t in MODES:
        if sub and SUBMODE_PARENT.get(sub) == t:
            return t, sub
        return t, None if not sub else sub
    if t in SUBMODE_PARENT:
        return SUBMODE_PARENT[t], t
    if t in PLACEHOLDERS:
        return t, None
    return t, sub  # unknown mode: keep as typed


def group(mode: str | None, submode: str | None = None) -> str | None:
    if not mode:
        return None
    m = mode.upper()
    if m in PLACEHOLDERS:
        return PLACEHOLDERS[m]
    if m == "CW":
        return "CW"
    if m in _PHONE:
        return "PHONE"
    if m in _IMAGE:
        return "IMAGE"
    return "DATA"


def display(mode: str | None, submode: str | None) -> str:
    """Show the most specific name: USB rather than SSB/USB."""
    return (submode or mode or "").upper()


def _generality(mode: str | None, submode: str | None) -> int:
    """0 = group placeholder, 1 = mode without submode, 2 = submode."""
    if mode and mode.upper() in PLACEHOLDERS:
        return 0
    return 2 if submode else 1


def relation(a: tuple[str | None, str | None], b: tuple[str | None, str | None]) -> Relation:
    am, asub = (a[0] or "").upper(), (a[1] or "").upper()
    bm, bsub = (b[0] or "").upper(), (b[1] or "").upper()
    if not am or not bm:
        return Relation.UNRELATED
    if am == bm and asub == bsub:
        return Relation.SAME
    ga, gb = group(am, asub), group(bm, bsub)
    if ga != gb:
        return Relation.UNRELATED
    # A group placeholder could be any mode of its group.
    if am in PLACEHOLDERS or bm in PLACEHOLDERS:
        return Relation.COULD_BE
    # SSB vs SSB/USB: same mode, one without submode.
    if am == bm and (not asub or not bsub):
        return Relation.COULD_BE
    return Relation.SIBLING


def is_known(mode: str | None) -> bool:
    return bool(mode) and (mode.upper() in MODES or mode.upper() in PLACEHOLDERS)


def all_mode_names() -> list[str]:
    """Every mode and submode name, for completion."""
    names = list(MODES) + list(SUBMODE_PARENT) + list(PLACEHOLDERS)
    return sorted(set(names))


def default_rst(mode: str | None, submode: str | None = None) -> str:
    g = group(mode, submode)
    if g == "PHONE":
        return "59"
    if g == "CW" or (mode or "").upper() in ("RTTY", "PSK"):
        return "599"
    if (mode or "").upper() in ("FT8",) or (submode or "").upper() in ("FT4", "Q65", "JS8", "FST4"):
        return ""  # digital reports are typed from the decoder
    return "599"
