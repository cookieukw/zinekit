"""Run the Punk Zine filter from Python.

The filter is the frei0r plugin from ``native/punkzine.c`` (the same source as
the Kdenlive effect).  It is compiled once into the user cache and loaded with
ctypes, the way a frei0r host such as MLT loads it, so a picture made here is
identical to what Kdenlive renders with the same parameter values.

    from PIL import Image
    from zinekit.engine import apply

    out = apply(Image.open("title.png"), {"mode": 1 / 3, "roughness": 0.8})

``Renderer`` keeps one plugin instance for a frame size, which is what the
preview and the video batch use: the element mode caches its cut-out between
frames of the same size.
"""
from __future__ import annotations

import ctypes as C
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Union

from .i18n import tr

NATIVE_DIR = Path(__file__).resolve().parent / "native"
SOURCE = NATIVE_DIR / "punkzine.c"
HEADER = NATIVE_DIR / "frei0r.h"
CFLAGS = ["-O3", "-fPIC", "-shared"]
LIBS = ["-lm", "-lpthread"]

PARAM_BOOL, PARAM_DOUBLE, PARAM_COLOR = 0, 1, 2

# where install scripts and distributions put the frei0r module; used only when
# the bundled source cannot be compiled (no C compiler)
INSTALLED_PLUGINS = [
    "~/.var/app/org.kde.kdenlive/data/frei0r-1/punkzine.so",
    "~/.frei0r-1/lib/punkzine.so",
    "/usr/local/lib/frei0r-1/punkzine.so",
    "/usr/lib/frei0r-1/punkzine.so",
    "/usr/lib/x86_64-linux-gnu/frei0r-1/punkzine.so",
    "/usr/lib/aarch64-linux-gnu/frei0r-1/punkzine.so",
    "/usr/lib64/frei0r-1/punkzine.so",
]

Value = Union[float, int, bool, str]


class EngineError(RuntimeError):
    """The plugin could not be built or loaded."""


def cache_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "zinekit"


def _lib_suffix() -> str:
    if sys.platform == "darwin":
        return ".dylib"
    if os.name == "nt":
        return ".dll"
    return ".so"


def find_compiler() -> Optional[str]:
    env = os.environ.get("CC")
    if env:
        return env
    for name in ("cc", "gcc", "clang"):
        path = shutil.which(name)
        if path:
            return path
    return None


def build_plugin(force: bool = False, verbose: bool = False) -> Path:
    """Compile native/punkzine.c into the cache (once per source version)."""
    cc = find_compiler()
    if not cc:
        raise EngineError(tr("no C compiler found (install gcc or clang, or set ZINEKIT_PLUGIN)"))
    digest = hashlib.sha256()
    for part in (SOURCE.read_bytes(), HEADER.read_bytes(), " ".join(CFLAGS + LIBS).encode(),
                 platform.machine().encode()):
        digest.update(part)
    out = cache_dir() / ("punkzine-%s%s" % (digest.hexdigest()[:16], _lib_suffix()))
    if out.exists() and not force:
        _link_for_hosts(out)
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".punkzine-", suffix=_lib_suffix(), dir=out.parent)
    os.close(fd)
    cmd = [cc, *CFLAGS, "-I", str(NATIVE_DIR), "-o", tmp, str(SOURCE), *LIBS]
    if verbose:
        print(" ".join(cmd), file=sys.stderr)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise EngineError(tr("compiling the plugin failed:") + "\n" + (res.stderr or res.stdout).strip())
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    _link_for_hosts(out)
    return out


def frei0r_dir() -> Path:
    """A folder with the current build as punkzine.so, for FREI0R_PATH (ffmpeg, melt)."""
    return cache_dir() / "frei0r-1"


def _link_for_hosts(lib: Path) -> None:
    link = frei0r_dir() / ("punkzine" + _lib_suffix())
    try:
        if link.is_symlink() and os.readlink(link) == os.path.join("..", lib.name):
            return
        link.parent.mkdir(parents=True, exist_ok=True)
        tmp = link.with_name(".punkzine-link")
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        try:
            os.symlink(os.path.join("..", lib.name), tmp)
        except OSError:                       # no symlinks (Windows): copy
            shutil.copyfile(lib, tmp)
        os.replace(tmp, link)
    except OSError:
        pass


def locate_plugin() -> Path:
    """ZINEKIT_PLUGIN, else the bundled source compiled, else an installed module."""
    env = os.environ.get("ZINEKIT_PLUGIN")
    if env:
        path = Path(env).expanduser()
        if not path.exists():
            raise EngineError("ZINEKIT_PLUGIN points to a missing file: %s" % path)
        return path
    try:
        return build_plugin()
    except EngineError as err:
        for cand in INSTALLED_PLUGINS:
            path = Path(cand).expanduser()
            if path.exists():
                return path
        raise EngineError(str(err) + "\n" + tr("and no installed punkzine frei0r module was found")) from None


class _Info(C.Structure):
    _fields_ = [("name", C.c_char_p), ("author", C.c_char_p), ("plugin_type", C.c_int), ("color_model", C.c_int),
                ("frei0r_version", C.c_int), ("major_version", C.c_int), ("minor_version", C.c_int),
                ("num_params", C.c_int), ("explanation", C.c_char_p)]


class _ParamInfo(C.Structure):
    _fields_ = [("name", C.c_char_p), ("type", C.c_int), ("explanation", C.c_char_p)]


class _Color(C.Structure):
    _fields_ = [("r", C.c_float), ("g", C.c_float), ("b", C.c_float)]


@dataclass(frozen=True)
class PluginParam:
    index: int
    name: str
    type: int
    explanation: str


