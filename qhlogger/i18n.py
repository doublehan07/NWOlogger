"""UI translation. Source strings are English; ``tr()`` looks them up in ``translations/<lang>.json``."""

from __future__ import annotations

import json
from pathlib import Path

TRANSLATIONS = Path(__file__).resolve().parent / "resources" / "translations"
LANGUAGES = {"en": "English", "zh_CN": "简体中文"}

_catalog: dict[str, str] = {}
_lang = "en"


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
    return _catalog.get(text, text)
