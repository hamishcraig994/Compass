import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import themes

BRANDS = re.compile(r"(?i)netflix|hulu|disney|amazon|prime video|apple ?tv|hbo")


class TestRegistry(unittest.TestCase):
    def test_keys_and_default(self):
        self.assertEqual([t["key"] for t in themes.THEMES], ["amber", "crimson", "lime", "ocean", "teal", "mono"])
        self.assertEqual(themes.DEFAULT, "amber")
        self.assertEqual(themes.COOKIE, "wn_theme")

    def test_fields(self):
        for t in themes.THEMES:
            self.assertRegex(t["key"], r"^[a-z]{1,20}$")
            for f in ("label", "description"):
                self.assertTrue(t[f])
                self.assertIsNone(BRANDS.search(t[f]), t)
            self.assertRegex(t["bg"], r"^#[0-9a-f]{6}$")
            self.assertRegex(t["bg_light"], r"^#[0-9a-f]{6}$")

    def test_tokens(self):
        self.assertEqual(len(themes.TOKENS), 15)
        self.assertEqual(len(set(themes.TOKENS)), 15)
        self.assertTrue(all(t.startswith("--") for t in themes.TOKENS))

    def test_is_valid_and_get(self):
        self.assertTrue(themes.is_valid("ocean"))
        for bad in ("Ocean", "", "evil", None, 3, ["ocean"]):
            self.assertFalse(themes.is_valid(bad))
        self.assertEqual(themes.get("ocean")["label"], "Ocean")
        self.assertEqual(themes.get("nope")["key"], "amber")
        self.assertEqual(themes.get(None)["key"], "amber")

    def test_set_cookie_value(self):
        self.assertEqual(themes.set_cookie_value("crimson"),
                         "wn_theme=crimson; Path=/; Max-Age=34560000; SameSite=Lax")
        for bad in ("evil", "Crimson", "", None):
            with self.assertRaises(ValueError):
                themes.set_cookie_value(bad)


class TestFromCookie(unittest.TestCase):
    def test_cases(self):
        cases = [(None, "amber"), ("", "amber"), ("wn_theme=lime", "lime"), ("a=1; wn_theme=ocean", "ocean"),
                 ('junk="a,b; c"; wn_theme=mono', "mono"), ("wn_theme=Crimson", "amber"),
                 ("wn_theme=evil", "amber"), ("wn_theme=<script>", "amber"), ("theme=crimson", "amber"),
                 ("wn_theme=", "amber"), ("wn_theme=evil; wn_theme=teal", "teal"),
                 ("wn_theme=lime; wn_theme=teal", "lime"), ("  wn_theme=mono  ", "mono"),
                 (";;=;wn_theme", "amber")]
        for header, want in cases:
            self.assertEqual(themes.from_cookie(header), want, header)

    def test_never_raises(self):
        for header in (123, b"wn_theme=lime", object()):
            self.assertEqual(themes.from_cookie(header), "amber")


if __name__ == "__main__":
    unittest.main()
