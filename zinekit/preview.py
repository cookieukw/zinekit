"""Live preview plumbing that does not depend on Qt.

``LatestWorker`` runs jobs on one background thread and always skips to the
newest one: dragging a slider submits dozens of jobs, but only the last one
still waiting is rendered, so the preview never lags behind.

``PreviewRenderer`` keeps a plugin instance per frame size (the element mode
caches its cut-out between renders of the same picture).
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Mapping, Optional, Tuple

from . import params as P
from .engine import Plugin, Renderer, get_plugin

Done = Callable[[int, Any, Optional[BaseException], float], None]   # (generation, result, error, seconds)


def downscale(img, max_height: int):
    """The image limited to max_height (0 keeps it); never upscales."""
    from PIL import Image
    if not max_height or img.height <= max_height:
        return img
    w = max(1, int(round(img.width * max_height / img.height)))
    return img.resize((w, max_height), Image.LANCZOS, reducing_gap=3.0)


class PreviewRenderer:
    def __init__(self, plugin: Optional[Plugin] = None, keep: int = 3):
        self._plugin = plugin
        self._keep = keep
        self._renderers: "OrderedDict[Tuple[int, int], Renderer]" = OrderedDict()

    @property
    def plugin(self) -> Plugin:
        if self._plugin is None:
            self._plugin = get_plugin()
        return self._plugin

    def render(self, image, values: Mapping[str, P.Value]):
        size = image.size
        r = self._renderers.pop(size, None)
        if r is None:
            r = Renderer(self.plugin, *size)
        self._renderers[size] = r
        while len(self._renderers) > self._keep:
            _, old = self._renderers.popitem(last=False)
            old.close()
        return r.process_image(image, P.complete(values))

    def close(self) -> None:
        for r in self._renderers.values():
            r.close()
        self._renderers.clear()


class LatestWorker:
    def __init__(self, fn: Callable[[Any], Any], on_done: Done, name: str = "zinekit-preview"):
        self._fn = fn
        self._on_done = on_done
        self._cond = threading.Condition()
        self._job: Any = None
        self._has_job = False
        self._gen = 0
        self._running = False
        self._closed = False
        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)
        self._thread.start()

    @property
    def busy(self) -> bool:
        with self._cond:
            return self._has_job or self._running

    @property
    def generation(self) -> int:
        return self._gen

    def submit(self, job: Any) -> int:
        with self._cond:
            self._gen += 1
            self._job = job
            self._has_job = True
            self._cond.notify()
            return self._gen

    def clear(self) -> None:
        """Drop the job still waiting (a running one finishes)."""
        with self._cond:
            self._job = None
            self._has_job = False

    def close(self, timeout: float = 5.0) -> bool:
        """Stop the thread; True when it has ended (a job still running past the timeout keeps it alive)."""
        with self._cond:
            self._closed = True
            self._has_job = False
            self._cond.notify()
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _loop(self) -> None:
        while True:
            with self._cond:
                while not self._has_job and not self._closed:
                    self._cond.wait()
                if self._closed:
                    return
                job, gen = self._job, self._gen
                self._job = None
                self._has_job = False
                self._running = True
            t0 = time.perf_counter()
            result, error = None, None
            try:
                result = self._fn(job)
            except BaseException as e:   # reported to the caller, never kills the thread
                error = e
            with self._cond:
                self._running = False
            try:
                self._on_done(gen, result, error, time.perf_counter() - t0)
            except Exception:
                pass
