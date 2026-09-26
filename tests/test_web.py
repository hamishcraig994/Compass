import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["SAMPLE"] = "1"
import config
import db
import sources
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

    def post(self, path, data="", follow=False):
        if follow:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data=data.encode(), method="POST")) as r:
                return r.status
        opener = urllib.request.build_opener(NoRedirect)
        try:
            return opener.open(urllib.request.Request(self.base + path, data=data.encode(), method="POST")).status
        except urllib.error.HTTPError as e:
            return e.code

    def test_health(self):
        self.assertEqual(self.get("/health"), (200, "ok"))

    def test_home_shows_stat_tiles(self):
        status, html = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("Recommended", html)
        self.assertIn("Added to library", html)
        self.assertIn("Not interested", html)
        self.assertIn("Watched titles analyzed", html)
        self.assertIn('href="/recommended"', html)
        self.assertIn('href="/library"', html)

    def test_home_and_recommended_and_library_all_highlight_their_own_nav_item(self):
        for path, section_href in (("/", "/"), ("/recommended", "/recommended"), ("/library", "/library")):
            _, html = self.get(path)
            self.assertEqual(html.count(f'class="nav-item active" href="{section_href}"'), 2, path)

    def test_recommended_page_shows_suggestions_and_sample_banner(self):
        status, html = self.get("/recommended")
        self.assertEqual(status, 200)
        self.assertIn("Gone Girl", html)
        self.assertIn("% match", html)
        self.assertIn("sample data", html)
        self.assertNotIn("Dune", html)  # in the library

    def test_recommended_subtabs_filter_by_type(self):
        _, movies = self.get("/recommended?type=movie")
        _, shows = self.get("/recommended?type=tv")
        self.assertIn("Gone Girl", movies)
        self.assertNotIn("Silo", movies)
        self.assertIn("Silo", shows)
        self.assertNotIn("Gone Girl", shows)

    def test_new_and_trending_subtab(self):
        _, html = self.get("/recommended?type=new")
        for title in ("The Long Signal", "Harbour Lights", "Orbit Nine", "Heat"):
            self.assertIn(title, html)
        self.assertNotIn("Gone Girl", html)   # neither new nor trending
        self.assertIn(">Trending<", html)
        self.assertIn(">New<", html)
        self.assertIn("New &amp; trending", html)

    def test_badges_appear_on_the_all_subtab_too(self):
        _, html = self.get("/recommended")
        self.assertIn(">Trending<", html)

    def test_unknown_subtab_falls_back_to_all(self):
        self.assertEqual(self.get("/recommended?type=<script>")[0], 200)

    def test_subtabs_link_to_recommended_not_the_bare_type_query(self):
        _, html = self.get("/recommended?type=movie")
        self.assertIn('href="/recommended?type=movie"', html)
        self.assertIn('class="subtab on" href="/recommended?type=movie"', html)

    def test_library_page_empty_state(self):
        _, html = self.get("/library")
        self.assertIn("Nothing added yet", html)

    def test_sample_mode_has_no_dismiss_button_and_ignores_dismiss_posts(self):
        _, html = self.get("/recommended")
        self.assertNotIn("Not interested", html)
        self.assertEqual(self.post("/dismiss", "type=movie&id=1017&return_to=/recommended%3Ftype%3Dall"), 303)
        self.assertEqual(db.dismissed(), set())  # fake sample ids must never reach the real dismissed list

    def test_sample_mode_has_no_add_button_and_ignores_add_posts(self):
        _, html = self.get("/recommended")
        self.assertNotIn("Add to library", html)
        opener = urllib.request.build_opener(NoRedirect)
        try:
            resp = opener.open(urllib.request.Request(self.base + "/add", data=b"type=movie&id=1017", method="POST"))
            location = resp.headers.get("Location")
        except urllib.error.HTTPError as e:
            location = e.headers.get("Location")
        self.assertNotIn("msg=", location)  # blocked before ever calling sources.add_to_library
        self.assertEqual(db.added_items(), [])

    def test_refresh_redirects_back_to_wherever_it_was_submitted_from(self):
        opener = urllib.request.build_opener(NoRedirect)
        try:
            resp = opener.open(urllib.request.Request(
                self.base + "/refresh", data=b"return_to=%2Frecommended%3Ftype%3Dtv", method="POST"))
            location = resp.headers.get("Location")
        except urllib.error.HTTPError as e:
            location = e.headers.get("Location")
        self.assertEqual(location, "/recommended?type=tv")

    def test_refresh_with_no_return_to_falls_back_to_recommended(self):
        self.assertEqual(self.post("/refresh"), 303)

    def test_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_settings_page_highlights_settings_in_nav(self):
        _, html = self.get("/settings")
        self.assertEqual(html.count('class="nav-item active" href="/settings"'), 2)
        self.assertIn("brand-lockup", html)

    def test_add_button_shown_only_when_dismissable_and_the_matching_arr_is_configured(self):
        old = (config.RADARR_URL, config.RADARR_API_KEY, config.SONARR_URL, config.SONARR_API_KEY)
        try:
            config.RADARR_URL = config.RADARR_API_KEY = "x"
            config.SONARR_URL = config.SONARR_API_KEY = ""
            self.assertIn("Add to library", web._card(MOVIE_ITEM, "/recommended", True))
            self.assertNotIn("Add to library", web._card(TV_ITEM, "/recommended", True))
            self.assertNotIn("Add to library", web._card(MOVIE_ITEM, "/recommended", False))  # sample mode
        finally:
            config.RADARR_URL, config.RADARR_API_KEY, config.SONARR_URL, config.SONARR_API_KEY = old

    def test_titles_are_html_escaped(self):
        card = web._card({"media_type": "movie", "tmdb_id": 1, "title": "<b>x</b>", "year": 2020, "match": 50,
                          "reason": "<i>r</i>", "matches": ["<u>"], "overview": "<script>alert(1)</script>",
                          "poster_url": 'http://a/"onerror="x', "url": "javascript:alert(1)"}, "/recommended", True)
        self.assertNotIn("<b>", card)
        self.assertNotIn("<script>", card)
        self.assertNotIn('"onerror="', card)
        self.assertNotIn("javascript:", card)