def parse_color(value: str) -> tuple:
    """'#rrggbb' (or 'rrggbb', '#rgb') -> (r, g, b) floats in 0..1."""
    s = str(value).strip().lstrip("#")
    if s.lower().startswith("0x"):
        s = s[2:]
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError("not a #rrggbb color: %r" % (value,))
    v = int(s, 16)
    return ((v >> 16) & 255) / 255.0, ((v >> 8) & 255) / 255.0, (v & 255) / 255.0


class Plugin:
    """The loaded frei0r module."""

    def __init__(self, path: Optional[Union[str, Path]] = None):
        self.path = Path(path) if path else locate_plugin()
        try:
            lib = C.CDLL(str(self.path))
        except OSError as err:
            raise EngineError("cannot load %s: %s" % (self.path, err)) from None
        lib.f0r_init.restype = C.c_int
        lib.f0r_get_plugin_info.argtypes = [C.POINTER(_Info)]
        lib.f0r_get_param_info.argtypes = [C.POINTER(_ParamInfo), C.c_int]
        lib.f0r_construct.restype = C.c_void_p
        lib.f0r_construct.argtypes = [C.c_uint, C.c_uint]
        lib.f0r_destruct.argtypes = [C.c_void_p]
        lib.f0r_set_param_value.argtypes = [C.c_void_p, C.c_void_p, C.c_int]
        lib.f0r_get_param_value.argtypes = [C.c_void_p, C.c_void_p, C.c_int]
        lib.f0r_update.argtypes = [C.c_void_p, C.c_double, C.c_void_p, C.c_void_p]
        lib.f0r_update.restype = None
        lib.f0r_init()
        info = _Info()
        lib.f0r_get_plugin_info(C.byref(info))
        if info.color_model != 1:
            raise EngineError("%s is not an RGBA8888 frei0r filter" % self.path)
        self.lib = lib
        self.name = (info.name or b"").decode()
        self.version = "%d.%d" % (info.major_version, info.minor_version)
        self.params = []
        for i in range(info.num_params):
            p = _ParamInfo()
            lib.f0r_get_param_info(C.byref(p), i)
            self.params.append(PluginParam(i, (p.name or b"").decode(), p.type, (p.explanation or b"").decode()))
        self.by_name = {p.name: p for p in self.params}

    def renderer(self, width: int, height: int) -> "Renderer":
        return Renderer(self, width, height)


class Renderer:
    """One plugin instance for frames of one size.  Not thread-safe: use one per thread."""

    def __init__(self, plugin: Plugin, width: int, height: int):
        if width <= 0 or height <= 0:
            raise ValueError("bad frame size %dx%d" % (width, height))
        self.plugin = plugin
        self.width, self.height = int(width), int(height)
        self.nbytes = self.width * self.height * 4
        self._inst = plugin.lib.f0r_construct(self.width, self.height)
        if not self._inst:
            raise EngineError("the plugin refused a %dx%d frame" % (self.width, self.height))
        self._values: dict = {}

    def set_params(self, values: Mapping[str, Value]) -> None:
        """Values in plugin units: doubles 0..1, bools, colors as '#rrggbb'.  Unknown names are ignored."""
        lib = self.plugin.lib
        for name, value in values.items():
            p = self.plugin.by_name.get(name)
            if p is None or self._values.get(name) == value:
                continue
            if p.type == PARAM_COLOR:
                r, g, b = parse_color(str(value))
                col = _Color(r, g, b)
                lib.f0r_set_param_value(self._inst, C.byref(col), p.index)
            else:
                d = C.c_double(float(value))
                lib.f0r_set_param_value(self._inst, C.byref(d), p.index)
            self._values[name] = value

    def process(self, frame: Union[bytes, bytearray, memoryview], out: Optional[bytearray] = None,
                time: float = 0.0) -> bytearray:
        """RGBA8888 bytes in, RGBA8888 bytes out (straight alpha, row-major)."""
        if self._inst is None:
            raise EngineError("renderer is closed")
        if len(frame) != self.nbytes:
            raise ValueError("frame has %d bytes, expected %d" % (len(frame), self.nbytes))
        if out is None:
            out = bytearray(self.nbytes)
        src = frame if isinstance(frame, bytes) else (C.c_char * self.nbytes).from_buffer(frame)
        dst = (C.c_char * self.nbytes).from_buffer(out)
        self.plugin.lib.f0r_update(self._inst, float(time), src, dst)
        del dst, src
        return out

    def process_image(self, image, params: Optional[Mapping[str, Value]] = None):
        """PIL image in (any mode, this size), RGBA PIL image out."""
        from PIL import Image
        if params:
            self.set_params(params)
        rgba = image if image.mode == "RGBA" else image.convert("RGBA")
        if rgba.size != (self.width, self.height):
            raise ValueError("image is %dx%d, renderer is %dx%d" % (*rgba.size, self.width, self.height))
        out = self.process(rgba.tobytes())
        return Image.frombuffer("RGBA", rgba.size, bytes(out), "raw", "RGBA", 0, 1)

    def close(self) -> None:
        if self._inst:
            self.plugin.lib.f0r_destruct(self._inst)
            self._inst = None

    def __enter__(self) -> "Renderer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


_plugin: Optional[Plugin] = None
_plugin_lock = threading.Lock()


def get_plugin() -> Plugin:
    """The process-wide plugin (built and loaded on first use)."""
    global _plugin
    with _plugin_lock:
        if _plugin is None:
            _plugin = Plugin()
        return _plugin


def apply(image, params: Optional[Mapping[str, Value]] = None, plugin: Optional[Plugin] = None):
    """Print one PIL image with the given parameters (plugin units).  Returns an RGBA image."""
    plugin = plugin or get_plugin()
    with Renderer(plugin, image.width, image.height) as r:
        return r.process_image(image, params or {})
