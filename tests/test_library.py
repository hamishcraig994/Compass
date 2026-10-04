"""Backend half of the Library page and personal ratings: the ratings table, the query/list helpers in
web.py, and the library/watched/thumbs snapshot. No page markup, no network."""
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["SAMPLE"] = "1"
import config
import db
import sample
import sources
import tmdb
import web


class DbCase(unittest.TestCase):
    def setUp(self):
        self._old = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old)


class TestRatingsTable(DbCase):
    def test_connect_twice_is_fine_and_keeps_data(self):
        db.set_rating("movie", 1, 4)
        db._connect().close()
        db._connect().close()
        self.assertEqual(db.ratings(), {("movie", 1): 4})

    def test_round_trip_replace_and_clear(self):
        self.assertEqual(db.ratings(), {})
        db.set_rating("movie", 1, 4)
        db.set_rating("tv", 1, 2)
        db.set_rating("movie", 1, 5)  # replaces
        self.assertEqual(db.ratings(), {("movie", 1): 5, ("tv", 1): 2})
        db.clear_rating("movie", 1)
        db.clear_rating("movie", 1)  # no-op when absent
        db.clear_rating("movie", 999)
        self.assertEqual(db.ratings(), {("tv", 1): 2})

    def test_out_of_range_or_wrong_type_raises_and_writes_nothing(self):
        for bad in (0, 6, -1, "3", 3.5, None, True):
            with self.assertRaises(ValueError, msg=repr(bad)):
                db.set_rating("movie", 1, bad)
        self.assertEqual(db.ratings(), {})

    def test_table_constraint_rejects_bad_stars_too(self):
        conn = db._connect()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO ratings VALUES ('movie', 1, 9, 'x')")
        conn.close()

    def test_rated_at_is_stored(self):
        db.set_rating("movie", 1, 3)
        conn = db._connect()
        (rated_at,) = conn.execute("SELECT rated_at FROM ratings").fetchone()
        conn.close()
        self.assertRegex(rated_at, r"^\d{4}-\d\d-\d\dT")

    def test_existing_database_without_the_table_is_upgraded(self):
        conn = sqlite3.connect(db.DB_PATH)
        conn.execute("CREATE TABLE dismissed (media_type TEXT NOT NULL, tmdb_id INTEGER NOT NULL, "
                     "PRIMARY KEY (media_type, tmdb_id))")
        conn.execute("INSERT INTO dismissed VALUES ('movie', 7)")
        conn.commit()
        conn.close()
        self.assertEqual(db.ratings(), {})
        self.assertEqual(db.dismissed(), {("movie", 7)})


class TestTmdbCachedDetails(DbCase):
    def test_reads_the_cache_and_never_requests(self):
        client = tmdb.TmdbClient("k" * 32)
        with mock.patch.object(tmdb, "get_json", side_effect=AssertionError("network")):
            self.assertIsNone(client.cached_details("movie", 5))
            db.cache_put("details:v2:movie:5", {"title": "X"})
            self.assertEqual(client.cached_details("movie", 5), {"title": "X"})
            self.assertIsNone(client.cached_details("tv", 5))


