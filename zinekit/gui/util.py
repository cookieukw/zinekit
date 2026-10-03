"""Small Qt helpers shared by the editor widgets."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, QObject, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QSlider, QSpinBox

ICON_PATH = Path(__file__).resolve().parent / "icon.svg"


def app_icon() -> QIcon:
    return QIcon(str(ICON_PATH)) if ICON_PATH.exists() else QIcon()


def pil_to_qimage(img: Image.Image) -> QImage:
    """A PIL image as a premultiplied QImage that owns its pixels (fast to paint)."""
    rgba = img if img.mode == "RGBA" else img.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    q = QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888)
    out = q.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    del q, data
    return out


def qimage_to_pil(q: QImage) -> Image.Image:
    """A QImage (any format) as an RGBA PIL image."""
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    q.save(buf, "PNG")
    data = bytes(buf.data().data())
    buf.close()
    img = Image.open(io.BytesIO(data))
    img.load()
    return img.convert("RGBA")


def checker_pixmap(size: int = 12, a: str = "#cfcfcf", b: str = "#f4f4f4") -> QPixmap:
    pm = QPixmap(size * 2, size * 2)
    pm.fill(QColor(b))
    p = QPainter(pm)
    p.fillRect(0, 0, size, size, QColor(a))
    p.fillRect(size, size, size, size, QColor(a))
    p.end()
    return pm


def swatch_icon(color: str, w: int = 28, h: int = 16) -> QIcon:
    pm = QPixmap(w, h)
    pm.fill(QColor(color))
    p = QPainter(pm)
    p.setPen(QColor("#555555"))
    p.drawRect(0, 0, w - 1, h - 1)
    p.end()
    return QIcon(pm)


class Bridge(QObject):
    """Signals emitted from worker threads, delivered on the GUI thread."""
    rendered = Signal(int, object, object, float)     # generation, PIL image, error, seconds
    exported = Signal(object, object, object)         # target (path or 'clipboard'), PIL image, error
    frame = Signal(int, object, object)               # generation, (path, PIL image), error
    progress = Signal(int, int, float, str)           # index, count, fraction, message
    batch_done = Signal(object)                       # BatchReport or Exception


# Sliders, spin boxes and combo boxes inside the scrolling parameter panel only take
# the mouse wheel when they have focus, so scrolling the panel never changes a value.
class NoWheelSlider(QSlider):
    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoWheelSpinBox(QSpinBox):
    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoWheelComboBox(QComboBox):
    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()
