"""Markup-level checks for the redesigned pages: building/updating states, the no-JS Undo note,
escaping in the poster cards and their detail view, the data-enhance hooks app.js relies on, the
partial add dialog, and the static CSS/JS links."""
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["SAMPLE"] = "1"
import config
import db
import sources
import web

ROOT = os.path.join(os.path.dirname(__file__), "..")


def read_static(name):
    with open(os.path.join(ROOT, "static", name), encoding="utf-8") as f:
        return f.read()


EMPTY_PROFILE = {"genre": {}, "keyword": {}, "director": {}, "actor": {}}
ITEM = {"media_type": "movie", "tmdb_id": 7, "title": "Heat", "year": 1995, "match": 88, "reason": "Because you liked Ronin",
        "matches": ["Crime", "Michael Mann"], "overview": "A heist.", "poster_url": None, "url": None,
        "new": True, "trending": True}
HOSTILE = {"media_type": "movie", "tmdb_id": 1, "title": "<script>alert('t')</script>", "year": 2020, "match": 50,
           "reason": "<img src=x onerror=alert(1)>", "matches": ["<b>chip</b>"],
           "overview": "</p><script>alert('o')</script>", "poster_url": 'http://a/"onerror="x',
           "url": "javascript:alert(1)"}


def status(state="ready", has_result=True, error=None, ai_state="idle", ai_error=None):
    return {"state": state, "has_result": has_result, "started": None, "updated": None, "error": error,
            "ai": {"state": ai_state, "error": ai_error}}


def result(items=None, sample=False):
    return {"sample": sample, "items": list(items if items is not None else [dict(ITEM)]), "profile": EMPTY_PROFILE,
            "notes": [], "watched_count": 12}


class PatchedState(unittest.TestCase):
    """Patches the backend's non-blocking accessors, so each test picks exactly what state it renders."""

    def use(self, res, age=60.0, st=None):
        patches = [mock.patch.object(web, "get_result_nowait", return_value=(res, age if res is not None else None)),
                   mock.patch.object(web, "build_status", return_value=st or status(has_result=res is not None))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def setUp(self):
        self._old_db = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old_db)


class TestBuildingStates(PatchedState):
    def test_recommended_shows_building_screen_when_there_is_no_result_yet(self):
        self.use(None, st=status("building", has_result=False))
        html = web.render_recommended("all")
        self.assertIn("Finding your recommendations...", html)
        self.assertIn('data-poll="recs"', html)
        self.assertIn('<noscript><meta http-equiv="refresh" content="5"></noscript>', html)
        self.assertNotIn('class="grid"', html)

    def test_home_shows_building_screen_when_there_is_no_result_yet(self):
        self.use(None, st=status("building", has_result=False))
        html = web.render_home()
        self.assertIn("Finding your recommendations...", html)
        self.assertIn('http-equiv="refresh"', html)

    def test_first_build_failure_shows_error_and_try_again_not_a_spinner(self):
        self.use(None, st=status("error", has_result=False, error="<b>Plex down</b>"))
        html = web.render_recommended("all")
        self.assertIn("&lt;b&gt;Plex down&lt;/b&gt;", html)
        self.assertNotIn("<b>Plex down", html)
        self.assertIn("Try again", html)
        self.assertNotIn('data-poll="recs"', html)
        self.assertNotIn('http-equiv="refresh"', html)

    def test_stale_result_while_rebuilding_shows_updating_indicator_and_noscript_refresh(self):
        self.use(result(), st=status("building"))
        html = web.render_recommended("all")
        self.assertIn("Heat (1995)", html)
        self.assertIn('data-poll="stale"', html)
        self.assertIn("Updating...", html)
        self.assertIn('http-equiv="refresh"', html)

    def test_ready_result_has_no_updating_indicator_or_auto_refresh(self):
        self.use(result(), st=status("ready"))
        for html in (web.render_recommended("all"), web.render_home()):
            self.assertNotIn("Updating...", html)
            self.assertNotIn('http-equiv="refresh"', html)

    def test_ready_with_error_shows_a_calm_escaped_notice(self):
        self.use(result(), st=status("ready", error="<i>timeout</i>"))
        html = web.render_recommended("all")
        self.assertIn("Showing your last list", html)
        self.assertIn("&lt;i&gt;timeout&lt;/i&gt;", html)

    def test_ai_page_shows_generating_screen_while_building(self):
        old = config.AI_TOKEN
        self.addCleanup(setattr, config, "AI_TOKEN", old)
        config.AI_TOKEN = "sk-x"
        self.use(None, st=status(ai_state="building"))
        html = web.render_ai_page()
        self.assertIn("Asking your AI for ideas...", html)
        self.assertIn('data-poll="ai"', html)
        self.assertIn('http-equiv="refresh"', html)

    def test_ai_error_is_shown_escaped(self):
        old = config.AI_TOKEN
        self.addCleanup(setattr, config, "AI_TOKEN", old)
        config.AI_TOKEN = "sk-x"
        self.use(None, st=status(ai_state="error", ai_error="<script>x</script>"))
        p = mock.patch.dict(web._ai_state, {"result": None})
        p.start()
        self.addCleanup(p.stop)
        html = web.render_ai_page()
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", html)
        self.assertIn(">Generate<", html)