class TestParseListQuery(unittest.TestCase):
    def parse(self, **q):
        return web.parse_list_query({k: [v] for k, v in q.items()})

    def test_defaults(self):
        self.assertEqual(web.parse_list_query({}), {"tab": "all", "q": "", "sort": "added", "show": "all", "page": 1,
                                                          "source": "all"})

    def test_valid_values(self):
        self.assertEqual(self.parse(type="watched", q="  dune ", sort="rating", show="rated", page="3"),
                         {"tab": "watched", "q": "dune", "sort": "rating", "show": "rated", "page": 3,
                          "source": "all"})

    def test_source_is_validated_per_tab(self):
        self.assertEqual(self.parse(type="tv", source="wanted")["source"], "wanted")
        self.assertEqual(self.parse(source="arr")["source"], "arr")
        self.assertEqual(self.parse(source="bogus")["source"], "all")
        self.assertEqual(self.parse(type="watched", source="wanted")["source"], "all")
        self.assertEqual(self.parse(type="added", source="plex")["source"], "all")

    def test_every_tab_has_valid_defaults(self):
        for tab in web.LIBRARY_TABS:
            sorts, shows = web.LIST_OPTIONS[tab]
            self.assertEqual(self.parse(type=tab)["sort"], sorts[0])
            self.assertEqual(self.parse(type=tab)["show"], shows[0])

    def test_bad_tab_falls_back(self):
        for bad in ("<script>", "", "WATCHED", "all ", "../x"):
            self.assertEqual(self.parse(type=bad)["tab"], "all", bad)

    def test_sort_and_show_are_validated_against_the_tab(self):
        self.assertEqual(self.parse(type="watched", sort="added")["sort"], "recent")
        self.assertEqual(self.parse(type="movie", sort="rating")["sort"], "added")
        self.assertEqual(self.parse(type="movie", show="rated")["show"], "all")
        self.assertEqual(self.parse(type="watched", show="unwatched")["show"], "all")
        self.assertEqual(self.parse(type="added", show="watched")["show"], "all")

    def test_bad_pages(self):
        for bad in ("-1", "0", "²", "x", "", "1.5", "9" * 13, " 2"):
            self.assertEqual(self.parse(page=bad)["page"], 1, bad)

    def test_q_is_cut_to_100_chars(self):
        self.assertEqual(len(self.parse(q="a" * 500)["q"]), 100)
        self.assertEqual(self.parse(q="   ")["q"], "")


def li(title, media_type="movie", **kw):
    return {"media_type": media_type, "tmdb_id": abs(hash(title)) % 1000, "title": title, "year": None,
            "added_at": None, "watched": False, **kw}


class TestListView(unittest.TestCase):
    def titles(self, items, **kw):
        return [i["title"] for i in web.list_view(items, **kw)["items"]]

    def test_type_filter_and_search(self):
        items = [li("Alpha"), li("Beta", "tv"), li("alphabet", "tv")]
        self.assertEqual(self.titles(items, tab="tv", sort="title"), ["alphabet", "Beta"])
        self.assertEqual(self.titles(items, tab="all", q="ALPHA", sort="title"), ["Alpha", "alphabet"])
        self.assertEqual(self.titles(items, tab="watched", sort="title"), ["Alpha", "alphabet", "Beta"])

    def test_show_filters(self):
        items = [li("A", watched=True, stars=4), li("B", watched=False, stars=None), li("C", watched=True, stars=None)]
        self.assertEqual(self.titles(items, show="watched", sort="title"), ["A", "C"])
        self.assertEqual(self.titles(items, show="unwatched"), ["B"])
        self.assertEqual(self.titles(items, tab="watched", show="rated"), ["A"])
        self.assertEqual(self.titles(items, tab="watched", show="unrated", sort="title"), ["B", "C"])

    def test_sorts_put_missing_values_last_and_break_ties_by_title(self):
        items = [li("b", year=2000, added_at="2026-01-01"), li("A", year=2010, added_at="2026-03-01"),
                 li("c", year=None, added_at=None), li("D", year=2000, added_at="2026-01-01")]
        self.assertEqual(self.titles(items, sort="title"), ["A", "b", "c", "D"])
        self.assertEqual(self.titles(items, sort="year"), ["A", "b", "D", "c"])
        self.assertEqual(self.titles(items, sort="added"), ["A", "b", "D", "c"])

    def test_recent_and_rating_sorts(self):
        items = [li("a", last_viewed="2026-01-01", stars=None, plex_stars=5),
                 li("b", last_viewed="2026-02-01", stars=2, plex_stars=5),
                 li("c", last_viewed=None, stars=None, plex_stars=None),
                 li("d", last_viewed="2026-02-01", stars=5, plex_stars=None)]
        self.assertEqual(self.titles(items, tab="watched", sort="recent"), ["b", "d", "a", "c"])
        # personal stars beat Plex's: a (plex 5) > d (you 5, tie broken by title) ... b (you 2); nothing at all is last
        self.assertEqual(self.titles(items, tab="watched", sort="rating"), ["a", "d", "b", "c"])

    def test_pagination_clamps(self):
        items = [li(f"T{n:03d}") for n in range(100)]
        view = web.list_view(items, sort="title", page=2, per_page=48)
        self.assertEqual((view["total"], view["page"], view["pages"], len(view["items"])), (100, 2, 3, 48))
        self.assertEqual(view["items"][0]["title"], "T048")
        last = web.list_view(items, sort="title", page=99, per_page=48)
        self.assertEqual((last["page"], len(last["items"])), (3, 4))
        self.assertEqual(web.list_view(items, page=-5)["page"], 1)

    def test_empty_has_one_page(self):
        self.assertEqual(web.list_view([]), {"items": [], "total": 0, "page": 1, "pages": 1})

    def test_does_not_mutate_the_input(self):
        items = [li("b"), li("a")]
        web.list_view(items, sort="title")
        self.assertEqual([i["title"] for i in items], ["b", "a"])

    def test_default_page_size(self):
        self.assertEqual(web.LIST_PAGE_SIZE, 48)
        self.assertEqual(len(web.list_view([li(f"T{n}") for n in range(60)])["items"]), 48)


