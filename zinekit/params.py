"""The Punk Zine parameters, as the Kdenlive effect panel shows them.

Two kinds of units are used throughout zinekit:

* plugin units: what the frei0r plugin (and MLT, and preset files) take.
  Doubles in 0..1, lists as evenly spaced values (4 items: 0, 1/3, 2/3, 1),
  bools as 0/1 and colors as ``#rrggbb``.
* UI units: what a slider shows.  Percent for most values, degrees for the
  dot angle, an integer for the seed, item names for lists.

``Param.to_ui`` and ``Param.from_ui`` convert between them, and
``parse_assignment`` reads ``name=value`` in UI units for the command line.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Tuple, Union

Value = Union[float, int, bool, str]

ALL = ("text", "element", "image")
TEXT, ELEMENT, IMAGE = ("text",), ("element",), ("image",)
MODES = ("auto", "text", "element", "image")
GROUPS = ("General", "Text", "Element", "Image", "Halftone", "Colors", "Layout")


@dataclass(frozen=True)
class Param:
    name: str
    kind: str                      # slider | list | bool | color | int
    label: str
    group: str
    default: Value                 # plugin units
    tip: str = ""
    applies: Tuple[str, ...] = ALL
    options: Tuple[str, ...] = ()  # list item labels
    keys: Tuple[str, ...] = ()     # short names of list items for the CLI
    ui_max: float = 100.0
    factor: float = 100.0          # ui = value * factor
    suffix: str = "%"

    # -- lists
    def index_of(self, value: float) -> int:
        n = len(self.options)
        i = int(round(max(0.0, min(1.0, float(value))) * (n - 1)))
        return max(0, min(n - 1, i))

    def value_of(self, index: int) -> float:
        n = len(self.options)
        return max(0, min(n - 1, int(index))) / float(n - 1)

    # -- conversions
    def to_ui(self, value: Value) -> Value:
        if self.kind == "list":
            return self.index_of(float(value))
        if self.kind == "bool":
            return bool(float(value) >= 0.5)
        if self.kind == "color":
            return normalize_color(str(value))
        ui = float(value) * self.factor
        return int(round(ui)) if self.kind == "int" else ui

    def from_ui(self, ui: Value) -> Value:
        if self.kind == "list":
            return self.value_of(int(ui))
        if self.kind == "bool":
            return 1.0 if ui else 0.0
        if self.kind == "color":
            return normalize_color(str(ui))
        v = float(ui) / self.factor
        return max(0.0, min(1.0, v))

    def clean(self, value: Value) -> Value:
        """A plugin-unit value made valid for this parameter (lists snap to an item)."""
        if self.kind == "color":
            return normalize_color(str(value))
        v = float(value)
        if v != v:
            return self.default
        if self.kind == "list":
            return self.value_of(self.index_of(v))
        if self.kind == "bool":
            return 1.0 if v >= 0.5 else 0.0
        return max(0.0, min(1.0, v))

    def describe(self, value: Value) -> str:
        """Human readable value, e.g. '45%', '30°', 'Riso'."""
        ui = self.to_ui(value)
        if self.kind == "list":
            return self.options[int(ui)]
        if self.kind == "bool":
            return "on" if ui else "off"
        if self.kind == "color":
            return str(ui)
        if self.kind == "int":
            return str(ui)
        return "%g%s" % (round(float(ui), 1), self.suffix)


def normalize_color(value: str) -> str:
    s = value.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    s = s.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6 or any(c not in "0123456789abcdef" for c in s):
        raise ValueError("not a #rrggbb color: %r" % (value,))
    return "#" + s


def _p(name, kind, label, group, default, tip="", applies=ALL, **kw) -> Param:
    return Param(name, kind, label, group, default, tip, applies, **kw)


# Same order, defaults, ranges and tooltips as the Kdenlive effect (punkzine.xml).
PARAMS: Tuple[Param, ...] = (
    _p("mode", "list", "Print mode", "General", 0.0,
       "Auto decides from transparency: many separate pieces = text, one piece = element, no transparency = image.",
       options=("Auto detect", "Text (ransom)", "Element (cut-out)", "Image (halftone)"), keys=MODES),
    _p("mix", "slider", "Effect amount", "General", 1.0),
    _p("roughness", "slider", "Rough edges", "General", 0.5,
       "Bitten ink edges on letters and cut-outs, ragged halftone dots on images."),
    _p("paper_texture", "slider", "Paper texture", "General", 0.6),
    _p("grain", "slider", "Toner grain", "General", 0.4, "Toner grain and specks."),
    _p("misregistration", "slider", "Misregistration", "General", 0.4,
       "Offset of the second color plate. Text, element and the riso image styles."),
    _p("shadow", "slider", "Shadow distance", "General", 0.5, "Shadow distance. Text and element.", TEXT + ELEMENT),
    _p("shadow_opacity", "slider", "Shadow opacity", "General", 0.5, "Text and element.", TEXT + ELEMENT),

    _p("scrap_palette", "list", "Scraps", "Text", 0.0, "", TEXT,
       options=("Mixed", "Black and white", "Plate color", "Plate color 2", "No paper"),
       keys=("mixed", "bw", "plate", "plate2", "none")),
    _p("chaos", "slider", "Chaos", "Text", 0.6, "Tilt, jump and size of each letter.", TEXT),
    _p("scrap_padding", "slider", "Padding", "Text", 0.5, "Paper around each letter.", TEXT),
    _p("keep_text_color", "bool", "Keep color", "Text", 0.0, "Letters keep the title's own color.", TEXT),

    _p("element_style", "list", "Style", "Element", 0.0,
       "Auto picks Zine palette for flat graphics such as logos and Riso for shaded pictures.", ELEMENT,
       options=("Auto detect", "Riso", "Palette", "Xerox", "Original"),
       keys=("auto", "riso", "palette", "xerox", "original")),
    _p("cut_margin", "slider", "Margin", "Element", 0.5, "Paper margin of the scissor cut.", ELEMENT),
    _p("outline", "slider", "Outline", "Element", 0.3, "Ink outline.", ELEMENT),
    _p("recolor", "slider", "Recolor", "Element", 1.0,
       "0% keeps the element's colors, 100% prints it fully in the chosen style.", ELEMENT),

    _p("image_style", "list", "Style", "Image", 0.0, "", IMAGE,
       options=("Xerox", "Riso", "Riso 2 colors", "Photocopy"), keys=("xerox", "riso", "riso2", "photocopy")),
    _p("burn", "slider", "Burn", "Image", 0.35, "Burned photocopy edges.", IMAGE),

    _p("dot_size", "slider", "Dot size", "Halftone", 0.22, "Halftone dot size. Element and image.", ELEMENT + IMAGE),
    _p("dot_angle", "slider", "Dot angle", "Halftone", 1.0, "Halftone angle. Element and image.", ELEMENT + IMAGE,
       ui_max=45.0, factor=45.0, suffix="°"),
    _p("contrast", "slider", "Xerox contrast", "Halftone", 0.55, "Xerox contrast. Element and image.", ELEMENT + IMAGE),

    _p("ink", "color", "Ink", "Colors", "#151311", "Letters on light paper, outlines, halftone dots, shadows."),
    _p("paper", "color", "Paper", "Colors", "#f7f3e8", "Paper scraps, cut-out margins and the printed page."),
    _p("color1", "color", "Plate color", "Colors", "#ff4fa8",
       "The riso second ink: off-register copy, riso plate on elements and images. Also used for paper scraps."),
    _p("color2", "color", "Plate color 2", "Colors", "#35d45b",
       "Second plate of Image style \"Riso 2 colors\". Also used for paper scraps."),
    _p("color3", "color", "Scrap color", "Colors", "#ffe24a",
       "Extra paper color for the letter scraps and the element palette.", TEXT + ELEMENT),

    _p("seed", "int", "Layout seed", "Layout", 0.001, "Changes scrap colors and tilts.", ui_max=1000.0, factor=1000.0,
       suffix=""),
)

BY_NAME: Dict[str, Param] = {p.name: p for p in PARAMS}

# frei0r parameter index order (hosts such as ffmpeg address parameters by position)
PLUGIN_ORDER: Tuple[str, ...] = (
    "mode", "image_style", "scrap_palette", "element_style", "mix", "roughness", "chaos", "scrap_padding",
    "cut_margin", "outline", "recolor", "dot_size", "dot_angle", "contrast", "grain", "burn", "paper_texture",
    "misregistration", "shadow", "shadow_opacity", "keep_text_color", "seed", "ink", "paper", "color1", "color2",
    "color3",
)


def defaults() -> Dict[str, Value]:
    return {p.name: p.default for p in PARAMS}


def complete(values: Mapping[str, Value]) -> Dict[str, Value]:
    """Defaults overlaid with the known, cleaned values."""
    out = defaults()
    for name, v in values.items():
        p = BY_NAME.get(name)
        if p is not None:
            out[name] = p.clean(v)
    return out


def diff_from_defaults(values: Mapping[str, Value]) -> Dict[str, Value]:
    """Only the values that differ from the defaults (what a preset needs to store)."""
    out: Dict[str, Value] = {}
    for p in PARAMS:
        if p.name not in values:
            continue
        v = p.clean(values[p.name])
        if p.kind == "color":
            if v != normalize_color(str(p.default)):
                out[p.name] = v
        elif abs(float(v) - float(p.default)) > 1e-6:
            out[p.name] = round(float(v), 6)
    return out


def mode_of(values: Mapping[str, Value]) -> str:
    """'auto', 'text', 'element' or 'image'."""
    return MODES[BY_NAME["mode"].index_of(float(values.get("mode", 0.0)))]


def applies(param: Param, mode: str) -> bool:
    return mode == "auto" or mode in param.applies


def _parse_bool(text: str) -> bool:
    t = text.strip().lower()
    if t in ("1", "on", "true", "yes", "y", "sim", "s"):
        return True
    if t in ("0", "off", "false", "no", "n", "nao", "não"):
        return False
    raise ValueError("expected on/off, got %r" % text)


def parse_ui_value(param: Param, text: str) -> Value:
    """A value as the GUI shows it ('80', '80%', '30°', 'riso', 'on', '#ff0000') -> plugin units."""
    t = text.strip()
    if param.kind == "color":
        return normalize_color(t)
    if param.kind == "bool":
        return param.from_ui(_parse_bool(t))
    if param.kind == "list":
        low = t.lower()
        for i, (label, key) in enumerate(zip(param.options, param.keys or param.options)):
            if low in (label.lower(), key.lower()):
                return param.value_of(i)
        for i, label in enumerate(param.options):
            if label.lower().startswith(low):
                return param.value_of(i)
        if low.isdigit() and int(low) < len(param.options):
            return param.value_of(int(low))
        raise ValueError("%s: expected one of %s" % (param.name, ", ".join(param.keys or param.options)))
    t = t.rstrip("%°").replace("deg", "").strip()
    return param.from_ui(float(t))


def parse_assignment(text: str) -> Tuple[str, Value]:
    """'roughness=80' -> ('roughness', 0.8).  UI units, see parse_ui_value."""
    if "=" not in text:
        raise ValueError("expected name=value, got %r" % text)
    name, _, raw = text.partition("=")
    name = name.strip().replace("-", "_")
    p = BY_NAME.get(name)
    if p is None:
        raise ValueError("unknown parameter %r (see 'zinekit params')" % name)
    return name, parse_ui_value(p, raw)


def ffmpeg_filter(values: Mapping[str, Value]) -> str:
    """The same print as an ffmpeg filter (needs the punkzine module on FREI0R_PATH)."""
    from .engine import parse_color
    full = complete(values)
    parts = []
    for name in PLUGIN_ORDER:
        p = BY_NAME[name]
        v = full[name]
        if p.kind == "color":
            r, g, b = parse_color(str(v))
            parts.append("%.4g/%.4g/%.4g" % (r, g, b))
        elif p.kind == "bool":
            parts.append("y" if float(v) >= 0.5 else "n")
        else:
            parts.append("%.6g" % float(v))
    return "frei0r=filter_name=punkzine:filter_params=" + "|".join(parts)


def table(values: Iterable[Param] = PARAMS) -> str:
    """Plain-text listing for 'zinekit params'."""
    rows = []
    for p in values:
        if p.kind == "list":
            rng = " | ".join("%s" % k for k in (p.keys or p.options))
        elif p.kind == "bool":
            rng = "on | off"
        elif p.kind == "color":
            rng = "#rrggbb"
        else:
            rng = "0..%g%s" % (p.ui_max, p.suffix)
        rows.append((p.name, p.group, p.describe(p.default), rng, "all" if p.applies == ALL else ", ".join(p.applies)))
    head = ("name", "group", "default", "values", "applies to")
    widths = [max(len(str(r[i])) for r in rows + [head]) for i in range(len(head))]
    lines = ["  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)).rstrip() for r in [head] + rows]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)
