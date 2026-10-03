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
        self.assertNotIn("brand-mark", html)
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
        self.assertIn("Top pick for you", hero)

    def test_hero_image_fallbacks(self):
        both = hero_html(self.render([rec(1, backdrop_url="https://i/b.jpg", poster_large_url="https://i/p.jpg")]))
        self.assertIn('<img class="hero-backdrop" src="https://i/b.jpg"', both)
        self.assertIn('<img class="hero-poster" src="https://i/p.jpg"', both)
        self.assertNotIn("hero-glyph", both)
        poster_only = hero_html(self.render([rec(1, poster_url="https://i/small.jpg")]))
        self.assertNotIn("hero-backdrop", poster_only)
        self.assertIn('<img class="hero-poster" src="https://i/small.jpg"', poster_only)
        neither = hero_html(self.render([rec(1)]))
        self.assertIn('<span class="hero-glyph">F</span>', neither)
        self.assertNotIn('<img class="hero-', neither)
        self.assertIn('style="--h:', neither)

    def test_javascript_image_urls_are_dropped(self):
        hero = hero_html(self.render([rec(1, backdrop_url="javascript:alert(1)", poster_large_url="javascript:alert(2)",
                                          poster_url="javascript:alert(3)", url="javascript:alert(4)")]))
        self.assertNotIn("javascript:", hero)
        self.assertIn("hero-glyph", hero)
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
        self.assertIn("<span>2h 46m</span>", meta(runtime=166))
        self.assertIn("<span>46m</span>", meta(runtime=46))
        self.assertIn("<span>2h</span>", meta(runtime=120))
        self.assertIn("<span>3 seasons</span>", meta(media_type="tv", seasons=3))
        self.assertIn("<span>1 season</span>", meta(media_type="tv", seasons=1))
        full = meta(certification="12A", runtime=90)
        self.assertIn('<span class="match">98% match</span>', full)
        self.assertIn('<span class="badge cert">12A</span>', full)
        self.assertIn('<span class="hero-genres">Drama &middot; Sci-Fi</span>', full)
        bare = meta()
        self.assertNotIn("cert", bare)
        self.assertNotRegex(bare, r"\d+m</span>")

    def test_in_library_replaces_add_in_the_hero(self):
        set_config(self, RADARR_URL="x", RADARR_API_KEY="x")
        with_add = hero_html(self.render([rec(1)]))
        self.assertIn('class="btn-add"', with_add)
        self.assertIn("data-add-dialog", with_add)
        owned = hero_html(self.render([rec(1, in_library=True)]))
        with mock.patch.object(web, "owned_keys", return_value={("movie", 101)}):
            owned = hero_html(self.render([rec(1)]))
        self.assertIn('<span class="lib-tag">In library</span>', owned)
        hero_actions = re.search(r'<div class="hero-actions">(.*?)<details', owned, re.S).group(1)
        self.assertNotIn("btn-add", hero_actions)

    def test_add_only_when_configured_and_not_sample(self):
        self.assertNotIn("btn-add", self.render([rec(1)]))
        set_config(self, RADARR_URL="x", RADARR_API_KEY="x")
        html = self.render([rec(1), rec(2, media_type="tv")])
        self.assertIn('href="/add-dialog?type=movie&amp;id=101&amp;return_to=%2F"', html)
        self.assertNotIn("type=tv&amp;id=102", html)        # Sonarr isn't configured
        self.use(browse_result([rec(1)], sample=True))
        self.assertNotIn("btn-add", pages.render_browse("all"))
        self.assertNotIn("/dismiss", pages.render_browse("all"))

    def test_more_info_lives_in_details_with_actions(self):
        hero = hero_html(self.render([rec(1, url="https://www.themoviedb.org/movie/1")]))
        details = re.search(r'<details class="card-details hero-details">.*?</details>', hero, re.S).group(0)
        self.assertIn("More info<span class=\"visually-hidden\">: Film 1</span>", details)
        self.assertIn('class="detail-body"', details)
        self.assertIn("Overview 1.", details)
        self.assertIn('class="ext-link"', details)
        self.assertIn('action="/dismiss" data-enhance="dismiss"', details)
        self.assertIn('<div class="card-poster" hidden>', hero)