class TestWatchedAndLibraryItems(DbCase):
    RESULT = {"watched": [
        {"media_type": "movie", "tmdb_id": 1, "title": "A", "year": 2020, "last_viewed": None, "user_rating": 9.0,
         "view_count": 1, "progress": None, "poster_key": None, "poster_url": None, "url": None},
        {"media_type": "tv", "tmdb_id": 1, "title": "B", "year": 2020, "last_viewed": None, "user_rating": None,
         "view_count": 1, "progress": None, "poster_key": None, "poster_url": None, "url": None}],
        "library": [{"title": "L"}]}

    def live(self):
        patch = mock.patch.object(sources, "use_sample", return_value=False)
        patch.start()
        self.addCleanup(patch.stop)

    def test_live_overlays_personal_stars_and_plex_stars(self):
        self.live()
        db.set_rating("movie", 1, 2)
        items = web.watched_items(self.RESULT)
        self.assertEqual([(i["stars"], i["plex_stars"]) for i in items], [(2, 5), (None, None)])
        self.assertNotIn("stars", self.RESULT["watched"][0])  # copies

    def test_sample_mode_ignores_stored_ratings(self):
        db.set_rating("movie", 1, 2)
        self.assertEqual([i["stars"] for i in web.watched_items(self.RESULT)], [None, None])
        self.assertEqual(web.watched_items(self.RESULT)[0]["plex_stars"], 5)

    def test_no_result(self):
        self.assertEqual(web.watched_items(None), [])
        self.assertEqual(web.watched_items({}), [])
        self.assertIsNone(web.library_items(None, "all"))
        self.assertIsNone(web.library_items({}, "movie"))
        self.assertEqual(web.library_items(None, "watched"), [])

    def test_library_tabs_use_the_snapshot(self):
        for tab in ("all", "movie", "tv"):
            (item,) = web.library_items(self.RESULT, tab)  # merged entries: the Plex entry plus sources etc.
            self.assertEqual((item["title"], item["sources"]), ("L", ["plex"]))
        self.assertIsNone(web.library_items({"library": None}, "all"))

    def test_added_tab_needs_no_build(self):
        self.assertEqual(web.library_items(None, "added"), [])
        db.record_added({"media_type": "movie", "tmdb_id": 5, "title": "M", "year": 2020,
                         "poster_url": "https://image.tmdb.org/p.jpg", "url": "https://www.themoviedb.org/movie/5"})
        (item,) = web.library_items(None, "added")
        self.assertEqual((item["media_type"], item["tmdb_id"], item["title"], item["year"], item["watched"],
                          item["poster_key"], item["poster_url"], item["progress"]),
                         ("movie", 5, "M", 2020, False, None, "https://image.tmdb.org/p.jpg", None))
        self.assertTrue(item["added_at"])


