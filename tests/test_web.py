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
        # Pages build in the background now and show a "Finding your recommendations..." screen
        # until it's done - build the (sample) result up front so these tests see the real pages.
        web.get_result()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        db.DB_PATH = cls._old_db

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.status, r.read().decode()

    def raw_get(self, path):
        """(status, Location) without following redirects."""
        try:
            r = urllib.request.build_opener(NoRedirect).open(self.base + path)
            return r.status, r.headers.get("Location")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location")

    def post(self, path, data="", follow=False):
        if follow:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data=data.encode(), method="POST")) as r:
                return r.status
        opener = urllib.request.build_opener(NoRedirect)
        try:
            return opener.open(urllib.request.Request(self.base + path, data=data.encode(), method="POST")).status
        except urllib.error.HTTPError as e:
            return e.code

    def get_cookie(self, path, cookie):
        req = urllib.request.Request(self.base + path, headers={"Cookie": cookie})
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()

    def test_appearance_page_defaults_to_amber(self):
        status, html = self.get("/appearance")
        self.assertEqual(status, 200)
        self.assertEqual(html.count('type="radio"'), 6)
        self.assertEqual(html.count('name="theme"'), 6)
        self.assertEqual(html.count(" checked"), 1)
        self.assertIn('value="amber" checked', html)
        self.assertRegex(html, r'<html[^>]*data-theme="amber"')

    def test_appearance_follows_cookie_and_all_pages_carry_it(self):
        for path in ("/", "/movies", "/library", "/settings", "/appearance"):
            status, html = self.get_cookie(path, "compass_theme=ocean")
            self.assertEqual(status, 200, path)
            self.assertIn('<html', html)
            self.assertRegex(html, r'<html[^>]*data-theme="ocean"', path)
        _, html = self.get_cookie("/appearance", "compass_theme=ocean")
        self.assertIn('value="ocean" checked', html)
        self.assertEqual(html.count(" checked"), 1)

    def test_bad_cookie_falls_back_to_amber(self):
        _, html = self.get_cookie("/appearance", "compass_theme=nope")
        self.assertRegex(html, r'<html[^>]*data-theme="amber"')

    def test_appearance_tab_row_marks_current(self):
        _, html = self.get("/appearance")
        self.assertIn("Appearance", html)
        # the settings sub-tab row marks the current tab with class "on" (existing markup, no aria-current)
        self.assertIn('<a class="subtab on" href="/appearance">Appearance</a>', html)
        _, settings = self.get("/settings")
        self.assertIn('href="/appearance"', settings)
        self.assertNotIn('<a class="subtab on" href="/appearance">', settings)

    def test_appearance_escapes_query(self):
        status, html = self.get("/appearance?x=<script>alert(1)</script>")
        self.assertEqual(status, 200)
        self.assertNotIn("<script>alert(1)", html)
        status, html = self.get("/appearance?msg=%3Cscript%3Ealert(1)%3C/script%3E")
        self.assertEqual(status, 200)
        self.assertNotIn("<script>alert(1)", html)

    def test_post_theme_sets_cookie_and_redirects_back(self):
        opener = urllib.request.build_opener(NoRedirect)
        try:
            r = opener.open(urllib.request.Request(self.base + "/theme", data=b"theme=ocean", method="POST"))
        except urllib.error.HTTPError as e:
            r = e
        self.assertEqual(r.code, 303)
        self.assertIn("compass_theme=ocean", r.headers.get("Set-Cookie", ""))
        loc = r.headers["Location"]
        self.assertTrue(loc.startswith("/appearance?msg="), loc)
        status, html = self.get(loc)
        self.assertEqual(status, 200)
        self.assertIn("Theme set to", html)

    def test_post_theme_rejects_unknocompass_theme(self):
        opener = urllib.request.build_opener(NoRedirect)
        try:
            r = opener.open(urllib.request.Request(self.base + "/theme", data=b"theme=hotpink", method="POST"))
        except urllib.error.HTTPError as e:
            r = e
        self.assertEqual(r.code, 303)
        self.assertIsNone(r.headers.get("Set-Cookie"))
        self.assertIn("valid+theme", r.headers["Location"].replace("%20", "+"))

    def test_appearance_msg_crlf_stays_encoded(self):
        status, loc = self.raw_get("/recommended?msg=a%0d%0aSet-Cookie:%20x=1")
        self.assertEqual(status, 303)
        self.assertNotIn("\r", loc)
        self.assertNotIn("\n", loc)
        status, html = self.get("/appearance?msg=a%0d%0aSet-Cookie:%20x=1")
        self.assertEqual(status, 200)

    def test_health(self):
        self.assertEqual(self.get("/health"), (200, "ok"))

    def test_home_is_a_hero_plus_rows(self):
        status, html = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn('class="cinematic"', html)
        self.assertEqual(html.count('<article class="hero-slide'), 5)
        self.assertEqual(html.count('data-slide'), 5)
        for heading in ("Recommended for You", "Top 10 picks for you", "Because you watched Arrival",
                        "New in your library"):
            self.assertIn(heading, html)
        self.assertNotIn("Watched titles analyzed", html)  # stat tiles were dropped
        self.assertIn("sample data", html)

    def test_movies_page_has_no_tv_titles(self):
        status, html = self.get("/movies")
        self.assertEqual(status, 200)
        self.assertIn("Gone Girl", html)
        self.assertIn("Because you watched Arrival", html)
        self.assertNotIn("Mindhunter", html)
        self.assertNotIn("Silo", html)

    def test_tv_page_has_no_movies_or_arrival_row(self):
        status, html = self.get("/tv")
        self.assertEqual(status, 200)
        self.assertIn("Mindhunter", html)
        self.assertIn("Silo", html)
        self.assertNotIn("Because you watched Arrival", html)
        self.assertNotIn("Gone Girl", html)

    def test_nav_has_movies_tv_ai_picks_and_no_recommended(self):
        for path in ("/", "/library", "/ai", "/settings"):
            _, html = self.get(path)
            for label in ("Home", "Movies", "TV", "Library", "AI picks"):
                self.assertIn(f"<span>{label}</span>", html, path)
            self.assertNotIn("<span>Recommended</span>", html, path)
            self.assertNotIn('href="/recommended"', html, path)

    def test_unknown_params_on_browse_pages_are_ignored(self):
        for path in ("/movies?x=1", "/tv?type=<script>", "/?sort=zzz"):
            self.assertEqual(self.get(path)[0], 200, path)

    def test_recommended_redirects_to_the_matching_browse_page(self):
        for query, target in (("type=movie", "/movies"), ("type=tv", "/tv"), ("type=all", "/"), ("type=new", "/"),
                              ("type=<script>", "/"), ("", "/")):
            status, location = self.raw_get("/recommended?" + query)
            self.assertEqual((status, location), (303, target), query)

    def test_recommended_redirect_keeps_msg_and_valid_undo_only(self):
        self.assertEqual(self.raw_get("/recommended?type=movie&msg=Hi&undo_type=movie&undo_id=5"),
                         (303, "/movies?msg=Hi&undo_type=movie&undo_id=5"))
        self.assertEqual(self.raw_get("/recommended?type=tv&msg=Hi&undo_type=tv&undo_id=x"), (303, "/tv?msg=Hi"))
        self.assertEqual(self.raw_get("/recommended?undo_type=book&undo_id=5"), (303, "/"))

    def test_recommended_redirect_keeps_crlf_encoded(self):
        _, location = self.raw_get("/recommended?msg=a%0d%0aX-Evil:%201")
        self.assertNotIn("\r", location)
        self.assertNotIn("\n", location)
        self.assertIn("%0D%0A", location)

    def test_recommended_lands_on_home(self):
        status, html = self.get("/recommended")
        self.assertEqual(status, 200)
        self.assertEqual(html.count('<article class="hero-slide'), 5)

    def test_home_movies_tv_library_all_highlight_their_own_nav_item(self):
        for path, section_href in (("/", "/"), ("/movies", "/movies"), ("/tv", "/tv"), ("/library", "/library"),
                                   ("/library?type=movie", "/library"), ("/library?type=tv", "/library"),
                                   ("/library?type=watched", "/library"), ("/library?type=added", "/library")):
            _, html = self.get(path)
            self.assertEqual(html.count(f'class="nav-item active" href="{section_href}"'), 2, path)
            self.assertEqual(html.count('aria-current="page"'), 2, path)

    def test_library_ai_and_settings_render_with_the_top_bar(self):
        for path in ("/library", "/ai", "/settings"):
            status, html = self.get(path)
            self.assertEqual(status, 200)
            self.assertIn('class="topbar"', html, path)
            self.assertNotIn("app-sidebar", html, path)
            self.assertNotIn('class="cinematic"', html, path)

    def test_nav_has_no_watched_item_and_watched_stays_a_tab(self):
        _, html = self.get("/library?type=watched")
        self.assertNotIn('href="/watched"', html)
        self.assertNotRegex(html, r'class="nav-item[^"]*" href="/library\?type=watched"')

    def test_library_lists_what_is_in_plex_in_sample_mode(self):
        _, html = self.get("/library")
        for title in ("Interstellar", "Dune", "Black Mirror"):
            self.assertIn(title, html)
        self.assertNotIn("Nothing added yet", html)

    def test_library_watched_tab_lists_history_with_star_buttons(self):
        _, html = self.get("/library?type=watched")
        self.assertIn("Severance", html)
        for n in range(1, 6):
            self.assertIn(f'name="stars" value="{n}"', html)
        self.assertEqual(self.get("/library?type=watched&q=severance")[0], 200)

    def test_library_bad_params_still_render(self):
        for query in ("type=%3Cscript%3E", "type=watched&sort=added", "page=-1", "page=%C2%B2", "q=" + "x" * 500,
                      "type=added&show=watched"):
            self.assertEqual(self.get("/library?" + query)[0], 200, query)

    def test_watched_is_not_a_route(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/watched")
        self.assertEqual(ctx.exception.code, 404)

    def test_home_shows_suggestions_and_hides_library_titles(self):
        _, html = self.get("/")
        self.assertIn("Gone Girl", html)
        self.assertIn("% match", html)
        self.assertNotIn("Dune", html.split("New in your library")[0])  # owned titles aren't recommended

    def test_library_page_empty_state(self):
        _, html = self.get("/library?type=added")
        self.assertIn("Nothing added yet", html)

    def test_sample_mode_has_no_dismiss_button_and_ignores_dismiss_posts(self):
        _, html = self.get("/")
        self.assertNotIn("Not interested", html)
        self.assertEqual(self.post("/dismiss", "type=movie&id=1017&return_to=/recommended%3Ftype%3Dall"), 303)
        self.assertEqual(db.dismissed(), set())  # fake sample ids must never reach the real dismissed list

    def test_sample_mode_has_no_add_button_and_ignores_add_posts(self):
        for path in ("/", "/movies", "/tv"):
            self.assertNotIn("Add to library", self.get(path)[1], path)
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
        self.assertEqual(html.count('class="nav-item active" href="/settings"'), 1)  # top bar only
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn("brand-name", html)

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

    def test_add_to_library_links_to_the_dialog_page_not_an_inline_modal(self):
        old = (config.RADARR_URL, config.RADARR_API_KEY)
        try:
            config.RADARR_URL = config.RADARR_API_KEY = "x"
            card = web._card(MOVIE_ITEM, "/recommended?type=all", True)
        finally:
            config.RADARR_URL, config.RADARR_API_KEY = old
        self.assertIn("/add-dialog?", card)
        self.assertIn("type=movie", card)
        self.assertIn("id=1", card)
        # no inline dialog markup on the card itself - that only exists on /add-dialog now
        self.assertNotIn("quality_profile_id", card)
        self.assertNotIn("Search and download", card)

    def test_titles_are_html_escaped(self):
        card = web._card({"media_type": "movie", "tmdb_id": 1, "title": "<b>x</b>", "year": 2020, "match": 50,
                          "reason": "<i>r</i>", "matches": ["<u>"], "overview": "<script>alert(1)</script>",
                          "poster_url": 'http://a/"onerror="x', "url": "javascript:alert(1)"}, "/recommended", True)
        self.assertNotIn("<b>", card)
        self.assertNotIn("<script>", card)
        self.assertNotIn('"onerror="', card)
        self.assertNotIn("javascript:", card)


class TestSearchAndArrLibrarySample(unittest.TestCase):
    """Sample mode end to end: search page + fragment, and the Radarr/Sonarr-merged Library."""
    setUpClass, tearDownClass, get = (TestWeb.__dict__[n] for n in ("setUpClass", "tearDownClass", "get"))

    def titles_on(self, path):
        _, body = self.get(path)
        import re
        return [re.sub(r"<[^>]+>", "", t) for t in re.findall(r'<h3 class="title">(.*?)</h3>', body)]

    def test_search_dune_is_in_library(self):
        _, body = self.get("/search?q=dune")
        self.assertIn("Dune", body)
        self.assertIn("In library", body)
        self.assertIn('data-search-state="ok"', body)

    def test_search_low_tide_is_in_sonarr_and_missing(self):
        _, body = self.get("/search?q=low+tide")
        self.assertIn("In Sonarr", body)
        self.assertIn("Missing", body)

    def test_search_paper_is_in_radarr(self):
        _, body = self.get("/search?q=paper")
        self.assertIn("Paper Moons", body)
        self.assertIn("In Radarr", body)

    def test_short_none_and_hostile_queries(self):
        self.assertIn("Type at least 2 characters", self.get("/search?q=x")[1])
        self.assertIn("No matches", self.get("/search?q=zzzz")[1])
        _, body = self.get("/search?q=%3Cscript%3E")
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_no_add_buttons_in_sample_mode(self):
        for path in ("/search?q=low+tide", "/search?q=paper", "/search?q=moons", "/search?q=ni", "/search?q=e&type=tv"):
            self.assertNotIn("Add to library", self.get(path)[1], path)
            self.assertNotIn("data-add-dialog", self.get(path)[1], path)

    def test_partial_fragment(self):
        status, body = self.get("/search?q=dune&partial=1")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith('<div class="search-fragment"'))
        for bad in ("<html", "topbar", "search-form"):
            self.assertNotIn(bad, body)
        self.assertIn('data-search-state="ok"', body)
        self.assertIn("Dune", body)
        self.assertIn("In library", body)
        with urllib.request.urlopen(self.base + "/search?q=dune&partial=1") as r:
            self.assertEqual(r.headers["Cache-Control"], "no-store")
            self.assertEqual(r.headers["Content-Type"], "text/html; charset=utf-8")
        self.assertIn('data-search-state="short"', self.get("/search?q=x&partial=1")[1])
        self.assertIn('data-search-state="empty"', self.get("/search?partial=1")[1])
        self.assertEqual(self.get("/search?type=bogus&partial=1")[0], 200)

    def test_partial_escapes_q_in_body_and_announce(self):
        _, body = self.get("/search?q=%3Cscript%3E&partial=1")
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertIn('data-announce="No matches for &quot;&lt;script&gt;&quot;"', body)

    def test_full_page_embeds_the_exact_fragment(self):
        _, page = self.get("/search?q=dune")
        _, fragment = self.get("/search?q=dune&partial=1")
        self.assertIn(fragment, page)
        self.assertEqual(self.get("/search?q=dune&partial=0")[1], page)

    def test_library_movie_source_arr(self):
        found = self.titles_on("/library?type=movie&source=arr")
        self.assertIn("Paper Moons", found)
        self.assertIn("Dune", found)
        self.assertNotIn("Arrival", found)

    def test_library_wanted(self):
        found = self.titles_on("/library?source=wanted")
        for title in ("Tiny New Thing", "Coming Soon", "Night Shift Diaries", "Low Tide"):
            self.assertIn(title, found)
        self.assertNotIn("Dune", found)

    def test_dune_once_and_badges_and_source_select(self):
        found = self.titles_on("/library?type=movie")
        self.assertEqual(sum(1 for t in found if t.startswith("Dune")), 1)
        _, body = self.get("/library?source=wanted")
        self.assertIn("Missing", body)
        self.assertIn("5/10 episodes", body)
        self.assertIn('name="source"', body)
        self.assertIn("Radarr", body)

    def test_bad_source_falls_back_to_everywhere(self):
        self.assertEqual(self.get("/library?source=bogus")[0], 200)
        self.assertEqual(self.titles_on("/library?source=bogus"), self.titles_on("/library"))