class TestUndoNote(PatchedState):
    def test_undo_note_is_a_form_posting_to_undismiss(self):
        self.use(result())
        html = web.render_recommended("tv", msg='Hidden "X"', undo=("movie", 42))
        note = re.search(r'<div class="note undo-note".*?</div>', html).group(0)
        self.assertIn('action="/undismiss"', note)
        self.assertIn('method="post"', note)
        self.assertIn('name="type" value="movie"', note)
        self.assertIn('name="id" value="42"', note)
        self.assertIn('name="return_to" value="/recommended?type=tv"', note)
        self.assertIn(">Undo</button>", note)
        self.assertIn("Hidden &quot;X&quot;", note)

    def test_ai_page_undo_returns_to_ai(self):
        old = (config.AI_TOKEN, dict(web._ai_state))
        self.addCleanup(lambda: (setattr(config, "AI_TOKEN", old[0]), web._ai_state.update(old[1])))
        config.AI_TOKEN = "sk-x"
        web._ai_state["result"] = result()
        web._ai_state["time"] = time.time()
        self.use(None, st=status(ai_state="ready"))
        html = web.render_ai_page(undo=("tv", 3))
        self.assertIn('action="/undismiss"', html)
        self.assertIn('name="return_to" value="/ai"', html)

    def test_no_or_invalid_undo_renders_no_note(self):
        self.use(result())
        for undo in (None, ("book", 1), ("movie", "x"), ("movie",)):
            self.assertNotIn("/undismiss", web.render_recommended("all", undo=undo), undo)


class TestCardMarkup(unittest.TestCase):
    def test_hostile_values_are_escaped_in_card_and_detail_view(self):
        card = web._card(HOSTILE, "/recommended", True)
        for raw in ("<script>", "<img src=x", "<b>chip", "</p><script>", '"onerror="', "javascript:"):
            self.assertNotIn(raw, card)
        details = re.search(r'<details class="card-details">.*</details>', card).group(0)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", details)       # reason
        self.assertIn("&lt;b&gt;chip&lt;/b&gt;", details)                 # chip
        self.assertIn("&lt;/p&gt;&lt;script&gt;alert(&#x27;o&#x27;)", details)  # full overview

    def test_placeholder_poster_escapes_the_title(self):
        card = web._card(dict(HOSTILE, poster_url=None), "/recommended", False)
        self.assertIn("poster-empty", card)
        self.assertNotIn("<script>", card)
        self.assertIn("&lt;script&gt;", card)

    def test_card_key_cannot_break_out_of_its_attribute(self):
        card = web._card(dict(ITEM, media_type='"><x'), "/recommended", False)
        self.assertNotIn('"><x', card)

    def test_card_is_poster_first_with_details_reason_and_overview(self):
        card = web._card(ITEM, "/recommended", True)
        self.assertLess(card.index("card-poster"), card.index('class="title"'))
        self.assertIn('data-card="movie-7"', card)
        self.assertIn("88% match", card)
        self.assertIn(">New<", card)
        self.assertIn(">Trending<", card)
        self.assertIn("Because you liked Ronin", card)
        self.assertIn('<span class="chip">Michael Mann</span>', card)
        self.assertIn("A heist.", card)

    def test_external_link_only_for_http_urls(self):
        self.assertIn('href="https://www.themoviedb.org/movie/7"',
                      web._card(dict(ITEM, url="https://www.themoviedb.org/movie/7"), "/", False))
        self.assertNotIn("ext-link", web._card(dict(ITEM, url="javascript:alert(1)"), "/", False))


