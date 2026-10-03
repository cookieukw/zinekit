import json
import re
import unittest

from zinekit import i18n
from tests.i18n_keys import all_keys

PLACEHOLDER = re.compile(r"%[sdr%]")


class I18nTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("en")

    def test_every_text_is_in_every_language(self):
        keys, problems = all_keys()
        self.assertEqual(problems, [], "tr()/N_() must take one double-quoted literal")
        for code in i18n.available():
            data = json.loads((i18n.LOCALES_DIR / (code + ".json")).read_text(encoding="utf-8"))
            self.assertTrue(data.get("_language"), code)
            have = set(data) - {"_language"}
            self.assertEqual(sorted(keys - have), [], "%s.json is missing these texts" % code)
            self.assertEqual(sorted(have - keys), [], "%s.json has texts the code no longer uses" % code)
            for key, value in data.items():
                self.assertTrue(value.strip(), (code, key))
                self.assertEqual(PLACEHOLDER.findall(key), PLACEHOLDER.findall(value), (code, key))

    def test_match(self):
        self.assertEqual(i18n.match("pt_BR.UTF-8"), "pt_BR")
        self.assertEqual(i18n.match("pt"), "pt_BR")
        self.assertEqual(i18n.match("pt-PT"), "pt_BR")
        self.assertEqual(i18n.match("en_US"), "en")
        self.assertIsNone(i18n.match("C"))
        self.assertIsNone(i18n.match("xx_YY"))

    def test_tr(self):
        self.assertEqual(i18n.set_language("pt"), "pt_BR")
        self.assertEqual(i18n.tr("Open image…"), "Abrir imagem…")
        self.assertEqual(i18n.tr("Rough edges"), "Bordas roídas")
        self.assertEqual(i18n.tr("not a known text"), "not a known text")
        self.assertEqual(i18n.set_language("klingon"), "en")
        self.assertEqual(i18n.tr("Open image…"), "Open image…")

    def test_translated_preset_names(self):
        from zinekit import presets
        self.assertEqual(presets.find("Riso duas cores").name, "Riso duotone")
        self.assertEqual(presets.find("fotocópia queimada").name, "Burned photocopy")


if __name__ == "__main__":
    unittest.main()