class TestAddDialogIsLazy(unittest.TestCase):
    """Forces sample mode off by patching sources.use_sample - same as TestAddRecordsToLibrary,
    since this file's shared server always runs in sample mode. _is_sample() is checked fresh
    each call rather than trusting a flag on the cached result, so patching it here (not the
    cached item) is what actually simulates "the app is live"."""

    def setUp(self):
        self._old_state = dict(web._state)
        self.addCleanup(web._state.update, self._old_state)
        two_movies = [dict(MOVIE_ITEM, tmdb_id=1), dict(MOVIE_ITEM, tmdb_id=2)]
        web._state["result"] = {"sample": False, "items": two_movies, "profile": {"genre": {}, "keyword": {},
                                "director": {}, "actor": {}}, "notes": [], "watched_count": 5}
        web._state["time"] = __import__("time").time()
        self._old_config = (config.RADARR_URL, config.RADARR_API_KEY)
        config.RADARR_URL = config.RADARR_API_KEY = "x"
        self.addCleanup(lambda: setattr(config, "RADARR_URL", self._old_config[0]))
        self.addCleanup(lambda: setattr(config, "RADARR_API_KEY", self._old_config[1]))
        self._sample_patch = mock.patch.object(sources, "use_sample", return_value=False)
        self._sample_patch.start()
        self.addCleanup(self._sample_patch.stop)

    def test_viewing_the_browse_pages_never_fetches_profiles(self):
        with mock.patch("radarr.RadarrClient.quality_profiles", return_value=[{"id": 1, "name": "HD"}]) as qp:
            web.render_browse("all")
        qp.assert_not_called()

    def test_opening_the_add_dialog_fetches_profiles_exactly_once(self):
        with mock.patch("radarr.RadarrClient.quality_profiles", return_value=[{"id": 1, "name": "HD"}]) as qp:
            html = web.render_add_dialog("movie", 1, "/recommended?type=all")
        qp.assert_called_once()
        self.assertIn('<option value="1">HD</option>', html)
        self.assertIn("Search and download immediately", html)

    def test_unknown_item_shows_a_safe_message_instead_of_a_dialog(self):
        html = web.render_add_dialog("movie", 999999, "/recommended?type=all")
        self.assertIn("isn't available to add", html)
        self.assertNotIn("quality_profile_id", html)

    def test_sample_mode_refuses_the_dialog_even_with_valid_looking_ids(self):
        self._sample_patch.stop()
        with mock.patch.object(sources, "use_sample", return_value=True):
            html = web.render_add_dialog("movie", 1, "/recommended?type=all")
        self._sample_patch.start()
        self.assertNotIn("quality_profile_id", html)

    def test_cancel_link_returns_to_where_the_dialog_was_opened_from(self):
        html = web.render_add_dialog("movie", 1, "/recommended?type=tv")
        self.assertIn('href="/recommended?type=tv" class="btn-ghost"', html)

    def test_return_to_is_sanitized_even_for_the_dialog_page(self):
        html = web.render_add_dialog("movie", 1, "https://evil.example/")
        self.assertNotIn("https://evil.example", html)


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

    def post_settings(self, section, data):
        opener = urllib.request.build_opener(NoRedirect)
        try:
            resp = opener.open(urllib.request.Request(
                f"{self.base}/settings?section={section}", data=data.encode(), method="POST"))
            return resp.status, resp.headers.get("Location"), resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location"), e.read().decode()

    def test_get_settings_shows_the_default_section(self):
        with urllib.request.urlopen(self.base + "/settings") as r:
            status, html = r.status, r.read().decode()
        self.assertEqual(status, 200)
        self.assertIn("Plex URL", html)

    def test_get_settings_shows_the_requested_section(self):
        with urllib.request.urlopen(self.base + "/settings?section=arr") as r:
            html = r.read().decode()
        self.assertIn("Radarr", html)
        self.assertNotIn("Plex URL", html)

    def test_post_settings_save_persists_and_redirects_with_saved_flag(self):
        status, location, _ = self.post_settings("plex", "action=save&PLEX_URL=http%3A%2F%2Fnewplex%3A32400")
        self.assertEqual(status, 303)
        self.assertEqual(location, "/settings?section=plex&saved=1")
        self.assertEqual(config.PLEX_URL, "http://newplex:32400")

    def test_post_settings_save_invalidates_the_cached_recommendations(self):
        web.get_result()  # populate the cache
        self.assertIsNotNone(web._state["result"])
        self.post_settings("plex", "action=save&PLEX_URL=x")
        self.assertIsNone(web._state["result"])

    def test_post_settings_test_renders_the_result_without_saving(self):
        with mock.patch("plex.PlexClient.test_connection", return_value=(True, "Connected to Test Server")):
            status, location, html = self.post_settings(
                "plex", "action=test_plex&PLEX_URL=http%3A%2F%2Funsaved%3A32400&PLEX_TOKEN=")
        self.assertEqual(status, 200)
        self.assertIsNone(location)  # rendered directly, not a redirect
        self.assertIn("Connected to Test Server", html)
        self.assertIn("http://unsaved:32400", html)  # the just-typed value, redisplayed
        self.assertNotEqual(config.PLEX_URL, "http://unsaved:32400")  # but never saved

    def test_post_settings_test_failure_is_shown_without_a_success_style(self):
        with mock.patch("plex.PlexClient.test_connection", return_value=(False, "401 Unauthorized")):
            _, _, html = self.post_settings("plex", "action=test_plex&PLEX_URL=http%3A%2F%2Fx&PLEX_TOKEN=bad")
        self.assertIn("401 Unauthorized", html)
        self.assertNotIn('note success">401', html)