class TestEnhanceHooks(PatchedState):
    def test_action_forms_carry_data_enhance(self):
        old = (config.RADARR_URL, config.RADARR_API_KEY)
        self.addCleanup(lambda: (setattr(config, "RADARR_URL", old[0]), setattr(config, "RADARR_API_KEY", old[1])))
        config.RADARR_URL = config.RADARR_API_KEY = "x"
        card = web._card(ITEM, "/recommended?type=all", True)
        self.assertIn('action="/dismiss" data-enhance="dismiss"', card)
        self.assertIn("data-add-dialog", card)
        self.use(result())
        self.assertIn('action="/refresh" data-enhance="refresh"', web.render_recommended("all"))
        self.assertIn('action="/refresh" data-enhance="refresh"', web.render_home())

    def test_generate_form_carries_data_enhance(self):
        old = config.AI_TOKEN
        self.addCleanup(setattr, config, "AI_TOKEN", old)
        config.AI_TOKEN = "sk-x"
        self.use(None, st=status())
        self.assertIn('action="/ai/generate" data-enhance="generate"', web.render_ai_page())


class TestAddDialogPartial(unittest.TestCase):
    def setUp(self):
        old_state = dict(web._state)
        self.addCleanup(web._state.update, old_state)
        web._state["result"] = result()
        web._state["time"] = time.time()
        p = mock.patch.object(sources, "use_sample", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        old = (config.RADARR_URL, config.RADARR_API_KEY)
        self.addCleanup(lambda: (setattr(config, "RADARR_URL", old[0]), setattr(config, "RADARR_API_KEY", old[1])))
        config.RADARR_URL = config.RADARR_API_KEY = "x"

    def test_partial_has_no_page_shell(self):
        with mock.patch("radarr.RadarrClient.quality_profiles", return_value=[{"id": 1, "name": "HD"}]):
            html = web.render_add_dialog("movie", 7, "/recommended?type=all", partial=True)
        for shell_bit in ("<html", "<head", "<body", "app-sidebar", "/static/app.css"):
            self.assertNotIn(shell_bit, html)
        self.assertIn('action="/add" data-enhance="add"', html)
        self.assertIn('id="add-dialog-title"', html)
        self.assertIn("data-close", html)

    def test_full_page_still_has_the_shell(self):
        with mock.patch("radarr.RadarrClient.quality_profiles", return_value=[]):
            html = web.render_add_dialog("movie", 7, "/recommended?type=all")
        self.assertIn("<html", html)
        self.assertIn('action="/add"', html)

    def test_partial_for_unknown_item_is_a_safe_message_without_shell(self):
        html = web.render_add_dialog("movie", 999, "/recommended", partial=True)
        self.assertIn("isn't available to add", html)
        self.assertNotIn("<html", html)


class TestStaticAssets(unittest.TestCase):
    def test_shell_links_static_css_and_js_and_has_no_inline_style(self):
        html = web._shell("", "home")
        self.assertIn('<link rel="stylesheet" href="/static/app.css">', html)
        self.assertIn('<script src="/static/app.js" defer></script>', html)
        self.assertNotIn("<style>", html)
        self.assertFalse(hasattr(web, "CSS"))

    def test_shell_has_live_region_skip_link_and_current_nav(self):
        html = web._shell("", "library")
        self.assertIn('id="toasts" role="status" aria-live="polite"', html)
        self.assertIn('href="#main"', html)
        self.assertEqual(html.count('href="/library" aria-current="page"'), 2)

    def test_static_files_exist_and_use_nothing_external(self):
        css = read_static("app.css")
        js = read_static("app.js")
        self.assertGreater(len(css), 1000)
        self.assertGreater(len(js), 1000)
        for text in (css, js):
            self.assertNotRegex(text, r"https?://")
            self.assertNotIn("@import", text)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("max-width: 820px", css)

    def test_js_only_uses_innerhtml_for_the_server_dialog_fragment(self):
        js = read_static("app.js")
        self.assertEqual(len(re.findall(r"\.innerHTML\s*=", js)), 1)
        self.assertIn("body.innerHTML = html;", js)
        self.assertNotIn("insertAdjacentHTML", js)
        self.assertNotIn("document.write", js)

    def test_static_files_are_served(self):
        server = web.make_server("127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        for path, kind in (("/static/app.css", "text/css"), ("/static/app.js", "javascript")):
            with urllib.request.urlopen(base + path) as r:
                self.assertEqual(r.status, 200)
                self.assertIn(kind, r.headers.get("Content-Type"))


class TestLibraryPage(PatchedState):
    def lib(self, **kw):
        base = {"media_type": "movie", "tmdb_id": 5, "title": "Arrival", "year": 2016, "added_at": "2024-03-01T10:00:00Z",
                "watched": False, "progress": None, "poster_key": None, "url": None}
        base.update(kw)
        return base

    def wat(self, **kw):
        base = {"media_type": "movie", "tmdb_id": 5, "title": "Arrival", "year": 2016, "last_viewed": "2024-05-02T10:00:00Z",
                "view_count": 1, "poster_key": None, "poster_url": None, "url": None, "stars": None, "plex_stars": None}
        base.update(kw)
        return base

    def render(self, items, tab="all", **kw):
        self.use(result())
        with mock.patch.object(web, "library_items", return_value=items):
            return web.render_library(tab=tab, **kw)

    def test_every_tab_escapes_hostile_titles(self):
        for tab in ("all", "movie", "tv", "watched", "added"):
            maker = self.wat if tab == "watched" else self.lib
            kind = "tv" if tab == "tv" else "movie"
            html = self.render([maker(title="<script>alert(1)</script>", url="javascript:alert(1)", media_type=kind)], tab=tab)
            self.assertNotIn("<script>alert", html, tab)
            self.assertIn("&lt;script&gt;alert(1)", html, tab)
            self.assertNotIn("javascript:", html, tab)

    def test_poster_key_uses_the_proxy_and_never_leaks_thumbs(self):
        html = self.render([self.lib(poster_key=42, thumb="/library/metadata/42/thumb/1")])
        self.assertIn('src="/poster?key=42"', html)
        self.assertNotIn("/library/metadata", html)
        self.assertNotIn("X-Plex-Token", html)

    def test_poster_url_wins_over_poster_key(self):
        html = self.render([self.wat(poster_key=42, poster_url="https://img.example/p.jpg")], tab="watched")
        self.assertIn('src="https://img.example/p.jpg"', html)
        self.assertNotIn("/poster?key=", html)

    def test_no_tmdb_id_renders_a_placeholder(self):
        html = self.render([self.lib(tmdb_id=None)])
        self.assertIn("poster-empty", html)

    def test_badges_watched_and_started(self):
        html = self.render([self.lib(watched=True), self.lib(title="Half", progress=0.4)])
        self.assertIn(">Watched<", html)
        self.assertIn(">Started<", html)

    def test_subtabs_carry_only_type_and_q_and_active_is_marked(self):
        html = self.render([self.lib()], tab="movie", q="arr", sort="title", show="unwatched", page=1)
        self.assertIn('class="subtab on" href="/library?type=movie&amp;q=arr"', html)
        self.assertIn('href="/library?type=watched&amp;q=arr"', html)
        self.assertNotRegex(html, r'class="subtab[^"]*" href="[^"]*sort=')
        for label in ("All", "Movies", "TV shows", "Watched", "Added here"):
            self.assertIn(f">{label}</a>", html)

    def test_toolbar_is_a_get_form_following_the_tab(self):
        html = self.render([self.lib()], tab="all")
        self.assertIn('<form class="toolbar" method="get" action="/library" role="search">', html)
        self.assertIn('<input type="hidden" name="type" value="all">', html)
        self.assertIn('type="search" name="q"', html)
        self.assertIn('name="sort"', html)
        self.assertIn('<option value="unwatched">', html)
        watched = self.render([self.wat()], tab="watched")
        self.assertIn('<option value="rated">', watched)
        self.assertNotIn('<option value="unwatched">', watched)
        added = self.render([self.lib()], tab="added")
        self.assertNotIn('name="show"', added)

    def test_pager_keeps_params_and_is_omitted_for_one_page(self):
        items = [self.lib(tmdb_id=n, title=f"Film {n:03d}") for n in range(1, 120)]
        html = self.render(items, tab="movie", q="Film", sort="title", show="all", page=2)
        self.assertIn("Page 2 of 3", html)
        self.assertIn('href="/library?type=movie&amp;q=Film&amp;sort=title&amp;show=all"', html)   # previous = page 1
        self.assertIn('href="/library?type=movie&amp;q=Film&amp;sort=title&amp;show=all&amp;page=3"', html)
        self.assertNotIn('class="pager"', self.render([self.lib()]))

    def test_added_tab_shows_added_date_and_year_in_title(self):
        html = self.render([self.lib(title="M", year=2020)], tab="added")
        self.assertIn("M (2020)", html)
        self.assertIn("Added 2024-03-01", html)

    def test_rate_form_personal_rating(self):
        html = self.render([self.wat(stars=4, plex_stars=5)], tab="watched")
        self.assertEqual(html.count('name="stars"'), 6)  # 5 stars + Clear
        self.assertEqual(html.count('aria-pressed="true"'), 1)
        self.assertRegex(html, r'value="4" class="star on" aria-pressed="true"')
        self.assertIn("Your rating: 4/5", html)
        self.assertIn("star-clear", html)
        self.assertNotIn("from-plex", html)
        self.assertIn('data-enhance="rate"', html)
        self.assertIn('name="return_to" value="/library?type=watched&amp;sort=recent&amp;show=all"', html)

    def test_rate_form_plex_fallback_and_unrated(self):
        html = self.render([self.wat(plex_stars=5)], tab="watched")
        self.assertIn("From Plex: 5/5", html)
        self.assertIn("from-plex", html)
        self.assertNotIn('aria-pressed="true"', html)
        self.assertNotIn("star-clear", html)
        plain = self.render([self.wat()], tab="watched")
        self.assertIn("Not rated", plain)
        self.assertEqual(plain.count('name="stars"'), 5)

    def test_ratings_changed_note_and_refresh_form(self):
        with mock.patch.object(web, "ratings_changed", return_value=True):
            html = self.render([self.wat()], tab="watched")
        self.assertIn("Your ratings changed - refresh to update your recommendations.", html)
        self.assertIn('action="/refresh"', html)
        self.assertNotIn("Your ratings changed", self.render([self.wat()], tab="watched"))
        self.assertNotIn("Your ratings changed", self.render([self.lib()], tab="all"))

    def test_sample_mode_notes_that_ratings_are_not_saved(self):
        html = self.render([self.wat()], tab="watched")
        self.assertIn("Sample data - ratings aren't saved.", html)

    def test_states(self):
        self.use(None, st=status("building", has_result=False))
        html = web.render_library(tab="all")
        self.assertIn('data-poll="recs"', html)
        self.assertIn('http-equiv="refresh"', html)
        self.use(None, st=status("error", has_result=False, error="boom <b>"))
        html = web.render_library(tab="watched")
        self.assertIn("boom &lt;b&gt;", html)
        self.assertIn("Connect Plex to browse your library", self.render(None, tab="all"))
        self.assertIn('href="/settings?section=plex"', self.render(None, tab="all"))
        self.assertIn("Nothing watched yet", self.render([], tab="watched"))
        none = self.render([self.lib()], q="zzz")
        self.assertIn("Nothing matches", none)
        self.assertIn('href="/library?type=all"', none)

    def test_added_tab_needs_no_build(self):
        self.use(None, st=status("building", has_result=False))
        html = web.render_library(tab="added")
        self.assertIn("Nothing added yet", html)
        self.assertNotIn("data-poll", html)

    def test_home_added_tile_links_to_added_tab(self):
        self.use(result())
        self.assertIn('href="/library?type=added"', web.render_home())

    def test_rate_native_fallback_carries_the_star_value(self):
        js = read_static("app.js")
        rate = js[js.index("rate: function"):js.index("generate: function")]
        self.assertIn('name: "stars"', rate)
        self.assertIn("nativeRate()", rate)
        self.assertNotIn("nativeSubmit(form); return;", rate)

    def test_app_js_has_rate_handler_without_innerhtml_additions(self):
        js = read_static("app.js")
        self.assertIn("rate: function", js)
        self.assertIn("e.submitter", js)
        self.assertEqual(js.count("innerHTML"), 1)


class TestSettingsRestyle(unittest.TestCase):
    def test_settings_keeps_logic_but_uses_new_wrapper(self):
        import settings_page
        html = settings_page.render("plex")
        self.assertIn('class="settings"', html)
        self.assertIn('aria-label="Settings sections"', html)
        self.assertIn('value="test_plex"', html)
        failed = settings_page.render("plex", test_result={"ok": False, "message": "<b>nope</b>"})
        self.assertIn('class="note error"', failed)
        self.assertIn("&lt;b&gt;nope&lt;/b&gt;", failed)


if __name__ == "__main__":
    unittest.main()
