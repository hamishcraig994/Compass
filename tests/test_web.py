import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["SAMPLE"] = "1"
import config
import db
import web

MOVIE_ITEM = {"media_type": "movie", "tmdb_id": 1, "title": "M", "year": 2020, "match": 50,
             "reason": "r", "matches": [], "overview": "o", "poster_url": None, "url": None}
TV_ITEM = {**MOVIE_ITEM, "media_type": "tv"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


class TestWeb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old_db = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        cls.server = web.make_server("127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        db.DB_PATH = cls._old_db

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.status, r.read().decode()

    def post(self, path, data=""):
        opener = urllib.request.build_opener(NoRedirect)
        try:
            return opener.open(urllib.request.Request(self.base + path, data=data.encode(), method="POST")).status
        except urllib.error.HTTPError as e:
            return e.code

    def test_health(self):
        self.assertEqual(self.get("/health"), (200, "ok"))

    def test_page_shows_suggestions_and_sample_banner(self):
        status, html = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("Gone Girl", html)
        self.assertIn("% match", html)
        self.assertIn("sample data", html)
        self.assertNotIn("Dune", html)  # in the library

    def test_tabs_filter_by_type(self):
        _, movies = self.get("/?type=movie")
        _, shows = self.get("/?type=tv")
        self.assertIn("Gone Girl", movies)
        self.assertNotIn("Silo", movies)
        self.assertIn("Silo", shows)
        self.assertNotIn("Gone Girl", shows)

    def test_new_and_trending_tab(self):
        _, html = self.get("/?type=new")
        for title in ("The Long Signal", "Harbour Lights", "Orbit Nine", "Heat"):
            self.assertIn(title, html)
        self.assertNotIn("Gone Girl", html)   # neither new nor trending
        self.assertIn(">Trending<", html)
        self.assertIn(">New<", html)
        self.assertIn("New &amp; trending", html)

    def test_badges_appear_on_the_main_tab_too(self):
        _, html = self.get("/")
        self.assertIn(">Trending<", html)

    def test_unknown_tab_falls_back_to_all(self):
        self.assertEqual(self.get("/?type=<script>")[0], 200)

    def test_sample_mode_has_no_dismiss_button_and_ignores_dismiss_posts(self):
        _, html = self.get("/")
        self.assertNotIn("Not interested", html)
        self.assertEqual(self.post("/dismiss", "type=movie&id=1017&tab=all"), 303)
        self.assertEqual(db.dismissed(), set())  # fake sample ids must never reach the real dismissed list

    def test_sample_mode_has_no_add_button_and_ignores_add_posts(self):
        _, html = self.get("/")
        self.assertNotIn("Add to library", html)
        opener = urllib.request.build_opener(NoRedirect)
        try:
            resp = opener.open(urllib.request.Request(self.base + "/add", data=b"type=movie&id=1017&tab=all", method="POST"))
            location = resp.headers.get("Location")
        except urllib.error.HTTPError as e:
            location = e.headers.get("Location")
        self.assertNotIn("msg=", location)  # blocked before ever calling sources.add_to_library

    def test_refresh_redirects_back(self):
        self.assertEqual(self.post("/refresh", "tab=tv"), 303)

    def test_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_add_button_shown_only_when_dismissable_and_the_matching_arr_is_configured(self):
        old = (config.RADARR_URL, config.RADARR_API_KEY, config.SONARR_URL, config.SONARR_API_KEY)
        try:
            config.RADARR_URL = config.RADARR_API_KEY = "x"
            config.SONARR_URL = config.SONARR_API_KEY = ""
            self.assertIn("Add to library", web._card(MOVIE_ITEM, "all", True))
            self.assertNotIn("Add to library", web._card(TV_ITEM, "all", True))
            self.assertNotIn("Add to library", web._card(MOVIE_ITEM, "all", False))  # sample mode
        finally:
            config.RADARR_URL, config.RADARR_API_KEY, config.SONARR_URL, config.SONARR_API_KEY = old

    def test_titles_are_html_escaped(self):
        card = web._card({"media_type": "movie", "tmdb_id": 1, "title": "<b>x</b>", "year": 2020, "match": 50,
                          "reason": "<i>r</i>", "matches": ["<u>"], "overview": "<script>alert(1)</script>",
                          "poster_url": 'http://a/"onerror="x', "url": "javascript:alert(1)"}, "all", True)
        self.assertNotIn("<b>", card)
        self.assertNotIn("<script>", card)
        self.assertNotIn('"onerror="', card)
        self.assertNotIn("javascript:", card)


if __name__ == "__main__":
    unittest.main()
