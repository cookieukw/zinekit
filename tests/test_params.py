import json
import os
import unittest

from zinekit import params as P
from zinekit import presets as PR
from tests import helpers


class ParamsTest(unittest.TestCase):
    def test_defaults_match_the_kdenlive_effect(self):
        d = P.defaults()
        self.assertEqual(d["mix"], 1.0)
        self.assertEqual(d["dot_angle"], 1.0)
        self.assertEqual(d["seed"], 0.001)
        self.assertEqual(d["color1"], "#ff4fa8")
        self.assertEqual(len(d), 27)

    def test_ui_round_trip(self):
        for p in P.PARAMS:
            ui = p.to_ui(p.default)
            back = p.from_ui(ui)
            if p.kind == "color":
                self.assertEqual(back, p.default)
            else:
                self.assertAlmostEqual(float(back), float(p.default), places=6, msg=p.name)

    def test_lists(self):
        mode = P.BY_NAME["mode"]
        self.assertEqual([mode.value_of(i) for i in range(4)], [0.0, 1 / 3, 2 / 3, 1.0])
        self.assertEqual(mode.index_of(0.333), 1)      # Kdenlive's paramlist value
        self.assertEqual(mode.index_of(0.667), 2)
        self.assertEqual(P.mode_of({"mode": 0.667}), "element")

    def test_parse_assignment(self):
        self.assertEqual(P.parse_assignment("roughness=80"), ("roughness", 0.8))
        self.assertEqual(P.parse_assignment("roughness=80%"), ("roughness", 0.8))
        self.assertEqual(P.parse_assignment("dot_angle=45"), ("dot_angle", 1.0))
        self.assertEqual(P.parse_assignment("mode=text"), ("mode", 1 / 3))
        self.assertEqual(P.parse_assignment("image_style=Riso 2 colors"), ("image_style", 2 / 3))
        self.assertEqual(P.parse_assignment("scrap_palette=bw"), ("scrap_palette", 0.25))
        self.assertEqual(P.parse_assignment("keep-text-color=on"), ("keep_text_color", 1.0))
        self.assertEqual(P.parse_assignment("ink=#ABC"), ("ink", "#aabbcc"))
        self.assertEqual(P.parse_assignment("seed=250"), ("seed", 0.25))
        for bad in ("nope=1", "mode=banana", "roughness", "ink=#12345", "keep_text_color=maybe"):
            with self.assertRaises(ValueError, msg=bad):
                P.parse_assignment(bad)

    def test_clean_and_complete(self):
        v = P.complete({"roughness": 5, "mode": 0.4, "nothing": 1, "grain": float("nan")})
        self.assertEqual(v["roughness"], 1.0)
        self.assertEqual(v["mode"], 1 / 3)
        self.assertEqual(v["grain"], 0.4)
        self.assertNotIn("nothing", v)

    def test_applies(self):
        self.assertTrue(P.applies(P.BY_NAME["chaos"], "auto"))
        self.assertTrue(P.applies(P.BY_NAME["chaos"], "text"))
        self.assertFalse(P.applies(P.BY_NAME["chaos"], "image"))
        self.assertFalse(P.applies(P.BY_NAME["shadow"], "image"))
        self.assertTrue(P.applies(P.BY_NAME["dot_size"], "element"))

    def test_ffmpeg_filter_order(self):
        s = P.ffmpeg_filter({"mode": 1.0, "keep_text_color": 1})
        values = s.split("filter_params=")[1].split("|")
        self.assertEqual(len(values), 27)
        self.assertEqual(values[0], "1")
        self.assertEqual(values[P.PLUGIN_ORDER.index("keep_text_color")], "y")
        self.assertEqual(values[P.PLUGIN_ORDER.index("color1")], "1/0.3098/0.6588")

    def test_table(self):
        t = P.table()
        for p in P.PARAMS:
            self.assertIn(p.name, t)


class PresetsTest(unittest.TestCase):
    def setUp(self):
        self.dir = helpers.env_isolated()

    def test_builtins_are_valid(self):
        for p in PR.builtins():
            v = p.values()
            self.assertEqual(set(v), set(P.BY_NAME))
        self.assertEqual(PR.find("riso duotone").name, "Riso duotone")
        self.assertEqual(PR.find("riso-duotone").name, "Riso duotone")
        with self.assertRaises(KeyError):
            PR.find("nope")

    def test_save_load_delete(self):
        values = P.complete({"roughness": 0.9, "ink": "#123456", "mode": 2 / 3})
        path = PR.save_user("Meu Preset", values)
        self.assertTrue(path.exists())
        self.assertEqual(path.parent, PR.presets_dir())
        data = json.loads(path.read_text())
        self.assertEqual(data["params"], {"mode": 0.666667, "roughness": 0.9, "ink": "#123456"})
        p = PR.find("meu preset")
        self.assertFalse(p.builtin)
        self.assertEqual(p.values()["roughness"], 0.9)
        PR.delete_user(p)
        self.assertFalse(path.exists())
        with self.assertRaises(ValueError):
            PR.delete_user(PR.builtins()[0])

    def test_parse_lenient(self):
        p = PR.parse('{"roughness": 0.2, "unknown": 3}', "flat")
        self.assertEqual(p.name, "flat")
        self.assertEqual(p.params, {"roughness": 0.2})
        with self.assertRaises(ValueError):
            PR.parse('{"params": {"ink": "red"}}')
        with self.assertRaises(ValueError):
            PR.parse("[1, 2]")

    def test_config_dir_env(self):
        self.assertEqual(str(PR.config_dir()), os.environ["ZINEKIT_CONFIG"])


if __name__ == "__main__":
    unittest.main()
