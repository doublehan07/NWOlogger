"""ADIF ADI reader and writer.

The reader is tolerant: tag names are case-insensitive, the header is optional, and field lengths are accepted
both as characters and as UTF-8 bytes, because real-world files disagree.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime

ADIF_VERSION = "3.1.8"

_TAG = re.compile(r"<([A-Za-z0-9_]+)(?::(\d+)(?::([A-Za-z]))?)?>")
_NEXT_TAG = re.compile(r"<[A-Za-z0-9_]+(?::\d+(?::[A-Za-z])?)?>")


def decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def _take(text: str, pos: int, n: int) -> tuple[str, int]:
    """Read a field value of declared length ``n`` starting at ``pos``.

    Tries ``n`` characters first. If the value contains non-ASCII text and the next tag does not follow cleanly,
    falls back to counting ``n`` UTF-8 bytes.
    """
    by_chars = text[pos:pos + n]
    if by_chars.isascii():
        return by_chars, pos + n
    # Count n bytes.
    count = 0
    end = pos
    while end < len(text) and count < n:
        count += len(text[end].encode("utf-8"))
        end += 1
    by_bytes = text[pos:end]

    def clean_after(e: int) -> bool:
        m = _NEXT_TAG.search(text, e)
        gap = text[e:m.start()] if m else text[e:]
        return gap.strip() == "" or m is None

    if _NEXT_TAG.search(by_chars) and not _NEXT_TAG.search(by_bytes):
        return by_bytes, end
    if clean_after(pos + n):
        return by_chars, pos + n
    if clean_after(end):
        return by_bytes, end
    return by_chars, pos + n


def parse(data: bytes | str) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Parse ADI text. Returns ``(header, records)``; tag names are upper-case."""
    text = decode(data) if isinstance(data, bytes) else data
    header: dict[str, str] = {}
    records: list[dict[str, str]] = []
    pos = 0
    # A header exists when the file does not start with '<'. It ends at <EOH>.
    first = text.lstrip()
    current: dict[str, str] = {}
    in_header = not first.startswith("<")
    if not in_header:
        # Some files start with a header field such as <ADIF_VER:5>. Look for <EOH> before the first <EOR>.
        eoh = re.search(r"<eoh>", text, re.I)
        eor = re.search(r"<eor>", text, re.I)
        if eoh and (not eor or eoh.start() < eor.start()):
            in_header = True
    while True:
        m = _TAG.search(text, pos)
        if not m:
            break
        name = m.group(1).upper()
        length = m.group(2)
        pos = m.end()
        if length is None:
            if name == "EOH":
                header.update(current)
                current = {}
                in_header = False
            elif name == "EOR":
                if current and not in_header:
                    records.append(current)
                current = {}
            continue
        n = int(length)
        value, pos = _take(text, pos, n)
        current[name] = value
    if current and not in_header and any(k in current for k in ("CALL", "QSO_DATE")):
        records.append(current)
    return header, records


def read_file(path: str) -> tuple[dict[str, str], list[dict[str, str]]]:
    with open(path, "rb") as fh:
        return parse(fh.read())


def _field(tag: str, value: str) -> str:
    return f"<{tag}:{len(value)}>{value}"


def dumps(records: Iterable[dict[str, str]], *, program_version: str, program_id: str = "QHlogger",
          comment: str | None = None) -> str:
    created = datetime.now(UTC).strftime("%Y%m%d %H%M%S")
    lines = [comment or f"ADIF export from {program_id} {program_version}", ""]
    head = [
        _field("ADIF_VER", ADIF_VERSION),
        _field("PROGRAMID", program_id),
        _field("PROGRAMVERSION", program_version),
        _field("CREATED_TIMESTAMP", created),
    ]
    lines.append(" ".join(head))
    lines.append("<EOH>")
    lines.append("")
    for rec in records:
        parts = [_field(tag, value) for tag, value in rec.items() if value is not None and value != ""]
        lines.append(" ".join(parts) + " <EOR>")
    return "\n".join(lines) + "\n"


def write_file(path: str, records: Iterable[dict[str, str]], *, program_version: str) -> int:
    recs = list(records)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(dumps(recs, program_version=program_version))
    return len(recs)