class TestSampleLibrary(unittest.TestCase):
    def test_sample_library_items(self):
        items = sample.library_items()
        by_title = {i["title"]: i for i in items}
        self.assertTrue(by_title["Interstellar"]["watched"])
        self.assertFalse(by_title["Dune"]["watched"])
        self.assertTrue(by_title["Severance"]["watched"])
        self.assertEqual(len(items), len(sample._WATCHED) + len(sample._EXTRA_IN_LIBRARY))
        for i in items:
            self.assertIsNone(i["poster_key"])
            self.assertTrue(i["added_at"])
            self.assertNotIn("thumb", i)


if __name__ == "__main__":
    unittest.main()


def plex_entry(tmdb_id, title, media_type="movie", **extra):
    return {"media_type": media_type, "tmdb_id": tmdb_id, "title": title, "year": 2020, "added_at": "2024-01-01T00:00:00",
            "watched": False, "progress": None, "poster_key": None, "url": None, **extra}


def arr_entry(tmdb_id, title, service="radarr", state="missing", **extra):
    return {"media_type": "movie" if service == "radarr" else "tv", "service": service, "tmdb_id": tmdb_id,
            "tvdb_id": None, "title": title, "year": 2020, "added_at": "2025-01-01T00:00:00Z", "monitored": True,
            "arr_state": state, "episodes": None, "poster_url": None, "url": None, **extra}


