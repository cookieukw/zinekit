"""Drive the editor window end to end.

With PySide6 installed this runs the real widgets on Qt's offscreen platform
(``./run.sh --selftest`` does that).  Without PySide6 it runs the same steps on
tests/fakeqt, a stand-in that executes the editor's own logic (signals, state,
worker threads) but draws nothing.
"""
import importlib.util
import os
import sys
import time
import unittest
from pathlib import Path

from tests import helpers

HERE = Path(__file__).resolve().parent
REAL_QT = importlib.util.find_spec("PySide6") is not None and not os.environ.get("ZINEKIT_FAKE_QT")
if REAL_QT:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
else:
    sys.path.insert(0, str(HERE / "fakeqt"))
    for mod in [m for m in sys.modules if m == "PySide6" or m.startswith("PySide6.") or m.startswith("zinekit.gui")]:
        del sys.modules[mod]


class GuiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = helpers.env_isolated()          # settings and presets go to a temporary folder
        from PySide6.QtWidgets import QApplication
        from zinekit.engine import get_plugin
        from zinekit.gui.window import MainWindow
        cls.app = QApplication.instance() or QApplication([])
        cls.MainWindow = MainWindow
        cls.plugin = get_plugin()
        cls.win = MainWindow(cls.plugin, [])
        cls.win.show()
        helpers.title().save(cls.tmp / "title.png")
        helpers.photo().save(cls.tmp / "photo.jpg")
        if helpers.HAVE_FFMPEG:
            helpers.video(cls.tmp / "clip.mp4", audio=True)

    @classmethod
    def tearDownClass(cls):
        if REAL_QT:
            cls.win.close()
        else:
            from PySide6.QtCore import _Any
            cls.win.closeEvent(_Any())

    def pump(self, cond, timeout=20.0):
        from PySide6.QtWidgets import QApplication
        end = time.time() + timeout
        while time.time() < end:
            QApplication.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        self.fail("timed out waiting")

    def rendered_after(self, gen):
        return lambda: self.win._shown_gen > gen and self.win.preview._after is not None \
            and not self.win.worker.busy

    def test_1_text_tab_renders(self):
        win = self.win
        win.tabs.setCurrentWidget(win.text_tab)
        g = win._shown_gen
        win.text_tab.edit.setPlainText("NO FUTURE")
        self.pump(self.rendered_after(g))
        self.assertTrue(win.info.text())
        self.assertEqual(win.preview._after.width(), win._src_preview.width)

    def test_2_param_change_rerenders(self):
        win = self.win
        win.tabs.setCurrentWidget(win.text_tab)
        self.pump(lambda: win._src_preview is not None and not win.worker.busy)
        g = win._shown_gen
        row = win.panel._rows["roughness"]
        row.slider.setValue(90)
        self.assertEqual(row.spin.value(), 90)
        self.assertAlmostEqual(win.panel.values()["roughness"], 0.9)
        self.pump(self.rendered_after(g))
        row.spin.setValue(20)
        self.assertEqual(row.slider.value(), 20)
        win.panel.reset_param("roughness")
        self.assertEqual(win.panel.values()["roughness"], 0.5)
        self.assertEqual(row.slider.value(), 50)

    def test_3_mode_greys_out_rows(self):
        win = self.win
        win.panel._rows["mode"].combo.setCurrentIndex(3)       # image
        self.assertEqual(win.panel.values()["mode"], 1.0)
        self.assertFalse(win.panel._rows["chaos"].slider.isEnabled())
        self.assertTrue(win.panel._rows["dot_size"].slider.isEnabled())
        win.panel._rows["mode"].combo.setCurrentIndex(0)
        self.assertTrue(win.panel._rows["chaos"].slider.isEnabled())

    def test_4_presets(self):
        win = self.win
        bar = win.presets
        names = [p.name for p in bar._presets]
        i = names.index("Riso duotone")
        bar.combo.setCurrentIndex(i)
        bar._activated(i)
        v = win.panel.values()
        self.assertEqual(v["mode"], 1.0)
        self.assertAlmostEqual(v["image_style"], 2 / 3)
        self.assertTrue(bar.modified.isHidden())
        win.panel._rows["grain"].slider.setValue(5)
        self.assertFalse(bar.modified.isHidden())
        from zinekit import presets as PR
        PR.save_user("Mine", win.panel.values())
        bar.reload(select="Mine")
        self.assertEqual(bar.current_name(), "Mine")
        self.assertTrue(bar.modified.isHidden())
        bar.reset_all()
        self.assertEqual(win.panel.values(), __import__("zinekit.params").params.defaults())

    def test_5_image_tab(self):
        win = self.win
        win.open_files([str(self.tmp / "photo.jpg")])
        self.assertIs(win.current_tab(), win.image_tab)
        self.assertEqual(win.image_tab.source().size, (640, 360))
        self.pump(lambda: win._src_key is not None and win._src_key[0][0] == "image"
                  and win.preview._after is not None and not win.worker.busy)
        out = self.tmp / "saved.png"
        win._export(win.image_tab.export_job(), out)
        self.pump(out.exists)
        from PIL import Image
        with Image.open(out) as im:
            self.assertEqual(im.size, (640, 360))
        win.copy_result()
        from PySide6.QtGui import QGuiApplication
        self.pump(lambda: not REAL_QT and QGuiApplication.clipboard().image_set is not None
                  or REAL_QT and not QGuiApplication.clipboard().image().isNull())

    def test_6_preview_size(self):
        win = self.win
        win.tabs.setCurrentWidget(win.image_tab)
        i = win.size_combo.findData(540)
        win.size_combo.setCurrentIndex(i)
        self.pump(lambda: win._src_preview is not None and win._src_preview.height <= 540)
        win.size_combo.setCurrentIndex(win.size_combo.findData(720))

    @unittest.skipUnless(helpers.HAVE_FFMPEG, "ffmpeg not installed")
    def test_7_batch(self):
        win = self.win
        win.open_files([str(self.tmp / "title.png"), str(self.tmp / "clip.mp4")])
        bt = win.batch_tab
        self.assertIs(win.current_tab(), bt)
        self.assertEqual(len(bt.files()), 2)
        self.pump(lambda: bt.source() is not None)
        bt.list.setCurrentRow(1)
        self.pump(lambda: bt._frame_for is not None and bt._frame_for[0].name == "clip.mp4")
        bt.frame_slider.setValue(800)
        self.pump(lambda: bt._frame_for[1] == 0.8)
        out = self.tmp / "batch-out"
        bt.out_dir.setText(str(out))
        bt.same_folder.setChecked(False)
        bt.start()
        self.pump(lambda: not bt.is_running(), timeout=60)
        self.assertTrue((out / "title_zine.png").exists())
        self.assertTrue((out / "clip_zine.mp4").exists())
        self.assertIn("2", bt.status.text())
        frame = self.tmp / "frame.png"
        win._export(bt.export_job(), frame)
        self.pump(frame.exists)

    def test_9_language_switch(self):
        from zinekit import i18n
        win = self.win
        win.open_files([str(self.tmp / "photo.jpg")])
        seen = []
        win.languageChanged.connect(lambda code: seen.append(code))
        try:
            win._set_language("pt_BR")
            self.assertEqual(seen, ["pt_BR"])
            win2 = self.MainWindow(self.plugin, [])
            win2.adopt(win)
            self.assertEqual(win2.panel._rows["roughness"].label.text(), "Bordas roídas")
            self.assertEqual(win2.image_tab.source().size, (640, 360))
            self.assertIs(win2.current_tab(), win2.image_tab)
            if REAL_QT:
                win2.close()
            else:
                from PySide6.QtCore import _Any
                win2.closeEvent(_Any())
        finally:
            win._set_language("en")
            i18n.set_language("en")
        self.assertEqual(seen, ["pt_BR", "en"])

    def test_8_settings_round_trip(self):
        win = self.win
        win.panel._rows["chaos"].slider.setValue(77)
        win.tabs.setCurrentWidget(win.text_tab)
        win.text_tab.edit.setPlainText("SAVED TEXT")
        win._save()
        win2 = self.MainWindow(self.plugin, [])
        try:
            self.assertAlmostEqual(win2.panel.values()["chaos"], 0.77)
            self.assertEqual(win2.text_tab.edit.toPlainText(), "SAVED TEXT")
            self.assertIs(win2.current_tab(), win2.text_tab)
        finally:
            if REAL_QT:
                win2.close()
            else:
                from PySide6.QtCore import _Any
                win2.closeEvent(_Any())


if __name__ == "__main__":
    unittest.main()
