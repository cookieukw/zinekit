"""zinekit command line.

    zinekit                         open the editor
    zinekit gui [FILES...]          open the editor with files
    zinekit apply INPUT... -o OUT   print images, videos or folders
    zinekit text "NO FUTURE" -o title.png
    zinekit params | presets | fonts | ffmpeg | doctor
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import __version__
from . import params as P
from .batch import IMAGE_FORMATS, VIDEO_FORMATS

EPILOG = """parameter values (--set) are written the way the editor shows them:
  --set roughness=80          percent
  --set dot_angle=30          degrees
  --set mode=text             list items by name (see 'zinekit params')
  --set keep_text_color=on    on/off
  --set ink=#000000           colors
  --set seed=42               0..1000
"""


def _values(args) -> Dict[str, P.Value]:
    from . import presets
    values = presets.find(args.preset).values() if getattr(args, "preset", None) else P.defaults()
    for item in getattr(args, "set", None) or []:
        name, v = P.parse_assignment(item)
        values[name] = v
    return values


def _add_value_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("-p", "--preset", help="preset name or .json file (see 'zinekit presets')")
    p.add_argument("-s", "--set", action="append", metavar="NAME=VALUE", help="override one parameter (repeatable)")


class _Bar:
    def __init__(self) -> None:
        self.tty = sys.stderr.isatty()
        self.last = 0.0

    def __call__(self, i: int, n: int, frac: float, msg: str) -> None:
        now = time.time()
        if frac >= 1.0 or frac == 0.0:
            if self.tty:
                sys.stderr.write("\r\033[K")
            if frac >= 1.0:
                sys.stderr.write("[%d/%d] %s\n" % (i + 1, n, msg))
            sys.stderr.flush()
            return
        if self.tty and now - self.last > 0.2:
            self.last = now
            sys.stderr.write("\r\033[K[%d/%d] %3d%% %s" % (i + 1, n, int(frac * 100), msg))
            sys.stderr.flush()


def cmd_apply(args) -> int:
    from . import batch, media
    values = _values(args)
    files = batch.expand_inputs(args.inputs, recursive=args.recursive)
    if not files:
        print("nothing to do: no images or videos found", file=sys.stderr)
        return 1
    out = Path(args.output).expanduser() if args.output else None
    single_target: Optional[Path] = None
    if out is not None and len(files) == 1 and out.suffix and not out.is_dir():
        single_target = out
    image_format, video_format = args.image_format, args.video_format
    if single_target is not None:
        ext = single_target.suffix.lower()
        if media.kind(files[0]) == "video":
            video_format = {".mp4": "mp4", ".m4v": "mp4", ".mov": "mov", ".webm": "webm", ".gif": "gif"}.get(ext, "")
            if not video_format:
                print("unsupported video output %s (use .mp4, .mov, .webm or .gif)" % ext, file=sys.stderr)
                return 2
        else:
            image_format = {".png": "png", ".jpg": "jpg", ".jpeg": "jpg", ".webp": "webp", ".tif": "tiff",
                            ".tiff": "tiff"}.get(ext, "")
            if not image_format:
                print("unsupported image output %s (use .png, .jpg, .webp or .tif)" % ext, file=sys.stderr)
                return 2
    opts = batch.BatchOptions(out_dir=None if single_target else out, suffix=args.suffix,
                              image_format=image_format, video_format=video_format, max_height=args.max_height,
                              background=args.background, overwrite=args.overwrite or bool(single_target),
                              jpeg_quality=args.quality, crf=args.crf)
    cancel = threading.Event()
    bar = _Bar()
    try:
        if single_target is not None:
            src = files[0]
            single_target.parent.mkdir(parents=True, exist_ok=True)
            bar(0, 1, 0.0, src.name)
            if media.kind(src) == "video":
                res = batch.process_video(src, single_target, values, opts,
                                          progress=lambda f, m: bar(0, 1, f, m), cancel=cancel)
            else:
                res = batch.process_image(src, single_target, values, opts)
            bar(0, 1, 1.0, "%s -> %s" % (src.name, res.output))
            return 0
        report = batch.run_batch(files, values, opts, progress=bar, cancel=cancel)
    except KeyboardInterrupt:
        cancel.set()
        print("\ncancelled", file=sys.stderr)
        return 130
    for r in report.results:
        if not r.ok:
            print("failed: %s\n  %s" % (r.source, r.error.replace("\n", "\n  ")), file=sys.stderr)
    print("%d done, %d failed" % (report.ok, report.failed), file=sys.stderr)
    return 0 if report.failed == 0 else 1


def cmd_text(args) -> int:
    from . import engine
    from .batch import BatchOptions, save_image
    from .textlayer import TextStyle, render_text
    text = sys.stdin.read() if args.text == "-" else args.text.replace("\\n", "\n")
    style = TextStyle(text=text.rstrip("\n"), font=args.font or "", size=args.size, color=args.color,
                      stroke=args.stroke, stroke_color=args.stroke_color, align=args.align,
                      line_spacing=args.line_spacing, letter_spacing=args.letter_spacing, canvas=args.canvas,
                      margin=args.margin)
    layer = render_text(style)
    values = _values(args)
    if not args.plain:
        chose_mode = args.preset or any(s.partition("=")[0].strip() == "mode" for s in args.set or [])
        if not chose_mode:
            values["mode"] = P.BY_NAME["mode"].value_of(1)      # text
        layer = engine.apply(layer, values)
    out = Path(args.output).expanduser()
    save_image(layer, out, values, BatchOptions(background=args.background))
    print(out)
    return 0


def cmd_params(args) -> int:
    print(P.table())
    print()
    print(EPILOG.rstrip())
    return 0


def cmd_presets(args) -> int:
    from . import presets
    if args.name:
        sys.stdout.write(presets.find(args.name).to_json())
        return 0
    for p in presets.all_presets():
        changed = P.diff_from_defaults(p.values())
        desc = ", ".join("%s=%s" % (k, P.BY_NAME[k].describe(v)) for k, v in changed.items()) or "defaults"
        print("%-30s %s%s" % (p.name, "" if p.builtin else "[user] ", desc))
    print("\nuser presets: %s" % presets.presets_dir())
    return 0


def cmd_fonts(args) -> int:
    from .fonts import default_font, list_fonts
    query = (args.filter or "").lower()
    for f in list_fonts():
        if query in f.label.lower():
            print("%-40s %s" % (f.label, f.path))
    d = default_font()
    print("\ndefault: %s" % (d.label if d else "Pillow's built-in font"))
    return 0


def cmd_ffmpeg(args) -> int:
    print(P.ffmpeg_filter(_values(args)))
    return 0


def cmd_doctor(args) -> int:
    import importlib.util
    import platform
    from . import engine, media
    ok = True
    print("zinekit %s, Python %s, %s" % (__version__, platform.python_version(), platform.platform()))
    print("compiler: %s" % (engine.find_compiler() or "none (needed once to build the plugin)"))
    try:
        plugin = engine.get_plugin()
        print("plugin:   %s (%s %s, %d parameters)" % (plugin.path, plugin.name, plugin.version, len(plugin.params)))
        print("          FREI0R_PATH for ffmpeg/melt: %s" % engine.frei0r_dir())
        missing = [p.name for p in P.PARAMS if p.name not in plugin.by_name]
        if missing:
            print("          warning: the plugin lacks %s" % ", ".join(missing))
    except engine.EngineError as e:
        ok = False
        print("plugin:   ERROR %s" % e)
    if media.have_ffmpeg():
        enc = media.encoders()
        names = [n for n in ("libx264", "prores_ks", "libvpx-vp9", "libopus", "png", "gif") if n in enc]
        print("ffmpeg:   %s (%s)" % (media.ffmpeg(), ", ".join(names)))
    else:
        print("ffmpeg:   not found (videos are disabled)")
    for mod in ("PIL", "PySide6"):
        spec = importlib.util.find_spec(mod)
        print("%-9s %s" % (mod + ":", "ok" if spec else "missing"))
        ok = ok and (spec is not None or mod == "PySide6")
    return 0 if ok else 1


def cmd_build(args) -> int:
    from . import engine
    print(engine.build_plugin(force=args.force, verbose=True))
    print("for ffmpeg/melt: FREI0R_PATH=%s" % engine.frei0r_dir())
    return 0


def cmd_gui(args) -> int:
    import importlib.util
    if importlib.util.find_spec("PySide6") is None:
        print("the editor needs PySide6: use ./run.sh, or pip install PySide6-Essentials\n"
              "(the command line works without it: zinekit --help)", file=sys.stderr)
        return 1
    from .gui.app import main as gui_main
    return gui_main([str(f) for f in getattr(args, "files", []) or []])


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="zinekit", description="Punk Zine print for images, videos and titles.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EPILOG)
    ap.add_argument("--version", action="version", version="zinekit " + __version__)
    sub = ap.add_subparsers(dest="cmd")

    g = sub.add_parser("gui", help="open the editor (the default)")
    g.add_argument("files", nargs="*", help="an image to open, or several files for the batch tab")
    g.set_defaults(func=cmd_gui)

    a = sub.add_parser("apply", help="print images, videos or whole folders",
                       formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EPILOG)
    a.add_argument("inputs", nargs="+", help="image/video files or folders")
    a.add_argument("-o", "--output", help="output folder, or a file name when there is one input "
                                          "(default: next to each input)")
    _add_value_args(a)
    a.add_argument("--image-format", default="png", choices=sorted(IMAGE_FORMATS))
    a.add_argument("--video-format", default="mp4", choices=sorted(VIDEO_FORMATS),
                   help="mp4 (H.264), mov (ProRes 4444 with alpha), webm (VP9 with alpha), png (sequence), gif")
    a.add_argument("--max-height", type=int, default=0, help="downscale to this height (0 keeps the size)")
    a.add_argument("--background", default="black",
                   help="for outputs without alpha (jpg, mp4): black, white, paper or #rrggbb")
    a.add_argument("--suffix", default="_zine", help="added to output names (default _zine)")
    a.add_argument("--overwrite", action="store_true", help="replace existing outputs instead of numbering")
    a.add_argument("-r", "--recursive", action="store_true", help="include subfolders")
    a.add_argument("--quality", type=int, default=92, help="JPEG quality")
    a.add_argument("--crf", type=int, default=18, help="H.264 quality, lower is better (default 18)")
    a.set_defaults(func=cmd_apply)

    t = sub.add_parser("text", help="render a title and print it as ransom-note letters",
                       formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EPILOG)
    t.add_argument("text", help="the text ('\\n' for a new line, '-' reads stdin)")
    t.add_argument("-o", "--output", required=True, help="output image (.png keeps the transparency)")
    t.add_argument("--font", help="font family, 'Family Style', or a font file (see 'zinekit fonts')")
    t.add_argument("--size", type=int, default=180, help="font size in px (default 180)")
    t.add_argument("--color", default="#ffffff")
    t.add_argument("--stroke", type=int, default=0, help="outline width in px")
    t.add_argument("--stroke-color", default="#000000")
    t.add_argument("--align", default="center", choices=["left", "center", "right"])
    t.add_argument("--line-spacing", type=float, default=1.0)
    t.add_argument("--letter-spacing", type=int, default=0, help="extra px between letters")
    t.add_argument("--canvas", default="1920x1080", help="WxH or 'fit' (default 1920x1080)")
    t.add_argument("--margin", type=int, default=80)
    t.add_argument("--background", default="black", help="for .jpg output: black, white, paper or #rrggbb")
    t.add_argument("--plain", action="store_true", help="only the text layer, no Punk Zine")
    _add_value_args(t)
    t.set_defaults(func=cmd_text)

    sub.add_parser("params", help="list the parameters").set_defaults(func=cmd_params)
    pr = sub.add_parser("presets", help="list presets, or show one as JSON")
    pr.add_argument("name", nargs="?")
    pr.set_defaults(func=cmd_presets)
    f = sub.add_parser("fonts", help="list the fonts the text tool can use")
    f.add_argument("filter", nargs="?")
    f.set_defaults(func=cmd_fonts)
    ff = sub.add_parser("ffmpeg", help="print the same look as an ffmpeg frei0r filter")
    _add_value_args(ff)
    ff.set_defaults(func=cmd_ffmpeg)
    sub.add_parser("doctor", help="check the plugin, ffmpeg and PySide6").set_defaults(func=cmd_doctor)
    b = sub.add_parser("build", help="(re)compile the plugin")
    b.add_argument("--force", action="store_true")
    b.set_defaults(func=cmd_build)
    return ap


COMMANDS = ("gui", "apply", "text", "params", "presets", "fonts", "ffmpeg", "doctor", "build")


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in COMMANDS and not argv[0].startswith("-")):
        argv = ["gui"] + argv          # 'zinekit photo.jpg' opens the editor
    if argv[0] != "gui":
        from . import i18n
        i18n.set_language("en")        # the command line speaks English
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except BrokenPipeError:            # 'zinekit presets | head'
        import os
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except FileNotFoundError as e:
        print("zinekit: no such file: %s" % (e.filename or e), file=sys.stderr)
        return 2
    except (ValueError, KeyError) as e:
        msg = e.args[0] if isinstance(e, KeyError) and e.args else e
        print("zinekit: %s" % msg, file=sys.stderr)
        return 2
    except Exception as e:
        from .engine import EngineError
        from .media import MediaError
        if isinstance(e, (EngineError, MediaError)):
            print("zinekit: %s" % e, file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