class TestAddRecordsToLibrary(unittest.TestCase):
    """The /add success path, forced out of sample mode by patching sources.use_sample, since the
    shared test server (this whole file) always runs in sample mode, which deliberately blocks
    real writes."""

    def setUp(self):
        self._old_db = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old_db)
        self._old_state = dict(web._state)
        self.addCleanup(web._state.update, self._old_state)
        web._state["result"] = {"sample": False, "items": [dict(MOVIE_ITEM)]}
        sample_patch = mock.patch.object(sources, "use_sample", return_value=False)
        sample_patch.start()
        self.addCleanup(sample_patch.stop)
        self.server = web.make_server("127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def post_add(self, media_type, tmdb_id, extra=""):
        opener = urllib.request.build_opener(NoRedirect)
        data = f"type={media_type}&id={tmdb_id}{extra}".encode()
        try:
            resp = opener.open(urllib.request.Request(self.base + "/add", data=data, method="POST"))
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
        html = urllib.request.urlopen(self.base + "/library?type=added").read().decode()
        self.assertIn("M (2020)", html)
        self.assertIn("Added ", html)

    def test_failed_add_is_not_logged(self):
        with mock.patch.object(sources, "add_to_library", return_value=(False, "Radarr isn't configured")):
            location = self.post_add("movie", 1)
        self.assertIn("Radarr", location)
        self.assertEqual(db.added_items(), [])
        self.assertEqual(len(web._state["result"]["items"]), 1)  # still there, wasn't removed

    def test_chosen_quality_profile_and_search_flag_are_passed_through(self):
        with mock.patch.object(sources, "add_to_library", return_value=(True, "Added")) as add:
            self.post_add("movie", 1, "&quality_profile_id=7&search=1")
        add.assert_called_once_with("movie", 1, search=True, quality_profile_id=7)

    def test_unchecked_search_box_means_dont_search(self):
        with mock.patch.object(sources, "add_to_library", return_value=(True, "Added")) as add:
            self.post_add("movie", 1)  # no "search" field at all - an unchecked checkbox isn't submitted
        add.assert_called_once_with("movie", 1, search=False, quality_profile_id=None)

    def test_blank_quality_profile_means_use_the_configured_default(self):
        with mock.patch.object(sources, "add_to_library", return_value=(True, "Added")) as add:
            self.post_add("movie", 1, "&quality_profile_id=&search=1")
        add.assert_called_once_with("movie", 1, search=True, quality_profile_id=None)


class TestAiPage(unittest.TestCase):
    """Manual-only: the AI page never computes anything by itself, only shows what the last
    Generate click produced. Its own server/cache, separate from TestWeb's shared sample-mode
    one, so config.AI_TOKEN and web._ai_state can be freely swapped here."""

    def setUp(self):
        self._old_db = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old_db)
        self._old_ai_state = dict(web._ai_state)
        self.addCleanup(web._ai_state.update, self._old_ai_state)
        self._old_ai_token = config.AI_TOKEN
        self.addCleanup(lambda: setattr(config, "AI_TOKEN", self._old_ai_token))
        self.server = web.make_server("127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.status, r.read().decode()

    def post(self, path, data=""):
        opener = urllib.request.build_opener(NoRedirect)
        try:
            resp = opener.open(urllib.request.Request(self.base + path, data=data.encode(), method="POST"))
            return resp.status, resp.headers.get("Location")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location")

    def test_not_configured_points_to_settings_and_has_no_generate_button(self):
        config.AI_TOKEN = ""
        web._ai_state["result"] = None
        _, html = self.get("/ai")
        self.assertIn("AI isn't configured", html)
        self.assertIn('href="/settings?section=ai"', html)
        self.assertNotIn("Generate<", html)

    def test_configured_but_never_generated_shows_a_generate_button(self):
        config.AI_TOKEN = "sk-x"
        web._ai_state["result"] = None
        _, html = self.get("/ai")
        self.assertIn(">Generate<", html)
        self.assertNotIn("Generate again", html)

    def test_populated_state_shows_cards_and_a_generate_again_button(self):
        config.AI_TOKEN = "sk-x"
        web._ai_state["result"] = {"items": [dict(MOVIE_ITEM)], "notes": [], "sample": False, "watched_count": 5}
        web._ai_state["time"] = __import__("time").time()
        _, html = self.get("/ai")
        self.assertIn("M (2020)", html)
        self.assertIn("Generate again", html)
        self.assertIn("Generated just now", html)

    def test_ai_nav_item_is_active_on_the_ai_page(self):
        config.AI_TOKEN = "sk-x"
        web._ai_state["result"] = None
        _, html = self.get("/ai")
        self.assertEqual(html.count('class="nav-item active" href="/ai"'), 2)

    def test_generate_populates_state_and_redirects_back(self):
        config.AI_TOKEN = "sk-x"
        web._ai_state["result"] = None
        fake_result = {"items": [dict(MOVIE_ITEM)], "notes": [], "sample": False, "watched_count": 3}
        with mock.patch.object(sources, "use_sample", return_value=False), \
             mock.patch.object(sources, "generate_ai_recommendations", return_value=fake_result) as gen:
            status, location = self.post("/ai/generate", "return_to=/ai")
            # Generate runs in a background thread now - wait for it while the mocks are still on
            deadline = __import__("time").time() + 5
            while web._ai_state["building"] and __import__("time").time() < deadline:
                __import__("time").sleep(0.01)
        gen.assert_called_once()
        self.assertEqual(status, 303)
        self.assertEqual(location, "/ai")
        self.assertEqual(web._ai_state["result"], fake_result)

    def test_generate_is_blocked_in_sample_mode(self):
        web._ai_state["result"] = None
        with mock.patch.object(sources, "use_sample", return_value=True), \
             mock.patch.object(sources, "generate_ai_recommendations") as gen:
            self.post("/ai/generate", "return_to=/ai")
        gen.assert_not_called()
        self.assertIsNone(web._ai_state["result"])

    def test_add_works_on_an_item_that_only_exists_in_the_ai_cache(self):
        """Regression check: 'Add to library' clicked from the AI page must resolve via
        _find_item(), not just the main Recommended cache."""
        config.AI_TOKEN = "sk-x"
        web._ai_state["result"] = {"items": [dict(MOVIE_ITEM)], "notes": [], "sample": False, "watched_count": 1}
        with mock.patch.object(sources, "use_sample", return_value=False), \
             mock.patch.object(sources, "add_to_library", return_value=(True, "Added")):
            self.post("/add", "type=movie&id=1&return_to=/ai")
        self.assertEqual(len(db.added_items()), 1)
        self.assertEqual(web._ai_state["result"]["items"], [])

    def test_dismiss_works_on_an_item_that_only_exists_in_the_ai_cache(self):
        config.AI_TOKEN = "sk-x"
        web._ai_state["result"] = {"items": [dict(MOVIE_ITEM)], "notes": [], "sample": False, "watched_count": 1}
        with mock.patch.object(sources, "use_sample", return_value=False):
            self.post("/dismiss", "type=movie&id=1&return_to=/ai")
        self.assertIn(("movie", 1), db.dismissed())
        self.assertEqual(web._ai_state["result"]["items"], [])


if __name__ == "__main__":
    unittest.main()