class TestArrLibraryItems(unittest.TestCase):
    def result(self, library=(), items=(), radarr_state="ok", sonarr_state="off"):
        return {"library": None if library is None else list(library),
                "arr": {"items": list(items), "radarr": {"state": radarr_state, "count": 0},
                        "sonarr": {"state": sonarr_state, "count": 0}}}

    def test_merges_for_all_movie_tv(self):
        r = self.result([plex_entry(1, "Dune")], [arr_entry(1, "Dune", state="downloaded"), arr_entry(2, "Solo"),
                                                  arr_entry(3, "Show", "sonarr")])
        for tab in ("all", "movie", "tv"):
            merged = web.library_items(r, tab)
            self.assertEqual([(i["title"], i["sources"]) for i in merged],
                             [("Dune", ["plex", "radarr"]), ("Solo", ["radarr"]), ("Show", ["sonarr"])])

    def test_none_only_when_no_plex_and_no_arr_items(self):
        self.assertIsNone(web.library_items(self.result(None, []), "all"))
        self.assertIsNone(web.library_items({"library": None}, "movie"))
        self.assertIsNone(web.library_items(None, "tv"))
        self.assertEqual(len(web.library_items(self.result(None, [arr_entry(1, "A")]), "all")), 1)
        self.assertEqual(web.library_items(self.result([], []), "all"), [])  # Plex readable but empty

    def test_old_results_without_arr_still_work(self):
        (item,) = web.library_items({"library": [plex_entry(1, "A")]}, "all")
        self.assertEqual(item["sources"], ["plex"])

    def test_arr_status_defaults_to_off(self):
        self.assertEqual(web.arr_status({}), {"radarr": "off", "sonarr": "off"})
        self.assertEqual(web.arr_status(None), {"radarr": "off", "sonarr": "off"})
        self.assertEqual(web.arr_status(self.result(radarr_state="error", sonarr_state="ok")),
                         {"radarr": "error", "sonarr": "ok"})
        self.assertEqual(web.arr_status({"arr": {"radarr": {"state": "ok"}}}), {"radarr": "ok", "sonarr": "off"})

    def test_owned_keys_includes_arr(self):
        r = self.result([plex_entry(1, "A")], [arr_entry(2, "B"), arr_entry(None, "C"), arr_entry(3, "S", "sonarr")])
        self.assertEqual(web.owned_keys(r), {("movie", 1), ("movie", 2), ("tv", 3)})

    def test_list_view_sources(self):
        r = self.result([plex_entry(1, "Dune"), plex_entry(5, "Arrival")],
                        [arr_entry(1, "Dune", state="downloaded"), arr_entry(2, "Paper", state="downloaded"),
                         arr_entry(3, "Soon", state="upcoming"), arr_entry(4, "Gone", state="missing"),
                         arr_entry(6, "Half", "sonarr", state="partial"), arr_entry(7, "Off", state="unmonitored")])
        merged = web.library_items(r, "all")

        def titles(source, tab="all"):
            return sorted(i["title"] for i in web.list_view(merged, tab, source=source)["items"])
        self.assertEqual(titles("all"), sorted(["Dune", "Arrival", "Paper", "Soon", "Gone", "Half", "Off"]))
        self.assertEqual(titles("plex"), ["Arrival", "Dune"])
        self.assertEqual(titles("arr"), sorted(["Dune", "Paper", "Soon", "Gone", "Half", "Off"]))
        self.assertEqual(titles("wanted"), ["Gone", "Half", "Soon"])
        self.assertEqual(titles("arr", "tv"), ["Half"])
        self.assertEqual(titles("bogus"), titles("all"))

    def test_source_is_ignored_on_watched_and_added_tabs(self):
        items = [{"title": "W", "media_type": "movie"}]
        for tab in ("watched", "added"):
            self.assertEqual(web.list_view(items, tab, sort="title", source="wanted")["total"], 1)

    def test_list_sources_table_and_list_options_unchanged(self):
        self.assertEqual(web.LIST_SOURCES["all"][0], "all")
        self.assertEqual(web.LIST_SOURCES["watched"], ("all",))
        self.assertEqual(set(web.LIST_SOURCES), set(web.LIST_OPTIONS))
        self.assertEqual(web.LIST_OPTIONS["all"], (("added", "title", "year"), ("all", "unwatched", "watched")))

    def test_parse_list_query_has_source_key(self):
        self.assertEqual(set(web.parse_list_query({})), {"tab", "q", "sort", "show", "page", "source"})


class TestSampleMergedLibrary(unittest.TestCase):
    def setUp(self):
        self.result = sources.run(sample_mode=True)

    def titles(self, tab, **kw):
        return [i["title"] for i in web.list_view(web.library_items(self.result, tab), tab, per_page=500, **kw)["items"]]

    def test_dune_appears_once_with_both_sources(self):
        self.assertEqual(self.titles("movie").count("Dune"), 1)
        dune = next(i for i in web.library_items(self.result, "movie") if i["title"] == "Dune")
        self.assertEqual(dune["sources"], ["plex", "radarr"])

    def test_arr_filter_lists_paper_moons_and_dune_but_not_arrival(self):
        found = self.titles("movie", source="arr")
        self.assertIn("Paper Moons", found)
        self.assertIn("Dune", found)
        self.assertNotIn("Arrival", found)

    def test_wanted_filter(self):
        self.assertEqual(sorted(self.titles("all", source="wanted")),
                         ["Coming Soon", "Low Tide", "Night Shift Diaries", "Tiny New Thing"])

    def test_counts(self):
        plex_only = len(sample.library_items())
        self.assertEqual(len(web.library_items(self.result, "all")), plex_only + 6)  # 2 merged into Plex titles, 6 arr-only

    def test_sample_arr_status(self):
        self.assertEqual(web.arr_status(self.result), {"radarr": "ok", "sonarr": "ok"})


