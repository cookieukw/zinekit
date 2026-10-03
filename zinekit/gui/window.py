"""The editor window: sources on the left, the live preview in the middle, the parameters on the right."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QByteArray, QEvent, QObject, QSettings, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QApplication, QComboBox,
                               QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from .. import __version__, media
from .. import i18n
from ..batch import BatchOptions, save_image
from ..engine import Plugin, apply
from ..fonts import FONT_EXTS
from ..i18n import N_, tr
from ..preview import LatestWorker, PreviewRenderer, downscale
from .preview_view import PreviewView
from .tabs import BatchTab, ImageTab, SourceTab, TextTab
from .util import Bridge, app_icon, pil_to_qimage
from .widgets import ParamPanel, PresetBar

PREVIEW_SIZES = [(540, "540p"), (720, "720p"), (1080, "1080p"), (0, N_("Full"))]
SAVE_FILTERS = "PNG (*.png);;JPEG (*.jpg *.jpeg);;WebP (*.webp);;TIFF (*.tif *.tiff)"


def settings() -> QSettings:
    return QSettings("zinekit", "zinekit")


class MainWindow(QMainWindow):
    languageChanged = Signal(str)       # app.py rebuilds the window in the new language

    def __init__(self, plugin: Plugin, files: Optional[List[str]] = None):
        super().__init__()
        self.plugin = plugin
        self.setWindowTitle("zinekit — Punk Zine")
        self.setWindowIcon(app_icon())
        self.setAcceptDrops(True)
        self._src_key: object = None
        self._src_preview = None
        self._shown_gen = 0
        self._closing = False
        self._exports: List[threading.Thread] = []

        self.bridge = Bridge()
        self.bridge.rendered.connect(self._rendered)
        self.bridge.exported.connect(self._exported)
        self.renderer = PreviewRenderer(plugin)
        self.worker = LatestWorker(self._render_job, lambda g, r, e, s: self.bridge.rendered.emit(g, r, e, s))

        # right: presets + parameters
        self.panel = ParamPanel()
        self.panel.changed.connect(self._params_changed)
        self.presets = PresetBar(self.panel.values)
        self.presets.chosen.connect(lambda values: self.panel.set_values(values))
        self.presets.message.connect(self.show_message)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.panel)
        scroll.setMinimumWidth(340)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.presets)
        rl.addWidget(scroll, 1)

        # middle: preview
        self.preview = PreviewView()
        self.compare = QComboBox()
        for key, label in (("after", N_("After")), ("before", N_("Before")), ("split", N_("Split"))):
            self.compare.addItem(tr(label), key)
        self.compare.currentIndexChanged.connect(lambda *_: self.preview.set_mode(str(self.compare.currentData())))
        hold = QPushButton(tr("Before"))
        hold.setToolTip(tr("Hold Space to see the original."))
        hold.pressed.connect(lambda: self.preview.set_peek(True))
        hold.released.connect(lambda: self.preview.set_peek(False))
        self.bg = QComboBox()
        for key, label in (("checker", N_("Checkerboard")), ("black", N_("Black")), ("gray", N_("Gray")),
                           ("white", N_("White"))):
            self.bg.addItem(tr(label), key)
        self.bg.currentIndexChanged.connect(lambda *_: self.preview.set_background(str(self.bg.currentData())))
        self.size_combo = QComboBox()
        for h, label in PREVIEW_SIZES:
            self.size_combo.addItem(tr(label), h)
        self.size_combo.setCurrentIndex(1)
        self.size_combo.setToolTip(tr("Preview size"))
        self.size_combo.currentIndexChanged.connect(lambda *_: self._preview_size_changed())
        self.info = QLabel("")
        self.info.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar = QHBoxLayout()
        bar.addWidget(QLabel(tr("Preview")))
        bar.addWidget(self.compare)
        bar.addWidget(hold)
        bar.addWidget(self.bg)
        bar.addWidget(self.size_combo)
        bar.addWidget(self.info, 1)
        middle = QWidget()
        ml = QVBoxLayout(middle)
        ml.setContentsMargins(0, 6, 0, 0)
        ml.addLayout(bar)
        ml.addWidget(self.preview, 1)

        # left: sources
        self.image_tab = ImageTab()
        self.text_tab = TextTab()
        self.batch_tab = BatchTab(self.panel.values, self.preview_height)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.image_tab, tr("Image"))
        self.tabs.addTab(self.text_tab, tr("Text"))
        self.tabs.addTab(self.batch_tab, tr("Batch"))
        self.tabs.setMinimumWidth(300)
        for tab in self._all_tabs():
            tab.sourceChanged.connect(lambda t=tab: self._source_changed(t))
            tab.saveRequested.connect(self.save_result)
            tab.copyRequested.connect(self.copy_result)
            tab.message.connect(self.show_message)
        self.tabs.currentChanged.connect(lambda *_: self._tab_changed())

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.tabs)
        self.splitter.addWidget(middle)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([330, 900, 380])
        self.setCentralWidget(self.splitter)
        self._build_menus()
        self.resize(1560, 900)
        self._restore()
        QApplication.instance().installEventFilter(self)
        if files:
            self.open_files(files)
        self._source_changed(self.current_tab())

    # ------------------------------------------------------------ menus
    def _action(self, text: str, slot, shortcut=None) -> QAction:
        act = QAction(text, self)
        if shortcut is not None:
            act.setShortcut(QKeySequence(shortcut))
        act.triggered.connect(lambda *_: slot())
        return act

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu(tr("File"))
        m.addAction(self._action(tr("Open image…"), self._open_image, QKeySequence.StandardKey.Open))
        m.addAction(self._action(tr("Paste image"), self._paste_image, "Ctrl+Shift+V"))
        m.addAction(self._action(tr("Add files…"), self._add_batch_files))
        m.addSeparator()
        m.addAction(self._action(tr("Save result…"), self.save_result, QKeySequence.StandardKey.Save))
        m.addAction(self._action(tr("Copy result"), self.copy_result, "Ctrl+Shift+C"))
        m.addSeparator()
        m.addAction(self._action(tr("Quit"), self.close, QKeySequence.StandardKey.Quit))

        p = mb.addMenu(tr("Presets"))
        p.addAction(self._action(tr("Save preset…"), self.presets.save_preset))
        p.addAction(self._action(tr("Import preset…"), self.presets.import_preset))
        p.addAction(self._action(tr("Export preset…"), self.presets.export_preset))
        p.addAction(self._action(tr("Delete preset"), self.presets.delete_preset))
        p.addSeparator()
        p.addAction(self._action(tr("Reset all"), self.presets.reset_all))
        p.addAction(self._action(tr("Copy ffmpeg filter"), self.presets.copy_ffmpeg))

        v = mb.addMenu(tr("View"))
        for i, name in enumerate((tr("Image"), tr("Text"), tr("Batch"))):
            v.addAction(self._action(name, lambda i=i: self.tabs.setCurrentIndex(i), "Ctrl+%d" % (i + 1)))
        v.addSeparator()
        lang = v.addMenu(tr("Language"))
        group = QActionGroup(self)
        self._language_actions = {}
        for code, label in i18n.available().items():
            act = QAction(label, self)
            self._language_actions[code] = act
            act.setCheckable(True)
            act.setChecked(i18n.language() == code)
            act.triggered.connect(lambda *_, c=code: self._set_language(c))
            group.addAction(act)
            lang.addAction(act)
        # the same choice, always visible at the right end of the menu bar
        self.lang_combo = QComboBox()
        for code, label in i18n.available().items():
            self.lang_combo.addItem(label, code)
        self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(i18n.language())))
        self.lang_combo.setToolTip(tr("Language"))
        self.lang_combo.currentIndexChanged.connect(lambda *_: self._set_language(str(self.lang_combo.currentData())))
        mb.setCornerWidget(self.lang_combo, Qt.Corner.TopRightCorner)

        h = mb.addMenu(tr("Help"))
        h.addAction(self._action(tr("About zinekit"), self._about))

    def _open_image(self) -> None:
        self.tabs.setCurrentWidget(self.image_tab)
        self.image_tab.open_dialog()

    def _paste_image(self) -> None:
        self.tabs.setCurrentWidget(self.image_tab)
        self.image_tab.paste()

    def _add_batch_files(self) -> None:
        self.tabs.setCurrentWidget(self.batch_tab)
        self.batch_tab.add_files_dialog()

    def _set_language(self, code: str) -> None:
        if code == i18n.language():
            return
        if self.batch_tab.is_running():
            QMessageBox.information(self, tr("Language"), tr("Finish or cancel the batch first."))
            act = self._language_actions.get(i18n.language())
            if act is not None:
                act.setChecked(True)
            self.lang_combo.blockSignals(True)
            self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(i18n.language())))
            self.lang_combo.blockSignals(False)
            return
        settings().setValue("language", code)
        i18n.set_language(code)
        self._save()
        self.languageChanged.emit(code)

    def adopt(self, old: "MainWindow") -> None:
        """Take over what another window had open (used when the language changes)."""
        img = old.image_tab.source()
        if img is not None:
            path = old.image_tab._path
            self.image_tab._path = path
            self.image_tab.set_image(img, path.name if path is not None else tr("clipboard"))
        files = old.batch_tab.files()
        if files:
            self.batch_tab.add_paths([str(f) for f in files])
            row = old.batch_tab.list.currentRow()
            if 0 <= row < self.batch_tab.list.count():
                self.batch_tab.list.setCurrentRow(row)
        self.restoreGeometry(old.saveGeometry())
        self.tabs.setCurrentIndex(old.tabs.currentIndex())

    def _about(self) -> None:
        QMessageBox.about(self, tr("About zinekit"),
                          "<b>zinekit %s</b><br>%s<br><br>%s: %s<br>%s %s"
                          % (__version__, tr("Punk Zine print for images, videos and titles."), tr("Plugin"),
                             self.plugin.path, self.plugin.name, self.plugin.version))

    # ------------------------------------------------------------ sources
    def _all_tabs(self) -> List[SourceTab]:
        return [self.image_tab, self.text_tab, self.batch_tab]

    def current_tab(self) -> SourceTab:
        w = self.tabs.currentWidget()
        return w if isinstance(w, SourceTab) else self.image_tab

    def preview_height(self) -> int:
        return int(self.size_combo.currentData() or 0)

    def _preview_size_changed(self) -> None:
        self._src_key = None
        self._tab_changed()

    def _tab_changed(self) -> None:
        tab = self.current_tab()
        if tab is self.batch_tab and self.batch_tab.needs_regrab():
            self.batch_tab.refresh_frame()     # the new frame arrives through sourceChanged
            return
        self._source_changed(tab)

    def _source_changed(self, tab: SourceTab) -> None:
        if self._closing or tab is not self.current_tab():
            return
        img = tab.source()
        if img is None:
            self._src_key = None
            self._src_preview = None
            self.preview.clear()
            self.preview.set_busy(False)
            self.info.setText("")
            return
        key = (tab.source_key(), self.preview_height())
        if key == self._src_key:
            return
        self._src_key = key
        self._src_preview = downscale(img, self.preview_height())
        # while typing a title, keep the last print on screen until the new one is ready
        self.preview.set_before(pil_to_qimage(self._src_preview), keep_after=tab is self.text_tab)
        self._request()

    def _params_changed(self) -> None:
        self.presets.refresh_modified()
        self._request()

    def _request(self) -> None:
        if self._src_preview is None:
            return
        self.worker.submit((self._src_key, self._src_preview, self.panel.values()))
        self.preview.set_busy(True)

    def _render_job(self, job):
        key, img, values = job
        try:
            return key, img.size, self.renderer.render(img, values), None
        except Exception as e:
            return key, img.size, None, e

    def _rendered(self, gen: int, result, error, seconds: float) -> None:
        if self._closing:
            return
        if gen == self.worker.generation:
            self.preview.set_busy(False)
        if gen < self._shown_gen:
            return
        self._shown_gen = gen
        if result is None:
            self.show_message(tr("Error") + ": %s" % error)
            return
        key, size, img, err = result
        if key != self._src_key:
            return              # the print of a picture that is no longer shown
        if err is not None:
            self.show_message(tr("Error") + ": %s" % err)
            return
        self.preview.set_after(pil_to_qimage(img))
        self.info.setText(tr("Rendered in %d ms at %dx%d") % (int(seconds * 1000), size[0], size[1]))

    # ------------------------------------------------------------ files and drops
    def open_files(self, files: List[str]) -> None:
        paths = [f for f in files if Path(f).exists()]
        if not paths:
            return
        fonts = [f for f in paths if Path(f).suffix.lower() in FONT_EXTS]
        if fonts:
            self.tabs.setCurrentWidget(self.text_tab)
            self.text_tab.set_font_file(fonts[0])
            return
        if self.current_tab() is self.batch_tab or len(paths) > 1 or Path(paths[0]).is_dir() \
                or media.kind(paths[0]) == "video":
            self.tabs.setCurrentWidget(self.batch_tab)
            self.batch_tab.add_paths(paths)
        else:
            self.tabs.setCurrentWidget(self.image_tab)
            self.image_tab.open_path(paths[0])

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.open_files(paths)

    # ------------------------------------------------------------ export
    def save_result(self) -> None:
        tab = self.current_tab()
        job = tab.export_job()
        if job is None:
            self.show_message(tr("No image"))
            return
        start_dir = tab.default_dir()
        path, chosen = QFileDialog.getSaveFileName(self, tab.save_label(),
                                                   str(Path(start_dir) / (tab.default_name() + ".png")), SAVE_FILTERS)
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            ext = ".png"
            for key, e in (("JPEG", ".jpg"), ("WebP", ".webp"), ("TIFF", ".tif")):
                if chosen.startswith(key):
                    ext = e
            target = target.with_suffix(ext)
        self._export(job, target)

    def copy_result(self) -> None:
        job = self.current_tab().export_job()
        if job is None:
            self.show_message(tr("No image"))
            return
        self._export(job, "clipboard")

    def _export(self, job, target) -> None:
        values = self.panel.values()
        background = self.batch_tab.background_name()
        self.show_message(tr("Rendering…"))

        def run() -> None:
            try:
                img = apply(job(), values, self.plugin)
                if target == "clipboard":
                    self.bridge.exported.emit(target, img, None)
                else:
                    save_image(img, target, values, BatchOptions(background=background))
                    self.bridge.exported.emit(target, None, None)
            except Exception as e:
                self.bridge.exported.emit(target, None, e)

        t = threading.Thread(target=run, name="zinekit-export", daemon=True)
        self._exports = [x for x in self._exports if x.is_alive()] + [t]
        t.start()

    def _exported(self, target, img, error) -> None:
        if error is not None:
            QMessageBox.warning(self, tr("Error"), tr("Could not save %s") % target + "\n\n%s" % error)
            return
        if target == "clipboard":
            QGuiApplication.clipboard().setImage(pil_to_qimage(img))
            self.show_message(tr("Copied to the clipboard."))
        else:
            self.show_message(tr("Saved %s") % target)

    def show_message(self, text: str) -> None:
        self.statusBar().showMessage(text, 6000)

    # ------------------------------------------------------------ space = before
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        t = event.type()
        if t in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and self.isActiveWindow():
            try:
                key = event.key()
            except AttributeError:
                return False
            if key == Qt.Key.Key_Space:
                fw = QApplication.focusWidget()
                if isinstance(fw, (QLineEdit, QPlainTextEdit, QAbstractSpinBox, QAbstractButton,
                                   QAbstractItemView)) or (isinstance(fw, QComboBox) and fw.isEditable()):
                    return False
                if not event.isAutoRepeat():
                    self.preview.set_peek(t == QEvent.Type.KeyPress)
                return True
        return False

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self.preview.set_peek(False)       # Space may be released in another window
        super().changeEvent(event)

    # ------------------------------------------------------------ settings
    def _restore(self) -> None:
        s = settings()
        geo = s.value("geometry")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)
        split = s.value("splitter")
        if isinstance(split, QByteArray):
            self.splitter.restoreState(split)
        try:
            st = json.loads(str(s.value("state", "{}")))
        except (TypeError, ValueError):
            st = {}
        if not isinstance(st, dict):
            st = {}

        def pick(combo: QComboBox, value) -> None:
            i = combo.findData(value)
            if i >= 0:
                combo.setCurrentIndex(i)
        def values() -> None:
            if isinstance(st.get("values"), dict):
                self.panel.set_values(st["values"], emit=False)

        def text() -> None:
            if isinstance(st.get("text"), dict):
                self.text_tab.set_style(st["text"])

        def batch() -> None:
            if isinstance(st.get("batch"), dict):
                self.batch_tab.set_state(st["batch"])

        def image_dir() -> None:
            self.image_tab.last_dir = str(st.get("image_dir") or self.image_tab.last_dir)

        steps = [
            values,
            lambda: self.presets.select(str(st.get("preset", "Default"))),
            lambda: pick(self.compare, st.get("compare", "after")),
            lambda: pick(self.bg, st.get("background", "checker")),
            lambda: pick(self.size_combo, st.get("preview_size", 720)),
            text, batch, image_dir,
            lambda: self.tabs.setCurrentIndex(int(st.get("tab", 1))),
        ]
        for step in steps:      # a broken saved value only loses that one setting
            try:
                step()
            except (TypeError, ValueError, KeyError):
                pass

    def _save(self) -> None:
        s = settings()
        s.setValue("geometry", self.saveGeometry())
        s.setValue("splitter", self.splitter.saveState())
        st = {"values": self.panel.values(), "preset": self.presets.current_name(),
              "compare": self.compare.currentData(), "background": self.bg.currentData(),
              "preview_size": self.size_combo.currentData(), "text": self.text_tab.text_style().to_dict(),
              "batch": self.batch_tab.get_state(), "image_dir": self.image_tab.last_dir,
              "tab": self.tabs.currentIndex()}
        s.setValue("state", json.dumps(st))
        s.sync()

    def closeEvent(self, event) -> None:
        if self.batch_tab.is_running():
            ans = QMessageBox.question(self, "zinekit", tr("A batch is still running. Stop it and quit?"))
            if ans != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._closing = True
        self._save()
        QApplication.instance().removeEventFilter(self)
        self.batch_tab.shutdown()
        for t in list(self._exports):
            t.join(timeout=30.0)          # let a running save finish writing its file
        if self.worker.close(timeout=10.0):
            self.renderer.close()          # only once no render can be using it
        super().closeEvent(event)
