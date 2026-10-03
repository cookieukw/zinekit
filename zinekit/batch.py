"""Apply Punk Zine to many images and videos.

Images are read with Pillow.  Videos are decoded by ffmpeg into raw RGBA
frames, printed one by one by the plugin and piped into a second ffmpeg that
encodes them and copies the audio of the original.  Reading, printing and
writing run on separate threads so ffmpeg and the plugin work at the same time.
"""
from __future__ import annotations

import os
import queue
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from . import media
from . import params as P
from .engine import Plugin, Renderer, get_plugin
from .i18n import N_, tr

IMAGE_FORMATS = {   # key: (extension, keeps alpha)
    "png": (".png", True),
    "jpg": (".jpg", False),
    "webp": (".webp", True),
    "tiff": (".tif", True),
    "same": ("", True),
}
VIDEO_FORMATS = {   # key: (extension, keeps alpha, description)
    "mp4": (".mp4", False, N_("MP4 H.264 (no transparency)")),
    "mov": (".mov", True, N_("MOV ProRes 4444 (transparency)")),
    "webm": (".webm", True, N_("WebM VP9 (transparency)")),
    "png": ("", True, N_("PNG sequence (transparency)")),
    "gif": (".gif", True, N_("GIF")),
}
BACKGROUNDS = ("black", "white", "paper", "transparent")

Progress = Callable[[int, int, float, str], None]   # (file index, file count, fraction of this file, message)


class Cancelled(Exception):
    pass


@dataclass
class BatchOptions:
    out_dir: Optional[Path] = None     # None: next to each source file
    suffix: str = "_zine"
    image_format: str = "png"
    video_format: str = "mp4"
    max_height: int = 0                # 0 keeps the size
    background: str = "black"          # for formats without transparency: black | white | paper | transparent | #rrggbb
    overwrite: bool = False
    jpeg_quality: int = 92
    crf: int = 18                      # H.264 quality (lower is better)

    def __post_init__(self) -> None:
        if self.out_dir is not None:
            self.out_dir = Path(self.out_dir).expanduser()
        if self.image_format not in IMAGE_FORMATS:
            raise ValueError("image format must be one of %s" % ", ".join(IMAGE_FORMATS))
        if self.video_format not in VIDEO_FORMATS:
            raise ValueError("video format must be one of %s" % ", ".join(VIDEO_FORMATS))


@dataclass
class JobResult:
    source: Path
    output: Optional[Path]
    ok: bool
    error: str = ""
    seconds: float = 0.0
    frames: int = 0


@dataclass
class BatchReport:
    results: List[JobResult] = field(default_factory=list)
    cancelled: bool = False

    @property
    def ok(self) -> int:
        return sum(r.ok for r in self.results)

    @property
    def failed(self) -> int:
        return sum(not r.ok for r in self.results)


def background_rgb(background: str, values: Mapping[str, P.Value]) -> Optional[Tuple[int, int, int]]:
    """The background color for formats without alpha, or None for transparent/keep."""
    b = (background or "black").strip().lower()
    if b == "transparent":
        return None
    named = {"black": "#000000", "white": "#ffffff"}
    if b == "paper":
        hexv = str(P.complete(values)["paper"])
    else:
        hexv = named.get(b, b)
    hexv = P.normalize_color(hexv)
    v = int(hexv[1:], 16)
    return (v >> 16) & 255, (v >> 8) & 255, v & 255


def expand_inputs(paths: Iterable[Union[str, os.PathLike]], recursive: bool = False) -> List[Path]:
    """Files as given, folders expanded to the images and videos inside them."""
    out: List[Path] = []
    seen = set()
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            it = p.rglob("*") if recursive else p.iterdir()
            found = sorted(f for f in it if f.is_file() and media.kind(f))
        elif p.is_file():
            found = [p]
        else:
            raise FileNotFoundError(str(p))
        for f in found:
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                out.append(f)
    return out


def output_path(src: Path, opts: BatchOptions, taken: Optional[set] = None) -> Path:
    """Where the print of src goes; never the source itself, never a name already taken."""
    k = media.kind(src)
    folder = opts.out_dir or src.parent
    if k == "video":
        ext = VIDEO_FORMATS[opts.video_format][0]
    else:
        ext = IMAGE_FORMATS[opts.image_format][0] or src.suffix.lower()
    stem = src.stem + opts.suffix
    taken = taken if taken is not None else set()
    n = 1
    while True:
        cand = folder / (stem + ("" if n == 1 else "-%d" % n) + ext)
        clash = cand.resolve() == src.resolve() or cand in taken
        if not clash and (opts.overwrite or not cand.exists()):
            taken.add(cand)
            return cand
        n += 1


