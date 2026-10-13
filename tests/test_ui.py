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
from html import escape
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["SAMPLE"] = "1"
import config
import pages
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
        html = pages.render_browse("all")
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
        html = pages.render_browse("all")
        self.assertIn("&lt;b&gt;Plex down&lt;/b&gt;", html)
        self.assertNotIn("<b>Plex down", html)
        self.assertIn("Try again", html)
        self.assertNotIn('data-poll="recs"', html)
        self.assertNotIn('http-equiv="refresh"', html)

    def test_stale_result_while_rebuilding_shows_updating_indicator_and_noscript_refresh(self):
        self.use(result(), st=status("building"))
        html = pages.render_browse("all")
        self.assertIn("Heat (1995)", html)
        self.assertIn('data-poll="stale"', html)
        self.assertIn("Updating...", html)
        self.assertIn('http-equiv="refresh"', html)

    def test_ready_result_has_no_updating_indicator_or_auto_refresh(self):
        self.use(result(), st=status("ready"))
        for html in (pages.render_browse("all"), web.render_home()):
            self.assertNotIn("Updating...", html)
            self.assertNotIn('http-equiv="refresh"', html)

    def test_ready_with_error_shows_a_calm_escaped_notice(self):
        self.use(result(), st=status("ready", error="<i>timeout</i>"))
        html = pages.render_browse("all")
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
        self.use(result([dict(ITEM, media_type="tv")]))
        html = pages.render_browse("tv", msg='Hidden "X"', undo=("movie", 42))
        note = re.search(r'<div class="note undo-note".*?</div>', html).group(0)
        self.assertIn('action="/undismiss"', note)
        self.assertIn('method="post"', note)
        self.assertIn('name="type" value="movie"', note)
        self.assertIn('name="id" value="42"', note)
        self.assertIn('name="return_to" value="/tv"', note)
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
            self.assertNotIn("/undismiss", pages.render_browse("all", undo=undo), undo)


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
        self.assertIn('action="/refresh" data-enhance="refresh"', pages.render_browse("all"))
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

    def test_js_only_uses_innerhtml_in_setfragment(self):
        js = read_static("app.js")
        self.assertEqual(len(re.findall(r"\.innerHTML\s*=", js)), 1)
        body = js[js.index("function setFragment"):]
        self.assertLess(body.index("node.innerHTML = html;"), body.index("\n  }\n"))  # inside setFragment
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
        for label in ("All", "Movies", "TV shows", "Watched", "Requests", "Lists"):
            self.assertIn(f">{label}</a>", html)
        self.assertNotIn("Added here", html)
        self.assertIn('<a class="subtab" href="/lists">Lists</a>', html)

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
        self.assertIn('name="show"', added)
        for value, label in (("all", "Everything"), ("open", "Not available yet"), ("available", "Available")):
            self.assertIn(f'<option value="{value}"{" selected" if value == "all" else ""}>{label}</option>', added)
        self.assertNotIn(">open<", added)

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

    def test_library_cards_have_a_preview_hook(self):
        for tab, maker in (("all", self.lib), ("added", self.lib)):
            html = self.render([maker()], tab=tab)
            cards = re.findall(r'<article class="card"[^>]*>', html)
            self.assertTrue(cards, tab)
            for tag in cards:
                self.assertIn("data-preview", tag)
                self.assertNotIn("data-card", tag)
        html = self.render([self.wat()], tab="watched")
        tags = re.findall(r'<article class="card"[^>]*>', html)
        self.assertTrue(tags)
        for tag in tags:
            self.assertIn("data-card=", tag)
            self.assertIn("data-preview", tag)

    def test_added_tab_needs_no_build(self):
        self.use(None, st=status("building", has_result=False))
        html = web.render_library(tab="added")
        self.assertIn("Nothing added yet", html)
        self.assertNotIn("data-poll", html)

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


def rec(n, **kw):
    base = {"media_type": "movie", "tmdb_id": 100 + n, "title": f"Film {n}", "year": 2000 + n, "match": 99 - n,
            "reason": f"Reason {n}", "matches": ["Drama"], "overview": f"Overview {n}.", "poster_url": None,
            "url": None, "genres": ["Drama", "Sci-Fi"], "because": ["Arrival"]}
    base.update(kw)
    return base


def browse_result(items, library=None, sample=False):
    res = result(items, sample=sample)
    if library is not None:
        res["library"] = library
    return res


def hero_html(html):
    return re.search(r'<section class="hero".*?</section>', html, re.S).group(0)


def set_config(test, **values):
    for name, value in values.items():
        test.addCleanup(setattr, config, name, getattr(config, name))
        setattr(config, name, value)


class TestBrowseShell(PatchedState):
    def test_top_bar_replaces_the_sidebar_on_every_page(self):
        html = web._shell("", "movies")
        self.assertIn('<header class="topbar">', html)
        self.assertIn('<a class="brand" href="/">', html)
        self.assertIn('<nav class="topnav" aria-label="Main">', html)
        self.assertIn('<div class="topbar-actions">', html)
        self.assertEqual(html.count('class="brand-mark"'), 1)
        self.assertIn('<h1 class="brand-name">Compass</h1>', html)
        self.assertIn("<title>Movies - Compass</title>", html)
        self.assertNotIn("What", html)
        self.assertNotIn("app-sidebar", html)
        self.assertNotIn("app-shell", html)
        top = re.search(r'<nav class="topnav".*?</nav>', html, re.S).group(0)
        bottom = re.search(r'<nav class="bottom-nav".*?</nav>', html, re.S).group(0)
        for nav in (top, bottom):
            self.assertEqual(re.findall(r'<span>([^<]*)</span></a>', nav), ["Home", "Movies", "TV", "Library", "AI picks"])
            self.assertNotIn("Settings", nav)
            self.assertNotIn("Recommended", nav)
        self.assertIn('href="/settings"', re.search(r'<div class="topbar-actions">.*?</div>', html, re.S).group(0))

    def test_aria_current_counts(self):
        self.assertEqual(web._shell("", "library").count('aria-current="page"'), 2)
        self.assertEqual(web._shell("", "movies").count('href="/movies" aria-current="page"'), 2)
        self.assertEqual(web._shell("", "settings").count('aria-current="page"'), 1)

    def test_body_class_only_when_cinematic(self):
        self.assertNotIn('class="cinematic"', web._shell("", "home"))
        html = web._shell("<p>x</p>", "home", "sub", show_refresh=True, cinematic=True)
        self.assertIn('<body class="cinematic">', html)
        self.assertLess(html.index("<p>x</p>"), html.index('class="top browse-top"'))
        self.assertIn('<h2 class="visually-hidden">Home</h2>', html)

    def test_plain_shell_keeps_the_title_strip_above_the_body(self):
        html = web._shell("<p>x</p>", "ai", "sub")
        self.assertLess(html.index('<div class="top">'), html.index("<p>x</p>"))
        self.assertIn("<h2>AI picks</h2>", html)

    def test_add_dialog_section_follows_return_to(self):
        for return_to, expected in (("/ai", "ai"), ("/movies", "movies"), ("/tv", "tv"), ("/library?type=all", "library"),
                                    ("/", "home"), ("/recommended", "home")):
            html = web.render_add_dialog("movie", 999, return_to)
            nav = re.search(r'<nav class="topnav".*?</nav>', html, re.S).group(0)
            self.assertEqual(re.findall(r'class="nav-item active" href="([^"]*)"', nav),
                             [dict((k, h) for k, _, h in pages.NAV_SECTIONS)[expected]], return_to)


