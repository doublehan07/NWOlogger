"""Which QSOs still need uploading to a service.

One rule for Upload pending, the "Not uploaded to …" presets and the grey rows in the log, so they always agree:
LoTW and eQSL are done when sent (Y) or ignored (I); QRZ.com and HRDLog when uploaded (Y), not when never sent
(empty, N) or modified since (M). Repeater and internet QSOs are never sent to LoTW.
"""

from __future__ import annotations

from . import propagation

SERVICES = {"lotw": "LoTW", "eqsl": "eQSL", "qrz": "QRZ.com", "hrdlog": "HRDLog"}
SENT_COL = {"lotw": "lotw_qsl_sent", "eqsl": "eqsl_qsl_sent", "qrz": "qrzcom_qso_upload_status",
            "hrdlog": "hrdlog_qso_upload_status"}
SENT_DATE = {"lotw": "lotw_qslsdate", "eqsl": "eqsl_qslsdate", "qrz": "qrzcom_qso_upload_date",
             "hrdlog": "hrdlog_qso_upload_date"}
_DONE = {"lotw": ("Y", "I"), "eqsl": ("Y", "I")}  # qrz / hrdlog: anything but empty, N and M is done
_REDO = ("N", "M")


def _sql(service: str) -> str:
    col = SENT_COL[service]
    if service in _DONE:
        done = ", ".join(f"'{v}'" for v in _DONE[service])
        w = f"({col} IS NULL OR {col} = '' OR {col} NOT IN ({done}))"
    else:
        w = f"({col} IS NULL OR {col} = '' OR {col} IN ({', '.join(repr(v) for v in _REDO)}))"
    if service == "lotw":
        skip = ", ".join(f"'{p}'" for p in sorted(propagation.LOTW_SKIP))
        w += f" AND (prop_mode IS NULL OR UPPER(prop_mode) NOT IN ({skip}))"
    return w


PENDING_SQL = {s: _sql(s) for s in SERVICES}
ANY_PENDING_SQL = "(" + " OR ".join(f"({w})" for w in PENDING_SQL.values()) + ")"


def lotw_skipped(q: dict) -> bool:
    return (q.get("prop_mode") or "").upper() in propagation.LOTW_SKIP


def needs_upload(q: dict, service: str) -> bool:
    if service == "lotw" and lotw_skipped(q):
        return False
    v = q.get(SENT_COL[service])
    if v in (None, ""):
        return True
    return v not in _DONE[service] if service in _DONE else v in _REDO


def missing(q: dict) -> list[str]:
    """The services this QSO still has to be uploaded to, e.g. ["eqsl", "hrdlog"]."""
    return [s for s in SERVICES if needs_upload(q, s)]