def flatten(img, rgb: Optional[Tuple[int, int, int]]):
    """RGBA over a solid color (None keeps the alpha)."""
    from PIL import Image
    if rgb is None:
        return img
    base = Image.new("RGBA", img.size, rgb + (255,))
    base.alpha_composite(img.convert("RGBA"))
    return base.convert("RGB")


def save_image(img, dst: Path, values: Mapping[str, P.Value], opts: BatchOptions) -> None:
    ext = dst.suffix.lower()
    keeps_alpha = ext in (".png", ".webp", ".tif", ".tiff", ".tga")
    rgb = background_rgb(opts.background, values) if not keeps_alpha else None
    if not keeps_alpha and rgb is None:
        rgb = (0, 0, 0)
    out = flatten(img, rgb)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(".%s.part%s" % (dst.stem, dst.suffix))
    kw: Dict[str, object] = {}
    if ext in (".jpg", ".jpeg", ".jfif"):
        kw = {"quality": int(opts.jpeg_quality), "optimize": True, "subsampling": 0}
    elif ext == ".webp":
        kw = {"quality": 92, "method": 4}
    elif ext == ".png":
        kw = {"compress_level": 6}
    try:
        out.save(tmp, format=_pil_format(ext), **kw)
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            tmp.unlink()


def _pil_format(ext: str) -> str:
    return {".jpg": "JPEG", ".jpeg": "JPEG", ".jfif": "JPEG", ".png": "PNG", ".webp": "WEBP", ".tif": "TIFF",
            ".tiff": "TIFF", ".bmp": "BMP", ".tga": "TGA"}.get(ext, "PNG")


def process_image(src: Path, dst: Path, values: Mapping[str, P.Value], opts: BatchOptions,
                  plugin: Optional[Plugin] = None) -> JobResult:
    from PIL import Image
    t0 = time.time()
    img = media.load_image(src)
    if opts.max_height and img.height > opts.max_height:
        img = img.resize(media.fit_size(img.width, img.height, opts.max_height, even=False), Image.LANCZOS)
    with Renderer(plugin or get_plugin(), img.width, img.height) as r:
        out = r.process_image(img, P.complete(values))
    save_image(out, dst, values, opts)
    return JobResult(src, dst, True, seconds=time.time() - t0, frames=1)


# ---------------------------------------------------------------- video
def _encode_args(fmt: str, info: media.VideoInfo, w: int, h: int, rgb: Optional[Tuple[int, int, int]],
                 crf: int) -> Tuple[List[str], List[str], List[str]]:
    """(filter args, video codec args, audio codec args) for the encoder."""
    fps = info.fps_str
    flt: List[str] = []
    if fmt == "mp4":
        if rgb is not None:
            flt = ["-filter_complex", "color=c=0x%02x%02x%02x:s=%dx%d:r=%s[bg];[bg][0:v]overlay=shortest=1:format=auto,"
                   "format=yuv420p[v]" % (rgb + (w, h, fps)), "-map", "[v]"]
        else:
            flt = ["-map", "0:v", "-pix_fmt", "yuv420p"]
        enc = media.pick_encoder("libx264", "libopenh264", "mpeg4")
        if enc == "libx264":
            vc = ["-c:v", "libx264", "-preset", "medium", "-crf", str(int(crf))]
        elif enc == "libopenh264":
            vc = ["-c:v", "libopenh264", "-b:v", "%dk" % max(2000, w * h * 8 // 1000)]
        elif enc == "mpeg4":
            vc = ["-c:v", "mpeg4", "-q:v", "2"]
        else:
            raise media.MediaError(tr("this ffmpeg has no H.264 or MPEG-4 encoder"))
        return flt, vc + ["-movflags", "+faststart"], ["-c:a", "aac", "-b:a", "192k"]
    if fmt == "mov":
        enc = media.pick_encoder("prores_ks", "prores")
        if not enc:
            raise media.MediaError(tr("this ffmpeg has no ProRes encoder"))
        vc = ["-c:v", enc, "-profile:v", "4444" if enc == "prores_ks" else "4", "-pix_fmt", "yuva444p10le"]
        if enc == "prores_ks":
            vc += ["-alpha_bits", "16", "-vendor", "apl0"]
        return ["-map", "0:v"], vc, ["-c:a", "pcm_s16le"]
    if fmt == "webm":
        if not media.pick_encoder("libvpx-vp9"):
            raise media.MediaError(tr("this ffmpeg has no VP9 encoder (libvpx-vp9)"))
        vc = ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "30", "-row-mt", "1",
              "-deadline", "good", "-cpu-used", "4", "-auto-alt-ref", "0"]
        audio = media.pick_encoder("libopus", "libvorbis")
        ac = ["-c:a", audio, "-b:a", "160k"] if audio else ["-an"]
        return ["-map", "0:v"], vc, ac
    if fmt == "gif":
        flt = ["-filter_complex", "[0:v]split[a][b];[a]palettegen=reserve_transparent=1:stats_mode=diff[p];"
               "[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle:alpha_threshold=128[v]",
               "-map", "[v]"]
        return flt, ["-loop", "0"], ["-an"]
    if fmt == "png":
        return ["-map", "0:v"], ["-c:v", "png", "-pix_fmt", "rgba", "-start_number", "1"], ["-an"]
    raise ValueError(fmt)


def _tail(path: str, limit: int = 1200) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - limit))
            return f.read().decode(errors="replace").strip()
    except OSError:
        return ""


