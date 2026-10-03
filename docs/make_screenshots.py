#!/usr/bin/env python3
"""Take the editor screenshots in docs/images (needs PySide6).

    QT_QPA_PLATFORM=offscreen .venv/bin/python docs/make_screenshots.py

The window is driven the way a user would (open files, pick presets, type),
then captured with QWidget.grab().  Settings go to a temporary folder, so your
own zinekit settings are not touched.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="zinekit-shots-"))
os.environ["ZINEKIT_CONFIG"] = str(TMP / "config")
os.environ["XDG_CONFIG_HOME"] = str(TMP / "xdg")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZINEKIT_LANG"] = "en"

from PySide6.QtWidgets import QApplication  # noqa: E402

from zinekit import i18n  # noqa: E402
from zinekit.engine import get_plugin  # noqa: E402

OUT = HERE / "images"
SIZE = (1600, 940)


def pump(cond, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if cond():
            for _ in range(5):
                QApplication.processEvents()
                time.sleep(0.02)
            return
        time.sleep(0.02)
    raise SystemExit("timed out")


def settled(win):
    """The preview shows the print of the current source with the current values."""
    def done():
        tab = win.current_tab()
        return (win.preview._after is not None and not win.worker.busy and win._shown_gen >= win.worker.generation
                and win._src_key is not None and win._src_key[0] == tab.source_key())
    return done


def type_text(win, text):
    k0 = win.text_tab._key
    win.text_tab.edit.setPlainText(text)
    pump(lambda: win.text_tab._key > k0)          # the title is redrawn after a short pause


def choose_preset(win, name):
    bar = win.presets
    i = [p.name for p in bar._presets].index(name)
    bar.combo.setCurrentIndex(i)
    bar._activated(i)


def shot(win, name):
    win.repaint()
    QApplication.processEvents()
    path = OUT / name
    win.grab().save(str(path))
    print("wrote", path.relative_to(ROOT), "%d KB" % (path.stat().st_size // 1024))


def new_window(plugin):
    from zinekit.gui.window import MainWindow
    win = MainWindow(plugin, [])
    win.resize(*SIZE)
    win.splitter.setSizes([340, 880, 380])
    win.show()
    QApplication.processEvents()
    return win


def main():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    plugin = get_plugin()
    frame = HERE / "input" / "frame.jpg"
    element = HERE / "input" / "element.png"
    clip = TMP / "trailer.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(frame), "-t", "2", "-r", "24",
                    "-vf", "scale=1280:720,zoompan=z='1+0.002*on':d=48:s=1280x720", "-pix_fmt", "yuv420p", str(clip)],
                   check=True)

    win = new_window(plugin)

    # 1. text tab: a title as ransom-note letters
    win.tabs.setCurrentWidget(win.text_tab)
    choose_preset(win, "Ransom note")
    win.text_tab.stroke.setValue(6)
    type_text(win, "NO FUTURE\nzine #1")
    win.bg.setCurrentIndex(win.bg.findData("gray"))
    pump(settled(win))
    shot(win, "editor-text.png")

    # 2. image tab: a game frame, riso duotone, split view
    win.open_files([str(frame)])
    choose_preset(win, "Riso duotone")
    win.compare.setCurrentIndex(win.compare.findData("split"))
    win.preview._split = 0.42
    pump(settled(win))
    shot(win, "editor-image.png")

    # 3. batch tab: a few files, the element previewed, a finished run in the log
    win.compare.setCurrentIndex(win.compare.findData("after"))
    win.bg.setCurrentIndex(win.bg.findData("checker"))
    bt = win.batch_tab
    bt.add_paths([str(element), str(frame), str(clip)])
    choose_preset(win, "Pink riso sticker")
    win.tabs.setCurrentWidget(bt)
    out = TMP / "zine"
    bt.out_dir.setText(str(out))
    bt.video_format.setCurrentIndex(bt.video_format.findData("mov"))
    bt.start()
    pump(lambda: not bt.is_running(), timeout=120)
    bt.out_dir.setText("~/Videos/zine")
    bt.list.setCurrentRow(0)
    pump(lambda: bt._frame_for is not None and bt._frame_for[0] == element)
    pump(settled(win))
    shot(win, "editor-batch.png")
    win.close()

    # 4. the same editor in Portuguese
    i18n.set_language("pt_BR")
    win = new_window(plugin)
    win.tabs.setCurrentWidget(win.text_tab)
    choose_preset(win, "Ransom note, black and white")
    type_text(win, "SEM FUTURO\nfanzine nº 1")
    win.bg.setCurrentIndex(win.bg.findData("gray"))
    pump(settled(win))
    shot(win, "editor-pt-BR.png")
    win.close()


if __name__ == "__main__":
    main()
