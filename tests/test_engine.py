import threading
import unittest

from PIL import Image

from zinekit import engine
from zinekit import params as P
from tests import helpers


class EngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin = engine.get_plugin()

    def test_plugin_matches_schema(self):
        names = [p.name for p in self.plugin.params]
        self.assertEqual(names, list(P.PLUGIN_ORDER))
        self.assertEqual(set(names), set(P.BY_NAME))
        for p in self.plugin.params:
            kind = P.BY_NAME[p.name].kind
            self.assertEqual(p.type == engine.PARAM_COLOR, kind == "color", p.name)
            self.assertEqual(p.type == engine.PARAM_BOOL, kind == "bool", p.name)

    def test_modes_produce_output(self):
        for img, mode in ((helpers.title(), 1 / 3), (helpers.blob(), 2 / 3), (helpers.photo(), 1.0)):
            out = engine.apply(img, {"mode": mode})
            self.assertEqual(out.size, img.size)
            self.assertEqual(out.mode, "RGBA")
            self.assertNotEqual(out.tobytes(), img.convert("RGBA").tobytes())

    def test_text_mode_adds_paper(self):
        img = helpers.title()
        out = engine.apply(img, {"mode": 1 / 3})
        def opaque(im):
            return im.getchannel("A").point(lambda a: 255 if a > 127 else 0).histogram()[255]
        self.assertGreater(opaque(out), opaque(img) * 1.2)

    def test_amount_zero_is_identity(self):
        img = helpers.blob()
        self.assertEqual(engine.apply(img, {"mix": 0.0}).tobytes(), img.tobytes())

    def test_deterministic_and_reusable(self):
        img = helpers.photo()
        with engine.Renderer(self.plugin, *img.size) as r:
            a = r.process_image(img, {"mode": 1.0, "image_style": 1 / 3})
            b = r.process_image(img)
        self.assertEqual(a.tobytes(), b.tobytes())
        self.assertEqual(a.tobytes(), engine.apply(img, {"mode": 1.0, "image_style": 1 / 3}).tobytes())

    def test_parameters_change_the_print(self):
        img = helpers.title()
        base = engine.apply(img, {"mode": 1 / 3}).tobytes()
        for name, value in (("roughness", 1.0), ("chaos", 0.0), ("ink", "#ff0000"), ("seed", 0.5),
                            ("scrap_palette", 0.25), ("shadow", 0.0)):
            out = engine.apply(img, {"mode": 1 / 3, name: value}).tobytes()
            self.assertNotEqual(out, base, name)

    def test_threads(self):
        img = helpers.photo(320, 180)
        ref = engine.apply(img, {}).tobytes()
        results, errors = [], []

        def work():
            try:
                results.append(engine.apply(img, {}).tobytes())
            except Exception as e:     # pragma: no cover
                errors.append(e)
        ts = [threading.Thread(target=work) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertFalse(errors)
        self.assertTrue(all(r == ref for r in results))

    def test_bad_input(self):
        with engine.Renderer(self.plugin, 4, 4) as r:
            with self.assertRaises(ValueError):
                r.process(b"\0" * 10)
            with self.assertRaises(ValueError):
                r.process_image(Image.new("RGBA", (5, 4)))
        with self.assertRaises(ValueError):
            engine.Renderer(self.plugin, 0, 4)

    def test_parse_color(self):
        self.assertEqual(engine.parse_color("#ff0000"), (1.0, 0.0, 0.0))
        self.assertEqual(engine.parse_color("0x00ff00"), (0.0, 1.0, 0.0))
        self.assertEqual(engine.parse_color("fff"), (1.0, 1.0, 1.0))
        with self.assertRaises(ValueError):
            engine.parse_color("#12")


if __name__ == "__main__":
    unittest.main()