def process_video(src: Path, dst: Path, values: Mapping[str, P.Value], opts: BatchOptions,
                  plugin: Optional[Plugin] = None, progress: Optional[Callable[[float, str], None]] = None,
                  cancel: Optional[threading.Event] = None) -> JobResult:
    t0 = time.time()
    info = media.probe(src)
    w, h = media.fit_size(info.width, info.height, opts.max_height, even=opts.video_format in ("mp4", "webm"))
    fmt = opts.video_format
    keeps_alpha = VIDEO_FORMATS[fmt][1]
    rgb = None if keeps_alpha else background_rgb(opts.background, values)
    flt, vc, ac = _encode_args(fmt, info, w, h, rgb, opts.crf)
    ff = media.ffmpeg()

    if fmt == "png":
        out_dir = dst          # output_path() gives a folder name for PNG sequences
        out_dir.mkdir(parents=True, exist_ok=True)
        target = str(out_dir / "frame_%06d.png")
        final = out_dir
        part = None
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        part = dst.with_name(".%s.part%s" % (dst.stem, dst.suffix))
        target = str(part)
        final = dst

    decode = [ff, "-v", "error", "-nostdin"] + media.decoder_args(info) + [
        "-i", str(src), "-map", "0:v:0",
        "-vf", "fps=%s,scale=%d:%d:flags=lanczos,format=rgba" % (info.fps_str, w, h),
        "-f", "rawvideo", "-pix_fmt", "rgba", "-"]
    encode = [ff, "-v", "error", "-nostdin", "-y",
              "-f", "rawvideo", "-pix_fmt", "rgba", "-s", "%dx%d" % (w, h), "-framerate", info.fps_str, "-i", "-"]
    with_audio = info.has_audio and "-an" not in ac
    if with_audio:
        encode += ["-i", str(src)]
    encode += flt
    if with_audio:
        encode += ["-map", "1:a:0?"] + ac + ["-shortest"]
    else:
        encode += ["-an"]
    encode += vc + [target]

    nbytes = w * h * 4
    total = info.frames or 0
    err_dec = tempfile.NamedTemporaryFile(prefix="zinekit-dec-", suffix=".log", delete=False)
    err_enc = tempfile.NamedTemporaryFile(prefix="zinekit-enc-", suffix=".log", delete=False)
    dec = subprocess.Popen(decode, stdout=subprocess.PIPE, stderr=err_dec, bufsize=0)
    enc = subprocess.Popen(encode, stdin=subprocess.PIPE, stderr=err_enc, bufsize=0)
    q_in: "queue.Queue" = queue.Queue(maxsize=3)
    q_out: "queue.Queue" = queue.Queue(maxsize=3)
    failure: List[str] = []
    stop = threading.Event()

    def reader() -> None:
        try:
            assert dec.stdout is not None
            while not stop.is_set():
                buf = bytearray(nbytes)
                view = memoryview(buf)
                got = 0
                while got < nbytes:
                    n = dec.stdout.readinto(view[got:])  # type: ignore[attr-defined]
                    if not n:
                        break
                    got += n
                if got < nbytes:
                    break       # end of stream (a partial frame at the end is dropped)
                q_in.put(buf)
        except Exception as e:  # pragma: no cover - pipe errors
            failure.append(tr("reading frames: %s") % e)
        finally:
            q_in.put(None)

    def writer() -> None:
        assert enc.stdin is not None
        broken = False
        while True:            # keeps draining after an error so the main loop never blocks
            item = q_out.get()
            if item is None:
                break
            if broken:
                continue
            try:
                enc.stdin.write(item)
            except (BrokenPipeError, OSError):
                broken = True
                failure.append(tr("the encoder stopped early"))
        try:
            enc.stdin.close()
        except OSError:
            pass

    rt = threading.Thread(target=reader, name="zinekit-read", daemon=True)
    wt = threading.Thread(target=writer, name="zinekit-write", daemon=True)
    rt.start()
    wt.start()
    frames = 0
    renderer = Renderer(plugin or get_plugin(), w, h)
    renderer.set_params(P.complete(values))
    cancelled = False
    try:
        while True:
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            if failure:
                break
            item = q_in.get()
            if item is None:
                break
            q_out.put(renderer.process(item, item))   # in place: the plugin copies the input first
            frames += 1
            if progress and (frames % 5 == 0 or frames == 1):
                frac = min(0.999, frames / total) if total else 0.0
                progress(frac, tr("%s: frame %d of %d") % (src.name, frames, total) if total
                         else tr("%s: frame %d") % (src.name, frames))
    finally:
        stop.set()
        if cancelled or failure:
            dec.kill()
        if cancelled:
            enc.kill()
        q_out.put(None)
        wt.join()
        # drain the reader so it can see the stop flag / EOF
        while rt.is_alive():
            try:
                q_in.get(timeout=0.1)
            except queue.Empty:
                pass
        rt.join()
        renderer.close()
        dec.wait()
        enc.wait()
        if dec.stdout is not None:
            dec.stdout.close()
        err_dec.close()
        err_enc.close()
    dec_log, enc_log = _tail(err_dec.name), _tail(err_enc.name)
    os.unlink(err_dec.name)
    os.unlink(err_enc.name)
    try:
        if cancelled:
            raise Cancelled()
        if dec.returncode not in (0, None) and not failure:
            failure.append(tr("ffmpeg could not decode %s:") % src.name + "\n" + dec_log)
        if enc.returncode != 0:
            failure.append(tr("ffmpeg could not encode %s:") % final.name + "\n" + (enc_log or dec_log))
        if frames == 0 and not failure:
            failure.append(tr("no frames could be read from %s:") % src.name + "\n" + dec_log)
        if failure:
            raise media.MediaError("\n".join(failure))
        if part is not None:
            os.replace(part, dst)
    finally:
        if part is not None and part.exists():
            part.unlink()
    return JobResult(src, final, True, seconds=time.time() - t0, frames=frames)


