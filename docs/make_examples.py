#!/usr/bin/env python3
"""Rebuild the pictures in docs/images from docs/input with the zinekit API.

    python3 docs/make_examples.py
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from zinekit import engine, presets  # noqa: E402
from zinekit.fonts import default_font  # noqa: E402
from zinekit.textlayer import TextStyle, render_text  # noqa: E402

IN = os.path.join(HERE, "input")
OUT = os.path.join(HERE, "images")
INK, PAPER = (21, 19, 17), (247, 243, 232)


def font(size):
    f = default_font()
    return ImageFont.truetype(f.path, size, index=f.index) if f else ImageFont.load_default()


def label(img, text):
    im = img.convert("RGB")
    d = ImageDraw.Draw(im)
    f = font(max(14, im.height // 22))
    x0, y0, x1, y1 = d.textbbox((0, 0), text, font=f)
    d.rectangle((0, 0, x1 + 20, y1 + 14), fill=INK)
    d.text((10, 7), text, font=f, fill=PAPER)
    return im


def over(rgba, under):
    base = under.convert("RGBA").resize(rgba.size)
    base.alpha_composite(rgba)
    return base


def grid(cells, cols, cw, ch, gap=6):
    rows = (len(cells) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cw + (cols - 1) * gap, rows * ch + (rows - 1) * gap), PAPER)
    for i, c in enumerate(cells):
        sheet.paste(c.resize((cw, ch), Image.LANCZOS), ((i % cols) * (cw + gap), (i // cols) * (ch + gap)))
    return sheet


def save(img, name):
    path = os.path.join(OUT, name)
    img.save(path, quality=84, optimize=True)
    print("wrote", os.path.relpath(path, os.path.dirname(HERE)), "%d KB" % (os.path.getsize(path) // 1024))


def main():
    os.makedirs(OUT, exist_ok=True)
    frame = Image.open(os.path.join(IN, "frame.jpg")).convert("RGBA")
    dim = Image.eval(frame.convert("RGB"), lambda v: int(v * 0.45))
    element = Image.open(os.path.join(IN, "element.png")).convert("RGBA")
    title = render_text(TextStyle(text="NO FUTURE\nzine #1", size=170, stroke=5, canvas="1280x720"))

    # the three modes, before and after
    cells = [label(over(title, dim), "text: input"), label(over(element, dim), "element: input"),
             label(frame, "image: input"),
             label(over(engine.apply(title, {"mode": 1 / 3}), dim), "text"),
             label(over(engine.apply(element, {"mode": 2 / 3}), dim), "element"),
             label(engine.apply(frame, {"mode": 1.0}), "image")]
    save(grid(cells, 3, 520, 292), "modes.jpg")

    # the built-in presets on the photo, the title and the element
    cells = []
    for name in ("Xerox photo", "Riso duotone", "Burned photocopy", "Blue riso"):
        cells.append(label(engine.apply(frame, presets.find(name).values()), name))
    for name in ("Ransom note", "Ransom note, black and white"):
        cells.append(label(over(engine.apply(title, presets.find(name).values()), dim), name))
    for name in ("Pink riso sticker", "Clean cut-out"):
        cells.append(label(over(engine.apply(element, presets.find(name).values()), dim), name))
    save(grid(cells, 4, 400, 225), "presets.jpg")

    # the text tool
    t = render_text(TextStyle(text="ZINEKIT", size=260, canvas="1600x500", margin=40))
    save(over(engine.apply(t, {"mode": 1 / 3, "chaos": 0.75, "roughness": 0.6}),
              Image.new("RGB", t.size, (36, 34, 40))).convert("RGB"), "text.jpg")


if __name__ == "__main__":
    main()
