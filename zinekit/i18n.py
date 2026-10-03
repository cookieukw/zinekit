"""Translations for the editor, read from zinekit/locales/*.json.

Every text the editor shows is written in English in the code and passed
through ``tr()``.  The text is the key: ``tr("Open image…")`` looks it up in
the current language file and falls back to ``en.json``, then to the key itself.

A language file is a flat JSON object.  ``"_language"`` holds the name shown
in the View → Language menu:

    {"_language": "Português (Brasil)", "Open image…": "Abrir imagem…", ...}

To add a language, copy ``en.json`` to ``<code>.json`` (``es``, ``fr``,
``pt_PT``…), translate the values and restart.  ``tests/test_i18n.py`` checks
that every file has every key and keeps the %s / %d placeholders.

The language is ``ZINEKIT_LANG``, else the one chosen in the editor, else the
system locale (``pt_BR.UTF-8`` -> ``pt_BR``; ``pt`` or ``pt_PT`` -> ``pt_BR`` while
that is the only Portuguese file), else English.  The command line is always
in English.
"""
from __future__ import annotations

import json
import locale
import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
DEFAULT = "en"

_lang: Optional[str] = None


def N_(text: str) -> str:
    """Marks a text for translation where it is defined; tr() translates it where it is shown."""
    return text


@lru_cache(maxsize=None)
def _table(code: str) -> Dict[str, str]:
    path = LOCALES_DIR / (code + ".json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def available() -> Dict[str, str]:
    """{code: language name} for every locales/*.json file."""
    return {f.stem: _table(f.stem).get("_language", f.stem) for f in sorted(LOCALES_DIR.glob("*.json"))}


def match(code: Optional[str]) -> Optional[str]:
    """The available language closest to a locale code ('pt_BR.UTF-8', 'pt', 'en_US')."""
    if not code:
        return None
    c = str(code).split(".")[0].split("@")[0].replace("-", "_").strip()
    if not c or c in ("C", "POSIX"):
        return None
    low = {k.lower(): k for k in available()}
    if c.lower() in low:
        return low[c.lower()]
    base = c.split("_")[0].lower()
    if base in low:
        return low[base]
    for k in sorted(low):              # 'pt' or 'pt_PT' -> 'pt_BR'
        if k.split("_")[0] == base:
            return low[k]
    return None


def system_language() -> str:
    for var in ("ZINEKIT_LANG", "LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        val = os.environ.get(var)
        if not val:
            continue
        for part in val.split(":"):
            m = match(part)
            if m:
                return m
        if var != "ZINEKIT_LANG":
            return DEFAULT
    try:
        loc = locale.getlocale()[0]
    except ValueError:
        loc = None
    return match(loc) or DEFAULT


def set_language(code: str) -> str:
    """Switch the language (unknown codes fall back to English); returns the code now in use."""
    global _lang
    _lang = match(code) or DEFAULT
    return _lang


def language() -> str:
    global _lang
    if _lang is None:
        _lang = system_language()
    return _lang


def tr(text: str) -> str:
    lang = language()
    if lang != DEFAULT:
        value = _table(lang).get(text)
        if value:
            return value
    return _table(DEFAULT).get(text) or text