def run_batch(files: Sequence[Union[str, os.PathLike]], values: Mapping[str, P.Value], opts: BatchOptions,
              progress: Optional[Progress] = None, cancel: Optional[threading.Event] = None,
              plugin: Optional[Plugin] = None) -> BatchReport:
    """Print every file; failures are reported per file and do not stop the batch."""
    plugin = plugin or get_plugin()
    report = BatchReport()
    taken: set = set()
    sources = [Path(f) for f in files]
    n = len(sources)
    for i, src in enumerate(sources):
        if cancel is not None and cancel.is_set():
            report.cancelled = True
            break
        k = media.kind(src)
        if progress:
            progress(i, n, 0.0, src.name)
        t0 = time.time()
        try:
            if k is None:
                raise ValueError(tr("not an image or video file"))
            dst = output_path(src, opts, taken)
            if k == "image":
                res = process_image(src, dst, values, opts, plugin)
            else:
                res = process_video(src, dst, values, opts, plugin,
                                    progress=(lambda f, m, i=i: progress(i, n, f, m)) if progress else None,
                                    cancel=cancel)
        except Cancelled:
            report.cancelled = True
            report.results.append(JobResult(src, None, False, "cancelled", time.time() - t0))
            break
        except Exception as e:
            res = JobResult(src, None, False, str(e) or e.__class__.__name__, time.time() - t0)
        report.results.append(res)
        if progress:
            progress(i, n, 1.0, ("%s -> %s" % (src.name, res.output.name)) if res.ok and res.output
                     else "%s: %s" % (src.name, res.error.splitlines()[0] if res.error else tr("failed")))
    return report
