"""ffmpeg/ffprobe helpers: what a file is, its size and rate, single frames."""
from __future__ import annotations

import io
import json
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple, Union

from .i18n import tr

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".tga", ".jfif"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv", ".flv", ".gif", ".mts",
              ".m2ts", ".ts", ".ogv", ".3gp", ".mxf"}

PathLike = Union[str, Path]


class MediaError(RuntimeError):
    pass


def kind(path: PathLike) -> Optional[str]:
    """'image', 'video' or None, from the extension."""
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise MediaError(tr("ffmpeg was not found on PATH (install ffmpeg to process videos)"))
    return exe


def ffprobe() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        raise MediaError(tr("ffprobe was not found on PATH (it comes with ffmpeg)"))
    return exe


def have_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@lru_cache(maxsize=1)
def encoders() -> frozenset:
    try:
        res = subprocess.run([ffmpeg(), "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30)
    except (MediaError, OSError, subprocess.SubprocessError):
        return frozenset()
    names = set()
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return frozenset(names)


def pick_encoder(*names: str) -> Optional[str]:
    have = encoders()
    for n in names:
        if n in have:
            return n
    return None


@dataclass
class VideoInfo:
    path: Path
    width: int            # as decoded (rotation applied)
    height: int
    fps: Fraction
    duration: float       # seconds, 0 if unknown
    frames: int           # estimate
    has_audio: bool
    codec: str
    pix_fmt: str
    alpha: bool           # the stream carries transparency

    @property
    def fps_str(self) -> str:
        return "%d/%d" % (self.fps.numerator, self.fps.denominator)


def _rate(text: str) -> Optional[Fraction]:
    try:
        f = Fraction(text)
    except (ValueError, ZeroDivisionError, TypeError):
        return None
    return f if 0 < f <= 1000 else None


def probe(path: PathLike) -> VideoInfo:
    path = Path(path)
    cmd = [ffprobe(), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.SubprocessError as err:
        raise MediaError("ffprobe failed on %s: %s" % (path.name, err)) from None
    if res.returncode != 0:
        raise MediaError(tr("ffprobe could not read %s:") % path.name + " " + res.stderr.strip()[-400:])
    data = json.loads(res.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if video is None:
        raise MediaError(tr("%s has no video stream") % path.name)
    w, h = int(video.get("width") or 0), int(video.get("height") or 0)
    rotation = 0.0
    for sd in video.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rotation = float(sd["rotation"])
            except (TypeError, ValueError):
                pass
    tag_rot = (video.get("tags") or {}).get("rotate")
    if tag_rot:
        try:
            rotation = float(tag_rot)
        except ValueError:
            pass
    if int(round(abs(rotation))) % 180 == 90:
        w, h = h, w
    fps = _rate(video.get("avg_frame_rate", "")) or _rate(video.get("r_frame_rate", "")) or Fraction(30)
    if fps > 240:
        fps = _rate(video.get("r_frame_rate", "")) or Fraction(30)
        if fps > 240:
            fps = Fraction(30)
    duration = 0.0
    for src in (video.get("duration"), (data.get("format") or {}).get("duration")):
        if src is None:
            continue
        try:
            duration = float(src)
            if duration > 0:
                break
        except (TypeError, ValueError):
            continue
    try:
        frames = int(video.get("nb_frames") or 0)
    except ValueError:
        frames = 0
    if frames <= 0 and duration > 0:
        frames = int(round(duration * float(fps)))
    pix_fmt = video.get("pix_fmt") or ""
    codec = video.get("codec_name") or ""
    alpha_tag = str((video.get("tags") or {}).get("alpha_mode", "")).strip() == "1"
    alpha = alpha_tag or pix_fmt.startswith(("yuva", "rgba", "bgra", "argb", "abgr", "ya", "gbrap", "pal8"))
    if codec == "gif":
        alpha = True
    return VideoInfo(path, w, h, fps, duration, max(frames, 0),
                     any(s.get("codec_type") == "audio" for s in streams), codec, pix_fmt, alpha)


def decoder_args(info: VideoInfo) -> list:
    """Input options that keep transparency (VP8/VP9 alpha needs the libvpx decoder)."""
    if info.alpha and info.codec in ("vp8", "vp9"):
        name = "libvpx-vp9" if info.codec == "vp9" else "libvpx"
        try:
            res = subprocess.run([ffmpeg(), "-hide_banner", "-decoders"], capture_output=True, text=True, timeout=30)
            if name in res.stdout:
                return ["-c:v", name]
        except (OSError, subprocess.SubprocessError):
            pass
    return []


def fit_size(w: int, h: int, max_height: int = 0, even: bool = True) -> Tuple[int, int]:
    """Size after limiting the height (never upscales), rounded to even numbers for video codecs."""
    tw, th = float(w), float(h)
    if max_height and h > max_height:
        th = float(max_height)
    if even:
        th = max(2, int(th) - int(th) % 2)
        tw = w * th / h
        return max(2, int(round(tw / 2.0)) * 2), int(th)
    tw = w * th / h
    return max(1, int(round(tw))), max(1, int(round(th)))


def grab_frame(path: PathLike, time: float = 0.0, max_height: int = 0, info: Optional[VideoInfo] = None):
    """One RGBA frame of a video as a PIL image."""
    from PIL import Image
    info = info or probe(path)
    w, h = fit_size(info.width, info.height, max_height, even=False)
    t = max(0.0, min(float(time), max(0.0, info.duration - 0.05))) if info.duration else max(0.0, float(time))
    cmd = [ffmpeg(), "-v", "error", "-nostdin"]
    if t > 0:
        cmd += ["-ss", "%.3f" % t]
    cmd += decoder_args(info) + ["-i", str(path), "-map", "0:v:0", "-frames:v", "1",
                                 "-vf", "scale=%d:%d:flags=lanczos,format=rgba" % (w, h),
                                 "-f", "image2pipe", "-c:v", "png", "-"]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=60)
    except subprocess.SubprocessError as err:
        raise MediaError("ffmpeg failed: %s" % err) from None
    if res.returncode != 0 or not res.stdout:
        if t > 0:     # seeking past the last decodable frame: take the first one
            return grab_frame(path, 0.0, max_height, info)
        raise MediaError(tr("could not read a frame of %s:") % Path(path).name + " "
                         + res.stderr.decode(errors="replace").strip()[-400:])
    img = Image.open(io.BytesIO(res.stdout))
    img.load()
    return img.convert("RGBA")


def load_image(path: PathLike):
    """An image file as RGBA, EXIF rotation applied."""
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        im.seek(0)
        im = ImageOps.exif_transpose(im)
        return im.convert("RGBA")
