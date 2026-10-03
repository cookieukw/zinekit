"""Test inputs made on the fly (no binary fixtures)."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="zinekit-test-"))


def title(w=640, h=360) -> Image.Image:
    """White blocky letters on transparency: what the text mode expects."""
    from zinekit.textlayer import TextStyle, render_text
    return render_text(TextStyle(text="ZINE", size=int(h * 0.3), canvas="%dx%d" % (w, h), margin=20))


def blob(w=640, h=360) -> Image.Image:
    """One shaded shape on transparency: an element."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((w * 0.3, h * 0.15, w * 0.7, h * 0.85), fill=(60, 170, 110, 255))
    d.ellipse((w * 0.4, h * 0.3, w * 0.55, h * 0.5), fill=(230, 220, 80, 255))
    return im


def photo(w=640, h=360) -> Image.Image:
    """An opaque gradient with shapes: an image."""
    im = Image.linear_gradient("L").resize((w, h)).convert("RGB")
    d = ImageDraw.Draw(im)
    d.rectangle((w * 0.1, h * 0.2, w * 0.4, h * 0.8), fill=(200, 60, 90))
    d.ellipse((w * 0.5, h * 0.1, w * 0.9, h * 0.9), fill=(40, 90, 200))
    return im


def video(path: Path, size="320x180", seconds=1, fps=12, audio=True, alpha=False) -> Path:
    """A test clip: testsrc2 (opaque, H.264) or a title on transparency (ProRes 4444)."""
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if alpha:
        w, h = (int(v) for v in size.split("x"))
        png = path.with_suffix(".title.png")
        title(w, h).save(png)
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", str(seconds), "-i", str(png)]
    else:
        cmd += ["-f", "lavfi", "-i", "testsrc2=s=%s:r=%d:d=%s" % (size, fps, seconds)]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=f=330:d=%s" % seconds]
    if alpha:
        cmd += ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le"]
    else:
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "pcm_s16le" if alpha else "aac", "-shortest"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    return path


def env_isolated():
    """Point zinekit's config at a temporary folder (presets, GUI settings)."""
    d = tmpdir()
    os.environ["ZINEKIT_CONFIG"] = str(d / "config")
    os.environ["XDG_CONFIG_HOME"] = str(d / "xdg")
    return d
