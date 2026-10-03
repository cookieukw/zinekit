"""Find the fonts installed on the system (for the text tool)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

FONT_EXTS = (".ttf", ".otf", ".ttc", ".otc")
PREFERRED = ("DejaVu Sans Bold", "Liberation Sans Bold", "Noto Sans Bold", "Arial Bold", "Helvetica Bold",
             "Cantarell Bold", "Ubuntu Bold", "FreeSans Bold", "DejaVu Sans Book", "Liberation Sans Regular")


@dataclass(frozen=True)
class FontFace:
    family: str
    style: str
    path: str
    index: int = 0

    @property
    def label(self) -> str:
        return ("%s %s" % (self.family, self.style)).strip()


def _font_dirs() -> List[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts"), Path("/Library/Fonts"), home / "Library" / "Fonts"]
    if os.name == "nt":
        return [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                Path(os.environ.get("LOCALAPPDATA", home)) / "Microsoft" / "Windows" / "Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), home / ".local" / "share" / "fonts",
            home / ".fonts"]


def _from_fc_list() -> List[FontFace]:
    exe = shutil.which("fc-list")
    if not exe:
        return []
    try:
        res = subprocess.run([exe, "--format", "%{family[0]}\t%{style[0]}\t%{file}\t%{index}\n", ":scalable=true"],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    faces = []
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 4 or not parts[2].lower().endswith(FONT_EXTS):
            continue
        try:
            index = int(parts[3])
        except ValueError:
            index = 0
        faces.append(FontFace(parts[0].strip(), parts[1].strip(), parts[2], index))
    return faces


def _from_scan(limit: int = 3000) -> List[FontFace]:
    from PIL import ImageFont
    faces = []
    for d in _font_dirs():
        if not d.is_dir():
            continue
        for root, _dirs, files in os.walk(d):
            for name in files:
                if not name.lower().endswith(FONT_EXTS):
                    continue
                path = os.path.join(root, name)
                try:
                    family, style = ImageFont.truetype(path, 12).getname()
                except (OSError, ValueError):
                    continue
                faces.append(FontFace(family or Path(name).stem, style or "", path))
                if len(faces) >= limit:
                    return faces
    return faces


@lru_cache(maxsize=1)
def list_fonts() -> tuple:
    """All usable font faces, sorted by family then style, without duplicates."""
    faces = _from_fc_list() or _from_scan()
    seen = set()
    out = []
    for f in sorted(faces, key=lambda f: (f.family.lower(), f.style.lower(), f.path)):
        if f.label.lower() in seen:
            continue
        seen.add(f.label.lower())
        out.append(f)
    return tuple(out)


def find_font(query: str) -> Optional[FontFace]:
    """A font by file path, by 'Family Style' label, or by family name (any style)."""
    if not query:
        return default_font()
    path = Path(query).expanduser()
    if path.suffix.lower() in FONT_EXTS and path.exists():
        return FontFace(path.stem, "", str(path))
    low = query.strip().lower()
    faces = list_fonts()
    for f in faces:
        if f.label.lower() == low:
            return f
    family = [f for f in faces if f.family.lower() == low]
    if family:
        for style in ("bold", "regular", "book", "medium"):
            for f in family:
                if f.style.lower() == style:
                    return f
        return family[0]
    for f in faces:
        if low in f.label.lower():
            return f
    return None


def default_font() -> Optional[FontFace]:
    faces = list_fonts()
    by_label = {f.label.lower(): f for f in faces}
    for name in PREFERRED:
        if name.lower() in by_label:
            return by_label[name.lower()]
    return faces[0] if faces else None
