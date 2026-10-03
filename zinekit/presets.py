"""Presets: named parameter sets in plugin units.

A preset file is JSON::

    {"zinekit": 1, "name": "Pink riso sticker",
     "params": {"mode": 0.6667, "element_style": 0.25, "cut_margin": 0.6}}

Only the values that differ from the defaults need to be listed; everything
else is the default.  User presets live in ``~/.config/zinekit/presets``
(``$XDG_CONFIG_HOME`` is honoured, ``ZINEKIT_CONFIG`` overrides the folder).
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Union

from . import params as P
from .i18n import tr

TEXT, ELEMENT, IMAGE = 1 / 3, 2 / 3, 1.0   # mode values

BUILTIN: Dict[str, Dict[str, P.Value]] = {
    "Default": {},
    "Ransom note": {"mode": TEXT, "chaos": 0.8, "roughness": 0.6, "scrap_padding": 0.55},
    "Ransom note, black and white": {"mode": TEXT, "scrap_palette": 0.25, "chaos": 0.65, "misregistration": 0.25},
    "Pink riso sticker": {"mode": ELEMENT, "element_style": 0.25, "cut_margin": 0.6, "outline": 0.35,
                          "misregistration": 0.5},
    "Clean cut-out": {"mode": ELEMENT, "element_style": 1.0, "roughness": 0.25, "grain": 0.15, "outline": 0.2,
                      "misregistration": 0.0, "paper_texture": 0.3},
    "Xerox photo": {"mode": IMAGE, "image_style": 0.0, "contrast": 0.7, "burn": 0.5, "grain": 0.5},
    "Riso duotone": {"mode": IMAGE, "image_style": 2 / 3, "misregistration": 0.5, "dot_size": 0.3},
    "Burned photocopy": {"mode": IMAGE, "image_style": 1.0, "burn": 0.8, "grain": 0.7, "contrast": 0.8},
    "Blue riso": {"mode": IMAGE, "image_style": 1 / 3, "color1": "#2f6bff", "dot_size": 0.26},
}


@dataclass
class Preset:
    name: str
    params: Dict[str, P.Value] = field(default_factory=dict)
    builtin: bool = False
    path: Optional[Path] = None

    def values(self) -> Dict[str, P.Value]:
        """All parameters, defaults filled in."""
        return P.complete(self.params)

    def to_json(self) -> str:
        return json.dumps({"zinekit": 1, "name": self.name, "params": P.diff_from_defaults(self.values())},
                          indent=2, ensure_ascii=False) + "\n"


def config_dir() -> Path:
    env = os.environ.get("ZINEKIT_CONFIG")
    if env:
        return Path(env).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "zinekit"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / "zinekit"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "zinekit"


def presets_dir() -> Path:
    return config_dir() / "presets"


def slug(name: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", name.strip().lower(), flags=re.UNICODE).strip("-")
    return s or "preset"


def parse(data: Union[str, Mapping], fallback_name: str = "Preset") -> Preset:
    """A preset from JSON text or a dict.  Accepts {'params': {...}} or a flat {name: value} map."""
    obj = json.loads(data) if isinstance(data, str) else dict(data)
    if not isinstance(obj, dict):
        raise ValueError(tr("a preset must be a JSON object"))
    name = str(obj.get("name") or fallback_name)
    raw = obj.get("params", obj)
    if not isinstance(raw, dict):
        raise ValueError(tr("'params' must be an object"))
    clean: Dict[str, P.Value] = {}
    for key, value in raw.items():
        p = P.BY_NAME.get(key)
        if p is None:
            continue
        try:
            clean[key] = p.clean(value)
        except (TypeError, ValueError):
            raise ValueError(tr("bad value for %s:") % key + " %r" % (value,)) from None
    return Preset(name, clean)


def load_file(path: Union[str, Path]) -> Preset:
    path = Path(path)
    preset = parse(path.read_text(encoding="utf-8"), fallback_name=path.stem)
    preset.path = path
    return preset


def builtins() -> List[Preset]:
    return [Preset(name, P.complete(values), builtin=True) for name, values in BUILTIN.items()]


def user_presets() -> List[Preset]:
    out = []
    d = presets_dir()
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            try:
                out.append(load_file(f))
            except (OSError, ValueError):
                continue
    return out


def all_presets() -> List[Preset]:
    return builtins() + user_presets()


def find(name: str) -> Preset:
    """A preset by name (case-insensitive), slug, or a path to a .json file."""
    path = Path(name).expanduser()
    if name.endswith(".json") and path.exists():
        return load_file(path)
    from .i18n import _table, available
    low = name.strip().lower()
    translated = [_table(code) for code in available()]
    for p in all_presets():
        names = {p.name.lower(), slug(p.name)}
        if p.builtin:
            for table in translated:          # built-in presets also answer to their translated names
                if table.get(p.name):
                    names |= {table[p.name].lower(), slug(table[p.name])}
        if low in names or slug(name) in names:
            return p
    raise KeyError("no preset named %r (see 'zinekit presets')" % name)


def save_user(name: str, values: Mapping[str, P.Value]) -> Path:
    d = presets_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / (slug(name) + ".json")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(Preset(name, dict(values)).to_json(), encoding="utf-8")
    os.replace(tmp, path)
    return path


def delete_user(preset: Preset) -> None:
    if preset.builtin or preset.path is None:
        raise ValueError(tr("Built-in presets cannot be deleted."))
    preset.path.unlink()
