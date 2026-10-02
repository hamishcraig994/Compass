"""Backend half of the Library page and personal ratings: the ratings table, the query/list helpers in
web.py, and the library/watched/thumbs snapshot. No page markup, no network."""
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["SAMPLE"] = "1"
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
        self.assertEqual(web.parse_list_query({}), {"tab": "all", "q": "", "sort": "added", "show": "all", "page": 1})

    def test_valid_values(self):
        self.assertEqual(self.parse(type="watched", q="  dune ", sort="rating", show="rated", page="3"),
                         {"tab": "watched", "q": "dune", "sort": "rating", "show": "rated", "page": 3})

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
            self.assertEqual(web.library_items(self.RESULT, tab), [{"title": "L"}])
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
