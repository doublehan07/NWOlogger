"""UI translation. Source strings are English; ``tr()`` looks them up in ``translations/<lang>.json``."""

from __future__ import annotations

import atexit
import json
import os
from pathlib import Path

TRANSLATIONS = Path(__file__).resolve().parent / "resources" / "translations"
LANGUAGES = {"en": "English", "zh_CN": "简体中文"}

_catalog: dict[str, str] = {}
_lang = "en"
# Developer aid: QHLOGGER_I18N_RECORD=<file> collects every string the UI asks to translate.
_record_path = os.environ.get("QHLOGGER_I18N_RECORD")
_recorded: set[str] = set()
if _record_path:
    def _dump() -> None:
        old = set()
        if os.path.exists(_record_path):
            with open(_record_path, encoding="utf-8") as fh:
                old = set(json.load(fh))
        with open(_record_path, "w", encoding="utf-8") as fh:
            json.dump(sorted(old | _recorded), fh, ensure_ascii=False, indent=0)

    atexit.register(_dump)


def load(lang: str) -> None:
    global _catalog, _lang
    _lang = lang if lang in LANGUAGES else "en"
    _catalog = {}
    if _lang == "en":
        return
    p = TRANSLATIONS / f"{_lang}.json"
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        _catalog = {k: v for k, v in data.items() if isinstance(v, str) and v}


def lang() -> str:
    return _lang


def tr(text: str) -> str:
    if _record_path:
        _recorded.add(text)
    return _catalog.get(text, text)
