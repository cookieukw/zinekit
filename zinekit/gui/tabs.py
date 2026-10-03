"""The three sources of the editor: one image, a typed title, a batch of files."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PIL import Image
from PySide6.QtCore import QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QCompleter, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QProgressBar, QPushButton, QSizePolicy, QSlider, QStyle, QVBoxLayout, QWidget)

from .. import batch as B
from .. import media
from .. import params as P
from ..fonts import FONT_EXTS, default_font, list_fonts
from ..i18n import N_, tr
from ..preview import LatestWorker
from ..textlayer import TextStyle, render_text
from .util import Bridge, NoWheelComboBox, NoWheelDoubleSpinBox, NoWheelSpinBox
from .widgets import ColorButton

ExportJob = Callable[[], Image.Image]
VIDEO_GLOBS = " ".join("*" + e for e in sorted(media.VIDEO_EXTS))


def image_filter() -> str:
    return "%s (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.tga *.jfif);;%s (*)" % (tr("Images"), tr("All files"))


class SourceTab(QWidget):
    """What the preview shows.  source() is the picture to preview, export_job() makes the full-size one."""
    sourceChanged = Signal()
    saveRequested = Signal()
    copyRequested = Signal()
    message = Signal(str)

    def source(self) -> Optional[Image.Image]:
        return None

    def source_key(self) -> object:
        """Changes whenever source() changes (the preview cache key)."""
        return None

    def export_job(self) -> Optional[ExportJob]:
        return None

    def default_name(self) -> str:
        return "zine"

    def default_dir(self) -> str:
        return str(Path.home())

    def save_label(self) -> str:
        return tr("Save result…")


def _button(text: str, slot: Callable[[], None]) -> QPushButton:
    b = QPushButton(text)
    b.clicked.connect(lambda: slot())
    return b


# ------------------------------------------------------------------ image
class ImageTab(SourceTab):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._image: Optional[Image.Image] = None
        self._path: Optional[Path] = None
        self._key = 0
        self.last_dir = str(Path.home())
        self.info = QLabel(tr("No image"))
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(_button(tr("Open image…"), self.open_dialog))
        row.addWidget(_button(tr("Paste image"), self.paste))
        lay.addLayout(row)
        lay.addWidget(self.info)
        row2 = QHBoxLayout()
        row2.addWidget(_button(tr("Save result…"), self.saveRequested.emit))
        row2.addWidget(_button(tr("Copy result"), self.copyRequested.emit))
        lay.addLayout(row2)
        hint = QLabel(tr("Hold Space to see the original."))
        hint.setWordWrap(True)
        hint.setEnabled(False)
        lay.addWidget(hint)
        lay.addStretch(1)

    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Open image…"), self.last_dir, image_filter())
        if path:
            self.open_path(path)

    def open_path(self, path: str) -> bool:
        try:
            img = media.load_image(path)
        except Exception as e:
            self.message.emit(tr("Could not open %s") % path + ": %s" % e)
            return False
        self._path = Path(path)
        self.last_dir = str(self._path.parent)
        self.set_image(img, self._path.name)
        return True

    def paste(self) -> None:
        from PySide6.QtGui import QGuiApplication
        from .util import qimage_to_pil
        cb = QGuiApplication.clipboard()
        md = cb.mimeData()
        if md is not None and md.hasUrls():
            for url in md.urls():
                if url.isLocalFile() and media.kind(url.toLocalFile()) == "image":
                    self.open_path(url.toLocalFile())
                    return
        q = cb.image()
        if q.isNull():
            self.message.emit(tr("No image in the clipboard."))
            return
        self._path = None
        self.set_image(qimage_to_pil(q), tr("clipboard"))

    def set_image(self, img: Image.Image, name: str) -> None:
        self._image = img
        self._key += 1
        self.info.setText("%s\n%d × %d" % (name, img.width, img.height))
        self.sourceChanged.emit()

    def source(self) -> Optional[Image.Image]:
        return self._image

    def source_key(self) -> object:
        return ("image", self._key)

    def export_job(self) -> Optional[ExportJob]:
        img = self._image
        return (lambda: img) if img is not None else None

    def default_name(self) -> str:
        return (self._path.stem if self._path else "pasted") + "_zine"

    def default_dir(self) -> str:
        return str(self._path.parent) if self._path else self.last_dir


# ------------------------------------------------------------------ text
CANVAS_LABELS = [("1920x1080", "1920 × 1080"), ("1280x720", "1280 × 720"), ("3840x2160", "3840 × 2160"),
                 ("1080x1080", "1080 × 1080"), ("1080x1350", "1080 × 1350"), ("1080x1920", "1080 × 1920"),
                 ("fit", "")]


class TextTab(SourceTab):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._layer: Optional[Image.Image] = None
        self._key = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(70)
        self._timer.timeout.connect(self._render)

        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText(tr("Type your text"))
        self.edit.setPlainText("PUNK ZINE")
        self.edit.setMaximumHeight(110)
        self.edit.textChanged.connect(self.schedule)

        self.font_box = QComboBox()
        self.font_box.setEditable(True)
        self.font_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.font_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.font_box.setMinimumContentsLength(14)
        self.font_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for f in list_fonts():
            self.font_box.addItem(f.label, f.label)
        comp = self.font_box.completer()
        if comp is not None:
            comp.setFilterMode(Qt.MatchFlag.MatchContains)
            comp.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        d = default_font()
        if d is not None:
            i = self.font_box.findData(d.label)
            if i >= 0:
                self.font_box.setCurrentIndex(i)
        self.font_box.currentIndexChanged.connect(self.schedule)
        if self.font_box.lineEdit() is not None:
            self.font_box.lineEdit().editingFinished.connect(self.schedule)
        browse = QPushButton("…")
        browse.setToolTip(tr("Browse font file…"))
        browse.setMaximumWidth(32)
        browse.clicked.connect(lambda: self.browse_font())

        self.size_box = NoWheelSpinBox()
        self.size_box.setRange(8, 2000)
        self.size_box.setValue(180)
        self.size_box.setSuffix(" px")
        self.size_box.valueChanged.connect(self.schedule)
        self.color = ColorButton("#ffffff", tr("Color"))
        self.color.colorChanged.connect(self.schedule)
        self.stroke = NoWheelSpinBox()
        self.stroke.setRange(0, 200)
        self.stroke.setSuffix(" px")
        self.stroke.valueChanged.connect(self.schedule)
        self.stroke_color = ColorButton("#000000", tr("Outline"))
        self.stroke_color.colorChanged.connect(self.schedule)
        self.align = NoWheelComboBox()
        for key, label in (("left", N_("Left")), ("center", N_("Center")), ("right", N_("Right"))):
            self.align.addItem(tr(label), key)
        self.align.setCurrentIndex(1)
        self.align.currentIndexChanged.connect(self.schedule)
        self.line = NoWheelDoubleSpinBox()
        self.line.setRange(0.5, 3.0)
        self.line.setSingleStep(0.05)
        self.line.setValue(1.0)
        self.line.valueChanged.connect(self.schedule)
        self.letter = NoWheelSpinBox()
        self.letter.setRange(-100, 400)
        self.letter.setSuffix(" px")
        self.letter.valueChanged.connect(self.schedule)
        self.canvas = NoWheelComboBox()
        for key, label in CANVAS_LABELS:
            self.canvas.addItem(label or tr("Fit to text"), key)
        self.canvas.currentIndexChanged.connect(self.schedule)
        self.margin = NoWheelSpinBox()
        self.margin.setRange(0, 2000)
        self.margin.setValue(80)
        self.margin.setSuffix(" px")
        self.margin.valueChanged.connect(self.schedule)

        lay = QVBoxLayout(self)
        lay.addWidget(self.edit)
        form = QFormLayout()
        font_row = QHBoxLayout()
        font_row.addWidget(self.font_box, 1)
        font_row.addWidget(browse)
        form.addRow(tr("Font"), font_row)
        form.addRow(tr("Size"), self.size_box)
        form.addRow(tr("Color"), self.color)
        outline = QHBoxLayout()
        outline.addWidget(self.stroke)
        outline.addWidget(self.stroke_color, 1)
        form.addRow(tr("Outline"), outline)
        form.addRow(tr("Align"), self.align)
        form.addRow(tr("Line spacing"), self.line)
        form.addRow(tr("Letter spacing"), self.letter)
        form.addRow(tr("Canvas"), self.canvas)
        form.addRow(tr("Margin"), self.margin)
        lay.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(_button(tr("Save PNG…"), self.saveRequested.emit))
        row.addWidget(_button(tr("Copy result"), self.copyRequested.emit))
        lay.addLayout(row)
        lay.addStretch(1)
        self._render()

    def browse_font(self) -> None:
        exts = " ".join("*" + e for e in FONT_EXTS)
        path, _ = QFileDialog.getOpenFileName(self, tr("Browse font file…"), str(Path.home()),
                                              "%s (%s)" % (tr("Font files"), exts))
        if path:
            self.set_font_file(path)

    def set_font_file(self, path: str) -> None:
        i = self.font_box.findData(path)
        if i < 0:
            self.font_box.addItem(Path(path).stem, path)
            i = self.font_box.count() - 1
        self.font_box.setCurrentIndex(i)

    def text_style(self) -> TextStyle:
        font = self.font_box.currentData()
        if not font or self.font_box.currentText() != self.font_box.itemText(self.font_box.currentIndex()):
            font = self.font_box.currentText()
        return TextStyle(text=self.edit.toPlainText(), font=str(font or ""), size=self.size_box.value(),
                         color=self.color.color(), stroke=self.stroke.value(), stroke_color=self.stroke_color.color(),
                         align=str(self.align.currentData() or "center"), line_spacing=self.line.value(),
                         letter_spacing=self.letter.value(), canvas=str(self.canvas.currentData() or "1920x1080"),
                         margin=self.margin.value())

    def set_style(self, data: Dict) -> None:
        st = TextStyle.from_dict(data)
        widgets = [self.edit, self.font_box, self.size_box, self.color, self.stroke, self.stroke_color, self.align,
                   self.line, self.letter, self.canvas, self.margin]
        for w in widgets:
            w.blockSignals(True)
        try:
            self.edit.setPlainText(st.text)
            if st.font:
                i = self.font_box.findData(st.font)
                if i < 0 and Path(st.font).suffix.lower() in FONT_EXTS and Path(st.font).exists():
                    self.font_box.addItem(Path(st.font).stem, st.font)
                    i = self.font_box.count() - 1
                if i >= 0:
                    self.font_box.setCurrentIndex(i)
            self.size_box.setValue(st.size)
            self.color.set_color(st.color)
            self.stroke.setValue(st.stroke)
            self.stroke_color.set_color(st.stroke_color)
            self.align.setCurrentIndex(max(0, self.align.findData(st.align)))
            self.line.setValue(st.line_spacing)
            self.letter.setValue(st.letter_spacing)
            i = self.canvas.findData(st.canvas)
            self.canvas.setCurrentIndex(i if i >= 0 else 0)
            self.margin.setValue(st.margin)
        finally:
            for w in widgets:
                w.blockSignals(False)
        self._render()

    def schedule(self, *_args) -> None:
        self._timer.start()

    def _render(self) -> None:
        try:
            self._layer = render_text(self.text_style())
        except Exception as e:
            self.message.emit(str(e))
            return
        self._key += 1
        self.sourceChanged.emit()

    def source(self) -> Optional[Image.Image]:
        return self._layer

    def source_key(self) -> object:
        return ("text", self._key)

    def export_job(self) -> Optional[ExportJob]:
        layer = self._layer
        return (lambda: layer) if layer is not None else None

    def default_name(self) -> str:
        words = "".join(ch if ch.isalnum() else "_" for ch in self.edit.toPlainText().strip().lower())[:32]
        return (words.strip("_") or "title") + "_zine"

    def save_label(self) -> str:
        return tr("Save PNG…")


# ------------------------------------------------------------------ batch
IMAGE_FORMAT_LABELS = [("png", "PNG"), ("jpg", "JPEG"), ("webp", "WebP"), ("tiff", "TIFF"), ("same", "")]
HEIGHTS = [0, 2160, 1440, 1080, 720, 480]


class BatchTab(SourceTab):
    def __init__(self, get_values: Callable[[], Dict[str, P.Value]], preview_height: Callable[[], int],
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._get_values = get_values
        self._preview_height = preview_height
        self._frame: Optional[Image.Image] = None
        self._frame_height = 0
        self._frame_for: Optional[tuple] = None
        self._thread: Optional[threading.Thread] = None
        self._key = 0
        self._info: Dict[str, media.VideoInfo] = {}
        self._running = False
        self._cancel: Optional[threading.Event] = None
        self._last_out: Optional[Path] = None
        self.last_dir = str(Path.home())
        self.bridge = Bridge()
        self.bridge.frame.connect(self._frame_ready)
        self.bridge.progress.connect(self._progress)
        self.bridge.batch_done.connect(self._done)
        self._grabber = LatestWorker(self._grab, lambda g, r, e, s: self.bridge.frame.emit(g, r, e),
                                     name="zinekit-frames")
        self._seek = QTimer(self)
        self._seek.setSingleShot(True)
        self._seek.setInterval(120)
        self._seek.timeout.connect(self._request_frame)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setMinimumHeight(140)
        self.list.currentItemChanged.connect(lambda *_: self._selection())
        self.list.setToolTip(tr("Drop images and videos here"))
        self.recursive = QCheckBox(tr("Include subfolders"))

        self.frame_slider = QSlider(Qt.Orientation.Horizontal)
        self.frame_slider.setRange(0, 1000)
        self.frame_slider.setEnabled(False)
        self.frame_slider.valueChanged.connect(lambda *_: self._seek.start())
        self.frame_label = QLabel("")

        self.same_folder = QCheckBox(tr("Same folder as each file"))
        self.same_folder.toggled.connect(lambda on: self.out_dir.setEnabled(not on))
        self.out_dir = QLineEdit()
        choose = QPushButton(tr("Choose…"))
        choose.clicked.connect(lambda: self.choose_out_dir())
        self.image_format = NoWheelComboBox()
        for key, label in IMAGE_FORMAT_LABELS:
            self.image_format.addItem(label or tr("Same as source"), key)
        self.video_format = NoWheelComboBox()
        for key, (_ext, _alpha, desc) in B.VIDEO_FORMATS.items():
            self.video_format.addItem(tr(desc), key)
        self.max_height = NoWheelComboBox()
        for h in HEIGHTS:
            self.max_height.addItem(tr("Keep size") if h == 0 else "%dp" % h, h)
        self.background = NoWheelComboBox()
        for key, label in (("black", N_("Black")), ("white", N_("White")), ("paper", N_("Paper color"))):
            self.background.addItem(tr(label), key)
        self.background.setToolTip(tr("Background for formats without transparency."))
        self.suffix = QLineEdit("_zine")
        self.overwrite = QCheckBox(tr("Overwrite existing files"))

        self.start_btn = QPushButton(tr("Start"))
        self.start_btn.clicked.connect(lambda: self.start())
        self.cancel_btn = QPushButton(tr("Cancel"))
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(lambda: self.cancel())
        self.open_out = QPushButton(tr("Output folder"))
        self.open_out.setEnabled(False)
        self.open_out.clicked.connect(lambda: self._open_output())
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1000)
        self.log.setMinimumHeight(70)

        lay = QVBoxLayout(self)
        files_row = QHBoxLayout()
        files_row.addWidget(_button(tr("Add files…"), self.add_files_dialog))
        files_row.addWidget(_button(tr("Add folder…"), self.add_folder_dialog))
        lay.addLayout(files_row)
        lay.addWidget(self.list, 1)
        edit_row = QHBoxLayout()
        edit_row.addWidget(self.recursive, 1)
        edit_row.addWidget(_button(tr("Remove"), self.remove_selected))
        edit_row.addWidget(_button(tr("Clear"), self.clear))
        lay.addLayout(edit_row)
        frame_row = QHBoxLayout()
        frame_row.addWidget(QLabel(tr("Frame")))
        frame_row.addWidget(self.frame_slider, 1)
        frame_row.addWidget(self.frame_label)
        lay.addLayout(frame_row)
        form = QFormLayout()
        out_row = QHBoxLayout()
        out_row.addWidget(self.out_dir, 1)
        out_row.addWidget(choose)
        form.addRow(tr("Output folder"), out_row)
        form.addRow("", self.same_folder)
        form.addRow(tr("Image format"), self.image_format)
        form.addRow(tr("Video format"), self.video_format)
        form.addRow(tr("Max height"), self.max_height)
        form.addRow(tr("Background"), self.background)
        form.addRow(tr("Suffix"), self.suffix)
        form.addRow("", self.overwrite)
        lay.addLayout(form)
        run_row = QHBoxLayout()
        run_row.addWidget(self.start_btn, 1)
        run_row.addWidget(self.cancel_btn)
        run_row.addWidget(self.open_out)
        lay.addLayout(run_row)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        lay.addWidget(self.log, 1)
        if not media.have_ffmpeg():
            self.log.appendPlainText(tr("ffmpeg was not found, videos cannot be processed."))

    # ---- files
    def files(self) -> List[Path]:
        return [Path(self.list.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.list.count())]

    def add_paths(self, paths: List[str]) -> int:
        try:
            found = B.expand_inputs(paths, recursive=self.recursive.isChecked())
        except FileNotFoundError as e:
            self.message.emit(tr("Could not open %s") % e)
            return 0
        have = {str(p) for p in self.files()}
        added = 0
        for f in found:
            if str(f) in have:
                continue
            icon = QStyle.StandardPixmap.SP_MediaPlay if media.kind(f) == "video" else QStyle.StandardPixmap.SP_FileIcon
            item = QListWidgetItem(self.style().standardIcon(icon), f.name)
            item.setData(Qt.ItemDataRole.UserRole, str(f))
            item.setToolTip(str(f))
            self.list.addItem(item)
            have.add(str(f))
            added += 1
        if found:
            self.last_dir = str(found[-1].parent)
            if not self.out_dir.text().strip():
                self.out_dir.setText(str(found[0].parent / "zine"))
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        return added

    def add_files_dialog(self) -> None:
        flt = "%s (%s %s);;%s (*)" % (tr("Images") + " / " + tr("Videos"),
                                      " ".join("*" + e for e in sorted(media.IMAGE_EXTS)), VIDEO_GLOBS,
                                      tr("All files"))
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Add files…"), self.last_dir, flt)
        if paths:
            self.add_paths(paths)

    def add_folder_dialog(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr("Add folder…"), self.last_dir)
        if path:
            self.add_paths([path])

    def remove_selected(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        if self.list.count() == 0:
            self._set_frame(None)

    def clear(self) -> None:
        self.list.clear()
        self._set_frame(None)

    def choose_out_dir(self) -> None:
        start = self.out_dir.text().strip() or self.last_dir
        path = QFileDialog.getExistingDirectory(self, tr("Output folder"), start)
        if path:
            self.out_dir.setText(path)
            self.same_folder.setChecked(False)

    # ---- preview of the selected file
    def current_path(self) -> Optional[Path]:
        item = self.list.currentItem()
        return Path(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def _selection(self) -> None:
        self._frame_for = None        # until the new frame arrives, nothing to save
        path = self.current_path()
        if path is None:
            self._set_frame(None)
            return
        is_video = media.kind(path) == "video"
        self.frame_slider.setEnabled(is_video)
        if not is_video:
            self.frame_label.setText("")
        self._request_frame()

    def _request_frame(self) -> None:
        path = self.current_path()
        if path is None:
            return
        frac = self.frame_slider.value() / 1000.0 if media.kind(path) == "video" else 0.0
        self._grabber.submit((path, frac, self._preview_height()))

    def _grab(self, job):
        path, frac, height = job
        if media.kind(path) == "video":
            info = self._info.get(str(path))
            if info is None:
                info = media.probe(path)
                self._info[str(path)] = info
            t = frac * info.duration if info.duration else 0.0
            return path, frac, t, media.grab_frame(path, t, max_height=height, info=info), height
        from ..preview import downscale
        return path, 0.0, 0.0, downscale(media.load_image(path), height), height

    def _frame_ready(self, gen: int, result, error) -> None:
        if gen != self._grabber.generation:
            return     # a newer request is on its way
        if error is not None:
            self.message.emit(str(error))
            self._set_frame(None)
            return
        path, frac, t, img, height = result
        if path != self.current_path():
            return
        if media.kind(path) == "video":
            self.frame_label.setText("%d:%05.2f" % (int(t // 60), t % 60))
        self._frame_for = (path, frac, t)
        self._set_frame(img, height)

    def _set_frame(self, img: Optional[Image.Image], height: int = 0) -> None:
        self._frame = img
        self._frame_height = height
        if img is None:
            self._frame_for = None
        self._key += 1
        self.sourceChanged.emit()

    def needs_regrab(self) -> bool:
        """The frame on hand was grabbed for another preview size."""
        return self._frame is not None and self._frame_height != self._preview_height()

    def refresh_frame(self) -> None:
        """Grab again (the preview size changed)."""
        self._request_frame()

    def source(self) -> Optional[Image.Image]:
        return self._frame

    def source_key(self) -> object:
        return ("batch", self._key)

    def export_job(self) -> Optional[ExportJob]:
        if self._frame_for is None:
            return None
        path, _frac, t = self._frame_for
        if media.kind(path) == "video":
            return lambda: media.grab_frame(path, t)
        return lambda: media.load_image(path)

    def default_name(self) -> str:
        p = self.current_path()
        return (p.stem if p else "frame") + "_zine"

    def default_dir(self) -> str:
        p = self.current_path()
        return str(p.parent) if p else self.last_dir

    def save_label(self) -> str:
        return tr("Save result…")

    # ---- options
    def options(self) -> B.BatchOptions:
        out = None if self.same_folder.isChecked() else (self.out_dir.text().strip() or None)
        return B.BatchOptions(out_dir=Path(out).expanduser() if out else None,
                              suffix=self.suffix.text(), image_format=str(self.image_format.currentData()),
                              video_format=str(self.video_format.currentData()),
                              max_height=int(self.max_height.currentData() or 0),
                              background=str(self.background.currentData()), overwrite=self.overwrite.isChecked())

    def background_name(self) -> str:
        return str(self.background.currentData() or "black")

    def get_state(self) -> Dict:
        return {"out_dir": self.out_dir.text(), "same_folder": self.same_folder.isChecked(),
                "image_format": self.image_format.currentData(), "video_format": self.video_format.currentData(),
                "max_height": self.max_height.currentData(), "background": self.background.currentData(),
                "suffix": self.suffix.text(), "overwrite": self.overwrite.isChecked(),
                "recursive": self.recursive.isChecked(), "last_dir": self.last_dir}

    def set_state(self, st: Dict) -> None:
        def pick(combo: QComboBox, value) -> None:
            i = combo.findData(value)
            if i >= 0:
                combo.setCurrentIndex(i)
        self.out_dir.setText(str(st.get("out_dir", "")))
        self.same_folder.setChecked(bool(st.get("same_folder", False)))
        pick(self.image_format, st.get("image_format", "png"))
        pick(self.video_format, st.get("video_format", "mp4"))
        pick(self.max_height, int(st.get("max_height", 0) or 0))
        pick(self.background, st.get("background", "black"))
        self.suffix.setText(str(st.get("suffix", "_zine")))
        self.overwrite.setChecked(bool(st.get("overwrite", False)))
        self.recursive.setChecked(bool(st.get("recursive", False)))
        self.last_dir = str(st.get("last_dir") or self.last_dir)

    # ---- running
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        files = self.files()
        if not files:
            self.message.emit(tr("Add some files first."))
            return
        if not self.same_folder.isChecked() and not self.out_dir.text().strip():
            self.message.emit(tr("Choose an output folder first."))
            return
        try:
            opts = self.options()
        except ValueError as e:
            self.message.emit(str(e))
            return
        values = self._get_values()
        self._running = True
        self._cancel = threading.Event()
        self._last_out = opts.out_dir
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.open_out.setEnabled(False)
        self.bar.setValue(0)
        self.log.appendPlainText(tr("— %d file(s) —") % len(files))
        cancel = self._cancel

        def run() -> None:
            try:
                report = B.run_batch(files, values, opts, progress=self.bridge.progress.emit, cancel=cancel)
                self.bridge.batch_done.emit(report)
            except Exception as e:
                self.bridge.batch_done.emit(e)

        self._thread = threading.Thread(target=run, name="zinekit-batch", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        if self._cancel is not None:
            self._cancel.set()

    def _progress(self, i: int, n: int, frac: float, msg: str) -> None:
        total = (i + max(0.0, min(1.0, frac))) / max(1, n)
        self.bar.setValue(int(total * 1000))
        self.status.setText("[%d/%d] %s" % (i + 1, n, msg))
        if frac >= 1.0:
            self.log.appendPlainText(msg)

    def _done(self, result) -> None:
        self._running = False
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if isinstance(result, Exception):
            self.status.setText(str(result))
            self.log.appendPlainText(tr("Error") + ": %s" % result)
            return
        for r in result.results:
            if not r.ok and r.error != "cancelled":
                self.log.appendPlainText("✗ %s\n   %s" % (r.source.name, r.error.replace("\n", "\n   ")))
        msg = tr("Cancelled") if result.cancelled else tr("Done: %d ok, %d failed") % (result.ok, result.failed)
        if not result.cancelled:
            self.bar.setValue(1000)
        self.status.setText(msg)
        self.log.appendPlainText(msg)
        outs = [r.output for r in result.results if r.ok and r.output]
        if outs:
            if self._last_out is None:          # "same folder as each file": open the first one
                self._last_out = outs[0].parent
            self.open_out.setEnabled(True)

    def _open_output(self) -> None:
        if self._last_out is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_out)))

    def shutdown(self) -> None:
        """Cancel a running batch and wait for it, so ffmpeg is stopped and partial files are removed."""
        self.cancel()
        if self._thread is not None:
            self._thread.join(timeout=30.0)
        self._grabber.close(timeout=5.0)