class TestParseSearchQuery(unittest.TestCase):
    def test_collapses_trims_cuts_and_defaults(self):
        self.assertEqual(web.parse_search_query({"q": ["  the   long \t dune "], "type": ["movie"]}),
                         {"q": "the long dune", "kind": "movie"})
        self.assertEqual(web.parse_search_query({"q": ["x" * 300]}), {"q": "x" * 100, "kind": "all"})
        self.assertEqual(web.parse_search_query({"type": ["bogus"]}), {"q": "", "kind": "all"})
        self.assertEqual(web.parse_search_query({}), {"q": "", "kind": "all"})
        self.assertEqual(web.parse_search_query({"q": ["a" * 99 + " b"]})["q"], "a" * 99)  # no trailing space


class LiveSearchCase(DbCase):
    def setUp(self):
        super().setUp()
        self._tok = config.TMDB_TOKEN
        config.TMDB_TOKEN = "t" * 32
        self.addCleanup(setattr, config, "TMDB_TOKEN", self._tok)
        patch = mock.patch.object(sources, "use_sample", return_value=False)
        patch.start()
        self.addCleanup(patch.stop)
        with web._budget_lock:
            web._budget_uses[:] = []
        self.addCleanup(lambda: web._budget_uses.clear())
        self.result = {"library": [plex_entry(1, "Dune")], "watched": [], "arr": {"items": [], "radarr": {"state": "off", "count": 0},
                       "sonarr": {"state": "off", "count": 0}}}
        patch = mock.patch.object(web, "get_result_nowait", side_effect=lambda: (self.result, 1.0))
        patch.start()
        self.addCleanup(patch.stop)

    @staticmethod
    def hit(i, title="Dune", media_type="movie"):
        return {"media_type": media_type, "tmdb_id": i, "title": title, "year": 2021, "release_date": None,
                "overview": "", "poster_url": None, "url": None, "vote_average": 0, "vote_count": 0}