class TestBrowseRows(PatchedState):
    def render(self, items, kind="all", library=None, **kw):
        self.use(browse_result(items, library=library), **kw)
        return pages.render_browse(kind)

    def test_row_structure_and_titles(self):
        html = self.render([rec(n) for n in range(1, 13)], library=[
            {"media_type": "movie", "tmdb_id": 900, "title": "Owned", "year": 2020, "added_at": "2024-01-01T00:00:00Z", "url": None}])
        self.assertIn('<div class="rows">', html)
        titles = re.findall(r'<h3 id="row-[^"]*">([^<]*)</h3>', html)
        self.assertEqual(titles, ["Recommended for You", "Because you watched Arrival", "Top 10 picks for you",
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
        self.assertIn('<div class="card-poster" data-open-detail>', card)
        self.assertIn("93% match", card)
        self.assertIn('<h4 class="title">Film 6 (2006)</h4>', card)
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

    def test_library_cards_have_no_data_card_or_actions(self):
        lib = [{"media_type": "movie", "tmdb_id": 900, "title": "<b>Owned</b>", "year": 2020, "added_at": "2024-01-01T00:00:00Z",
                "url": "https://www.themoviedb.org/movie/900"},
               {"media_type": "tv", "tmdb_id": 901, "title": "Show", "year": None, "added_at": None, "url": "javascript:x"}]
        html = self.render([rec(1)], library=lib)
        row = re.search(r'<section class="row" data-row="library_new".*?</section>', html, re.S).group(0)
        self.assertEqual(row.count('<article class="card row-card lib-card">'), 2)
        self.assertNotIn("data-card", row)
        self.assertNotIn("<form", row)
        self.assertNotIn("<details", row)
        self.assertIn('<a href="https://www.themoviedb.org/movie/900"', row)
        self.assertIn("&lt;b&gt;Owned&lt;/b&gt;", row)
        self.assertNotIn("javascript:", row)
        self.assertEqual(row.count('<span class="lib-tag">In library</span>'), 2)
        for card in re.findall(r'<article class="card row-card lib-card">.*?</article>', row, re.S):
            poster = re.search(r'<div class="card-poster">.*?</div>', card, re.S).group(0)
            self.assertNotIn("lib-tag", poster)                      # a direct child of the article instead
            self.assertIn('</div><span class="lib-tag">In library</span><div class="card-info">', card)

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
    def test_rate_form_in_hero_and_row_cards_posts_to_rate(self):
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
        self.assertIn('data-enhance="rate"', hero_html(html))

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
        form = re.search(r'<form class="rate".*?</form>', hero_html(html), re.S).group(0)
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
            form = re.search(r'<form class="rate".*?</form>', hero_html(html), re.S).group(0)
            self.assertIn("Not rated", form, value)
            self.assertNotIn("star-clear", form)
            self.assertNotIn('aria-pressed="true"', form)

    def test_js_preview_icons_poster_click_and_focus_rules(self):
        js = read_static("app.js")
        body = js[js.index("function openPreview"):]
        for needle in ('title: "More info"', '"Not interested"', "\\u2715", "\\uFF0B", "openDetail(card, card.querySelector"):
            self.assertIn(needle, body)
        self.assertIn("closeFallback: true", js)
        self.assertIn("main .row:not([hidden])", js)
        self.assertIn("override", js)
        self.assertIn("inSlide(e.target)", js)
        self.assertIn("lastPreviewCard", js)

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


def _blocks(css, depth_in_media=False):
    """Yield (selector, body, in_light_media) for every rule, with braces matched."""
    out = []
    i = 0
    while i < len(css):
        j = css.find("{", i)
        if j < 0:
            break
        selector = css[i:j].strip()
        depth, k = 1, j + 1
        while k < len(css) and depth:
            depth += {"{": 1, "}": -1}.get(css[k], 0)
            k += 1
        body = css[j + 1:k - 1]
        if selector.startswith("@media"):
            for sel, b, _ in _blocks(body):
                out.append((sel, b, "prefers-color-scheme: light" in selector))
        else:
            out.append((selector, body, False))
        i = k
    return out


class TestThemeShell(unittest.TestCase):
    def render(self, key):
        with mock.patch.object(web, "current_theme", return_value=key):
            return pages.render_appearance()

    def test_data_theme_and_metas(self):
        html = self.render("ocean")
        self.assertIn('<html lang="en" data-theme="ocean">', html)
        self.assertIn('<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#0c1224">', html)
        self.assertIn('<meta name="theme-color" media="(prefers-color-scheme: light)" content="#eef2f9">', html)
        self.assertEqual(html.count('name="theme-color"'), 2)
        self.assertNotIn('name="theme-color" content=', html)

    def test_unknown_and_unpatched_render_amber(self):
        self.assertIn('data-theme="amber"', self.render("<script>"))
        self.assertIn('data-theme="amber"', pages.render_appearance())


class TestAppearancePage(unittest.TestCase):
    def render(self, key="crimson", msg=""):
        with mock.patch.object(web, "current_theme", return_value=key):
            return pages.render_appearance(msg)

    def test_options_in_registry_order(self):
        import themes
        html = self.render()
        keys = re.findall(r'<label class="theme-option" data-theme="(\w+)" data-meta-dark="(#\w+)" data-meta-light="(#\w+)"', html)
        self.assertEqual([k[0] for k in keys], [t["key"] for t in themes.THEMES])
        self.assertEqual([(k[1], k[2]) for k in keys], [(t["bg"], t["bg_light"]) for t in themes.THEMES])

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
    @classmethod
    def setUpClass(cls):
        import themes
        cls.themes = themes
        cls.raw = read_static("app.css")
        cls.css = _strip_css(cls.raw)
        cls.blocks = [(i, sel, body, light) for i, (sel, body, light) in enumerate(_blocks(cls.css))]

    def decls(self, body):
        return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", body))

    def find(self, key, light):
        sel = ':root, [data-theme="amber"]' if key == "amber" else f'[data-theme="{key}"]'
        return [b for b in self.blocks if b[1] == sel and b[3] == light]

    def test_one_dark_and_one_light_block_per_theme_with_exact_tokens(self):
        for t in self.themes.THEMES:
            for light in (False, True):
                found = self.find(t["key"], light)
                self.assertEqual(len(found), 1, (t["key"], light))
                self.assertEqual(sorted(self.decls(found[0][2])), sorted(self.themes.TOKENS), (t["key"], light))

    def test_no_unknocompass_theme_keys(self):
        keys = set(re.findall(r'data-theme="([^"]*)"', self.css))
        self.assertLessEqual(keys, set(self.themes.BY_KEY))

    def test_bg_matches_registry(self):
        for t in self.themes.THEMES:
            self.assertEqual(self.decls(self.find(t["key"], False)[0][2])["--bg"], t["bg"], t["key"])
            self.assertEqual(self.decls(self.find(t["key"], True)[0][2])["--bg"], t["bg_light"], t["key"])

    def test_block_order(self):
        amber = [self.find("amber", False)[0][0], self.find("amber", True)[0][0]]
        for t in self.themes.THEMES[1:]:
            dark, light = self.find(t["key"], False)[0][0], self.find(t["key"], True)[0][0]
            self.assertGreater(dark, max(amber), t["key"])
            self.assertGreater(light, dark, t["key"])

    def test_amber_unchanged(self):
        d = self.decls(self.find("amber", False)[0][2])
        self.assertEqual(d["--accent"], "#ffb224")
        self.assertEqual(d["--bg"], "#0a0c11")

    def test_amber_literals_only_in_custom_properties(self):
        for line in self.css.splitlines():
            if re.search(r"#ffb224|#ffc557|255, 178, 36", line, re.I):
                self.assertRegex(line, r"^\s*--[a-z0-9-]+:", line)

    def test_new_tokens_used(self):
        rules = {sel: body for sel, body, _ in _blocks(self.css)}
        self.assertIn("var(--match-text)", rules[".match"])

    def test_no_brand_names(self):
        self.assertIsNone(BRANDS.search(self.raw))


class TestThemeJs(unittest.TestCase):
    def test_hooks(self):
        js = read_static("app.js")
        for needle in ("data-enhance=theme", "pageshow", "compass_theme", "theme-color"):
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
