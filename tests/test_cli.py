import contextlib
import io
import json
import unittest

from PIL import Image

from zinekit import cli
from tests import helpers


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.d = helpers.env_isolated()
        helpers.photo().save(self.d / "photo.png")

    def test_apply_single_file(self):
        code, _, err = run("apply", str(self.d / "photo.png"), "-o", str(self.d / "x.jpg"), "-p", "xerox photo",
                           "-s", "dot_size=40", "-s", "ink=#200000")
        self.assertEqual(code, 0, err)
        with Image.open(self.d / "x.jpg") as im:
            self.assertEqual(im.size, (640, 360))

    def test_apply_folder(self):
        code, _, err = run("apply", str(self.d), "-o", str(self.d / "out"), "--image-format", "webp")
        self.assertEqual(code, 0, err)
        self.assertTrue((self.d / "out" / "photo_zine.webp").exists())

    def test_text(self):
        code, out, err = run("text", "NO\\nFUTURE", "-o", str(self.d / "t.png"), "--canvas", "fit", "-s", "chaos=0")
        self.assertEqual(code, 0, err)
        with Image.open(self.d / "t.png") as im:
            self.assertEqual(im.mode, "RGBA")
        code, _, _ = run("text", "x", "-o", str(self.d / "plain.png"), "--plain", "--canvas", "64x64")
        self.assertEqual(code, 0)

    def test_errors(self):
        self.assertEqual(run("apply", str(self.d / "nope.png"))[0], 2)
        self.assertEqual(run("apply", str(self.d / "photo.png"), "-s", "mode=zzz")[0], 2)
        self.assertEqual(run("apply", str(self.d / "photo.png"), "-p", "no such preset")[0], 2)
        self.assertEqual(run("apply", str(self.d / "photo.png"), "-o", str(self.d / "x.xyz"))[0], 2)

    def test_listings(self):
        for cmd in (["params"], ["presets"], ["fonts"], ["ffmpeg", "-p", "blue riso"], ["doctor"]):
            code, out, err = run(*cmd)
            self.assertIn(code, (0, 1), cmd)
            self.assertTrue(out.strip(), cmd)
        code, out, _ = run("presets", "Riso duotone")
        self.assertEqual(json.loads(out)["name"], "Riso duotone")


if __name__ == "__main__":
    unittest.main()
