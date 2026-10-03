import unittest

from zinekit import fonts
from zinekit.textlayer import TextStyle, parse_canvas, render_text


class TextTest(unittest.TestCase):
    def test_canvas(self):
        im = render_text(TextStyle(text="HELLO", canvas="1280x720"))
        self.assertEqual(im.size, (1280, 720))
        self.assertEqual(im.mode, "RGBA")
        self.assertEqual(im.getpixel((0, 0))[3], 0)
        self.assertIsNotNone(im.getchannel("A").getbbox())

    def test_fit(self):
        im = render_text(TextStyle(text="A\nBB", canvas="fit", margin=10))
        bbox = im.getchannel("A").getbbox()
        self.assertEqual((bbox[0], bbox[1]), (10, 10))
        self.assertEqual((im.width - bbox[2], im.height - bbox[3]), (10, 10))

    def test_too_big_text_is_shrunk(self):
        im = render_text(TextStyle(text="W" * 60, size=200, canvas="640x360", margin=20))
        bbox = im.getchannel("A").getbbox()
        self.assertGreaterEqual(bbox[0], 19)
        self.assertLessEqual(bbox[2], 621)

    def test_options_change_output(self):
        base = render_text(TextStyle(text="ZINE")).tobytes()
        for kw in ({"stroke": 6}, {"letter_spacing": 30}, {"align": "left"}, {"color": "#ff0000"}, {"size": 90}):
            self.assertNotEqual(render_text(TextStyle(text="ZINE", **kw)).tobytes(), base, kw)

    def test_empty_text(self):
        im = render_text(TextStyle(text="", canvas="fit"))
        self.assertGreater(im.width, 0)

    def test_parse_canvas(self):
        self.assertEqual(parse_canvas("800x600"), (800, 600))
        self.assertEqual(parse_canvas("800×600"), (800, 600))
        self.assertIsNone(parse_canvas("fit"))
        for bad in ("800", "4x4", "100000x100000"):
            with self.assertRaises(ValueError):
                parse_canvas(bad)

    def test_style_dict(self):
        st = TextStyle.from_dict({"text": "x", "size": "40", "align": "weird", "unknown": 1})
        self.assertEqual(st.size, 40)
        self.assertEqual(st.align, "center")
        self.assertEqual(TextStyle.from_dict(st.to_dict()), st)

    def test_fonts(self):
        faces = fonts.list_fonts()
        if not faces:
            self.skipTest("no fonts installed")
        f = faces[0]
        self.assertEqual(fonts.find_font(f.label), f)
        self.assertEqual(fonts.find_font(f.path).path, f.path)
        self.assertIsNotNone(fonts.default_font())


if __name__ == "__main__":
    unittest.main()
