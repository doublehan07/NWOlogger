"""ADIF band enumeration and frequency → band lookup. Frequencies are in MHz."""

from __future__ import annotations

import math
import re
from decimal import Decimal, DecimalException

_FREQUENCY_RE = re.compile(r"([+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:e[+-]?\d+)?)\s*(hz|khz|mhz|ghz)?", re.I)
_UNIT_EXPONENT = {"hz": -6, "khz": -3, "mhz": 0, "ghz": 3}

# (band, lower MHz, upper MHz), in frequency order.
BANDS: list[tuple[str, float, float]] = [
    ("2190m", 0.1357, 0.1378),
    ("630m", 0.472, 0.479),
    ("560m", 0.501, 0.504),
    ("160m", 1.8, 2.0),
    ("80m", 3.5, 4.0),
    ("60m", 5.06, 5.45),
    ("40m", 7.0, 7.3),
    ("30m", 10.1, 10.15),
    ("20m", 14.0, 14.35),
    ("17m", 18.068, 18.168),
    ("15m", 21.0, 21.45),
    ("12m", 24.89, 24.99),
    ("10m", 28.0, 29.7),
    ("8m", 40.0, 45.0),
    ("6m", 50.0, 54.0),
    ("5m", 54.000001, 69.9),
    ("4m", 70.0, 71.0),
    ("2m", 144.0, 148.0),
    ("1.25m", 222.0, 225.0),
    ("70cm", 420.0, 450.0),
    ("33cm", 902.0, 928.0),
    ("23cm", 1240.0, 1300.0),
    ("13cm", 2300.0, 2450.0),
    ("9cm", 3300.0, 3500.0),
    ("6cm", 5650.0, 5925.0),
    ("3cm", 10000.0, 10500.0),
    ("1.25cm", 24000.0, 24250.0),
    ("6mm", 47000.0, 47200.0),
    ("4mm", 75500.0, 81000.0),
    ("2.5mm", 119980.0, 123000.0),
    ("2mm", 134000.0, 149000.0),
    ("1mm", 241000.0, 250000.0),
    ("submm", 300000.0, 7500000.0),
]

BAND_NAMES = [b[0] for b in BANDS]
_ORDER = {name: i for i, name in enumerate(BAND_NAMES)}

# Bands commonly used in contests (HF, no WARC).
CONTEST_BANDS = ["160m", "80m", "40m", "20m", "15m", "10m"]


def parse_frequency(value: object, *, default_unit: str = "MHz") -> float | None:
    """Return a positive, finite frequency in MHz; bare values use ``default_unit``.

    A decimal comma is accepted alongside a decimal point. Units are explicit rather than guessed from
    magnitude, so e.g. a bare 10000 MHz microwave frequency remains 10000 MHz.
    """
    if value is None or isinstance(value, bool):
        return None
    match = _FREQUENCY_RE.fullmatch(str(value).strip())
    if not match:
        return None
    unit = (match[2] or default_unit).lower()
    exponent = _UNIT_EXPONENT.get(unit)
    if exponent is None:
        return None
    try:
        # Convert in decimal before producing the float, so e.g. 479 kHz stays exactly the same
        # float as the 0.479 MHz band edge instead of landing a binary rounding step above it.
        mhz = float(Decimal(match[1].replace(",", ".")).scaleb(exponent))
    except (ValueError, OverflowError, DecimalException):
        return None
    return mhz if math.isfinite(mhz) and mhz > 0 else None


def normalize_band(band: str | None) -> str | None:
    if not band:
        return None
    b = band.strip().lower()
    return b if b in _ORDER else None


def band_for_freq(mhz: float | None) -> str | None:
    if mhz is None:
        return None
    try:
        f = float(mhz)
    except (TypeError, ValueError):
        return None
    for name, lo, hi in BANDS:
        if lo <= f <= hi:
            return name
    return None


def band_order(band: str | None) -> int:
    return _ORDER.get((band or "").lower(), 999)


def display_band(band: str | None) -> str:
    return (band or "").upper()


def default_freq(band: str) -> float | None:
    for name, lo, _hi in BANDS:
        if name == band:
            return lo
    return None
