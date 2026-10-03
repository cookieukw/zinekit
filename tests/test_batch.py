import threading
import unittest

from PIL import Image

from zinekit import batch as B
from zinekit import media
from tests import helpers


class ImageBatchTest(unittest.TestCase):
    def setUp(self):
        self.d = helpers.tmpdir()
        self.src = self.d / "in"
        self.src.mkdir()
        helpers.title().save(self.src / "title.png")
        helpers.photo().save(self.src / "photo.jpg", quality=95)
        (self.src / "notes.txt").write_text("not an image")
        sub = self.src / "sub"
        sub.mkdir()
        helpers.blob().save(sub / "blob.webp")

    def test_expand_inputs(self):
        self.assertEqual(sorted(p.name for p in B.expand_inputs([self.src])), ["photo.jpg", "title.png"])
        self.assertEqual(len(B.expand_inputs([self.src], recursive=True)), 3)
        self.assertEqual(len(B.expand_inputs([self.src, self.src / "title.png"])), 2)
        with self.assertRaises(FileNotFoundError):
            B.expand_inputs([self.src / "missing.png"])

    def test_formats_and_background(self):
        out = self.d / "out"
        rep = B.run_batch(B.expand_inputs([self.src], True), {}, B.BatchOptions(out_dir=out, image_format="jpg",
                                                                                 background="white"))
        self.assertEqual((rep.ok, rep.failed), (3, 0))
        for r in rep.results:
            self.assertEqual(r.output.suffix, ".jpg")
            with Image.open(r.output) as im:
                self.assertEqual(im.mode, "RGB")
        with Image.open(out / "title_zine.jpg") as im:
            corner = im.getpixel((2, 2))
        self.assertTrue(all(c > 240 for c in corner), corner)

    def test_png_keeps_alpha_and_names_do_not_clash(self):
        out = self.d / "out"
        opts = B.BatchOptions(out_dir=out)
        B.run_batch([self.src / "title.png"], {}, opts)
        rep = B.run_batch([self.src / "title.png"], {}, opts)
        self.assertEqual(rep.results[0].output.name, "title_zine-2.png")
        with Image.open(out / "title_zine.png") as im:
            self.assertEqual(im.getpixel((0, 0))[3], 0)
        rep = B.run_batch([self.src / "title.png"], {}, B.BatchOptions(out_dir=out, overwrite=True))
        self.assertEqual(rep.results[0].output.name, "title_zine.png")

    def test_same_folder_and_max_height(self):
        rep = B.run_batch([self.src / "photo.jpg"], {}, B.BatchOptions(image_format="same", max_height=120))
        r = rep.results[0]
        self.assertEqual(r.output, self.src / "photo_zine.jpg")
        with Image.open(r.output) as im:
            self.assertEqual(im.height, 120)

    def test_failures_do_not_stop(self):
        bad = self.src / "broken.png"
        bad.write_bytes(b"not a png")
        rep = B.run_batch([bad, self.src / "photo.jpg"], {}, B.BatchOptions(out_dir=self.d / "o"))
        self.assertEqual((rep.ok, rep.failed), (1, 1))
        self.assertFalse(rep.results[0].ok)
        self.assertFalse(list((self.d / "o").glob(".*part*")))

    def test_progress_and_cancel(self):
        seen = []
        cancel = threading.Event()

        def progress(i, n, frac, msg):
            seen.append((i, n, frac))
            if frac >= 1.0:
                cancel.set()
        rep = B.run_batch(B.expand_inputs([self.src]), {}, B.BatchOptions(out_dir=self.d / "o"),
                          progress=progress, cancel=cancel)
        self.assertTrue(rep.cancelled)
        self.assertEqual(len(rep.results), 1)
        self.assertEqual(seen[0], (0, 2, 0.0))

    def test_background_rgb(self):
        self.assertEqual(B.background_rgb("paper", {}), (0xf7, 0xf3, 0xe8))
        self.assertEqual(B.background_rgb("#102030", {}), (16, 32, 48))
        self.assertIsNone(B.background_rgb("transparent", {}))


@unittest.skipUnless(helpers.HAVE_FFMPEG, "ffmpeg not installed")
class VideoBatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = helpers.tmpdir()
        cls.clip = helpers.video(cls.d / "clip.mp4", audio=True)
        cls.alpha = helpers.video(cls.d / "title.mov", audio=False, alpha=True)

    def test_probe(self):
        info = media.probe(self.clip)
        self.assertEqual((info.width, info.height), (320, 180))
        self.assertEqual(info.fps, 12)
        self.assertTrue(info.has_audio)
        self.assertFalse(info.alpha)
        self.assertTrue(media.probe(self.alpha).alpha)

    def test_grab_frame(self):
        im = media.grab_frame(self.clip, 0.5)
        self.assertEqual(im.size, (320, 180))
        im = media.grab_frame(self.clip, 99, max_height=90)   # past the end
        self.assertEqual(im.size, (160, 90))
        self.assertEqual(media.grab_frame(self.alpha, 0.2).getpixel((0, 0))[3], 0)

    def _check(self, fmt, src, alpha_expected, audio_expected):
        out = self.d / ("out-" + fmt + "-" + src.stem)
        rep = B.run_batch([src], {}, B.BatchOptions(out_dir=out, video_format=fmt))
        r = rep.results[0]
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.frames, 12)
        if fmt == "png":
            frames = sorted(r.output.glob("frame_*.png"))
            self.assertEqual(len(frames), 12)
            with Image.open(frames[0]) as im:
                px = im.convert("RGBA").getpixel((0, 0))[3]
            self.assertEqual(px == 0, alpha_expected)
            return
        info = media.probe(r.output)
        self.assertEqual((info.width, info.height), (320, 180))
        self.assertEqual(info.has_audio, audio_expected)
        frame = media.grab_frame(r.output, 0.3)
        self.assertEqual(frame.getpixel((0, 0))[3] == 0, alpha_expected, fmt)

    def test_mp4_keeps_audio(self):
        self._check("mp4", self.clip, False, True)

    def test_mov_keeps_alpha(self):
        self._check("mov", self.alpha, True, False)

    def test_webm_keeps_alpha(self):
        self._check("webm", self.alpha, True, False)

    def test_png_sequence(self):
        self._check("png", self.alpha, True, False)

    def test_gif(self):
        self._check("gif", self.clip, False, False)

    def test_scaled_and_cancelled(self):
        out = self.d / "scaled"
        rep = B.run_batch([self.clip], {}, B.BatchOptions(out_dir=out, max_height=91))
        self.assertTrue(rep.results[0].ok, rep.results[0].error)
        info = media.probe(rep.results[0].output)
        self.assertEqual((info.width, info.height), (160, 90))   # even sizes for H.264
        cancel = threading.Event()
        cancel.set()
        rep = B.run_batch([self.clip], {}, B.BatchOptions(out_dir=self.d / "cancelled"), cancel=cancel)
        self.assertTrue(rep.cancelled)
        cancel = threading.Event()
        res = []

        def progress(frac, msg):
            res.append(frac)
            cancel.set()
        with self.assertRaises(B.Cancelled):
            B.process_video(self.clip, self.d / "c.mp4", {}, B.BatchOptions(), progress=progress, cancel=cancel)
        self.assertFalse((self.d / "c.mp4").exists())
        self.assertFalse(list(self.d.glob(".c.part*")))


if __name__ == "__main__":
    unittest.main()