class TestBrowseHero(PatchedState):
    def render(self, items, kind="all", **kw):
        self.use(browse_result(items), **kw)
        return pages.render_browse(kind)

    def test_ready_page_is_cinematic_with_one_visible_slide(self):
        html = self.render([rec(n) for n in range(1, 9)])
        self.assertIn('<body class="cinematic">', html)
        hero = hero_html(html)
        slides = re.findall(r'<article class="hero-slide[^"]*"[^>]*>', hero)
        self.assertEqual(len(slides), 5)
        self.assertEqual([" hidden" in s or s.endswith(" hidden>") for s in slides], [False, True, True, True, True])
        self.assertIn('class="hero-slide on"', slides[0])
        self.assertIn('aria-label="1 of 5: Film 1"', slides[0])
        self.assertIn('data-card="movie-101"', slides[0])
        self.assertIn("data-hero-slides", hero)
        self.assertNotIn("Top pick for you", hero)       # the kicker is gone
        self.assertNotIn("kicker", hero)

    def test_hero_image_fallbacks(self):
        both = hero_html(self.render([rec(1, backdrop_url="https://i/b.jpg", poster_large_url="https://i/p.jpg")]))
        self.assertIn('<img class="hero-backdrop" src="https://i/b.jpg"', both)
        self.assertIn('<img class="hero-poster" src="https://i/p.jpg"', both)
        self.assertNotIn("hero-glyph", both)
        poster_only = hero_html(self.render([rec(1, poster_url="https://i/small.jpg")]))
        self.assertNotIn("hero-backdrop", poster_only)
        self.assertIn('<img class="hero-poster" src="https://i/small.jpg"', poster_only)
        neither = hero_html(self.render([rec(1)]))
        self.assertNotIn("hero-glyph", neither)               # an image-less slide is just the tinted field
        self.assertNotIn('<img class="hero-', neither)
        self.assertIn('style="--h:', neither)

    def test_javascript_image_urls_are_dropped(self):
        hero = hero_html(self.render([rec(1, backdrop_url="javascript:alert(1)", poster_large_url="javascript:alert(2)",
                                          poster_url="javascript:alert(3)", url="javascript:alert(4)")]))
        self.assertNotIn("javascript:", hero)
        self.assertNotIn("hero-glyph", hero)
        self.assertNotIn("ext-link", hero)

    def test_hostile_values_are_escaped_everywhere(self):
        evil = "<script>alert('x')</script>"
        item = rec(1, title=evil, reason=evil, genres=[evil], certification=evil, overview=evil, matches=[evil],
                   because=[evil] * 1)
        items = [item] + [rec(n, because=[evil]) for n in range(2, 8)]
        html = self.render(items)
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;alert(", html)
        self.assertIn("Because you watched &lt;script&gt;", html)

    def test_meta_text(self):
        def meta(**kw):
            return re.search(r'<p class="hero-meta card-meta">(.*?)</p>', hero_html(self.render([rec(1, **kw)])), re.S).group(1)
        self.assertIn('<span class="runtime">2h 46m</span>', meta(runtime=166))
        self.assertIn('<span class="runtime">46m</span>', meta(runtime=46))
        self.assertIn('<span class="runtime">2h</span>', meta(runtime=120))
        self.assertIn('<span class="runtime">3 seasons</span>', meta(media_type="tv", seasons=3))
        self.assertIn('<span class="runtime">1 season</span>', meta(media_type="tv", seasons=1))
        full = meta(certification="12A", runtime=90)
        self.assertIn('<span class="match">98% match</span>', full)
        self.assertIn('<span class="badge cert">12A</span>', full)
        self.assertIn('<span class="hero-genres">Drama, Sci-Fi</span>', full)
        bare = meta()
        self.assertNotIn("cert", bare)
        self.assertNotIn("runtime", bare)

    def test_in_library_replaces_add_in_the_hero(self):
        set_config(self, RADARR_URL="x", RADARR_API_KEY="x")
        with_add = hero_html(self.render([rec(1)]))
        self.assertIn('class="btn-add"', with_add)
        self.assertIn("data-add-dialog", with_add)
        owned = hero_html(self.render([rec(1, in_library=True)]))
        with mock.patch.object(web, "owned_keys", return_value={("movie", 101)}):
            owned = hero_html(self.render([rec(1)]))
        self.assertIn('<span class="lib-tag">In library</span>', owned)
        hero_actions = re.search(r'<div class="hero-actions">(.*?)</div></div></article>', owned, re.S).group(1)
        self.assertNotIn("btn-add", hero_actions)
        self.assertIn('class="btn-ghost hero-more" href="/title/movie/101"', hero_actions)

    def test_add_only_when_configured_and_not_sample(self):
        self.assertNotIn("btn-add", self.render([rec(1)]))
        set_config(self, RADARR_URL="x", RADARR_API_KEY="x")
        html = self.render([rec(1), rec(2, media_type="tv")])
        self.assertIn('href="/add-dialog?type=movie&amp;id=101&amp;return_to=%2F"', html)
        self.assertNotIn("/add-dialog?type=tv&amp;id=102", html)        # Sonarr isn't configured
        self.use(browse_result([rec(1)], sample=True))
        self.assertNotIn("btn-add", pages.render_browse("all"))
        self.assertNotIn("/dismiss", pages.render_browse("all"))

    def test_more_info_is_a_link_to_the_title_page_and_there_is_no_details_block(self):
        hero = hero_html(self.render([rec(1, url="https://www.themoviedb.org/movie/1")]))
        self.assertIn('<a class="btn-ghost hero-more" href="/title/movie/101">More info'
                      '<span class="visually-hidden">: Film 1</span></a>', hero)
        for gone in ("<details", "hero-details", "detail-body", 'class="card-poster"', "/dismiss", "ext-link"):
            self.assertNotIn(gone, hero)

    def test_hero_tools_are_the_watchlist_toggle_only(self):
        hero = hero_html(self.render([rec(1)]))
        tools = re.search(r'<div class="card-tools hero-tools">.*?</div></div></article>', hero, re.S).group(0)
        self.assertNotIn('class="rate', hero)
        self.assertIn("list-toggle-form", tools)
        self.assertIn('<input type="hidden" name="list" value="watchlist">', tools)
        self.assertNotIn("list-link", tools)


class TestBrowseRows(PatchedState):
    def render(self, items, kind="all", library=None, **kw):
        self.use(browse_result(items, library=library), **kw)
        return pages.render_browse(kind)

    def test_row_structure_and_titles(self):
        html = self.render([rec(n) for n in range(1, 13)], library=[
            {"media_type": "movie", "tmdb_id": 900, "title": "Owned", "year": 2020, "added_at": "2024-01-01T00:00:00Z", "url": None}])
        self.assertIn('<div class="rows">', html)
        titles = re.findall(r'<h3 id="row-[^"]*">([^<]*)</h3>', html)
        self.assertEqual(titles, ["Recommended for you", "Because you watched Arrival", "Top 10 picks for you",
                                  "New in your library"])
        self.assertIn('<section class="row" data-row="recommended" aria-labelledby="row-recommended">', html)
        self.assertIn('<p class="row-sub">Based on your watch history</p>', html)
        self.assertEqual(html.count("data-track"), 4)

    def test_numbered_ranks_run_one_to_n(self):
        html = self.render([rec(n) for n in range(1, 13)])
        top = re.search(r'<section class="row" data-row="top10".*?</section>\s*(?=<section|</div>)', html, re.S).group(0)
        self.assertIn('class="track track-numbered"', top)
        self.assertEqual(re.findall(r'<span class="rank" aria-hidden="true">(\d+)</span>', top), [str(n) for n in range(1, 11)])
        rec_row = re.search(r'<section class="row" data-row="recommended".*?</section>', html, re.S).group(0)
        self.assertNotIn('class="rank"', rec_row)

    def test_row_card_actions_sit_inside_details(self):
        set_config(self, RADARR_URL="x", RADARR_API_KEY="x")
        html = self.render([rec(n) for n in range(1, 9)])
        card = re.search(r'<article class="card row-card" data-card="movie-106">.*?</article>', html, re.S).group(0)
        self.assertIn('<div class="card-poster"><a class="poster-link" href="/title/movie/106" aria-label="More info: Film 6 (2006)">', card)
        self.assertNotIn("data-open-detail", card)
        self.assertIn("93% match", card)
        self.assertIn('<h4 class="title">Film 6</h4>', card)          # the year moved to .card-facts
        self.assertIn('<span>2006</span>', card)
        details = re.search(r'<details class="card-details">.*</details>', card, re.S).group(0)
        for needle in ("card-actions", "data-add-dialog", 'data-enhance="dismiss"', "Reason 6", "Overview 6.", "card-meta"):
            self.assertIn(needle, details)
            self.assertEqual(card.count(needle), details.count(needle), needle)
        self.assertIn('name="return_to" value="/"', card)

    def test_in_library_tag_on_recommendation_cards(self):
        with mock.patch.object(web, "owned_keys", return_value={("movie", 106)}):
            html = self.render([rec(n) for n in range(1, 9)])
        card = re.search(r'<article class="card row-card" data-card="movie-106">.*?</article>', html, re.S).group(0)
        self.assertIn('<span class="lib-tag">In library</span>', card)
        other = re.search(r'<article class="card row-card" data-card="movie-107">.*?</article>', html, re.S).group(0)
        self.assertNotIn("lib-tag", other)

    def test_library_row_cards_link_to_the_title_page_and_carry_tools(self):
        lib = [{"media_type": "movie", "tmdb_id": 900, "title": "<b>Owned</b>", "year": 2020, "added_at": "2024-01-01T00:00:00Z",
                "url": "https://www.themoviedb.org/movie/900"},
               {"media_type": "tv", "tmdb_id": None, "title": "Show", "year": None, "added_at": None, "url": "javascript:x"}]
        html = self.render([rec(1)], library=lib)
        row = re.search(r'<section class="row" data-row="library_new".*?</section>', html, re.S).group(0)
        self.assertEqual(row.count('<article class="card row-card lib-card" data-preview>'), 2)
        self.assertNotIn("data-card", row)
        self.assertNotIn("<details", row)
        self.assertNotIn("data-open-detail", row)
        self.assertNotIn("javascript:", row)
        self.assertNotIn("themoviedb.org", row)                       # titles go to our page, not TMDB
        self.assertIn("&lt;b&gt;Owned&lt;/b&gt;", row)
        self.assertNotIn("lib-tag", row)                           # the row heading already says "In library"
        owned, bare = re.findall(r'<article class="card row-card lib-card" data-preview>.*?</article>', row, re.S)
        self.assertIn('<a class="poster-link" href="/title/movie/900"', owned)
        self.assertIn('<h4 class="title"><a href="/title/movie/900">', owned)
        self.assertIn('<div class="card-tools">', owned)
        self.assertIn('<form class="rate rate-compact"', owned)
        self.assertIn('class="list-link"', owned)
        for gone in ("poster-link", "card-tools", "<a ", "<form"):     # no tmdb_id: nothing to link or rate
            self.assertNotIn(gone, bare)
        self.assertIn('<p class="card-facts"><span>2020</span></p>', owned)
        self.assertNotIn("card-facts", bare)                         # no year: no facts line

    def test_movies_and_tv_pages_filter_and_return_to_their_own_path(self):
        items = [rec(n) for n in range(1, 6)] + [rec(n, media_type="tv", title=f"Show {n}") for n in range(6, 11)]
        movies = self.render(items, "movie")
        self.assertNotIn("Show 6", movies)
        self.assertIn('name="return_to" value="/movies"', movies)
        self.assertIn("<h2 class=\"visually-hidden\">Movies</h2>", movies)
        tv = self.render(items, "tv")
        self.assertNotIn("Film 1<", tv)
        self.assertIn('name="return_to" value="/tv"', tv)
        self.assertEqual(pages.render_browse("bogus").count("<h2"), pages.render_browse("all").count("<h2"))

    def test_subtitle_footer_and_refresh(self):
        res = browse_result([rec(n) for n in range(1, 4)])
        res["notes"] = ["<b>note</b>"]
        res["profile"] = {"genre": {"Drama": 1.0}, "keyword": {}, "director": {}, "actor": {}}
        self.use(res, age=300)
        html = pages.render_browse("all")
        foot = re.search(r'<div class="browse-foot">.*?</div>\s*</div>|<div class="browse-foot">.*', html, re.S).group(0)
        self.assertIn("&lt;b&gt;note&lt;/b&gt;", foot)
        self.assertIn('class="taste"', foot)
        self.assertIn("Based on 12 watched titles - updated 5 min ago", html)
        self.assertIn('action="/refresh" data-enhance="refresh"', html)
        self.assertIn('name="return_to" value="/"', html)

    def test_ai_generation_is_never_started(self):
        self.use(browse_result([rec(1)]))
        with mock.patch.object(web, "start_ai_generation") as start:
            for kind in ("all", "movie", "tv"):
                pages.render_browse(kind)
        start.assert_not_called()


class TestBrowseRate(PatchedState):
    def test_rate_form_in_row_cards_posts_to_rate_and_not_in_hero(self):
        self.use(browse_result([rec(n) for n in range(1, 9)]))
        html = pages.render_browse("movie")
        card = re.search(r'<article class="card row-card" data-card="movie-106">.*?</article>', html, re.S).group(0)
        details = re.search(r'<details class="card-details">.*</details>', card, re.S).group(0)
        form = re.search(r'<form class="rate".*?</form>', details, re.S).group(0)
        self.assertIn('action="/rate" data-enhance="rate"', form)
        self.assertIn('name="type" value="movie"', form)
        self.assertIn('name="id" value="106"', form)
        self.assertIn('name="return_to" value="/movies"', form)
        self.assertEqual(len(re.findall(r'name="stars" value="[1-5]"', form)), 5)
        self.assertEqual(form.count('aria-pressed="false"'), 5)
        self.assertIn('aria-label="Rate 3 out of 5"', form)
        self.assertNotIn('value="0"', form)
        self.assertNotIn('data-enhance="rate"', hero_html(html))

    def test_rate_form_escapes_title_and_shows_in_sample_mode(self):
        self.use(browse_result([rec(1, title='"><script>x</script>')], sample=True))
        html = pages.render_browse("all")
        self.assertNotIn("<script>x", html)
        self.assertIn('data-enhance="rate"', html)
        self.assertNotIn("/dismiss", html)

    def test_saved_rating_is_shown(self):
        self.use(browse_result([rec(1)]))
        with mock.patch.object(web, "_is_sample", return_value=False), \
                mock.patch.object(db, "ratings", return_value={("movie", 101): 4}):
            html = pages.render_browse("all")
        form = re.search(r'<form class="rate[^"]*" (?:(?!</form>).)*?name="id" value="101".*?</form>', html, re.S).group(0)
        self.assertRegex(form, r'value="4" class="star on" aria-pressed="true"')
        self.assertEqual(form.count('aria-pressed="true"'), 1)
        self.assertEqual(form.count('class="star on"'), 4)
        self.assertIn("Your rating: 4/5", form)
        self.assertIn('name="stars" value="0" class="link-btn star-clear">Clear rating', form)

    def test_unrated_and_bad_star_values_render_not_rated(self):
        for value in (None, 0, 9, "x"):
            self.use(browse_result([rec(1)]))
            with mock.patch.object(web, "_is_sample", return_value=False), \
                    mock.patch.object(db, "ratings", return_value={("movie", 101): value}):
                html = pages.render_browse("all")
            form = re.search(r'<form class="rate[^"]*" (?:(?!</form>).)*?name="id" value="101".*?</form>', html, re.S).group(0)
            self.assertIn("Not rated", form, value)
            self.assertNotIn("star-clear", form)
            self.assertNotIn('aria-pressed="true"', form)

    def test_js_preview_icons_poster_click_and_focus_rules(self):
        js = read_static("app.js")
        body = js[js.index("function openPreview"):]
        for needle in ('title: "More info"', '"Not interested"', "setIcon(more, \"chevron\")", "a.poster-link", "cloneListTools"):
            self.assertIn(needle, body + js[js.index("function cloneListTools"):])
        self.assertNotIn("openDetail", js)
        self.assertNotIn("closeFallback", js)
        self.assertIn("main .row:not([hidden])", js)
        self.assertIn("override", js)
        self.assertIn("inSlide(e.target)", js)
        self.assertIn("lastPreviewCard", js)

    def test_js_preview_hover_intent_icons_and_anchoring(self):
        js = read_static("app.js")
        start = js.index("Hover preview: one shared element")
        section = js[start:js.index("Cinematic top bar")]
        self.assertEqual(js.count("innerHTML"), 1)
        self.assertIn("createElementNS", section)
        for path in ("M12 5v14M5 12h14", "M6 6l12 12M18 6L6 18", "M6 9l6 6 6-6"):
            self.assertIn(path, section)
        for glyph in ("\\uFF0B", "\\u2715", "\\u2304", "\uFF0B", "\u2715", "\u2304"):
            self.assertNotIn(glyph, section)
        for needle in ("preview-icon", "createDocumentFragment"):
            self.assertIn(needle, section)
        for const in ("PREVIEW_OPEN_MS = 350", "PREVIEW_OPEN_MAX_MS = 700", "PREVIEW_MOVE_PX = 5",
                      "PREVIEW_SWITCH_MS = 120", "PREVIEW_CLOSE_MS = 150"):
            self.assertIn(const, section)
        self.assertIn("rect.top)", section)
        self.assertRegex(section, r"mousemove")

    def test_js_preview_covers_library_cards(self):
        js = read_static("app.js")
        self.assertIn('".row-card[data-card], [data-preview]"', js)
        self.assertEqual(js.count("closest(PREVIEW_CARD)"), 2)
        section = js[js.index("Hover preview: one shared element"):js.index("Cinematic top bar")]
        for needle in ("is-library", "preview-open", "preview-title", "preview-sub", "preview-badges",
                       "preview-sources", 'previewIcon("open")', "M14 4h6v6M20 4l-9 9"):
            self.assertIn(needle, section)
        self.assertEqual(js.count("innerHTML"), 1)

    def test_js_preview_rate_order_and_handler(self):
        js = read_static("app.js")
        body = js[js.index("function openPreview"):]
        self.assertLess(body.index("form[data-enhance=dismiss]"), body.index("form[data-enhance=rate]"))
        self.assertLess(body.index("form[data-enhance=rate]"), body.index('title: "More info"'))
        self.assertIn("applyRating(other", js)


class TestBrowseStates(PatchedState):
    def test_notes_above_the_hero_and_undo_return_to(self):
        self.use(browse_result([rec(1)]))
        html = pages.render_browse("movie", msg='Hidden "X"', undo=("movie", 42))
        note = re.search(r'<div class="note undo-note".*?</div>', html).group(0)
        self.assertIn('name="return_to" value="/movies"', note)
        self.assertIn('name="id" value="42"', note)
        self.assertLess(html.index("browse-messages"), html.index('class="hero"'))
        plain = pages.render_browse("all", msg="Added <b>")
        self.assertIn('<p class="note" role="status">Added &lt;b&gt;</p>', plain)
        self.assertLess(plain.index("Added &lt;b&gt;"), plain.index('class="hero"'))

    def test_building_error_and_empty_states_for_each_kind(self):
        for kind in ("all", "movie", "tv"):
            self.use(None, st=status("building", has_result=False))
            html = pages.render_browse(kind)
            self.assertIn("Finding your recommendations...", html)
            self.assertIn('<noscript><meta http-equiv="refresh" content="5"></noscript>', html)
            self.assertNotIn('class="cinematic"', html)
            self.use(None, st=status("error", has_result=False, error="<b>boom</b>"))
            html = pages.render_browse(kind)
            self.assertIn("&lt;b&gt;boom&lt;/b&gt;", html)
            self.assertIn("Try again", html)
            self.assertNotIn('class="cinematic"', html)
            self.assertNotIn("http-equiv", html)
            self.use(browse_result([rec(1, media_type="tv" if kind == "movie" else "movie")]))
            html = pages.render_browse(kind) if kind != "all" else None
            if html is not None:
                self.assertIn("Nothing to recommend here yet", html)
                self.assertIn('action="/refresh"', html)
                self.assertNotIn('class="cinematic"', html)
                self.assertNotIn('class="hero"', html)

    def test_empty_all_page(self):
        self.use(browse_result([]))
        html = pages.render_browse("all")
        self.assertIn("Nothing to recommend here yet", html)
        self.assertNotIn('class="cinematic"', html)

    def test_render_raising_shows_error_screen(self):
        with mock.patch.object(web, "get_result_nowait", side_effect=RuntimeError("<i>x</i>")):
            html = pages.render_browse("tv")
        self.assertIn("&lt;i&gt;x&lt;/i&gt;", html)

    def test_stale_building_shows_updating_and_refresh(self):
        self.use(browse_result([rec(1)]), st=status("building"))
        html = pages.render_browse("all")
        self.assertIn("Updating...", html)
        self.assertIn('data-poll="stale"', html)
        self.assertIn('http-equiv="refresh"', html)

    def test_render_home_is_the_all_browse_page(self):
        self.use(browse_result([rec(1)]))
        self.assertEqual(web.render_home(), pages.render_browse("all"))
        self.assertIn("Added", web.render_home(msg="Added"))

    def test_ready_page_without_result_extras_still_renders(self):
        self.use({"sample": True, "items": [rec(1)], "profile": EMPTY_PROFILE, "notes": [], "watched_count": 1})
        self.assertIn('class="hero"', pages.render_browse("all"))


class TestBrowseAssets(unittest.TestCase):
    def test_css_has_the_cinematic_classes(self):
        css = read_static("app.css")
        for needle in (".hero", ".track", ".topbar", ".preview", "html.has-dialog", "prefers-reduced-motion", "max-width: 820px"):
            self.assertIn(needle, css)

    def test_js_wires_the_cinematic_hooks(self):
        js = read_static("app.js")
        for needle in ("has-dialog", "findCards", "data-hero", "data-track", "hero-dots", "hero-pause", "track-arrow",
                       "preview-actions", "is-scrolled", "syncHero", "hero-backdrop", "role", "(min-width: 821px)",
                       "(hover: hover) and (pointer: fine)"):
            self.assertIn(needle, js)
        self.assertNotIn("function findCard(", js)
        self.assertEqual(js.count("innerHTML"), 1)

    def test_pages_module_has_no_innerhtml_or_external_urls(self):
        with open(os.path.join(ROOT, "pages.py"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("innerHTML", text)
        self.assertNotRegex(text, r'(src|href)="https?://')


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


BRANDS = re.compile(r"(?i)netflix|hulu|disney|amazon|prime video|apple ?tv|hbo")


def _strip_css(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _blocks(css):
    """Every rule as (selector, body, media): braces matched, at-rule bodies flattened, media = the
    enclosing @media prelude ("" at top level). @font-face and @keyframes come through as ordinary rules."""
    out = []
    i = 0
    while i < len(css):
        j = css.find("{", i)
        if j < 0:
            break
        selector = " ".join(css[i:j].split())
        depth, k = 1, j + 1
        while k < len(css) and depth:
            depth += {"{": 1, "}": -1}.get(css[k], 0)
            k += 1
        body = css[j + 1:k - 1]
        if selector.startswith("@media"):
            for sel, b, _ in _blocks(body):
                out.append((sel, b, selector))
        else:
            out.append((selector, body, ""))
        i = k
    return out


class TestThemeShell(unittest.TestCase):
    def render(self, key):
        with mock.patch.object(web, "current_theme", return_value=key):
            return pages.render_appearance()

    def test_data_theme_and_metas(self):
        html = self.render("ocean")
        self.assertIn('<html lang="en" data-theme="ocean">', html)
        self.assertIn('<meta name="theme-color" content="#0d1220">', html)
        self.assertEqual(html.count('name="theme-color"'), 1)
        self.assertNotIn("prefers-color-scheme", html)
        self.assertIn('<meta name="color-scheme" content="dark">', html)

    def test_unknown_and_unpatched_render_crimson(self):
        self.assertIn('data-theme="crimson"', self.render("<script>"))
        self.assertIn('data-theme="crimson"', pages.render_appearance())

    def test_logo_follows_theme(self):
        from urllib.parse import unquote
        html = self.render("ocean")
        mark = re.search(r'<a class="brand" href="/"><svg [^>]*class="brand-mark"[^>]*>.*?</svg>', html, re.S)
        self.assertIsNotNone(mark)
        self.assertIn('aria-hidden="true"', mark.group(0))
        self.assertIn('stroke="#2f8cff"', mark.group(0))
        self.assertIn('fill="#0d1220"', mark.group(0))
        icons = re.findall(r'<link rel="icon" type="image/svg\+xml" href="data:image/svg\+xml,([^"]+)">', html)
        self.assertEqual(len(icons), 1)
        self.assertNotIn("<", icons[0])
        self.assertIn('stroke="#2f8cff"', unquote(icons[0]))
        self.assertIn('stroke="#e50914"', self.render("crimson"))

    def test_logo_every_theme(self):
        import themes
        for t in themes.THEMES:
            html = self.render(t["key"])
            mark = re.search(r'<svg [^>]*class="brand-mark"[^>]*>', html).group(0)
            self.assertIn(f'data-fg="{t["logo"]}" data-bg="{t["bg"]}"', mark, t["key"])
            self.assertIn(f'stroke="{t["logo"]}"', html, t["key"])

    def test_theme_options_carry_logo_and_favicon(self):
        import themes
        html = self.render("crimson")
        for t in themes.THEMES:
            option = re.search(r'<label class="theme-option" data-theme="%s"[^>]*>' % t["key"], html).group(0)
            self.assertIn(f'data-logo="{t["logo"]}"', option)
            self.assertIn(f'data-favicon="{pages._favicon_href(t)}"', option)


class TestAppearancePage(unittest.TestCase):
    def render(self, key="crimson", msg=""):
        with mock.patch.object(web, "current_theme", return_value=key):
            return pages.render_appearance(msg)

    def test_options_in_registry_order(self):
        import themes
        html = self.render()
        keys = re.findall(r'<label class="theme-option" data-theme="(\w+)" data-theme-color="(#\w+)"', html)
        self.assertEqual([k[0] for k in keys], [t["key"] for t in themes.THEMES])
        self.assertEqual([k[1] for k in keys], [t["bg"] for t in themes.THEMES])
        self.assertNotIn("data-meta-", html)
        self.assertNotIn("light", re.search(r'<p class="muted theme-help">(.*?)</p>', html).group(1).lower())

    def test_checked_and_current_only_on_current_theme(self):
        html = self.render("lime")
        self.assertEqual(html.count(" checked"), 1)
        self.assertEqual(html.count('class="theme-current"'), 1)
        self.assertIn('value="lime" checked>', html)
        option = re.search(r'<label class="theme-option" data-theme="lime".*?</label>', html, re.S).group(0)
        self.assertIn('class="theme-current"', option)
        self.assertIn('<input class="theme-radio" type="radio"', option)

    def test_form_contract(self):
        html = self.render()
        self.assertIn('method="post" action="/theme" data-enhance="theme"', html)
        self.assertIn('<input type="hidden" name="return_to" value="/appearance">', html)
        self.assertIn('class="btn-add theme-submit"', html)
        self.assertIn("<legend>Colour theme</legend>", html)

    def test_message_escaped(self):
        html = self.render(msg="<b>x</b>")
        self.assertIn('<p class="note" role="status">&lt;b&gt;x&lt;/b&gt;</p>', html)
        self.assertNotIn("<b>x</b>", html)

    def test_nav_and_subtabs(self):
        html = self.render()
        self.assertRegex(html, r'<a class="nav-item active" href="/settings" aria-current="page"')
        self.assertIn('<a class="subtab on" href="/appearance">Appearance</a>', html)
        self.assertEqual(html.count('class="subtab on"'), 1)

    def test_no_inline_style(self):
        self.assertNotIn("style=", self.render())


class TestThemeCss(unittest.TestCase):
    """Dark only: one block per theme, declaring exactly themes.TOKENS (12 colours, #rrggbb)."""

    @classmethod
    def setUpClass(cls):
        import themes
        cls.themes = themes
        cls.raw = read_static("app.css")
        cls.css = _strip_css(cls.raw)
        cls.blocks = _blocks(cls.css)

    def decls(self, body):
        return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", body))

    def find(self, key):
        sel = ':root, [data-theme="crimson"]' if key == "crimson" else f'[data-theme="{key}"]'
        return [b for b in self.blocks if b[0] == sel]

    def test_default_is_crimson(self):
        self.assertEqual(self.themes.DEFAULT, "crimson")

    def test_one_block_per_theme_with_exact_tokens(self):
        self.assertEqual(len(self.themes.TOKENS), 12)
        for t in self.themes.THEMES:
            found = self.find(t["key"])
            self.assertEqual(len(found), 1, t["key"])
            self.assertEqual(found[0][2], "", t["key"])                       # not inside a media query
            d = self.decls(found[0][1])
            self.assertEqual(sorted(d), sorted(self.themes.TOKENS), t["key"])
            for token, value in d.items():
                self.assertRegex(value.strip(), r"^#[0-9a-fA-F]{6}$", (t["key"], token))

    def test_no_other_theme_blocks_or_keys(self):
        keys = set(re.findall(r'data-theme="([^"]*)"', self.css))
        self.assertLessEqual(keys, set(self.themes.BY_KEY))
        theme_blocks = [b for b in self.blocks if re.fullmatch(r'(:root, )?\[data-theme="[a-z]+"\]', b[0])]
        self.assertEqual(len(theme_blocks), len(self.themes.THEMES))

    def test_bg_matches_registry(self):
        for t in self.themes.THEMES:
            self.assertEqual(self.decls(self.find(t["key"])[0][1])["--bg"].strip(), t["bg"], t["key"])

    def test_dark_only(self):
        self.assertNotIn("prefers-color-scheme", self.css)
        self.assertRegex(self.css, r"color-scheme\s*:\s*dark\s*;")
        self.assertNotRegex(self.css, r"color-scheme\s*:\s*dark\s+light")
        self.assertNotRegex(self.css, r"color-scheme\s*:\s*light")

    def test_theme_accent_hexes_only_in_custom_properties(self):
        hexes = set()
        for t in self.themes.THEMES:
            d = self.decls(self.find(t["key"])[0][1])
            hexes |= {d["--accent"].strip().lower(), d["--accent-hover"].strip().lower()}
        for line in self.css.splitlines():
            if any(h in line.lower() for h in hexes):
                self.assertRegex(line, r"^\s*--[a-z0-9-]+:", line)

    def test_no_brand_names(self):
        self.assertIsNone(BRANDS.search(self.raw))


class TestThemeJs(unittest.TestCase):
    def test_hooks(self):
        js = read_static("app.js")
        for needle in ("data-enhance=theme", "pageshow", "compass_theme", "theme-color",
                       "data-logo", "data-favicon", "data-fg", "link[rel=icon]"):
            self.assertIn(needle, js)
        self.assertEqual(js.count("innerHTML"), 1)
        self.assertNotRegex(js, r"https?://")

    def test_stale_responses_and_pagehide(self):
        js = read_static("app.js")
        for needle in ("pickSeq", "markCurrent", "stale()", "pagehide", "keepalive"):
            self.assertIn(needle, js)

    def test_pages_py_has_no_nested_same_quote_fstrings(self):
        with open(os.path.join(ROOT, "pages.py"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("f'{'", text)
        self.assertNotIn('f"{"', text)


if __name__ == "__main__":
    unittest.main()


def sresult(**kw):
    base = {"media_type": "movie", "tmdb_id": 11, "title": "Dune", "year": 2021, "release_date": "2021-10-22",
            "overview": "Sand.", "poster_url": None, "url": "https://www.themoviedb.org/movie/11",
            "vote_average": 8.04, "vote_count": 100, "status": "none", "in_library": False, "sources": [],
            "arr_state": None, "episodes": None, "watched": False, "dismissed": False}
    base.update(kw)
    return base


def sview(state="ok", results=None, q="dune", kind="all", **kw):
    base = {"q": q, "kind": kind, "state": state, "message": None,
            "results": results if results is not None else ([sresult()] if state == "ok" else []),
            "capped": False, "library_known": True, "sample": False}
    base.update(kw)
    return base


class SearchBase(PatchedState):
    def setUp(self):
        super().setUp()
        self.old_cfg = (config.RADARR_URL, config.RADARR_API_KEY, config.SONARR_URL, config.SONARR_API_KEY)
        self.addCleanup(lambda: (setattr(config, "RADARR_URL", self.old_cfg[0]), setattr(config, "RADARR_API_KEY", self.old_cfg[1]),
                                 setattr(config, "SONARR_URL", self.old_cfg[2]), setattr(config, "SONARR_API_KEY", self.old_cfg[3])))
        config.RADARR_URL = config.RADARR_API_KEY = config.SONARR_URL = config.SONARR_API_KEY = "x"

    def page(self, view, q="dune", kind="all", msg=""):
        with mock.patch.object(web, "search_view", return_value=view):
            return pages.render_search(q, kind, msg)

    def frag(self, view, q="dune", kind="all"):
        with mock.patch.object(web, "search_view", return_value=view):
            return pages.render_search_results(q, kind)


class TestTopbarSearch(PatchedState):
    def test_form_and_link_on_ordinary_pages(self):
        for html in (pages._shell("", "home"), pages._shell("", "library"), pages._shell("", "settings")):
            self.assertIn('<form class="topbar-search" method="get" action="/search" role="search">', html)
            self.assertIn('name="q" maxlength="100"', html)
            self.assertIn('class="nav-item search-link" href="/search">', html)
            self.assertNotIn("data-search-live", html)   # the top-bar box never searches live
        self.assertNotIn('class="search-link"', pages._shell("", "home"))

    def test_search_page_has_no_topbar_form_and_marks_the_link_current(self):
        html = pages._shell("", "search")
        self.assertNotIn("topbar-search", html)
        self.assertIn('class="nav-item search-link active" href="/search" aria-current="page"', html)
        self.assertIn("<h2>Search</h2>", html)
        self.assertEqual(html.count('aria-current="page"'), 1)   # no bottom-nav item is active


class TestSearchFragment(SearchBase):
    def test_every_state_renders_with_state_and_announce(self):
        cases = [(sview("empty", q=""), "empty", "", "search-hint"),
                 (sview("short", q="x"), "short", "Type at least 2 characters", "Type at least 2 characters."),
                 (sview("error", q="dune", message=web.SEARCH_ERROR), "error", web.SEARCH_ERROR, 'class="note error"'),
                 (sview("limited", q="dune", message=web.SEARCH_LIMITED), "limited", web.SEARCH_LIMITED, 'class="note error"'),
                 (sview("ok", [], q="zz"), "ok", 'No matches for "zz"', "No matches for &ldquo;zz&rdquo;"),
                 (sview("ok", [sresult(), sresult(tmdb_id=12)]), "ok", '2 matches for "dune"', "2 matches for &ldquo;dune&rdquo;")]
        for view, state, announce, bit in cases:
            html = self.frag(view, q=view["q"])
            self.assertTrue(html.startswith('<div class="search-fragment" data-search-fragment '), state)
            self.assertIn(f'data-search-state="{state}"', html)
            self.assertIn('data-announce="' + escape(announce, quote=True) + '"', html)
            self.assertIn(bit, html)

    def test_one_match_is_singular_and_unknown_state_is_empty(self):
        self.assertIn('data-announce="1 match for &quot;dune&quot;"', self.frag(sview()))
        self.assertIn('data-search-state="empty"', self.frag(sview("bogus")))

    def test_fragment_has_no_shell_and_no_live_roles(self):
        for view in (sview(), sview("short", q="x"), sview("error", message="m"), sview("ok", [])):
            html = self.frag(view)
            for bit in ("<html", "topbar", "search-form", 'role="status"', 'role="alert"', "<body"):
                self.assertNotIn(bit, html)

    def test_full_page_embeds_exactly_the_fragment(self):
        for view in (sview(), sview("short", q="x"), sview("ok", []), sview("limited", message="m")):
            self.assertIn(self.frag(view, q=view["q"]), self.page(view, q=view["q"]))

    def test_capped_and_library_loading_notes(self):
        html = self.frag(sview(capped=True, library_known=False))
        self.assertIn("showing the top 20", html)
        self.assertIn('some "In library" labels may be missing', html)
        plain = self.frag(sview())
        self.assertNotIn("showing the top 20", plain)
        self.assertNotIn("still loading", plain)

    def test_hostile_q_and_titles_are_escaped_everywhere(self):
        q = '<script>alert("q")</script>'
        hostile = sresult(title="<script>alert('t')</script>", overview="</p><script>x</script>",
                          poster_url="javascript:alert(1)", url="javascript:alert(1)")
        for view in (sview("ok", [hostile], q=q), sview("ok", [], q=q)):
            for html in (self.frag(view, q=q), self.page(view, q=q)):
                self.assertNotIn("<script>", html)
                self.assertNotIn("javascript:", html)
                self.assertIn("&lt;script&gt;", html)
        self.assertIn('data-announce="No matches for &quot;&lt;script&gt;', self.frag(sview("ok", [], q=q), q=q))
        self.assertNotIn('value="<', self.page(sview("empty", q=q), q=q))

    def test_add_links_return_to_the_search_url_without_partial(self):
        html = self.frag(sview(q="blade runner", kind="movie"), q="blade runner", kind="movie")
        self.assertIn("return_to=%2Fsearch%3Fq%3Dblade%2Brunner%26type%3Dmovie", html)
        self.assertNotIn("partial", html)
        self.assertEqual(pages._search_url("a b", "tv"), "/search?q=a+b&type=tv")
        self.assertEqual(pages._search_url("", "all"), "/search?type=all")


class TestSearchCard(SearchBase):
    def card(self, **kw):
        return pages._search_card(sresult(**kw), "/search?q=dune&type=all", True)

    def test_status_tags_per_status_and_none(self):
        labels = {"plex": "In library", "radarr": "In Radarr", "sonarr": "In Sonarr", "added": "Added"}
        for status, label in labels.items():
            html = self.card(status=status)
            self.assertIn(f'<span class="lib-tag status-tag status-{status}">{label}</span>', html)
        self.assertNotIn("status-tag", self.card(status="none"))

    def test_add_only_when_untracked_configured_and_not_sample(self):
        item = sresult()
        self.assertIn("data-add-dialog", pages._search_card(item, "/search", True))
        self.assertNotIn("data-add-dialog", pages._search_card(item, "/search", False))   # sample mode
        for status in ("plex", "radarr", "sonarr", "added"):
            self.assertNotIn("data-add-dialog", pages._search_card(sresult(status=status), "/search", True))
        config.RADARR_URL = config.RADARR_API_KEY = ""
        self.assertNotIn("data-add-dialog", pages._search_card(item, "/search", True))
        self.assertIn("data-add-dialog", pages._search_card(sresult(media_type="tv"), "/search", True))

    def test_keep_on_add_and_card_key(self):
        html = self.card()
        self.assertIn('data-card="movie-11" data-keep-on-add', html)
        self.assertIn('<a class="poster-link" href="/title/movie/11"', html)
        self.assertNotIn("data-open-detail", html)

    def test_undismiss_form_only_when_dismissed(self):
        self.assertNotIn("/undismiss", self.card())
        html = self.card(dismissed=True)
        self.assertIn('action="/undismiss" data-enhance="undismiss"', html)
        self.assertIn("Not interested", html)
        self.assertNotIn("/undismiss", pages._search_card(sresult(dismissed=True), "/search", False))

    def test_arr_line_watched_and_meta(self):
        html = self.card(status="sonarr", arr_state="partial", episodes={"have": 5, "total": 10}, watched=True,
                         media_type="tv")
        self.assertIn('<p class="card-sub arr-line">5/10 episodes</p>', html)
        self.assertIn('<span class="badge">Watched</span>', html)
        self.assertIn("<span>TMDB 8.0</span>", html)
        self.assertIn("Sand.", html)
        self.assertIn("More on TMDB", html)
        for state, label in (("missing", "Missing"), ("upcoming", "Upcoming"), ("downloaded", "Downloaded"),
                             ("unmonitored", "Unmonitored")):
            self.assertIn(f'arr-line">{label}<', self.card(arr_state=state))
        self.assertIn(">Partly downloaded<", self.card(arr_state="partial"))
        self.assertNotIn("arr-line", self.card())

    def test_missing_overview_and_poster_placeholder(self):
        html = self.card(overview="", poster_url=None)
        self.assertIn("No overview available.", html)
        self.assertIn("poster-empty", html)


class TestSearchPage(SearchBase):
    def test_live_markup_contract(self):
        html = self.page(sview(), q="dune")
        self.assertRegex(html, r'<form class="search-form" method="get" action="/search" role="search" data-search-live>')
        self.assertIn('id="search-results" data-search-results aria-busy="false"', html)
        self.assertRegex(html, r'<p class="search-status visually-hidden" role="status" aria-live="polite" data-search-status></p>')
        self.assertIn('data-search-spinner aria-hidden="true" hidden>', html)
        self.assertIn('aria-controls="search-results"', html)
        self.assertIn('minlength="2" required', html)
        self.assertIn('<h2>Search</h2>', html)
        self.assertNotIn("topbar-search", html)

    def test_autofocus_only_without_a_query(self):
        self.assertIn(" autofocus", self.page(sview("empty", q=""), q=""))
        self.assertNotIn("autofocus", self.page(sview(), q="dune"))

    def test_type_pills_follow_kind_and_value_is_escaped(self):
        html = self.page(sview(kind="tv", q='a"b'), q='a"b', kind="tv")
        self.assertIn('<input type="radio" name="type" value="tv" checked>', html)
        self.assertEqual(html.count(" checked>"), 1)
        self.assertIn('value="a&quot;b"', html)

    def test_sample_note_message_and_no_add_in_sample(self):
        html = self.page(sview(sample=True), msg="Added <b>")
        self.assertIn("Sample data - searching the built-in sample catalogue.", html)
        self.assertNotIn("data-add-dialog", html)
        self.assertIn("Added &lt;b&gt;", html)
        self.assertNotIn("Sample data", self.page(sview()))
        self.assertIn("data-add-dialog", self.page(sview()))

    def test_search_view_called_once(self):
        with mock.patch.object(web, "search_view", return_value=sview()) as m:
            pages.render_search("dune", "all")
        self.assertEqual(m.call_count, 1)
        with mock.patch.object(web, "search_view", return_value=sview()) as m:
            pages.render_search_results("dune", "all")
        self.assertEqual(m.call_count, 1)

    def test_real_sample_view_renders(self):
        html = pages.render_search("dune", "all")      # web.search_view in sample mode: no requests
        self.assertIn("Dune", html)
        self.assertIn("In library", html)
        self.assertNotIn("Add to library", html)


class TestAddDialogLookup(unittest.TestCase):
    def test_uses_lookup_item_and_maps_search_section(self):
        old = (config.RADARR_URL, config.RADARR_API_KEY)
        self.addCleanup(lambda: (setattr(config, "RADARR_URL", old[0]), setattr(config, "RADARR_API_KEY", old[1])))
        config.RADARR_URL = config.RADARR_API_KEY = "x"
        found = dict(ITEM, tmdb_id=99, title="Found <b>")
        with mock.patch.object(sources, "use_sample", return_value=False), \
                mock.patch.object(web, "lookup_item", return_value=found) as lookup, \
                mock.patch("radarr.RadarrClient.quality_profiles", return_value=[]):
            full = pages.render_add_dialog("movie", 99, "/search?q=dune&type=all")
            part = pages.render_add_dialog("movie", 99, "/search?q=dune&type=all", partial=True)
        lookup.assert_called_with("movie", 99)
        self.assertIn("Found &lt;b&gt;", part)
        self.assertIn('class="nav-item search-link active"', full)
        self.assertIn("<h2>Search</h2>", full)
        with mock.patch.object(sources, "use_sample", return_value=False), mock.patch.object(web, "lookup_item", return_value=None):
            self.assertIn("isn't available to add", pages.render_add_dialog("movie", 5, "/search", partial=True))


class TestLibraryArr(PatchedState):
    def lib(self, **kw):
        base = {"media_type": "movie", "tmdb_id": 5, "title": "Arrival", "year": 2016, "added_at": "2024-03-01T10:00:00Z",
                "watched": False, "progress": None, "poster_key": None, "url": None, "sources": ["plex"],
                "arr_state": None, "episodes": None}
        base.update(kw)
        return base

    def render(self, items, arr=None, library=True, tab="all", **kw):
        res = result()
        if library:
            res["library"] = []
        if arr:
            res["arr"] = arr
        self.use(res)
        with mock.patch.object(web, "library_items", return_value=items):
            return pages.render_library(tab=tab, **kw)

    def arr(self, radarr="ok", sonarr="off"):
        return {"items": [], "radarr": {"state": radarr, "count": 0}, "sonarr": {"state": sonarr, "count": 0}}

    def test_source_badges_and_state_badges(self):
        html = self.render([self.lib(sources=["plex", "radarr"], arr_state="downloaded"),
                            self.lib(tmdb_id=6, title="Tiny", sources=["radarr"], arr_state="missing"),
                            self.lib(tmdb_id=7, title="Show", media_type="tv", sources=["sonarr"], arr_state="partial",
                                     episodes={"have": 3, "total": 10})], arr=self.arr("ok", "ok"))
        self.assertIn('<p class="card-sources"><span class="visually-hidden">In </span>'
                      '<span class="src src-plex">Plex</span> <span class="src src-radarr">Radarr</span></p>', html)
        self.assertNotIn(">Downloaded<", html)                      # downloaded and also in Plex: noise
        self.assertIn('<span class="badge arr-state state-missing">Missing</span>', html)
        self.assertIn('<span class="badge arr-state state-partial">3/10 episodes</span>', html)
        self.assertIn(">Downloaded<", self.render([self.lib(sources=["radarr"], arr_state="downloaded")], arr=self.arr()))

    def test_source_select_only_when_an_arr_service_is_on(self):
        on = self.render([self.lib()], arr=self.arr("ok"))
        self.assertIn('<select name="source">', on)
        for label in ("Everywhere", "In Plex", "In Radarr or Sonarr", "Wanted (not downloaded)"):
            self.assertIn(f">{label}</option>", on)
        self.assertIn(">In Radarr</option>", self.render([self.lib()], arr=self.arr("ok"), tab="movie"))
        self.assertIn(">In Sonarr</option>", self.render([self.lib(media_type="tv")], arr=self.arr("ok"), tab="tv"))
        self.assertNotIn('name="source"', self.render([self.lib()], arr=self.arr("off", "off")))
        self.assertNotIn('name="source"', self.render([self.lib()]))
        self.assertNotIn('name="source"', self.render([self.lib()], arr=self.arr("ok"), tab="watched"))

    def test_source_selected_and_kept_in_pager_and_return_to(self):
        items = [self.lib(tmdb_id=n, title=f"Film {n:03d}", sources=["radarr"]) for n in range(1, 120)]
        with mock.patch.object(web, "list_view", wraps=web.list_view) as lv:
            html = self.render(items, arr=self.arr("ok"), tab="movie", source="arr", sort="title")
        self.assertEqual(lv.call_args.kwargs["source"], "arr")
        self.assertIn('<option value="arr" selected>', html)
        self.assertIn("source=arr&amp;page=2", html)
        self.assertIn("source=arr", pages._library_url("movie", "", "title", "all", 1, "arr"))
        self.assertNotIn("source=", pages._library_url("movie", "", "title", "all", 1, "all"))
        self.assertNotIn("source=", self.render([self.lib()], arr=self.arr("ok"), source="bogus"))
        self.assertNotRegex(self.render([self.lib()], arr=self.arr("ok"), source="wanted", tab="watched"), r"source=wanted")

    def test_arr_error_and_no_plex_notes(self):
        html = self.render([self.lib()], arr=self.arr("error", "error"))
        self.assertIn("Couldn't reach Radarr at the last refresh, so its titles aren't shown.", html)
        self.assertIn("reach Sonarr at the last refresh", html)
        self.assertNotIn("reach Radarr", self.render([self.lib()], arr=self.arr("ok", "off")))
        no_plex = self.render([self.lib(sources=["radarr"])], arr=self.arr("ok"), library=False)
        self.assertIn("Plex isn't connected, so only Radarr and Sonarr titles are shown.", no_plex)
        self.assertNotIn("Plex isn't connected", self.render([self.lib()], arr=self.arr("ok")))

    def test_empty_states(self):
        self.assertIn("Nothing in Plex, Radarr or Sonarr yet.", self.render([], arr=self.arr("ok")))
        html = self.render([self.lib()], q='zz"<b>')
        self.assertIn('href="/search?q=zz%22%3Cb%3E"', html)
        self.assertIn("Search all movies and TV for &quot;zz&quot;&lt;b&gt;&quot;", html)
        self.assertNotIn("/search?q=", self.render([self.lib()], q="zzz", tab="watched"))
        self.assertNotIn("/search?q=", self.render([self.lib(), self.lib(tmdb_id=9, title="Zed")], q=""))

    def test_hostile_arr_title_escaped(self):
        html = self.render([self.lib(title="<script>x</script>", sources=["radarr"], arr_state="missing")], arr=self.arr())
        self.assertNotIn("<script>x", html)


class TestSearchJsHooks(unittest.TestCase):
    def test_app_js_has_the_search_hooks(self):
        js = read_static("app.js")
        for needle in ("data-keep-on-add", "data-search-live", "partial=1", "AbortController", "replaceState",
                       "isComposing", "data-announce", "setFragment(", "compositionend", "aria-busy", "is-loading",
                       "SEARCH_DEBOUNCE_MS = 400", "SEARCH_MIN = 2", "markAdded("):
            self.assertIn(needle, js, needle)
        self.assertGreaterEqual(js.count("setFragment("), 3)   # definition + add dialog + search
        self.assertNotIn("pushState", js)

    def test_the_single_innerhtml_is_inside_setfragment(self):
        js = read_static("app.js")
        self.assertEqual(js.count("innerHTML"), 1)
        start = js.index("function setFragment")
        end = js.index("\n  }\n", start)
        self.assertGreater(js.index("innerHTML"), start)
        self.assertLess(js.index("innerHTML"), end)


class TestSearchArrNote(SearchBase):
    def with_arr(self, radarr, sonarr, view=None):
        res = result()
        res["arr"] = {"items": [], "radarr": {"state": radarr, "count": 0}, "sonarr": {"state": sonarr, "count": 0}}
        self.use(res)
        return view or sview()

    def test_unreachable_service_notes_in_fragment_and_page_alike(self):
        view = self.with_arr("error", "ok")
        frag, page = self.frag(view), self.page(view)
        self.assertIn("Couldn't reach Radarr at the last refresh", frag)
        self.assertIn("without an In Radarr label", frag)
        self.assertNotIn("reach Sonarr", frag)
        self.assertIn(frag, page)                      # byte identity holds
        self.assertIn("reach Sonarr", self.frag(self.with_arr("ok", "error")))

    def test_no_note_when_ok_off_or_not_a_result_list(self):
        self.assertNotIn("Couldn't reach", self.frag(self.with_arr("ok", "off")))
        for state in ("short", "empty"):
            view = self.with_arr("error", "error", sview(state, q="x"))
            self.assertNotIn("Couldn't reach", self.frag(view, q="x"))


class TestAddKeepsFocus(unittest.TestCase):
    def test_add_handler_refocuses_the_kept_card(self):
        js = read_static("app.js")
        add = js[js.index("    add: function"):js.index("    refresh: function")]
        self.assertIn("markAdded(card)", add)
        self.assertIn("focusCard(kept[0])", add)
        self.assertIn("function focusCard", js)


# ---------------------------------------------------------------------------
# Seerr parity, phase B: title page, poster links and card tools, season picker, lists, Requests
# ---------------------------------------------------------------------------
STATUS_NONE = {"status": "none", "in_library": False, "sources": [], "arr_state": None, "episodes": None,
               "watched": False, "dismissed": False}


def season_row(number, **kw):
    base = {"number": number, "name": f"Season {number}", "episodes": 8, "air_date": f"{2019 + number}-05-01",
            "monitored": None, "have": None, "total": None, "state": None, "requested": False, "selectable": True}
    base.update(kw)
    return base


def title_view(media_type="movie", tmdb_id=7, **kw):
    item = {"media_type": media_type, "tmdb_id": tmdb_id, "title": "Heat", "year": 1995, "release_date": "1995-12-15",
            "overview": "A heist.", "poster_url": "https://i/p.jpg", "poster_large_url": "https://i/big.jpg",
            "backdrop_url": "https://i/b.jpg", "url": f"https://www.themoviedb.org/{media_type}/{tmdb_id}",
            "genres": ["Crime", "Drama"], "directors": ["Michael Mann"], "cast": ["Al Pacino"],
            "vote_average": 8.2, "vote_count": 100, "runtime": 170 if media_type == "movie" else None,
            "seasons": 2 if media_type == "tv" else None, "certification": "R"}
    base = {"media_type": media_type, "tmdb_id": tmdb_id, "state": "ok", "message": None, "item": item, "extras": {
                "tagline": "A los angeles crime saga", "status": "Released", "cast": [
                    {"name": "Al Pacino", "character": "Vincent", "profile_url": "https://i/al.jpg"},
                    {"name": "Val Kilmer", "character": "", "profile_url": None}],
                "crew": [{"name": "Michael Mann", "job": "Director"}], "trailer": {"name": "T", "url": "https://www.youtube.com/watch?v=abcdef"},
                "seasons": [], "networks": [], "studios": ["Regency"], "tvdb_id": None, "imdb_id": "tt0113277", "similar": []},
            "rec": None, "fit": None, "status": dict(STATUS_NONE), "arr": None, "seasons": [], "stars": None, "lists": [],
            "on_watchlist": False, "list_names": [], "similar": [], "can_add": False, "sample": False, "library_known": True}
    base.update(kw)
    return base


class TitleBase(PatchedState):
    def setUp(self):
        super().setUp()
        self.old_cfg = (config.RADARR_URL, config.RADARR_API_KEY, config.SONARR_URL, config.SONARR_API_KEY)
        self.addCleanup(lambda: (setattr(config, "RADARR_URL", self.old_cfg[0]), setattr(config, "RADARR_API_KEY", self.old_cfg[1]),
                                 setattr(config, "SONARR_URL", self.old_cfg[2]), setattr(config, "SONARR_API_KEY", self.old_cfg[3])))
        config.RADARR_URL = config.RADARR_API_KEY = config.SONARR_URL = config.SONARR_API_KEY = ""

    def configure(self):
        config.RADARR_URL = config.RADARR_API_KEY = config.SONARR_URL = config.SONARR_API_KEY = "x"

    def render(self, **kw):
        msg, undo = kw.pop("msg", ""), kw.pop("undo", None)
        return pages.render_title(title_view(**kw), msg, undo)


class TestTitlePage(TitleBase):
    def test_ok_movie_structure(self):
        html = self.render()
        self.assertIn('<body class="cinematic">', html)
        self.assertIn('<article class="title-page" data-card="movie-7" data-keep-on-add>', html)
        self.assertIn('<h2 class="visually-hidden">Heat (1995)</h2>', html)
        self.assertIn("<title>Heat (1995) - Compass</title>", html)
        self.assertEqual(html.count("<h1 "), 2)                       # the brand and the title
        self.assertIn('<h1 class="title title-name">Heat</h1>', html)
        self.assertNotIn("kicker", html)
        self.assertIn('<p class="title-tagline">A los angeles crime saga</p>', html)
        self.assertIn('<img class="title-backdrop" src="https://i/b.jpg" alt="">', html)
        self.assertIn('<img class="poster" src="https://i/big.jpg"', html)                  # the large poster wins
        self.assertIn('<span class="title-genres">Crime, Drama</span>', html)
        self.assertIn('<span class="badge cert">R</span>', html)
        self.assertIn('<span class="runtime">2h 50m</span>', html)
        self.assertNotIn("% match", html)                           # no match for a non-recommendation
        self.assertIn('<h2>Overview</h2><p class="overview">A heist.</p>', html)
        for fact in ("<dt>Status</dt><dd>Released</dd>", "<dt>Release</dt><dd>1995-12-15</dd>", "<dt>Runtime</dt><dd>2h 50m</dd>",
                     "<dt>Studio</dt><dd>Regency</dd>", "<dt>Director</dt><dd>Michael Mann</dd>", "<dt>TMDB</dt><dd>8.2 / 10</dd>"):
            self.assertIn(fact, html)
        self.assertIn('href="https://www.themoviedb.org/movie/7"', html)
        self.assertIn('href="https://www.imdb.com/title/tt0113277/"', html)
        self.assertIn('href="https://www.youtube.com/watch?v=abcdef" target="_blank" rel="noopener noreferrer">Watch trailer', html)
        self.assertIn("(opens YouTube in a new tab)", html)
        self.assertNotIn("seasons\"><h2", html)

    def test_actions_full_rate_form_watchlist_toggle_and_list_link(self):
        html = self.render(stars=4)
        actions = re.search(r'<div class="title-actions">.*?</div></div></div></section>', html, re.S).group(0)
        self.assertIn('<form class="rate" method="post" action="/rate" data-enhance="rate">', actions)    # not compact
        self.assertNotIn("rate-compact", actions)
        self.assertRegex(actions, r'value="4" class="star on" aria-pressed="true"')
        self.assertIn("Your rating: 4/5", actions)
        self.assertNotIn("card-tools", actions)                    # the toggle and link are direct children
        self.assertIn('action="/lists/add" data-enhance="list"', actions)
        self.assertIn('<span class="list-toggle-text">Watchlist</span>', actions)
        self.assertIn('<span class="list-link-text">Add to list</span>', actions)
        self.assertIn('aria-label="Add to list: Heat (1995)"', actions)      # starts with the visible text
        self.assertNotIn('<span class="visually-hidden">Lists</span>', actions)
        self.assertIn('href="/list-dialog?type=movie&amp;id=7&amp;return_to=%2Ftitle%2Fmovie%2F7" data-list-dialog', actions)
        self.assertEqual(actions.count('name="return_to" value="/title/movie/7"'), 3)      # rate, toggle, dismiss
        on = self.render(on_watchlist=True)
        self.assertIn('action="/lists/remove"', on)
        self.assertIn('class="list-toggle on" aria-pressed="true"', on)
        self.assertIn(">On Watchlist<", on)

    def test_add_request_more_and_in_library_actions(self):
        self.configure()
        self.assertIn('class="btn-add" href="/add-dialog?type=movie&amp;id=7&amp;return_to=%2Ftitle%2Fmovie%2F7" data-add-dialog>Add to library',
                      self.render(can_add=True))
        self.assertNotIn("btn-add", self.render(can_add=False))
        owned = self.render(status=dict(STATUS_NONE, status="radarr", in_library=True, sources=["radarr"], arr_state="missing"))
        self.assertIn('<span class="lib-tag">In library</span>', owned)
        self.assertIn('<span class="lib-tag status-tag status-radarr">In Radarr</span>', owned)
        self.assertIn('<span class="badge arr-state state-missing">Missing</span>', owned)
        self.assertNotIn("btn-add", owned)
        tv = self.render(media_type="tv", tmdb_id=9, status=dict(STATUS_NONE, status="sonarr", in_library=True),
                         arr={"service": "sonarr"}, seasons=[season_row(1, requested=True, selectable=False), season_row(2)])
        self.assertIn('data-add-dialog>Request more seasons</a>', tv)
        done = self.render(media_type="tv", tmdb_id=9, status=dict(STATUS_NONE, status="sonarr", in_library=True),
                           arr={"service": "sonarr"}, seasons=[season_row(1, requested=True, selectable=False)])
        self.assertNotIn("Request more seasons", done)
        self.assertIn('<span class="lib-tag">In library</span>', done)

    def test_dismiss_or_undismiss_and_nothing_destructive_in_sample(self):
        html = self.render()
        self.assertIn('action="/dismiss"', html)
        self.assertNotIn('action="/dismiss" data-enhance', html)    # plain: the redirect brings the Undo note back
        self.assertNotIn("/undismiss", html)
        hidden = self.render(status=dict(STATUS_NONE, dismissed=True))
        self.assertIn('action="/undismiss"', hidden)
        self.assertIn("Show in recommendations again", hidden)
        self.assertNotIn('action="/dismiss"', hidden)
        self.assertIn('<span class="badge">Not interested</span>', hidden)
        self.configure()
        sample = self.render(sample=True, can_add=True, status=dict(STATUS_NONE, dismissed=True))
        for gone in ("/dismiss", "/undismiss", "btn-add", "data-add-dialog"):
            self.assertNotIn(gone, sample)
        self.assertIn('data-enhance="rate"', sample)                # rating and lists still render (the POST refuses)
        self.assertIn("list-toggle", sample)

    def test_status_badges_and_in_lists_links(self):
        html = self.render(status=dict(STATUS_NONE, status="plex", watched=True), on_watchlist=True,
                           list_names=[{"id": 1, "name": "Watchlist"}, {"id": 3, "name": "Horror <b>night</b>"}])
        self.assertIn('<p class="title-status badges">', html)
        self.assertIn('<span class="lib-tag status-tag status-plex">In library</span>', html)
        self.assertIn('<span class="badge">Watched</span>', html)
        self.assertIn('<span class="badge">On Watchlist</span>', html)
        self.assertIn('<p class="in-lists">On your lists: <a href="/lists/1">Watchlist</a>, '
                      '<a href="/lists/3">Horror &lt;b&gt;night&lt;/b&gt;</a></p>', html)
        plain = self.render()
        self.assertNotIn("title-status", plain)
        self.assertNotIn("in-lists", plain)

    def test_why_for_recommendations_and_fit_for_the_rest(self):
        rec_html = self.render(rec={"match": 91, "reason": "Because you liked Ronin", "matches": ["Crime", "Mann"],
                                    "because": ["Ronin", "Collateral"], "new": False, "trending": False, "source": "main"},
                               fit=None)
        self.assertIn("<h2>Why Compass picked this</h2>", rec_html)
        self.assertIn('<p class="why-match match">91% match</p>', rec_html)
        self.assertIn('<span class="match">91% match</span>', rec_html)                 # also in the meta line
        self.assertIn('<p class="reason">Because you liked Ronin</p>', rec_html)
        self.assertIn('<span class="chip">Mann</span>', rec_html)
        self.assertIn("Because you watched Ronin, Collateral", rec_html)
        self.assertNotIn("How it fits", rec_html)
        fit = self.render(fit={"matches": ["Crime"]})
        self.assertIn("<h2>How it fits your taste</h2>", fit)
        self.assertIn('<span class="chip">Crime</span>', fit)
        self.assertNotIn("Why Compass picked this", fit)
        self.assertNotIn("% match", fit)
        plain = self.render()
        self.assertNotIn("Why Compass", plain)
        self.assertNotIn("How it fits", plain)

    def test_cast_section_and_name_only_fallback(self):
        html = self.render()
        self.assertIn('<ul class="cast-list">', html)
        self.assertIn('<span class="cast-photo" style="--h:', html)
        self.assertIn('<img src="https://i/al.jpg" alt="" loading="lazy">', html)
        self.assertIn('<span class="cast-name">Al Pacino</span><span class="cast-role">Vincent</span>', html)
        self.assertRegex(html, r'<span class="cast-photo" style="--h:\d+">V</span><span class="cast-name">Val Kilmer</span></li>')
        view = title_view()
        view["extras"]["cast"] = []
        names = pages.render_title(view)
        self.assertIn('<span class="cast-name">Al Pacino</span></li>', names)             # from item["cast"]
        view["item"]["cast"] = []
        self.assertNotIn("cast-list", pages.render_title(view))

    def test_similar_row_uses_row_cards_with_match_only_for_recs(self):
        sim = [{"media_type": "movie", "tmdb_id": 21, "title": "Ronin", "year": 1998, "poster_url": None, "url": None,
                "status": "none", "match": 77, "stars": None, "lists": [], "on_watchlist": False},
               {"media_type": "movie", "tmdb_id": 22, "title": "Collateral", "year": 2004, "poster_url": None, "url": None,
                "status": "radarr", "match": None, "stars": 3, "lists": [2], "on_watchlist": True},
               {"media_type": "movie", "tmdb_id": None, "title": "No id", "year": None, "match": None}]
        html = self.render(similar=sim)
        row = re.search(r'<section class="row title-similar".*?</section>', html, re.S).group(0)
        self.assertIn('<h3 id="row-similar">More like this</h3>', row)
        cards = re.findall(r'<article class="card row-card" data-card="[^"]*">.*?</article>', row, re.S)
        self.assertEqual(len(cards), 2)                             # the title without an id is dropped
        self.assertIn('<span class="match">77% match</span>', cards[0])
        self.assertNotIn("% match", cards[1])
        self.assertIn('status-tag status-radarr">In Radarr<', cards[1])
        self.assertIn('<a class="poster-link" href="/title/movie/21"', cards[0])
        self.assertNotIn("data-add-dialog", cards[1])               # already tracked: no Add
        self.assertIn('name="return_to" value="/title/movie/7"', cards[0])
        self.assertNotIn("title-similar", self.render())

    def test_tv_facts_use_networks_and_creators_and_season_count(self):
        view = title_view("tv", 9)
        view["extras"].update(networks=["HBO", "Max"], studios=[], crew=[{"name": "Vince", "job": "Creator"}], status="Returning Series")
        html = pages.render_title(view)
        for fact in ("<dt>First aired</dt>", "<dt>Seasons</dt><dd>2</dd>", "<dt>Networks</dt><dd>HBO, Max</dd>",
                     "<dt>Creator</dt><dd>Vince</dd>", "<dt>Status</dt><dd>Returning Series</dd>"):
            self.assertIn(fact, html)
        self.assertNotIn("kicker", html)
        self.assertIn('<span class="runtime">2 seasons</span>', html)
        self.assertIn('data-card="tv-9"', html)
        self.assertIn('<body class="cinematic">', html)
        self.assertRegex(html, r'<a class="nav-item active" href="/tv" aria-current="page">')

    def test_notes_message_undo_and_partial(self):
        html = self.render(msg="Hidden <b>x</b>", undo=("movie", 7))
        self.assertIn('<div class="note undo-note" role="status"><span>Hidden &lt;b&gt;x&lt;/b&gt;</span>', html)
        self.assertIn('name="return_to" value="/title/movie/7"', re.search(r'<div class="note undo-note".*?</div>', html).group(0))
        self.assertIn('<p class="note" role="status">Saved</p>', self.render(msg="Saved"))
        part = self.render(state="partial", message=web.TITLE_PARTIAL)
        self.assertIn(f'<p class="note">{escape(web.TITLE_PARTIAL)}</p>', part)
        self.assertIn('<h2>Overview</h2>', part)
        self.assertNotIn('class="note">Some details', self.render())

    def test_unavailable_and_not_found_render_inside_the_shell(self):
        for state, message, status_code in (("unavailable", web.TITLE_UNAVAILABLE, 200), ("not_found", web.TITLE_NOT_FOUND, 404)):
            html = pages.render_title(title_view(state=state, message=message, item=None, extras=None), msg="hi")
            self.assertIn("<html", html)
            self.assertIn(f'<div class="empty"><h3>{escape(message)}</h3><a class="btn-ghost" href="/search">Search instead</a></div>', html)
            self.assertIn('<p class="note" role="status">hi</p>', html)
            self.assertNotIn("title-hero", html)
            self.assertNotIn("data-card", html)
        self.assertIn("<title>Title not found - Compass</title>",
                      pages.render_title(title_view(state="not_found", message="x", item=None)))

    def test_hostile_values_are_escaped_and_bad_urls_dropped(self):
        evil = "<script>alert('x')</script>"
        view = title_view("tv", 9, state="partial", message=evil, seasons=[season_row(1, name=evil)],
                          rec={"match": 50, "reason": evil, "matches": [evil], "because": [evil], "new": False,
                               "trending": False, "source": "main"},
                          list_names=[{"id": 4, "name": evil}],
                          similar=[{"media_type": "tv", "tmdb_id": 3, "title": evil, "year": 2020, "poster_url": 'http://a/"onerror="x',
                                    "url": "javascript:alert(1)", "status": "none", "match": None}])
        view["item"].update(title=evil, overview=evil, genres=[evil], certification=evil, backdrop_url="javascript:alert(1)",
                            poster_large_url="javascript:alert(2)", poster_url="javascript:alert(3)", url="javascript:alert(4)",
                            directors=[evil], cast=[evil])
        view["extras"].update(tagline=evil, status=evil, networks=[evil], studios=[evil], imdb_id='tt1"><x',
                              trailer={"name": evil, "url": "javascript:alert(5)"},
                              cast=[{"name": evil, "character": evil, "profile_url": "javascript:alert(6)"}],
                              crew=[{"name": evil, "job": "Creator"}])
        self.configure()
        html = pages.render_title(view, msg=evil)
        for raw in ("<script>", "javascript:", '"onerror="', 'tt1"><x', "<b>"):
            self.assertNotIn(raw, html)
        self.assertIn("&lt;script&gt;alert(", html)
        self.assertNotIn("title-backdrop", html)
        self.assertNotIn("trailer-link", html)
        self.assertNotIn("imdb.com", html)
        self.assertNotIn("ext-link", html)
        self.assertIn("poster-empty", html)

    def test_bad_ids_and_types_cannot_break_the_markup(self):
        html = pages.render_title(title_view(media_type='"><x', tmdb_id="12"))
        self.assertNotIn('"><x', html)
        self.assertIn('data-card="movie-12"', html)


class TestTitleSeasons(TitleBase):
    def tv(self, rows, **kw):
        return pages.render_title(title_view("tv", 9, seasons=rows, **kw))

    def form(self, html):
        return re.search(r'<form class="season-form".*?</form>', html, re.S).group(0)

    def test_untracked_series_with_sonarr_has_checkboxes_two_submits_and_reload_contract(self):
        self.configure()
        form = self.form(self.tv([season_row(1), season_row(2)], can_add=True))
        self.assertIn('method="post" action="/add" data-enhance="add" data-after="reload"', form)
        self.assertIn('<input type="hidden" name="type" value="tv"><input type="hidden" name="id" value="9">'
                      '<input type="hidden" name="return_to" value="/title/tv/9">', form)
        self.assertEqual(re.findall(r'<input type="checkbox" class="season-check" name="season" value="(\d+)" id="s-\d+">', form), ["1", "2"])
        self.assertIn('<label for="s-1" class="season-name">Season 1</label>', form)
        self.assertIn('<span class="season-eps">8 episodes</span> <span class="season-year">2020</span>', form)
        self.assertIn('<input type="checkbox" name="search" value="1" checked> Search now', form)
        self.assertIn('<button type="submit" name="seasons" value="pick" class="btn-add">Request selected seasons</button>', form)
        self.assertIn('<button type="submit" name="seasons" value="all" class="btn-ghost">Request all seasons</button>', form)
        self.assertIn('href="/add-dialog?type=tv&amp;id=9&amp;return_to=%2Ftitle%2Ftv%2F9" data-add-dialog>More options</a>', form)

    def test_requested_and_complete_seasons_have_no_checkbox_and_show_their_state(self):
        self.configure()
        rows = [season_row(1, requested=True, selectable=False, state="available", have=8, total=8, monitored=True),
                season_row(2, requested=True, selectable=False, state="partial", have=3, total=8, monitored=True),
                season_row(3, requested=True, selectable=False, state="missing", monitored=True, air_date="2020-01-01"),
                season_row(4, requested=True, selectable=False, state="upcoming", monitored=True, air_date=None, episodes=None),
                season_row(5, state="unmonitored", monitored=False)]
        form = self.form(self.tv(rows, arr={"service": "sonarr"}))
        self.assertEqual(form.count('class="season-check"'), 1)
        self.assertIn('value="5"', form)
        for label, state in (("Available", "available"), ("3/8 episodes", "partial"), ("Wanted", "missing"),
                             ("Upcoming", "upcoming"), ("Not requested", "unmonitored")):
            self.assertIn(f'<span class="badge arr-state season-state state-{state}">{label}</span>', form)
        self.assertIn('<span class="season-name">Season 1</span>', form)
        self.assertNotIn("season-eps\"></span>", form)

    def test_no_checkboxes_or_buttons_without_sonarr_or_in_sample_or_when_nothing_is_selectable(self):
        rows = [season_row(1, state="missing", monitored=True), season_row(2)]
        no_sonarr = self.form(self.tv(rows))
        sample = None
        self.configure()
        sample = self.form(self.tv(rows, sample=True))
        done = self.form(self.tv([season_row(1, requested=True, selectable=False, state="available", have=8, total=8)]))
        for form in (no_sonarr, sample, done):
            self.assertNotIn("season-check", form)
            self.assertNotIn('name="seasons"', form)
            self.assertNotIn("season-actions", form)
            self.assertIn("season-name", form)

    def test_no_seasons_section_for_movies_or_empty_lists(self):
        self.assertNotIn("season-form", self.render())
        self.assertNotIn("season-form", self.tv([]))


class TestPosterLinksAndTools(unittest.TestCase):
    def test_pages_py_has_no_modal_hooks(self):
        with open(os.path.join(ROOT, "pages.py"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("data-open-detail", text)
        self.assertNotIn("hero-details", text)

    def test_title_url_and_poster_link(self):
        self.assertEqual(pages._title_url({"media_type": "tv", "tmdb_id": 5}), "/title/tv/5")
        self.assertEqual(pages._title_url({"media_type": "movie", "tmdb_id": "12"}), "/title/movie/12")
        link = pages._poster_linked(dict(ITEM, title="<b>x</b>"))
        self.assertIn('<a class="poster-link" href="/title/movie/7" aria-label="More info: &lt;b&gt;x&lt;/b&gt; (1995)"><div class="poster', link)
        for bad in (dict(ITEM, tmdb_id=None), dict(ITEM, media_type="book"), dict(ITEM, tmdb_id=0)):
            self.assertNotIn("poster-link", pages._poster_linked(bad))

    def test_every_card_renderer_links_the_poster_to_the_title_page(self):
        lib = {"media_type": "movie", "tmdb_id": 7, "title": "Heat", "year": 1995, "added_at": "2024-01-01T00:00:00Z",
               "watched": False, "progress": None, "poster_key": None, "url": None}
        watched = dict(lib, last_viewed="2024-01-01T00:00:00Z", view_count=1, stars=None, plex_stars=None)
        search = {"media_type": "movie", "tmdb_id": 7, "title": "Heat", "year": 1995, "status": "none", "poster_url": None}
        listed = dict(search, position=0, match=None, stars=None, lists=[], on_watchlist=False)
        view = {"list": {"id": 3, "name": "L", "description": "", "kind": "custom", "count": 1, "url": "/lists/3", "posters": []},
                "sort": "manual", "can_move": True}
        cards = {"ai": pages._card(ITEM, "/ai", True), "row": pages._row_card(ITEM, "/", True),
                 "search": pages._search_card(search, "/search", True), "library": pages._library_card(lib),
                 "requests": pages._library_card(lib, True), "watched": pages._watched_card(watched, "/library"),
                 "libcard": pages._library_row_card(lib), "list": pages._list_card(listed, view, "/lists/3"),
                 "hero": pages._hero_slide(dict(ITEM, genres=[]), 1, 1, "/", True)}
        for name, card in cards.items():
            if name != "hero":
                self.assertIn('<a class="poster-link" href="/title/movie/7"', card, name)
            self.assertIn('class="card-tools', card, name)
            # row and watched cards keep their full stars (row: in the details block); the rest get the compact line
            if name == "hero":
                self.assertNotIn('<form class="rate', card)
            else:
                self.assertIn('<form class="rate" ' if name in ("row", "watched") else '<form class="rate rate-compact"', card, name)
            self.assertNotIn("data-open-detail", card, name)
        self.assertIn('href="/title/movie/7"', cards["hero"])

    def test_cards_without_a_tmdb_id_have_no_link_and_no_tools(self):
        lib = {"media_type": "movie", "tmdb_id": None, "title": "Heat", "year": 1995, "added_at": None, "watched": False,
               "progress": None, "poster_key": None, "url": "https://www.themoviedb.org/movie/7"}
        watched = dict(lib, last_viewed=None, view_count=1, stars=None, plex_stars=None)
        for card in (pages._library_card(lib), pages._library_card(lib, True), pages._library_row_card(lib)):
            self.assertNotIn("poster-link", card)
            self.assertNotIn("card-tools", card)
            self.assertNotIn("<a ", card)                           # the title stays unlinked (no TMDB link either)
            self.assertNotIn("<form", card)
        self.assertEqual(pages._card_tools(lib, "/"), "")
        self.assertEqual(pages._poster_linked(lib).count("<a "), 0)

    def test_library_titles_link_to_the_title_page_not_tmdb(self):
        lib = {"media_type": "tv", "tmdb_id": 12, "title": "Show", "year": 2020, "added_at": "2024-01-01T00:00:00Z", "watched": False,
               "progress": None, "poster_key": None, "url": "https://www.themoviedb.org/tv/12"}
        for card in (pages._library_card(lib), pages._library_card(lib, True),
                     pages._watched_card(dict(lib, last_viewed=None, view_count=1, stars=None, plex_stars=None), "/library")):
            self.assertIn('<h3 class="title"><a href="/title/tv/12">', card)
            self.assertNotIn("themoviedb.org", card)
            self.assertNotIn("_blank", card)

    def test_card_tools_markup_and_states(self):
        tools = pages._card_tools(dict(ITEM, stars=3, on_watchlist=True), "/movies")
        self.assertTrue(tools.startswith('<div class="card-tools">'))
        self.assertIn('<form class="rate rate-compact" method="post" action="/rate" data-enhance="rate">', tools)
        self.assertIn('<p class="rating-text visually-hidden">Your rating: 3/5</p>', tools)
        self.assertIn('<form class="inline list-toggle-form" method="post" action="/lists/remove" data-enhance="list">', tools)
        self.assertIn('<input type="hidden" name="list" value="watchlist">', tools)
        self.assertIn('class="list-toggle on" aria-pressed="true"', tools)
        self.assertIn('<svg class="list-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"', tools)
        self.assertIn('<span class="list-toggle-text">On Watchlist</span>', tools)
        self.assertIn('data-list-dialog aria-label="Add Heat (1995) to a list">', tools)
        self.assertIn('<span class="visually-hidden">Lists</span></a></div>', tools)
        self.assertIn('href="/list-dialog?type=movie&amp;id=7&amp;return_to=%2Fmovies"', tools)
        off = pages._card_tools(ITEM, "/movies")
        self.assertIn('action="/lists/add"', off)
        self.assertIn('class="list-toggle" aria-pressed="false"', off)
        self.assertIn("Not rated", off)
        no_stars = pages._card_tools(ITEM, "/", stars=False)
        self.assertNotIn('action="/rate"', no_stars)
        self.assertIn("list-toggle", no_stars)
        no_lists = pages._card_tools(ITEM, "/", lists=False)
        self.assertIn('action="/rate"', no_lists)
        self.assertNotIn("list-toggle", no_lists)
        self.assertNotIn("list-link", no_lists)
        self.assertEqual(pages._card_tools(ITEM, "/", stars=False, lists=False), "")
        self.assertEqual(pages._card_tools(dict(ITEM, tmdb_id=None), "/"), "")

    def test_card_tools_hostile_title_is_escaped(self):
        tools = pages._card_tools(dict(HOSTILE), "/")
        self.assertNotIn("<script>", tools)
        self.assertIn("&lt;script&gt;", tools)

    def test_rate_form_compact_variant(self):
        compact = pages._rate_form(ITEM, "/", compact=True)
        full = pages._rate_form(ITEM, "/")
        self.assertIn('<form class="rate rate-compact"', compact)
        self.assertIn('class="rating-text visually-hidden"', compact)
        self.assertIn('<form class="rate" ', full)
        self.assertIn('<p class="rating-text">', full)
        self.assertEqual(compact.count('name="stars"'), full.count('name="stars"'))
        clear = pages._rate_form(dict(ITEM, stars=2), "/", compact=True)
        self.assertIn("star-clear", clear)

    def test_row_card_details_carry_the_watchlist_toggle_for_the_preview_and_no_js(self):
        card = pages._row_card(ITEM, "/", True)
        details = re.search(r'<details class="card-details">.*</details>', card, re.S).group(0)
        self.assertEqual(details.count("list-toggle-form"), 1)
        self.assertIn("data-list-dialog", details)
        self.assertEqual(card.count('action="/rate"'), 1)           # still one (full) rate form per row card

    def test_ai_page_applies_user_state(self):
        old = config.AI_TOKEN
        self.addCleanup(setattr, config, "AI_TOKEN", old)
        config.AI_TOKEN = "sk-x"
        with mock.patch.object(web, "build_status", return_value=status()), \
                mock.patch.object(web, "with_user_state", side_effect=lambda items: [dict(i, on_watchlist=True) for i in items]) as ws, \
                mock.patch.dict(web._ai_state, {"result": result(), "time": time.time()}):
            html = pages.render_ai_page()
        ws.assert_called_once()
        self.assertIn('class="list-toggle on"', html)
        self.assertIn('class="poster-link"', html)


class TestAddDialogSeasons(TitleBase):
    def dialog(self, media_type="tv", choices=None, partial=True, title="Show <b>"):
        item = {"media_type": media_type, "tmdb_id": 9, "title": title, "year": 2020, "poster_url": None}
        patches = [mock.patch.object(web, "_is_sample", return_value=False),
                   mock.patch.object(web, "lookup_item", return_value=item),
                   mock.patch.object(web, "season_choices", return_value=choices),
                   mock.patch.object(pages, "_configured_profiles", return_value=[(1, "HD")])]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return pages.render_add_dialog(media_type, 9, "/tv", partial=partial)

    def test_untracked_series_gets_the_radios_and_the_season_list(self):
        html = self.dialog(choices={"seasons": [season_row(1), season_row(2, name="Season <i>2</i>")], "tracked": False, "known": True})
        picker = re.search(r'<fieldset class="season-picker".*?</fieldset>', html, re.S).group(0)
        self.assertIn('<input type="radio" name="seasons" value="all" checked> All seasons', picker)
        self.assertIn('<input type="radio" name="seasons" value="pick"> Choose seasons', picker)
        self.assertEqual(re.findall(r'class="season-check"\s+name="season" value="(\d+)">', picker), ["1", "2"])
        self.assertNotIn("disabled", picker)
        self.assertIn("Season &lt;i&gt;2&lt;/i&gt;", picker)
        self.assertIn('<span class="season-eps">8 episodes</span> <span class="season-year">2020</span>', picker)
        self.assertIn('<select name="quality_profile_id">', html)
        self.assertIn('<h3 id="add-dialog-title">Add &quot;Show &lt;b&gt;&quot; to library</h3>', html)
        self.assertIn('name="search" value="1" checked', html)
        self.assertLess(html.index("season-picker"), html.index("quality_profile_id"))
        self.assertNotIn("<html", html)

    def test_tracked_series_says_request_more_and_hides_the_profile_select(self):
        rows = [season_row(1, requested=True, selectable=False, state="available", have=8, total=8), season_row(2)]
        html = self.dialog(choices={"seasons": rows, "tracked": True, "known": True})
        self.assertIn("Request more seasons of &quot;Show &lt;b&gt;&quot;", html)
        self.assertIn("All remaining seasons", html)
        self.assertNotIn("quality_profile_id", html)
        self.assertRegex(html, r'name="season" value="1" checked disabled>')
        self.assertRegex(html, r'name="season" value="2">')
        self.assertIn('<span class="badge arr-state season-state state-available">Available</span>', html)
        self.assertIn(">Request</button>", html)
        self.assertIn('name="search" value="1" checked', html)

    def test_unknown_seasons_offer_only_all_and_a_note(self):
        html = self.dialog(choices={"seasons": [], "tracked": False, "known": False})
        self.assertIn('name="seasons" value="all" checked', html)
        self.assertNotIn('value="pick"', html)
        self.assertNotIn("season-check", html)
        self.assertIn("Season list unavailable right now - you can still add all seasons.", html)

    def test_a_failing_season_lookup_degrades_to_the_note(self):
        with mock.patch.object(web, "season_choices", side_effect=RuntimeError("boom")):
            item = {"media_type": "tv", "tmdb_id": 9, "title": "S", "year": 2020, "poster_url": None}
            with mock.patch.object(web, "lookup_item", return_value=item), mock.patch.object(web, "_is_sample", return_value=False), \
                    mock.patch.object(pages, "_configured_profiles", return_value=[]):
                html = pages.render_add_dialog("tv", 9, "/tv", partial=True)
        self.assertIn("Season list unavailable", html)
        self.assertNotIn("boom", html)

    def test_movies_have_no_picker_and_never_ask_for_seasons(self):
        with mock.patch.object(web, "season_choices") as choices:
            html = self.dialog("movie", choices=None)
        choices.assert_not_called()
        self.assertNotIn("season", html)
        self.assertIn('<select name="quality_profile_id">', html)
        self.assertIn(">Add</button>", html)

    def test_season_picker_function_directly(self):
        self.assertIn('id="x-seasons"', pages._season_picker({"seasons": [], "tracked": False, "known": False}, "x"))
        rows = [season_row(1, requested=True, selectable=False)]
        self.assertIn("checked disabled", pages._season_picker({"seasons": rows, "tracked": True, "known": True}, "x"))

    def test_section_follows_the_new_return_targets(self):
        for return_to, expected in (("/title/movie/3", "movies"), ("/title/tv/3", "tv"), ("/lists/2", "library"),
                                    ("/watchlist", "library"), ("/ai", "ai")):
            self.assertEqual(pages._section_for(return_to), expected, return_to)
        self.assertEqual(pages._section_for("/"), "home")


def summary(list_id=1, name="Watchlist", kind="watchlist", count=0, description="", posters=()):
    return {"id": list_id, "name": name, "description": description, "kind": kind, "count": count,
            "url": f"/lists/{list_id}" if list_id is not None else None, "posters": list(posters)}


def lists_view(*lists, sample=False, can_create=True):
    return {"lists": list(lists), "sample": sample, "can_create": can_create, "max_lists": 50}


class TestListDialog(PatchedState):
    STUB = {"media_type": "movie", "tmdb_id": 7, "title": "Heat <b>", "year": 1995, "poster_url": None, "url": None}

    def render(self, view=None, member=(1,), stub="stub", partial=True, sample=False, return_to="/title/movie/7"):
        stub = self.STUB if stub == "stub" else stub
        view = view or lists_view(summary(1, "Watchlist", count=2), summary(3, "Horror <i>night</i>", "custom", 5))
        patches = [mock.patch.object(web, "_is_sample", return_value=sample),
                   mock.patch.object(web, "title_stub", return_value=stub),
                   mock.patch.object(web, "lists_view", return_value=view),
                   mock.patch.object(web, "with_user_state", return_value=[dict(self.STUB or {}, lists=list(member))])]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return pages.render_list_dialog("movie", 7, return_to, partial=partial)

    def test_partial_has_the_form_with_checked_state_and_no_shell(self):
        html = self.render()
        self.assertNotIn("<html", html)
        self.assertIn('<div class="dialog-box list-dialog">', html)
        self.assertIn('<h3 id="list-dialog-title">Add &quot;Heat &lt;b&gt;&quot; to lists</h3>', html)
        self.assertIn('<form method="post" action="/lists/set" data-enhance="lists">', html)
        self.assertIn('name="return_to" value="/title/movie/7"', html)
        self.assertIn('<legend class="visually-hidden">Lists</legend>', html)
        self.assertIn('<input type="checkbox" name="list_id" value="1" checked> Watchlist <span class="muted">2</span>', html)
        self.assertIn('<input type="checkbox" name="list_id" value="3"> Horror &lt;i&gt;night&lt;/i&gt; <span class="muted">5</span>', html)
        self.assertLess(html.index('value="1"'), html.index('value="3"'))
        self.assertIn('<label class="list-new">New list<input type="text" name="new_list" maxlength="60" placeholder="Name"></label>', html)
        self.assertIn('<a class="btn-ghost" href="/title/movie/7" data-close>Cancel</a>', html)
        self.assertIn('<button type="submit" class="btn-add">Save</button>', html)

    def test_new_list_field_only_when_can_create(self):
        view = lists_view(summary(1), can_create=False)
        self.assertNotIn("new_list", self.render(view))

    def test_page_mode_has_the_shell_and_the_right_nav(self):
        html = self.render(partial=False, return_to="/lists/3")
        self.assertIn("<html", html)
        self.assertIn('<div class="dialog-page">', html)
        self.assertRegex(html, r'<a class="nav-item active" href="/library" aria-current="page">')

    def test_sample_mode_shows_a_note_and_no_form(self):
        html = self.render(sample=True)
        self.assertIn("Sample data - lists aren't saved.", html)
        self.assertNotIn("<form", html)
        self.assertIn("data-close", html)

    def test_unknown_title_is_a_safe_message(self):
        html = self.render(stub=None)
        self.assertIn("Can&#39;t add this one", html)
        self.assertNotIn("<form", html)
        self.assertIn('data-close', html)

    def test_return_to_is_made_safe(self):
        html = self.render(return_to="http://evil.example/x")
        self.assertNotIn("evil.example", html)


class TestListsPages(PatchedState):
    def test_lists_page_tiles_create_form_and_subtabs(self):
        view = lists_view(summary(1, "Watchlist", count=1, posters=["https://i/a.jpg", "javascript:x"]),
                          summary(3, "Horror <b>", "custom", 0, "Scary <i>things</i>"))
        with mock.patch.object(web, "lists_view", return_value=view):
            html = pages.render_lists(msg="Created <b>")
        self.assertRegex(html, r'<a class="subtab on" href="/lists">Lists</a>')
        self.assertRegex(html, r'<a class="nav-item active" href="/library" aria-current="page">')
        self.assertIn('<p class="note" role="status">Created &lt;b&gt;</p>', html)
        tile = re.search(r'<a class="list-tile" href="/lists/1">.*?</a>', html, re.S).group(0)
        self.assertEqual(tile.count("<img"), 1)                    # the javascript: poster is dropped
        self.assertEqual(tile.count('<span aria-hidden="true"></span>'), 3)    # always four cells
        self.assertIn('<span class="list-tile-name">Watchlist</span><span class="list-tile-meta">1 title</span>', tile)
        self.assertIn('<span class="list-tile-meta">0 titles</span><span class="list-tile-desc">Scary &lt;i&gt;things&lt;/i&gt;</span>', html)
        self.assertIn("Horror &lt;b&gt;", html)
        self.assertNotIn("<b>", html)
        self.assertIn('<form class="list-create" method="post" action="/lists/create">', html)
        self.assertIn('<input name="name" maxlength="60" required>', html)
        self.assertIn('<input name="description" maxlength="300">', html)
        self.assertIn(">Create list</button>", html)

    def test_sample_virtual_watchlist_is_not_a_link_and_there_is_no_create_form(self):
        view = lists_view(summary(None, "Watchlist"), sample=True, can_create=False)
        with mock.patch.object(web, "lists_view", return_value=view):
            html = pages.render_lists()
        self.assertIn('<div class="list-tile">', html)
        self.assertNotIn('<a class="list-tile"', html)
        self.assertIn("Sample data - lists aren't saved.", html)
        self.assertNotIn("list-create", html)

    def test_create_form_hidden_at_the_limit(self):
        with mock.patch.object(web, "lists_view", return_value=lists_view(summary(1), can_create=False)):
            self.assertNotIn("list-create", pages.render_lists())

    def list_view(self, kind="custom", sort="manual", page=1, pages_=1, items=None, can_move=True, **kw):
        base = {"list": summary(3, "Horror <b>night</b>", kind, 2, "A <i>desc</i>"), "sort": sort, "sorts": web.LIST_SORTS,
                "page": page, "pages": pages_, "total": 2, "can_move": can_move, "sample": False,
                "items": [] if items is None else items}
        base.update(kw)
        return base

    def card(self, n=1, **kw):
        base = {"media_type": "movie", "tmdb_id": n, "title": f"Film {n}", "year": 2000 + n, "poster_url": None, "status": "none",
                "arr_state": None, "episodes": None, "watched": False, "dismissed": False, "match": None, "stars": None,
                "lists": [3], "on_watchlist": False, "position": n, "added_at": "2024-01-01"}
        base.update(kw)
        return base

    def test_custom_list_header_has_edit_and_delete_details(self):
        html = pages.render_list(self.list_view(items=[self.card()]), msg="Saved")
        head = re.search(r'<header class="list-head">.*?</header>', html, re.S).group(0)
        self.assertIn("<h3>Horror &lt;b&gt;night&lt;/b&gt;</h3>", head)
        self.assertIn('<p class="muted">A &lt;i&gt;desc&lt;/i&gt;</p>', head)
        self.assertIn('<details class="list-edit"><summary>Edit</summary>', head)
        self.assertIn('action="/lists/update"', head)
        self.assertIn('<input type="hidden" name="list_id" value="3">', head)
        self.assertIn('value="Horror &lt;b&gt;night&lt;/b&gt;"', head)
        self.assertIn('<details class="list-delete"><summary>Delete list</summary>', head)
        self.assertIn("Delete &quot;Horror &lt;b&gt;night&lt;/b&gt;&quot;? This can't be undone.", head)
        self.assertIn('action="/lists/delete"', head)
        self.assertIn('<button type="submit" class="btn-ghost danger">Delete</button>', head)
        self.assertIn('<input type="hidden" name="return_to" value="/lists">', head)
        self.assertRegex(html, r'<a class="subtab on" href="/lists">Lists</a>')
        self.assertIn('<p class="note" role="status">Saved</p>', html)

    def test_watchlist_has_no_edit_or_delete(self):
        html = pages.render_list(self.list_view(kind="watchlist", items=[self.card()]))
        self.assertNotIn("list-edit", html)
        self.assertNotIn("list-delete", html)
        self.assertNotIn("/lists/update", html)

    def test_toolbar_sort_options_and_selection(self):
        html = pages.render_list(self.list_view(sort="rating", items=[self.card()]))
        toolbar = re.search(r'<form class="toolbar".*?</form>', html, re.S).group(0)
        self.assertIn('method="get" action="/lists/3"', toolbar)
        for value, label in (("manual", "Your order"), ("added", "Recently added"), ("title", "Title A-Z"),
                             ("year", "Newest year"), ("rating", "Your rating"), ("match", "Match %")):
            self.assertIn(f'<option value="{value}"{" selected" if value == "rating" else ""}>{label}</option>', toolbar)

    def test_cards_have_move_forms_only_when_can_move_and_always_a_remove_form(self):
        movable = pages.render_list(self.list_view(items=[self.card(1)]))
        card = re.search(r'<article class="card search-card list-card".*?</article>', movable, re.S).group(0)
        self.assertIn('data-card="movie-1"', card)
        self.assertIn('<a class="poster-link" href="/title/movie/1"', card)
        self.assertIn('<div class="list-move" role="group" aria-label="Reorder Film 1 (2001)">', card)
        self.assertEqual(re.findall(r'<input type="hidden" name="direction" value="(\w+)">', card), ["up", "down", "top"])
        self.assertEqual(card.count('action="/lists/move" data-enhance="list-move"'), 3)
        self.assertIn('<input type="hidden" name="list_id" value="3">', card)
        self.assertIn('class="inline list-remove" method="post" action="/lists/remove" data-enhance="list" data-remove-card', card)
        self.assertIn('aria-label="Remove Film 1 (2001) from this list"', card)
        self.assertIn('class="card-tools"', card)
        fixed = pages.render_list(self.list_view(sort="title", can_move=False, items=[self.card(1)]))
        self.assertNotIn("list-move", fixed)
        self.assertNotIn("/lists/move", fixed)
        self.assertIn("list-remove", fixed)
        self.assertIn('name="return_to" value="/lists/3?sort=title"', fixed)

    def test_card_badges_match_and_hostile_titles(self):
        html = pages.render_list(self.list_view(items=[self.card(1, title="<script>x</script>", match=82, status="plex", watched=True,
                                                                   arr_state="missing")]))
        self.assertNotIn("<script>x", html)
        self.assertIn("&lt;script&gt;x", html)
        self.assertIn('<span class="match">82% match</span>', html)
        self.assertIn('status-tag status-plex">In library<', html)
        self.assertIn('<span class="badge">Watched</span>', html)
        self.assertIn('<p class="card-sub arr-line">Missing</p>', html)

    def test_pager_keeps_the_sort_and_is_omitted_for_one_page(self):
        html = pages.render_list(self.list_view(sort="title", page=2, pages_=3, items=[self.card()], can_move=False))
        self.assertIn("Page 2 of 3", html)
        self.assertIn('href="/lists/3?sort=title" rel="prev"', html)
        self.assertIn('href="/lists/3?sort=title&amp;page=3" rel="next"', html)
        self.assertNotIn('class="pager"', pages.render_list(self.list_view(items=[self.card()])))
        manual = pages.render_list(self.list_view(page=1, pages_=2, items=[self.card()]))
        self.assertIn('href="/lists/3?page=2" rel="next"', manual)

    def test_empty_state(self):
        html = pages.render_list(self.list_view(items=[]))
        self.assertIn("<h3>Nothing on this list yet</h3>", html)
        self.assertIn('<a class="btn-add" href="/">Browse recommendations</a>', html)
        self.assertNotIn('class="grid"', html)


class TestRequestsTab(PatchedState):
    def lib(self, **kw):
        base = {"media_type": "tv", "tmdb_id": 5, "title": "Show", "year": 2016, "added_at": "2024-03-01T10:00:00Z", "watched": False,
                "progress": None, "poster_key": None, "url": None, "seasons": None, "request_state": None, "episodes": None,
                "sources": [], "stars": None, "lists": [], "on_watchlist": False}
        base.update(kw)
        return base

    def render(self, items, **kw):
        self.use(result())
        with mock.patch.object(web, "library_items", return_value=items):
            return web.render_library(tab="added", **kw)

    def test_tab_label_and_nav(self):
        html = self.render([self.lib()])
        self.assertIn('<a class="subtab on" href="/library?type=added">Requests</a>', html)
        self.assertNotIn("Added here", html)

    def test_every_state_has_a_badge_with_text(self):
        cases = (("requested", None, "Requested"), ("processing", None, "Searching"), ("upcoming", None, "Upcoming"),
                 ("unmonitored", None, "Not monitored"), ("available", None, "Available"),
                 ("partial", {"have": 3, "total": 10}, "3/10 episodes"), ("partial", None, "Partly available"))
        for state, episodes, label in cases:
            html = self.render([self.lib(request_state=state, episodes=episodes)])
            self.assertIn(f'<span class="badge req-state req-{state}">{label}</span>', html, state)
            self.assertIn('<p class="card-badges">', html)
            self.assertNotIn("poster-top", html)
        none = self.render([self.lib(request_state=None)])
        self.assertNotIn("req-state", none)

    def test_seasons_line_for_tv_only(self):
        self.assertIn('<p class="card-sub req-seasons">All seasons</p>', self.render([self.lib(seasons="all")]))
        self.assertIn('<p class="card-sub req-seasons">Seasons 1, 3</p>', self.render([self.lib(seasons=[1, 3])]))
        self.assertIn('<p class="card-sub req-seasons">Season 2</p>', self.render([self.lib(seasons=[2])]))
        self.assertNotIn("req-seasons", self.render([self.lib(seasons=None)]))
        self.assertNotIn("req-seasons", self.render([self.lib(media_type="movie", seasons="all")]))
        self.assertNotIn("req-seasons", self.render([self.lib(seasons=[])]))

    def test_cards_link_to_the_title_page_show_added_date_and_have_tools(self):
        html = self.render([self.lib(stars=4, on_watchlist=True)])
        self.assertIn('<a class="poster-link" href="/title/tv/5"', html)
        self.assertIn('<h3 class="title"><a href="/title/tv/5">Show (2016)</a></h3>', html)
        self.assertIn("Added 2024-03-01", html)
        self.assertIn("Your rating: 4/5", html)
        self.assertIn('class="list-toggle on"', html)
        self.assertIn('name="return_to" value="/library?type=added&amp;sort=added&amp;show=all"', html)

    def test_show_filter_labels_and_empty_copy(self):
        html = self.render([self.lib()], show="open")
        self.assertIn('<option value="open" selected>Not available yet</option>', html)
        self.assertIn('<option value="available">Available</option>', html)
        empty = self.render([])
        self.assertIn("No requests yet", empty)
        self.assertIn("Nothing added yet", empty)
        self.assertIn("Requests", empty)

    def test_library_cards_get_user_state_and_watched_keeps_its_stars(self):
        self.use(result())
        lib = {"media_type": "movie", "tmdb_id": 5, "title": "Arrival", "year": 2016, "added_at": "2024-03-01T10:00:00Z",
               "watched": False, "progress": None, "poster_key": None, "url": None}
        def fake(items, maps=None):
            return [dict(i, stars=2, lists=[1], on_watchlist=True) for i in items]
        with mock.patch.object(web, "library_items", return_value=[lib]), mock.patch.object(web, "with_user_state", side_effect=fake):
            html = web.render_library(tab="all")
        self.assertIn('class="list-toggle on"', html)
        self.assertIn("Your rating: 2/5", html)
        wat = {"media_type": "movie", "tmdb_id": 5, "title": "Arrival", "year": 2016, "last_viewed": "2024-05-02T10:00:00Z", "view_count": 1,
               "poster_key": None, "poster_url": None, "url": None, "stars": 4, "plex_stars": None}
        with mock.patch.object(web, "library_items", return_value=[wat]), mock.patch.object(web, "with_user_state", side_effect=fake):
            html = web.render_library(tab="watched")
        self.assertIn("Your rating: 4/5", html)               # the watched list's own stars win
        self.assertEqual(html.count('action="/rate"'), 1)       # full stars only: the tools line has no second form
        self.assertIn('class="list-toggle on"', html)
        self.assertIn('class="list-link"', html)


class TestTitlePageJsHooks(unittest.TestCase):
    def test_app_js_has_the_new_hooks_and_no_modal_leftovers(self):
        js = read_static("app.js")
        for needle in ("data-list-dialog", "poster-link", "data-after", "list-move", "data-remove-card", "season-check",
                       "on_watchlist", "openFragmentDialog", "syncWatchlist", "withMessage", "title-backdrop", "cloneListTools"):
            self.assertIn(needle, js)
        for gone in ("openDetail(", "data-open-detail", "openAddDialog"):
            self.assertNotIn(gone, js)
        self.assertEqual(len(re.findall(r"innerHTML", js)), 1)
        self.assertEqual(len(re.findall(r"\.innerHTML\s*=", js)), 1)

    def test_handlers_exist_for_every_new_form_kind(self):
        js = read_static("app.js")
        pages_text = open(os.path.join(ROOT, "pages.py"), encoding="utf-8").read()
        for kind in re.findall(r'data-enhance="([a-z-]+)"', pages_text):
            self.assertTrue(f"{kind}: function" in js or f'"{kind}": function' in js, kind)

    def test_add_handler_sends_the_pressed_button_and_reloads_title_pages(self):
        js = read_static("app.js")
        add = js[js.index("    add: function"):js.index("    list: function")]
        self.assertIn("postForm(form, submitter)", add)
        self.assertIn('getAttribute("data-after") === "reload"', add)
        self.assertIn("withMessage(", add)
        self.assertIn("failed(form, err, submitter)", add)

    def test_season_ticks_select_the_pick_radio(self):
        js = read_static("app.js")
        self.assertIn('input[name=seasons][value=pick]', js)

    def test_move_handler_keeps_focus_and_handles_cross_page_swaps(self):
        js = read_static("app.js")
        move = js[js.index('"list-move": function'):js.index("    refresh: function")]
        for needle in ("data.moved", "insertBefore", "button.focus()", "window.location.href"):
            self.assertIn(needle, move)


class TestSubmitterFallbackAndMoveTop(unittest.TestCase):
    def test_last_pressed_named_button_stands_in_for_a_missing_submitter(self):
        js = read_static("app.js")
        self.assertIn("e.submitter || form._lastSubmit", js)
        self.assertIn('closest("button[name]")', js)
        self.assertIn("named.form._lastSubmit = named", js)

    def test_move_top_only_moves_in_place_on_page_one_and_bottom_reloads(self):
        js = read_static("app.js")
        move = js[js.index('"list-move": function'):js.index("    refresh: function")]
        self.assertIn('direction === "top" && before && onFirstPage()', move)
        self.assertNotIn('direction === "bottom"', move)


# ---------------------------------------------------------------------------
# Streaming redesign ("projection room"): specs/streaming-redesign.md
# ---------------------------------------------------------------------------
def _channel(value):
    value = value / 255
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(color):
    color = color.strip().lstrip("#")
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _dim(body, props):
    """The first px (or rem, as 16px) value any of `props` declares in a rule body, else None."""
    for prop in props:
        m = re.search(r"(?<![\w-])%s\s*:\s*([\d.]+)(px|rem)\b" % prop, body)
        if m:
            return float(m.group(1)) * (16 if m.group(2) == "rem" else 1)
    return None


def _decls(body):
    return {k: v.strip() for k, v in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", body)}


class TestRedesignCss(unittest.TestCase):
    """Design rules read straight from static/app.css (specs/streaming-redesign.md, frontend acceptance criteria)."""

    @classmethod
    def setUpClass(cls):
        import themes
        cls.themes = themes
        cls.css = _strip_css(read_static("app.css"))
        cls.rules = _blocks(cls.css)
        cls.js = read_static("app.js")
        texts = []
        for name in ("pages.py", "settings_page.py"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                texts.append(f.read())
        cls.pages_py, cls.settings_py = texts

    def theme_decls(self, key):
        sel = ':root, [data-theme="crimson"]' if key == "crimson" else f'[data-theme="{key}"]'
        found = [b for b in self.rules if b[0] == sel]
        self.assertEqual(len(found), 1, key)
        return _decls(found[0][1])

    def root_decls(self):
        found = [b for b in self.rules if b[0] == ":root"]
        self.assertEqual(len(found), 1, "one plain :root block for the non-theme tokens")
        return _decls(found[0][1])

    def rules_for(self, part):
        """Rules with a selector (comma part) equal to `part` or ending in " part"."""
        return [(sel, body, media) for sel, body, media in self.rules
                if any(p == part or p.endswith(" " + part) for p in (x.strip() for x in sel.split(",")))]

    # -- contrast --------------------------------------------------------------------------------
    def test_contrast_table_per_theme(self):
        pairs = (("--text", "--bg", 4.5), ("--text-2", "--surface-2", 4.5), ("--muted", "--bg", 4.5),
                 ("--muted", "--surface", 4.5), ("--muted", "--surface-2", 4.5), ("--accent-ink", "--accent", 4.5),
                 ("--accent-ink", "--accent-hover", 4.5), ("--focus", "--bg", 3.0), ("--focus", "--surface", 3.0))
        root = self.root_decls()
        for t in self.themes.THEMES:
            d = self.theme_decls(t["key"])
            for fg, bg, need in pairs:
                self.assertGreaterEqual(_contrast(d[fg], d[bg]), need, (t["key"], fg, bg))
            for name, need in (("--star-off", 3.0), ("--danger", 4.5), ("--ok", 4.5)):
                self.assertRegex(root.get(name, ""), r"^#[0-9a-fA-F]{6}$", name)
                self.assertGreaterEqual(_contrast(root[name], d["--surface-2"]), need, (t["key"], name))

    # -- fonts -----------------------------------------------------------------------------------
    def test_three_font_faces_served_by_the_whitelist(self):
        faces = [body for sel, body, _ in self.rules if sel == "@font-face"]
        self.assertEqual(len(faces), 3)
        served = {k for k, v in web.Handler.STATIC_FILES.items() if v == "font/woff2"}
        used = set()
        for body in faces:
            self.assertRegex(body, r"font-display\s*:\s*swap")
            m = re.search(r'src\s*:\s*url\("/static/(fonts/[a-z0-9-]+\.woff2)"\)\s*format\("woff2"\)', body)
            self.assertIsNotNone(m, body)
            used.add(m.group(1))
        self.assertEqual(used, served)
        for name in served:
            with open(os.path.join(ROOT, "static", *name.split("/")), "rb") as f:
                self.assertEqual(f.read(4), b"wOF2", name)

    def test_font_tokens_and_no_synthesis_or_italic(self):
        text = self.css
        for token in ("--font", "--font-display", "--font-note"):
            self.assertRegex(text, r"(?<![\w-])%s\s*:" % token)
        self.assertNotRegex(text, r"font-style\s*:\s*italic")
        self.assertRegex(text, r"font-synthesis\s*:\s*none")

    # -- accent discipline and tokens ------------------------------------------------------------
    def test_accent_only_on_primary_rules(self):
        allowed = ("btn-add", "primary", "why", "swatch", "checkbox", "radio", "season-check")
        for sel, body, _ in self.rules:
            if re.search(r"var\(--accent(-hover)?\)", body):
                self.assertTrue(any(a in sel for a in allowed), sel)

    def test_removed_tokens_are_gone_and_every_var_is_declared(self):
        self.assertNotRegex(self.css, r"var\(--(accent-text|accent-soft|accent-glow|match-text)\)")
        declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", self.css))
        set_by_js = {"--h", "--from-scale", "--origin-x", "--origin-y", "--hero-ms"}
        for name in set(re.findall(r"var\(\s*(--[a-z0-9-]+)", self.css)):
            self.assertTrue(name in declared or name in set_by_js, name)

    # -- no template slop ------------------------------------------------------------------------
    def test_no_uppercase_smallcaps_radial_gradients_or_pills(self):
        for needle in ("uppercase", "small-caps", "radial-gradient", "999px"):
            self.assertNotIn(needle, self.css)

    def test_gradients_only_where_the_spec_allows(self):
        for sel, body, _ in self.rules:
            if "linear-gradient" in body:
                self.assertTrue(any(k in sel for k in ("hero-art", "title-hero", "topbar", "track-arrow")), sel)

    def test_hover_never_moves_things(self):
        for sel, body, _ in self.rules:
            if ":hover" in sel:
                self.assertNotRegex(body, r"(?<![\w-])transform\s*:", sel)

    # -- focus -----------------------------------------------------------------------------------
    def test_global_focus_ring(self):
        rules = [b for sel, b, _ in self.rules if sel in (":focus-visible", "*:focus-visible", ":where(:focus-visible)")]
        self.assertTrue(rules, "a global :focus-visible rule")
        body = rules[0]
        m = re.search(r"outline\s*:\s*(\d+)px\s+solid\s+var\(--focus\)", body)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(int(m.group(1)), 2)
        offset = re.search(r"outline-offset\s*:\s*(\d+)px", body)
        self.assertTrue(offset and int(offset.group(1)) > 0)

    def test_outline_removed_only_where_focus_is_drawn_elsewhere(self):
        for sel, body, _ in self.rules:
            if re.search(r"(?<![\w-])outline\s*:\s*(none|0)\s*(;|$)", body.strip()):
                self.assertTrue(".theme-radio" in sel or ".search-type input" in sel, sel)

    def test_poster_link_focus_ring_sits_on_the_poster_box(self):
        self.assertTrue(any(".card-poster:has(> .poster-link:focus-visible)" in sel for sel, _, _ in self.rules))

    # -- touch targets ---------------------------------------------------------------------------
    def test_targets_are_44px(self):
        for target in ("button", ".btn-add", ".btn-ghost", ".nav-item", ".subtab", ".pager a", ".list-toggle",
                       ".list-link", ".hero-dot", ".modal-close"):
            sizes = [_dim(body, ("min-height", "height")) for _, body, _ in self.rules_for(target)]
            self.assertTrue(any(s is not None and s >= 44 for s in sizes), target)
        text_inputs = [_dim(body, ("min-height", "height")) for sel, body, _ in self.rules
                       if "select" in sel or 'input[type="text"]' in sel or 'input[type="search"]' in sel]
        self.assertTrue(any(s is not None and s >= 44 for s in text_inputs), "text inputs and selects")

    def test_stars_and_preview_buttons(self):
        stars = [body for _, body, _ in self.rules_for(".star")]
        widths = [_dim(b, ("min-width", "width")) for b in stars]
        heights = [_dim(b, ("min-height", "height")) for b in stars]
        self.assertTrue(any(w is not None and w >= 28 for w in widths))
        self.assertTrue(any(h is not None and h >= 32 for h in heights))
        for w in widths:
            self.assertTrue(w is None or w >= 28)
        for h in heights:
            self.assertTrue(h is None or h >= 32)
        buttons = [_dim(b, ("min-height", "height")) for _, b, _ in self.rules_for(".preview-btn")]
        self.assertIn(36, buttons)                                 # the documented exception

    # -- motion and breakpoints ------------------------------------------------------------------
    def test_reduced_motion_covers_hero_and_preview(self):
        sel = " ".join(s for s, _, media in self.rules if "prefers-reduced-motion: reduce" in media)
        self.assertIn(".hero-slide", sel)
        self.assertIn(".preview", sel)
        self.assertIn("max-width: 820px", self.css)

    # -- dead CSS --------------------------------------------------------------------------------
    @staticmethod
    def _class_tokens(text):
        """Class names the sources can put on an element: class="..." values in pages.py / settings_page.py
        (literal parts, plus quoted literals inside {...}; a trailing "-" is a dynamic prefix) and the
        className / classList arguments in app.js."""
        tokens = set()
        for value in re.findall(r'class="((?:[^"{]|\{[^}]*\})*)"', text):
            outside = re.sub(r"\{[^}]*\}", " ", value)
            tokens.update(outside.split())
            for expr in re.findall(r"\{([^}]*)\}", value):
                for lit in re.findall(r"""["']([^"']*)["']""", expr):
                    tokens.update(lit.split())
        for value in re.findall(r"""class(?:Name)?\s*[:=]\s*["']([^"']*)["']""", text):
            tokens.update(value.split())
        for value in re.findall(r"""setAttribute\("class",\s*"([^"]*)"\)""", text):
            tokens.update(value.split())
        for line in text.splitlines():
            if "className" in line:                    # "track-arrow " + dir, "modal" + (x ? " narrow" : "")
                for lit in re.findall(r"""["']([^"']*)["']""", line):
                    tokens.update(lit.split())
        for args in re.findall(r"classList\.(?:add|remove|toggle|replace|contains)\(([^)]*)\)", text):
            for lit in re.findall(r"""["']([^"']*)["']""", args):
                tokens.update(lit.split())
        return tokens

    def test_every_css_class_is_rendered_somewhere(self):
        tokens = self._class_tokens(self.pages_py + "\n" + self.settings_py + "\n" + self.js)
        prefixes = [t for t in tokens if t.endswith("-")]
        # Built from a variable (dir = prev|next) or passed to a helper as a literal argument.
        tokens |= {"prev", "next"} & set(re.findall(r'"(prev|next)"', self.js))
        tokens |= {c for c in ("title-similar", "why-match") if f'"{c}' in self.pages_py}
        dead = set()
        for sel, _, _ in self.rules:
            if sel.startswith("@"):
                continue
            for cls in re.findall(r"\.(-?[_a-zA-Z][\w-]*)", sel):
                if cls in tokens or any(cls.startswith(p) for p in prefixes):
                    continue
                dead.add(cls)
        self.assertEqual(sorted(dead), [])

    def test_every_new_class_has_a_rule(self):
        selectors = " ".join(sel for sel, _, _ in self.rules)
        for cls in ("card-facts", "card-badges", "runtime", "hero-reason", "hero-overview", "title-body", "title-main",
                    "title-side", "title-details", "title-links", "season-year", "why-because", "ext-icon", "preview-title"):
            self.assertRegex(selectors, r"\.%s(?![\w-])" % cls, cls)


class TestRedesignShell(unittest.TestCase):
    def head(self):
        with mock.patch.object(web, "current_theme", return_value="crimson"):
            html = web._shell("", "home")
        return html[:html.index("</head>")]

    def test_dark_only_metas(self):
        head = self.head()
        self.assertEqual(re.findall(r'<meta name="theme-color"[^>]*>', head), ['<meta name="theme-color" content="#101114">'])
        self.assertIn('<meta name="color-scheme" content="dark">', head)
        self.assertNotIn("prefers-color-scheme", head)

    def test_two_font_preloads_that_the_server_serves(self):
        links = re.findall(r'<link rel="preload"[^>]*>', self.head())
        self.assertEqual(len(links), 2)
        for link in links:
            self.assertIn('as="font" type="font/woff2" crossorigin', link)
            href = re.search(r'href="([^"]+)"', link).group(1)
            self.assertTrue(href.startswith("/static/"))
            self.assertEqual(web.Handler.STATIC_FILES.get(href[len("/static/"):]), "font/woff2", href)

    def test_stylesheet_and_script_strings_unchanged_and_nothing_external(self):
        head = self.head()
        self.assertIn('<link rel="stylesheet" href="/static/app.css"><script src="/static/app.js" defer></script>', head)
        for tag in re.findall(r"<(?:link|script)[^>]*>", head):
            self.assertNotRegex(tag, r"https?://")

    def test_appearance_options_carry_theme_color_only(self):
        import themes
        with mock.patch.object(web, "current_theme", return_value="crimson"):
            html = pages.render_appearance()
        options = re.findall(r'<label class="theme-option"[^>]*>', html)
        self.assertEqual(len(options), 6)
        for t, option in zip(themes.THEMES, options):
            self.assertIn(f'data-theme-color="{t["bg"]}"', option)
        self.assertNotIn("data-meta-", html)
        self.assertNotIn("light", re.search(r'theme-help">(.*?)</p>', html).group(1).lower())

    def test_pages_py_no_longer_reads_bg_light(self):
        with open(os.path.join(ROOT, "pages.py"), encoding="utf-8") as f:
            self.assertNotIn("bg_light", f.read())


class TestRedesignJs(unittest.TestCase):
    def test_hooks(self):
        js = read_static("app.js")
        for needle in ("data-theme-color", "card-badges", "preview-title"):
            self.assertIn(needle, js)
        for gone in ("data-meta-light", "data-meta-dark", "prefers-color-scheme: light", "poster-top"):
            self.assertNotIn(gone, js)
        self.assertEqual(js.count("innerHTML"), 1)
        self.assertNotRegex(js, r"https?://")


class TestRedesignMarkup(PatchedState):
    def setUp(self):
        super().setUp()
        self.old_cfg = (config.RADARR_URL, config.RADARR_API_KEY, config.SONARR_URL, config.SONARR_API_KEY)
        self.addCleanup(lambda: (setattr(config, "RADARR_URL", self.old_cfg[0]), setattr(config, "RADARR_API_KEY", self.old_cfg[1]),
                                 setattr(config, "SONARR_URL", self.old_cfg[2]), setattr(config, "SONARR_API_KEY", self.old_cfg[3])))
        config.RADARR_URL = config.RADARR_API_KEY = config.SONARR_URL = config.SONARR_API_KEY = ""

    def home(self, items, **kw):
        self.use(browse_result(items, **kw))
        return pages.render_browse("all")

    # -- hero ------------------------------------------------------------------------------------
    def test_reason_html(self):
        item = {"reason": "Because you watched Arrival and Dune", "because": ["Arrival", "Dune", "Heat"]}
        self.assertEqual(pages._reason_html(item), "Because you watched <strong>Arrival</strong> and <strong>Dune</strong>")
        one = {"reason": "Because you watched Arrival", "because": ["Arrival", "Dune"]}
        self.assertEqual(pages._reason_html(one), "Because you watched <strong>Arrival</strong>")
        self.assertEqual(pages._reason_html({"reason": "Popular right now", "because": ["Arrival"]}), "Popular right now")
        self.assertEqual(pages._reason_html({"reason": "Because you watched Arrival and Dune", "because": ["Arrival"]}),
                         "Because you watched Arrival and Dune")
        self.assertEqual(pages._reason_html({"reason": "", "because": ["A"]}), "")
        self.assertEqual(pages._reason_html({}), "")
        evil = {"reason": "Because you watched <b> and <i>", "because": ["<b>", "<i>"]}
        self.assertEqual(pages._reason_html(evil),
                         "Because you watched <strong>&lt;b&gt;</strong> and <strong>&lt;i&gt;</strong>")
        self.assertEqual(pages._reason_html({"reason": "<img src=x>", "because": []}), "&lt;img src=x&gt;")

    def test_hero_slide_order_and_pieces(self):
        hero = hero_html(self.home([rec(1, reason="Because you watched Arrival", because=["Arrival"])]))
        self.assertLess(hero.index("hero-reason"), hero.index('class="title hero-title"'))
        self.assertIn('<p class="reason hero-reason">Because you watched <strong>Arrival</strong></p>', hero)
        self.assertLess(hero.index("hero-meta"), hero.index('class="hero-overview"'))
        self.assertLess(hero.index('class="hero-overview"'), hero.index('class="hero-actions"'))
        self.assertIn('<p class="hero-overview">Overview 1.</p>', hero)
        self.assertIn('<span class="hero-genres">Drama, Sci-Fi</span>', hero)
        for gone in ("kicker", "hero-glyph", "&middot;"):
            self.assertNotIn(gone, hero)

    def test_hero_without_reason_or_overview_omits_them(self):
        hero = hero_html(self.home([rec(1, reason="", overview="")]))
        self.assertNotIn("hero-reason", hero)
        self.assertNotIn("hero-overview", hero)

    def test_hero_hostile_overview_is_escaped(self):
        hero = hero_html(self.home([rec(1, overview="<script>x</script>")]))
        self.assertNotIn("<script>", hero)
        self.assertIn('<p class="hero-overview">&lt;script&gt;x&lt;/script&gt;</p>', hero)

    # -- rows ------------------------------------------------------------------------------------
    def test_row_card_markup(self):
        html = self.home([rec(n) for n in range(1, 13)])
        top = re.search(r'<section class="row" data-row="top10".*?</section>', html, re.S).group(0)
        card = re.search(r'<article class="card row-card" data-card="movie-101">.*?</article>', top, re.S).group(0)
        self.assertTrue(card.startswith('<article class="card row-card" data-card="movie-101"><span class="rank" aria-hidden="true">1</span>'
                                        '<div class="card-poster"><a class="poster-link"'))
        self.assertNotIn("poster-top", card)
        self.assertNotIn("<h4 class=\"title\">Film 1 (", card)
        self.assertIn('<h4 class="title">Film 1</h4>', card)
        self.assertIn('<p class="card-facts"><span class="match">98%<span class="visually-hidden"> match</span></span> '
                      '<span>2001</span></p>', card)
        poster = re.search(r'<div class="card-poster">.*?</a></div>', card, re.S).group(0)
        self.assertNotIn("rank", poster)
        self.assertNotIn("match", poster)

    def test_row_card_tag_moves_into_the_facts(self):
        with mock.patch.object(web, "owned_keys", return_value={("movie", 101)}):
            html = self.home([rec(n) for n in range(1, 9)])
        card = re.search(r'<article class="card row-card" data-card="movie-101">.*?</article>', html, re.S).group(0)
        self.assertRegex(card, r'<p class="card-facts">.*<span class="lib-tag">In library</span></p>')

    def test_row_card_without_match_year_or_tag_has_no_facts(self):
        html = pages._row_card(rec(1, match=None, year=None), "/", False)
        self.assertNotIn("card-facts", html)

    def test_library_row_card_has_no_tag(self):
        lib = [{"media_type": "movie", "tmdb_id": 900, "title": "Owned", "year": 2020, "added_at": "2024-01-01T00:00:00Z", "url": None}]
        html = self.home([rec(1)], library=lib)
        row = re.search(r'<section class="row" data-row="library_new".*?</section>', html, re.S).group(0)
        self.assertNotIn("lib-tag", row)
        self.assertIn('<h4 class="title"><a href="/title/movie/900">Owned</a></h4><p class="card-facts"><span>2020</span></p>', row)

    # -- grid cards ------------------------------------------------------------------------------
    def assert_clean_poster(self, card):
        self.assertNotIn("poster-top", card)
        self.assertNotIn('class="kind"', card)
        self.assertNotIn('class="badges"', card)
        self.assertNotIn("badge alt", card)
        poster = re.search(r'<div class="card-poster">.*?</div>', card, re.S).group(0)
        self.assertNotIn("<span", poster)

    def test_ai_card(self):
        card = web._card(ITEM, "/ai", True)
        self.assert_clean_poster(card)
        self.assertIn('<h3 class="title">Heat (1995)</h3>'
                      '<p class="card-facts"><span class="match">88% match</span> <span>Movie</span></p>'
                      '<p class="card-badges"><span class="badge">New</span><span class="badge">Trending</span></p>'
                      '<details class="card-details">', card)

    def test_ai_card_without_flags_has_no_badge_line(self):
        self.assertNotIn("card-badges", web._card(dict(ITEM, new=False, trending=False), "/ai", True))

    def test_search_card(self):
        card = pages._search_card(sresult(status="plex", watched=True, arr_state="missing"), "/search", False)
        self.assert_clean_poster(card)
        self.assertIn('<h3 class="title">Dune (2021)</h3><p class="card-facts"><span>Movie</span></p>'
                      '<p class="card-badges"><span class="lib-tag status-tag status-plex">In library</span>'
                      '<span class="badge">Watched</span></p><p class="card-sub arr-line">Missing</p><details', card)
        self.assertNotIn("card-badges", pages._search_card(sresult(), "/search", False))

    def test_list_card(self):
        view = {"list": {"id": 3, "name": "L", "description": "", "kind": "custom", "count": 1}, "can_move": False}
        item = {"media_type": "tv", "tmdb_id": 4, "title": "Show", "year": 2010, "poster_url": None, "status": "radarr",
                "arr_state": None, "episodes": None, "watched": False, "dismissed": True, "match": 71, "stars": None,
                "lists": [3], "on_watchlist": False}
        card = pages._list_card(item, view, "/lists/3")
        self.assert_clean_poster(card)
        self.assertIn('<h3 class="title">Show (2010)</h3><p class="card-facts"><span class="match">71% match</span> <span>TV</span></p>'
                      '<p class="card-badges"><span class="lib-tag status-tag status-radarr">In Radarr</span>'
                      '<span class="badge">Not interested</span></p>', card)

    def test_library_card(self):
        item = {"media_type": "movie", "tmdb_id": 5, "title": "Arrival", "year": 2016, "added_at": "2024-03-01T10:00:00Z",
                "watched": True, "progress": None, "poster_key": None, "url": None, "request_state": "requested"}
        card = pages._library_card(item)
        self.assert_clean_poster(card)
        self.assertIn('<h3 class="title"><a href="/title/movie/5">Arrival</a></h3>'
                      '<p class="card-sub"><span>Movie</span> <span>2016</span></p>'
                      '<p class="card-badges"><span class="badge">Watched</span></p>', card)
        added = pages._library_card(item, show_added=True)
        self.assertIn('<p class="card-sub"><span>Movie</span> <span>Added 2024-03-01</span></p>', added)
        self.assertIn('<span class="badge req-state req-requested">Requested</span>', added)
        bare = pages._library_card(dict(item, year=None, watched=False))
        self.assertIn('<p class="card-sub"><span>Movie</span></p>', bare)
        self.assertNotIn("card-badges", bare)

    def test_watched_card(self):
        item = {"media_type": "movie", "tmdb_id": 5, "title": "Arrival", "year": 2016, "last_viewed": "2024-05-02T10:00:00Z",
                "view_count": 3, "poster_key": None, "poster_url": None, "url": None, "stars": None, "plex_stars": None}
        card = pages._watched_card(item, "/library?type=watched")
        self.assert_clean_poster(card)
        self.assertIn('<p class="card-sub"><span>Movie</span> <span>Watched 2024-05-02</span> <span>3 plays</span></p>', card)
        once = pages._watched_card(dict(item, view_count=1), "/library?type=watched")
        self.assertNotIn("plays", once)
        self.assertNotIn("&middot;", card)

    # -- title page ------------------------------------------------------------------------------
    def title(self, media_type="movie", **kw):
        return pages.render_title(title_view(media_type, 9 if media_type == "tv" else 7, **kw))

    def test_title_page_layout(self):
        rec_data = {"match": 92, "reason": "Because you watched A and B", "matches": ["Crime"], "because": ["A", "B"]}
        html = self.title("tv", rec=rec_data, seasons=[season_row(1), season_row(2, air_date=None)])
        self.assertNotIn("kicker", html)
        body = re.search(r'<div class="title-body">.*</aside></div>', html, re.S).group(0)
        main, side = body.split('<aside class="title-side" aria-label="Details and cast">')
        self.assertTrue(main.startswith('<div class="title-body"><div class="title-main">'))
        for needle in ('title-section title-overview', 'class="title-section why"', 'section class="title-section seasons"'):
            self.assertIn(needle, main)
        self.assertNotIn("title-facts", main)
        self.assertNotIn('class="cast-list"', main)
        self.assertIn('<section class="title-section title-details"><h2>Details</h2><dl class="title-facts">', side)
        self.assertIn('<p class="title-links">', side)
        self.assertIn('<section class="title-section cast"><h2>Cast</h2><ul class="cast-list">', side)
        self.assertLess(side.index("title-details"), side.index("title-section cast"))
        self.assertIn('<p class="why-because">Because you watched A, B</p>', main)
        self.assertNotIn('<p class="muted">Because', html)
        self.assertIn('<span class="season-eps">8 episodes</span> <span class="season-year">2020</span>', main)
        self.assertEqual(main.count("season-year"), 1)                  # season 2 has no air date
        self.assertLess(html.index("</aside></div>"), html.index("</article>"))

    def test_title_similar_row_is_after_the_body(self):
        html = self.title(similar=[{"media_type": "movie", "tmdb_id": 3, "title": "Other", "year": 2001, "poster_url": None}])
        self.assertLess(html.index("</aside></div>"), html.index('class="row title-similar"'))

    def test_title_without_facts_or_links_has_no_details_section(self):
        view = title_view()
        view["item"].update(url=None, release_date=None, year=None, runtime=None, vote_count=0, directors=[])
        view["extras"].update(status="", studios=[], imdb_id="", crew=[])
        html = pages.render_title(view)
        self.assertNotIn("title-details", html)
        self.assertIn('<aside class="title-side"', html)

    def test_title_actions_order(self):
        self.configure_radarr()
        html = self.title(can_add=True)
        actions = re.search(r'<div class="title-actions">.*?</div></div></div></section>', html, re.S).group(0)
        order = ["data-add-dialog", "list-toggle-form", 'class="list-link"', "trailer-link", 'action="/dismiss"', '<form class="rate"']
        positions = [actions.index(x) for x in order]
        self.assertEqual(positions, sorted(positions))

    def configure_radarr(self):
        config.RADARR_URL = config.RADARR_API_KEY = "x"

    def test_tv_genres_use_commas(self):
        html = self.title("tv")
        self.assertIn('<span class="title-genres">Crime, Drama</span>', html)
        self.assertNotIn("&middot;", html)

    def test_season_picker_year_span(self):
        picker = pages._season_picker({"known": True, "tracked": False, "seasons": [season_row(1, episodes=None)]}, "add")
        self.assertNotIn("season-eps", picker)
        self.assertIn('<span class="season-year">2020</span>', picker)

    # -- meta lines read as separate words ---------------------------------------------------------
    @staticmethod
    def text_of(html):
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()

    def test_meta_lines_have_literal_spaces_between_items(self):
        hero = hero_html(self.home([rec(1, certification="12A", runtime=90)]))
        meta = re.search(r'<p class="hero-meta card-meta">(.*?)</p>', hero, re.S).group(1)
        self.assertEqual(self.text_of(meta), "98% match 2001 12A 1h 30m Drama, Sci-Fi")
        rec_data = {"match": 92, "reason": "r", "matches": [], "because": []}
        html = self.title(rec=rec_data)
        meta = re.search(r'<p class="card-meta title-meta">(.*?)</p>', html, re.S).group(1)
        self.assertEqual(self.text_of(meta), "92% match 1995 R 2h 50m Crime, Drama")
        search = pages._search_card(sresult(), "/search", False)
        meta = re.search(r'<p class="card-meta">(.*?)</p>', search, re.S).group(1)
        self.assertEqual(self.text_of(meta), "Movie 2021 TMDB 8.0")
        tv = self.title("tv", seasons=[season_row(1, state="available")])
        row = re.search(r'<li class="season-row">.*?</li>', tv, re.S).group(0)
        self.assertEqual(self.text_of(row), "Season 1 8 episodes 2020 Available")
        picker = pages._season_picker({"known": True, "tracked": False,
                                       "seasons": [season_row(1, state="available", requested=True)]}, "add")
        self.assertIn("Season 1 8 episodes 2020 Requested Available", self.text_of(picker))

    # -- external links --------------------------------------------------------------------------
    def test_ext_icon_is_decorative_and_everywhere(self):
        html = self.title()
        self.assertGreaterEqual(html.count('class="ext-icon"'), 2)
        self.assertEqual(html.count('<span class="ext-icon" aria-hidden="true">&#8599;</span>'), html.count("&#8599;"))
        self.assertNotIn(" &#8599;", html)
        self.assertIn('<span class="ext-icon" aria-hidden="true">&#8599;</span>', web._card(dict(ITEM, url="https://www.themoviedb.org/movie/7"), "/", False))


class TestNoSlopInRenderedPages(PatchedState):
    """No middot separators, kickers or shouting capitals in any page the redesign touches."""

    def setUp(self):
        super().setUp()
        self.old_cfg = (config.RADARR_URL, config.RADARR_API_KEY, config.SONARR_URL, config.SONARR_API_KEY)
        self.addCleanup(lambda: (setattr(config, "RADARR_URL", self.old_cfg[0]), setattr(config, "RADARR_API_KEY", self.old_cfg[1]),
                                 setattr(config, "SONARR_URL", self.old_cfg[2]), setattr(config, "SONARR_API_KEY", self.old_cfg[3])))
        config.RADARR_URL = config.RADARR_API_KEY = config.SONARR_URL = config.SONARR_API_KEY = "x"

    def pages_html(self):
        out = {}
        items = [rec(n) for n in range(1, 13)]
        library = [{"media_type": "movie", "tmdb_id": 900, "title": "Owned", "year": 2020, "added_at": "2024-01-01T00:00:00Z", "url": None}]
        self.use(browse_result(items, library=library))
        out["home"] = pages.render_browse("all")
        out["movies"] = pages.render_browse("movie")
        out["tv"] = pages.render_browse("tv")
        rec_data = {"match": 92, "reason": "Because you watched A and B", "matches": ["Crime"], "because": ["A", "B"]}
        out["title-movie"] = pages.render_title(title_view("movie", 7, rec=rec_data, can_add=True))
        out["title-tv"] = pages.render_title(title_view("tv", 9, seasons=[season_row(1), season_row(2)], rec=rec_data))
        lib = {"media_type": "movie", "tmdb_id": 5, "title": "Arrival", "year": 2016, "added_at": "2024-03-01T10:00:00Z",
               "watched": True, "progress": None, "poster_key": None, "url": None, "request_state": "available",
               "seasons": None, "episodes": None, "sources": ["plex", "radarr"], "arr_state": "downloaded", "stars": None,
               "last_viewed": "2024-05-02T10:00:00Z", "view_count": 3, "plex_stars": None, "poster_url": None}
        for tab in ("all", "movie", "tv", "watched", "added"):
            with mock.patch.object(web, "library_items", return_value=[dict(lib, media_type="tv" if tab == "tv" else "movie")]):
                out["library-" + tab] = web.render_library(tab=tab)
        with mock.patch.object(web, "search_view", return_value=sview(results=[sresult(status="plex", watched=True)])):
            out["search"] = pages.render_search("dune", "all")
        with mock.patch.object(web, "lists_view", return_value=lists_view(summary(1, count=2), summary(3, "Horror", "custom", 5))):
            out["lists"] = pages.render_lists()
        list_view = {"list": summary(3, "Horror", "custom", 2, "d"), "sort": "manual", "sorts": web.LIST_SORTS, "page": 1,
                     "pages": 1, "total": 1, "can_move": True, "sample": False,
                     "items": [{"media_type": "movie", "tmdb_id": 1, "title": "Film 1", "year": 2001, "poster_url": None,
                                "status": "plex", "arr_state": "missing", "episodes": None, "watched": True, "dismissed": False,
                                "match": 80, "stars": None, "lists": [3], "on_watchlist": False, "position": 1}]}
        out["list"] = pages.render_list(list_view)
        out["ai"] = web.render_ai_page() + web._card(ITEM, "/ai", True)
        out["appearance"] = pages.render_appearance()
        return out

    def test_no_middots_kickers_or_kind_labels(self):
        for name, html in self.pages_html().items():
            for bad in ("&middot;", "·", "&#183;", 'class="kicker"', 'class="kind"', "hero-glyph", "poster-top"):
                self.assertNotIn(bad, html, (name, bad))

    def test_no_shouting_text_nodes(self):
        for name, html in self.pages_html().items():
            html = re.sub(r"<script.*?</script>", "", html, flags=re.S)
            html = re.sub(r'<span class="badge cert">[^<]*</span>', "", html)
            for node in re.findall(r">([^<>]+)<", html):
                letters = re.sub(r"[^A-Za-z]", "", node.replace("TMDB", ""))
                if len(letters) >= 4 and letters.isupper():
                    self.fail(f"{name}: all-uppercase text {node!r}")

    def test_sources_have_no_middots_kickers_or_light_meta(self):
        for fname in ("pages.py", os.path.join("static", "app.js")):
            with open(os.path.join(ROOT, fname), encoding="utf-8") as f:
                text = f.read()
            for bad in ("&middot;", "·", "&#183;", "kicker", "hero-glyph", 'class="kind"'):
                self.assertNotIn(bad, text, (fname, bad))
