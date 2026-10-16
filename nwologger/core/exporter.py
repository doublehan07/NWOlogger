"""ADIF export of all, selected or filtered QSOs."""

from __future__ import annotations

from collections.abc import Iterable

from .. import __version__
from . import adif
from . import qso as qsomod


def records(qsos: Iterable[dict], *, include_private: bool = True, service: str | None = None,
            sat_catalog=None, include_service_status: bool = True) -> list[dict[str, str]]:
    out = []
    for q in qsos:
        sat = sat_catalog.to_service(service, q.get("sat_name")) if (service and sat_catalog) else None
        out.append(qsomod.to_adif(q, include_private=include_private, sat_name=sat,
                                  include_service_status=include_service_status))
    return out


def export_ids(db, ids: list[int] | None, path: str, *, include_private: bool = True) -> int:
    """Write the given QSO ids (None = whole log) to an ADIF file, oldest first."""
    if ids is None:
        rows = list(db.iter_all())
    else:
        got = db.get_many(ids)
        rows = sorted((got[i] for i in ids if i in got), key=lambda q: (q.get("time_on") or "", q["id"]))
    return adif.write_file(path, records(rows, include_private=include_private), program_version=__version__)


def dumps(qsos: Iterable[dict], **kw) -> str:
    return adif.dumps(records(qsos, **kw), program_version=__version__)