class TestSafePath(unittest.TestCase):
    def test_accepts_internal_paths(self):
        self.assertEqual(web._safe_path("/recommended?type=tv"), "/recommended?type=tv")
        self.assertEqual(web._safe_path("/"), "/")

    def test_rejects_external_and_protocol_relative_urls(self):
        self.assertEqual(web._safe_path("https://evil.example/"), "/recommended")
        self.assertEqual(web._safe_path("//evil.example/"), "/recommended")
        self.assertEqual(web._safe_path("javascript:alert(1)"), "/recommended")
        self.assertEqual(web._safe_path(""), "/recommended")
        self.assertEqual(web._safe_path(None), "/recommended")


class TestSettingsRoute(unittest.TestCase):
    """Isolated from TestWeb's shared server/state, since these tests actually save settings."""

    def setUp(self):
        self._old_db = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.server = web.make_server("127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.addCleanup(setattr, db, "DB_PATH", self._old_db)
        self.addCleanup(config._apply)

    def test_get_settings_shows_the_form(self):
        with urllib.request.urlopen(self.base + "/settings") as r:
            status, html = r.status, r.read().decode()
        self.assertEqual(status, 200)
        self.assertIn("Plex URL", html)
        self.assertIn("Radarr", html)

    def test_post_settings_saves_and_redirects_with_saved_flag(self):
        opener = urllib.request.build_opener(NoRedirect)
        try:
            resp = opener.open(urllib.request.Request(
                self.base + "/settings", data=b"PLEX_URL=http%3A%2F%2Fnewplex%3A32400", method="POST"))
            location = resp.headers.get("Location")
        except urllib.error.HTTPError as e:
            location = e.headers.get("Location")
        self.assertEqual(location, "/settings?saved=1")
        self.assertEqual(config.PLEX_URL, "http://newplex:32400")

    def test_post_settings_invalidates_the_cached_recommendations(self):
        web.get_result()  # populate the cache
        self.assertIsNotNone(web._state["result"])
        opener = urllib.request.build_opener(NoRedirect)
        try:
            opener.open(urllib.request.Request(self.base + "/settings", data=b"PLEX_URL=x", method="POST"))
        except urllib.error.HTTPError:
            pass
        self.assertIsNone(web._state["result"])


class TestAddRecordsToLibrary(unittest.TestCase):
    """The /add success path, forced out of sample mode by hand since the shared test server
    (this whole file) always runs in sample mode, which deliberately blocks real writes."""

    def setUp(self):
        self._old_db = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old_db)
        self._old_state = dict(web._state)
        self.addCleanup(web._state.update, self._old_state)
        web._state["result"] = {"sample": False, "items": [dict(MOVIE_ITEM)]}
        self.server = web.make_server("127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def post_add(self, media_type, tmdb_id):
        opener = urllib.request.build_opener(NoRedirect)
        try:
            resp = opener.open(urllib.request.Request(
                self.base + "/add", data=f"type={media_type}&id={tmdb_id}".encode(), method="POST"))
            return resp.headers.get("Location")
        except urllib.error.HTTPError as e:
            return e.headers.get("Location")

    def test_successful_add_is_logged_and_removed_from_the_current_list(self):
        with mock.patch.object(sources, "add_to_library", return_value=(True, 'Added "M" to Radarr')):
            location = self.post_add("movie", 1)
        self.assertIn("msg=", location)
        added = db.added_items()
        self.assertEqual(len(added), 1)
        self.assertEqual((added[0]["media_type"], added[0]["tmdb_id"], added[0]["title"]), ("movie", 1, "M"))
        self.assertEqual(web._state["result"]["items"], [])
        html = urllib.request.urlopen(self.base + "/library").read().decode()
        self.assertIn("M (2020)", html)
        self.assertIn("Added ", html)

    def test_failed_add_is_not_logged(self):
        with mock.patch.object(sources, "add_to_library", return_value=(False, "Radarr isn't configured")):
            location = self.post_add("movie", 1)
        self.assertIn("Radarr", location)
        self.assertEqual(db.added_items(), [])
        self.assertEqual(len(web._state["result"]["items"]), 1)  # still there, wasn't removed


if __name__ == "__main__":
    unittest.main()