class TestSearchView(LiveSearchCase):
    def test_empty_and_short_make_no_request(self):
        with mock.patch.object(tmdb.TmdbClient, "search_titles", side_effect=AssertionError("no")), \
             mock.patch.object(tmdb.TmdbClient, "cached_search", side_effect=AssertionError("no")):
            self.assertEqual(web.search_view("", "all")["state"], "empty")
            self.assertEqual(web.search_view("   ", "all")["state"], "empty")
            short = web.search_view("x", "movie")
        self.assertEqual((short["state"], short["results"], short["kind"]), ("short", [], "movie"))

    def test_ok_annotates_statuses(self):
        found = {"results": [self.hit(1), self.hit(2, "Other")], "capped": True}
        db.record_added({"media_type": "movie", "tmdb_id": 2, "title": "Other", "year": 2020})
        with mock.patch.object(tmdb.TmdbClient, "cached_search", return_value=None), \
             mock.patch.object(tmdb.TmdbClient, "search_titles", return_value=found) as search:
            view = web.search_view("dune", "bogus")
        search.assert_called_once_with("dune", "all")
        self.assertEqual((view["state"], view["capped"], view["library_known"], view["sample"], view["message"]),
                         ("ok", True, True, False, None))
        self.assertEqual([(r["status"], r["in_library"]) for r in view["results"]], [("plex", True), ("added", True)])
        self.assertEqual(list(view), ["q", "kind", "state", "message", "results", "capped", "library_known", "sample"])

    def test_dismissed_flag(self):
        db.dismiss("movie", 1)
        with mock.patch.object(tmdb.TmdbClient, "cached_search", return_value={"results": [self.hit(1)], "capped": False}):
            (r,) = web.search_view("dune", "all")["results"]
        self.assertTrue(r["dismissed"])

    def test_library_unknown_before_the_first_build(self):
        self.result = None
        with mock.patch.object(tmdb.TmdbClient, "cached_search", return_value={"results": [self.hit(9)], "capped": False}):
            view = web.search_view("dune", "all")
        self.assertEqual((view["state"], view["library_known"], view["results"][0]["status"]), ("ok", False, "none"))

    def test_error_never_leaks_exception_text(self):
        boom = RuntimeError("HTTP 401 for https://api.themoviedb.org/3/search/multi?api_key=SECRETKEY")
        with mock.patch.object(tmdb.TmdbClient, "cached_search", return_value=None), \
             mock.patch.object(tmdb.TmdbClient, "search_titles", side_effect=boom):
            view = web.search_view("dune", "all")
        self.assertEqual((view["state"], view["message"], view["results"]), ("error", web.SEARCH_ERROR, []))
        self.assertNotIn("SECRETKEY", repr(view))

    def test_no_token_is_an_error_not_a_request(self):
        config.TMDB_TOKEN = ""
        self.assertEqual(web.search_view("dune", "all")["state"], "error")

    def test_search_limited_at_the_51st_uncached_request_but_lookups_continue(self):
        search_max = web.TMDB_BUDGET_MAX - web.LOOKUP_RESERVE
        self.assertEqual((web.TMDB_BUDGET_MAX, web.LOOKUP_RESERVE, search_max), (60, 10, 50))
        found = {"results": [], "capped": False}
        with mock.patch.object(tmdb.TmdbClient, "cached_search", return_value=None), \
             mock.patch.object(tmdb.TmdbClient, "search_titles", return_value=found) as search:
            states = [web.search_view(f"query {n}", "all")["state"] for n in range(search_max + 1)]
        self.assertEqual(states[:-1], ["ok"] * search_max)
        self.assertEqual(states[-1], "limited")
        self.assertEqual(search.call_count, search_max)
        # lookup_item's own budget (no reserve) still has the last 10
        self.assertEqual([web._tmdb_budget() for _ in range(12)], [True] * 10 + [False] * 2)
        with mock.patch.object(tmdb.TmdbClient, "cached_search", return_value=found):  # cached: still fine
            self.assertEqual(web.search_view("query 0", "all")["state"], "ok")
        with mock.patch.object(tmdb.TmdbClient, "cached_search", return_value=None):
            limited = web.search_view("another", "all")
        self.assertEqual((limited["message"], limited["results"]), (web.SEARCH_LIMITED, []))

    def test_limited_text(self):
        self.assertEqual(web.SEARCH_LIMITED, "Too many new searches in the last minute - wait a moment and try again. "
                                             "Searches you've already made still work.")

    def test_reserve_argument(self):
        self.assertEqual([web._tmdb_budget(reserve=50) for _ in range(52)], [True] * 10 + [False] * 42)
        web._budget_uses.clear()
        self.assertEqual(sum(web._tmdb_budget(reserve=web.LOOKUP_RESERVE) for _ in range(80)), 50)

    def test_cached_short_and_empty_never_record_a_use(self):
        with mock.patch.object(tmdb.TmdbClient, "cached_search", return_value={"results": [], "capped": False}):
            for q in ("dune", "", " ", "x"):
                web.search_view(q, "all")
        self.assertEqual(web._budget_uses, [])

    def test_budget_window_expires_and_is_thread_safe(self):
        with mock.patch.object(web.time, "monotonic", return_value=1000.0):
            granted = []
            threads = [threading.Thread(target=lambda: granted.append(web._tmdb_budget())) for _ in range(60)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            self.assertEqual(granted.count(True), web.TMDB_BUDGET_MAX)
            self.assertFalse(web._tmdb_budget())
        with mock.patch.object(web.time, "monotonic", return_value=1000.0 + web.TMDB_BUDGET_WINDOW + 1):
            self.assertTrue(web._tmdb_budget())

    def test_budget_does_not_touch_compute_lock(self):
        with web.compute_lock:  # a build holds it for minutes: the budget must not wait
            self.assertTrue(web._tmdb_budget())


class TestSearchViewSample(DbCase):
    def test_sample_mode_uses_the_catalogue_and_marks_statuses(self):
        result = sources.run(sample_mode=True)
        with mock.patch.object(web, "get_result_nowait", return_value=(result, 0.0)), \
             mock.patch.object(tmdb.TmdbClient, "search_titles", side_effect=AssertionError("network")):
            def by(q, kind="all"):
                view = web.search_view(q, kind)
                self.assertTrue(view["sample"])
                return {r["title"]: r for r in view["results"]}
            self.assertEqual(by("dune")["Dune"]["status"], "plex")
            low = by("low tide")["Low Tide"]
            self.assertEqual((low["status"], low["arr_state"]), ("sonarr", "missing"))
            self.assertEqual(by("paper")["Paper Moons"]["status"], "radarr")
            self.assertEqual(by("zzzz"), {})
            self.assertEqual(web.search_view("zzzz", "all")["state"], "ok")
            self.assertEqual(list(by("e", "tv")), [])  # "e" is too short for the page: state short


class TestLookupItem(LiveSearchCase):
    def setUp(self):
        super().setUp()
        self._saved = (dict(web._state), dict(web._ai_state))
        self.addCleanup(lambda: (web._state.update(self._saved[0]), web._ai_state.update(self._saved[1])))
        web._state.update(result=None)
        web._ai_state.update(result=None)

    def test_recommendation_cache_first_without_any_request(self):
        rec = {"media_type": "movie", "tmdb_id": 5, "title": "R"}
        web._state.update(result={"items": [rec]})
        with mock.patch.object(tmdb.TmdbClient, "cached_details", side_effect=AssertionError("no")), \
             mock.patch.object(tmdb.TmdbClient, "details", side_effect=AssertionError("no")):
            self.assertIs(web.lookup_item("movie", 5), rec)

    def test_cached_details_before_a_request(self):
        with mock.patch.object(tmdb.TmdbClient, "cached_details", return_value={"title": "C"}), \
             mock.patch.object(tmdb.TmdbClient, "details", side_effect=AssertionError("no")):
            self.assertEqual(web.lookup_item("movie", 6), {"title": "C"})
        self.assertEqual(web._budget_uses, [])

    def test_one_budgeted_details_request(self):
        with mock.patch.object(tmdb.TmdbClient, "cached_details", return_value=None), \
             mock.patch.object(tmdb.TmdbClient, "details", return_value={"title": "D"}) as details:
            self.assertEqual(web.lookup_item("tv", 7), {"title": "D"})
        details.assert_called_once_with("tv", 7, timeout=tmdb.SEARCH_TIMEOUT, retries=1)
        self.assertEqual(len(web._budget_uses), 1)

    def test_none_when_budget_is_used_up(self):
        web._budget_uses[:] = [web.time.monotonic()] * web.TMDB_BUDGET_MAX
        with mock.patch.object(tmdb.TmdbClient, "cached_details", return_value=None), \
             mock.patch.object(tmdb.TmdbClient, "details", side_effect=AssertionError("no")):
            self.assertIsNone(web.lookup_item("tv", 7))

    def test_none_without_a_token_or_in_sample_mode_or_on_failure(self):
        with mock.patch.object(tmdb.TmdbClient, "details", side_effect=AssertionError("no")), \
             mock.patch.object(tmdb.TmdbClient, "cached_details", side_effect=AssertionError("no")):
            config.TMDB_TOKEN = ""
            self.assertIsNone(web.lookup_item("movie", 1))
            config.TMDB_TOKEN = "t" * 32
            with mock.patch.object(sources, "use_sample", return_value=True):
                self.assertIsNone(web.lookup_item("movie", 1))
        with mock.patch.object(tmdb.TmdbClient, "cached_details", return_value=None), \
             mock.patch.object(tmdb.TmdbClient, "details", side_effect=RuntimeError("api_key=SECRET")):
            self.assertIsNone(web.lookup_item("movie", 1))
