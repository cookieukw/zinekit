"""Render a title as a transparent PNG, the kind of layer the Punk Zine text mode expects.

The letters are drawn on a transparent canvas; every glyph becomes a separate
piece of alpha, which is what makes the plugin cut each one onto its own scrap.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .fonts import find_font

CANVASES: Dict[str, Optional[Tuple[int, int]]] = {
    "1920x1080": (1920, 1080),
    "1280x720": (1280, 720),
    "3840x2160": (3840, 2160),
    "1080x1080": (1080, 1080),
    "1080x1350": (1080, 1350),
    "1080x1920": (1080, 1920),
    "fit": None,
}


@dataclass
class TextStyle:
    text: str = "PUNK ZINE"
    font: str = ""                 # 'Family Style', a family, or a font file; '' = a bold sans
    size: int = 180                # px
    color: str = "#ffffff"
    stroke: int = 0                # outline width, px
    stroke_color: str = "#000000"
    align: str = "center"          # left | center | right
    line_spacing: float = 1.0      # multiple of the font's line height
    letter_spacing: int = 0        # extra px between letters
    canvas: str = "1920x1080"      # a key of CANVASES, 'WxH', or 'fit'
    margin: int = 80               # px kept free around the text

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TextStyle":
        known = {k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        style = cls(**known)
        style.size = max(4, int(style.size))
        style.stroke = max(0, int(style.stroke))
        style.margin = max(0, int(style.margin))
        style.letter_spacing = int(style.letter_spacing)
        style.line_spacing = float(style.line_spacing)
        if style.align not in ("left", "center", "right"):
            style.align = "center"
        return style


def parse_canvas(spec: str) -> Optional[Tuple[int, int]]:
    spec = (spec or "fit").strip().lower().replace("×", "x")
    if spec in CANVASES:
        return CANVASES[spec]
    w, sep, h = spec.partition("x")
    if not sep:
        raise ValueError("canvas must be WxH or 'fit', got %r" % spec)
    size = (int(w), int(h))
    if size[0] < 8 or size[1] < 8 or size[0] * size[1] > 8192 * 8192:
        raise ValueError("canvas size out of range: %r" % spec)
    return size


def load_font(name: str, size: int):
    face = find_font(name)
    if face is None:
        try:
            return ImageFont.load_default(size)
        except TypeError:          # Pillow < 10.1
            return ImageFont.load_default()
    return ImageFont.truetype(face.path, size, index=face.index)


def _line_width(draw, line: str, font, spacing: int) -> float:
    if not line:
        return 0.0
    if spacing == 0:
        return draw.textlength(line, font=font)
    return sum(draw.textlength(glyph, font=font) for glyph in line) + spacing * (len(line) - 1)


def render_text(style: TextStyle) -> Image.Image:
    """The text as an RGBA image (transparent background)."""
    font = load_font(style.font, style.size)
    lines = (style.text or " ").replace("\r\n", "\n").split("\n")
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    try:
        ascent, descent = font.getmetrics()
    except AttributeError:
        ascent, descent = style.size, style.size // 4
    line_h = max(1, int(round((ascent + descent) * style.line_spacing)))
    widths = [_line_width(probe, ln, font, style.letter_spacing) for ln in lines]
    pad = style.size + style.stroke * 2 + abs(style.letter_spacing) + 8
    block_w = int(max(widths + [1.0])) + 2 * pad
    block_h = line_h * (len(lines) - 1) + ascent + descent + 2 * pad
    scratch = Image.new("RGBA", (block_w, block_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(scratch)
    inner = max(widths + [1.0])
    for i, (ln, lw) in enumerate(zip(lines, widths)):
        if style.align == "left":
            x = float(pad)
        elif style.align == "right":
            x = pad + inner - lw
        else:
            x = pad + (inner - lw) / 2.0
        y = pad + ascent + i * line_h
        kw = dict(font=font, fill=style.color, anchor="ls")
        if style.stroke:
            kw.update(stroke_width=style.stroke, stroke_fill=style.stroke_color)
        if style.letter_spacing == 0:
            draw.text((x, y), ln, **kw)
        else:
            for glyph in ln:
                draw.text((x, y), glyph, **kw)
                x += draw.textlength(glyph, font=font) + style.letter_spacing
    bbox = scratch.getchannel("A").getbbox()
    if bbox is None:
        bbox = (0, 0, 1, 1)
    block = scratch.crop(bbox)
    m = style.margin
    canvas = parse_canvas(style.canvas)
    if canvas is None:
        out = Image.new("RGBA", (block.width + 2 * m, block.height + 2 * m), (0, 0, 0, 0))
        out.paste(block, (m, m))
        return out
    cw, ch = canvas
    room_w, room_h = max(1, cw - 2 * m), max(1, ch - 2 * m)
    if block.width > room_w or block.height > room_h:
        k = min(room_w / block.width, room_h / block.height)
        block = block.resize((max(1, int(block.width * k)), max(1, int(block.height * k))), Image.LANCZOS)
    out = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    if style.align == "left":
        x = m
    elif style.align == "right":
        x = cw - m - block.width
    else:
        x = (cw - block.width) // 2
    out.paste(block, (x, (ch - block.height) // 2))
    return out
