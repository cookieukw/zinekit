"""The preview canvas: before / after / split view over a chosen background."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..i18n import tr
from .util import checker_pixmap

BACKGROUNDS = {"checker": None, "black": "#000000", "gray": "#5a5a5a", "white": "#ffffff"}


class PreviewView(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._before: Optional[QImage] = None
        self._after: Optional[QImage] = None
        self._mode = "after"           # after | before | split
        self._background = "checker"
        self._split = 0.5
        self._peek = False
        self._busy = False
        self._message = tr("Drop an image here or use Open image…")
        self._checker = QBrush(checker_pixmap())
        self.setMinimumSize(320, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    # state
    def set_before(self, img: Optional[QImage], keep_after: bool = False) -> None:
        self._before = img
        if not keep_after or (img is not None and self._after is not None and self._after.size() != img.size()):
            self._after = None
        self.update()

    def set_after(self, img: Optional[QImage]) -> None:
        self._after = img
        self.update()

    def clear(self, message: str = "") -> None:
        self._before = self._after = None
        self._message = message or tr("Drop an image here or use Open image…")
        self.update()

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self.update()

    def set_background(self, name: str) -> None:
        self._background = name if name in BACKGROUNDS else "checker"
        self.update()

    def set_peek(self, on: bool) -> None:
        if on != self._peek:
            self._peek = on
            self.update()

    def set_busy(self, on: bool) -> None:
        if on != self._busy:
            self._busy = on
            self.update()

    def has_image(self) -> bool:
        return self._before is not None

    # geometry
    def _target(self, img: QImage) -> QRectF:
        area = QRectF(self.rect()).adjusted(10, 10, -10, -10)
        if img.width() <= 0 or img.height() <= 0 or area.width() <= 0 or area.height() <= 0:
            return QRectF()
        k = min(area.width() / img.width(), area.height() / img.height())
        w, h = img.width() * k, img.height() * k
        return QRectF(area.x() + (area.width() - w) / 2, area.y() + (area.height() - h) / 2, w, h)

    def _shown(self) -> Optional[QImage]:
        return self._after if self._after is not None else self._before

    # painting
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), self.palette().window().color().darker(115))
        base = self._before if self._before is not None else self._after
        if base is None:
            p.setPen(self.palette().text().color())
            p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), self._message)
            p.end()
            return
        target = self._target(base)
        bg = BACKGROUNDS.get(self._background)
        if bg is None:
            p.setBrushOrigin(target.topLeft())
            p.fillRect(target, self._checker)
        else:
            p.fillRect(target, QColor(bg))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        show_before = self._peek or self._mode == "before" or self._after is None
        if self._mode == "split" and not self._peek and self._after is not None and self._before is not None:
            x = target.x() + target.width() * self._split
            p.save()
            p.setClipRect(QRectF(target.x(), target.y(), x - target.x(), target.height()))
            p.drawImage(target, self._before)
            p.restore()
            p.save()
            p.setClipRect(QRectF(x, target.y(), target.right() - x, target.height()))
            p.drawImage(target, self._after)
            p.restore()
            pen = QPen(QColor("#ff4fa8"))
            pen.setWidth(2)
            p.setPen(pen)
            p.drawLine(QPointF(x, target.top()), QPointF(x, target.bottom()))
            self._tag(p, QPointF(target.x() + 8, target.y() + 8), tr("Before"))
            self._tag(p, QPointF(target.right() - 8, target.y() + 8), tr("After"), right=True)
        else:
            img = self._before if show_before and self._before is not None else self._after
            if img is not None:
                p.drawImage(target, img)
            if show_before and self._after is not None:
                self._tag(p, QPointF(target.x() + 8, target.y() + 8), tr("Before"))
        if self._busy:
            self._tag(p, QPointF(target.right() - 8, target.bottom() - 30), tr("Rendering…"), right=True)
        p.end()

    def _tag(self, p: QPainter, at: QPointF, text: str, right: bool = False) -> None:
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(text) + 14, fm.height() + 6
        x = at.x() - w if right else at.x()
        box = QRectF(x, at.y(), w, h)
        p.fillRect(box, QColor(21, 19, 17, 210))
        p.setPen(QColor("#f7f3e8"))
        p.drawText(box, int(Qt.AlignmentFlag.AlignCenter), text)

    # split handle
    def _move_split(self, pos: QPointF) -> None:
        base = self._before if self._before is not None else self._after
        if base is None or self._mode != "split":
            return
        t = self._target(base)
        if t.width() > 0:
            self._split = max(0.0, min(1.0, (pos.x() - t.x()) / t.width()))
            self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._move_split(event.position())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._move_split(event.position())
        super().mouseMoveEvent(event)
