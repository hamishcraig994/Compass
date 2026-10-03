import copy
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ["SAMPLE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import browse
import db
import sample
import sources
import web


def item(n, media_type="movie", match=None, because=(), genres=(), votes=1000, **kw):
    return {"media_type": media_type, "tmdb_id": n, "title": f"T{n}", "match": 99 - n if match is None else match,
            "because": list(because), "genres": list(genres), "vote_count": votes,
            "trending": False, "new": False, **kw}


def ids(row):
    return [i["tmdb_id"] for i in row["items"]]


def rows(v):
    return {r["id"]: r for r in v["rows"]}


class TestKinds(unittest.TestCase):
    def test_kind_items_filters_and_keeps_order(self):
        items = [item(1), item(2, "tv"), item(3), item(4, "tv")]
        self.assertEqual([i["tmdb_id"] for i in browse.kind_items(items, "movie")], [1, 3])
        self.assertEqual([i["tmdb_id"] for i in browse.kind_items(items, "tv")], [2, 4])
        self.assertEqual(len(browse.kind_items(items, "all")), 4)
        self.assertEqual(len(browse.kind_items(items, "bogus")), 4)
        self.assertEqual(browse.kind_items(None, "all"), [])

    def test_hero_picks_is_first_five_of_the_kind(self):
        items = [item(n, "tv" if n % 2 else "movie") for n in range(30)]
        self.assertEqual([i["tmdb_id"] for i in browse.hero_picks(items, "all")], [0, 1, 2, 3, 4])
        self.assertEqual([i["tmdb_id"] for i in browse.hero_picks(items, "tv")], [1, 3, 5, 7, 9])
        self.assertEqual(len(browse.hero_picks(items[:3], "all")), 3)


class TestRecommendedAndTop10(unittest.TestCase):
    def test_row_order_and_basic_rows(self):
        result = {"items": [item(n) for n in range(30)], "library": [
            {"media_type": "movie", "tmdb_id": 900, "title": "L", "added_at": "2026-01-01T00:00:00"}]}
        v = browse.view(result, "all")
        self.assertEqual(v["kind"], "all")
        self.assertEqual(v["total"], 30)
        self.assertEqual([i["tmdb_id"] for i in v["hero"]], [0, 1, 2, 3, 4])
        order = [r["id"] for r in v["rows"]]
        self.assertEqual(order, [r for r in browse.ROW_ORDER if r in order])
        self.assertEqual(order[0], "recommended")
        self.assertIn("top10", order)
        self.assertEqual(order[-1], "library_new")

    def test_recommended_skips_hero_and_caps(self):
        v = browse.view({"items": [item(n) for n in range(40)]}, "all")
        r = rows(v)["recommended"]
        self.assertEqual(ids(r), list(range(5, 25)))
        self.assertEqual((r["title"], r["subtitle"], r["numbered"], r["source"]),
                         ("Recommended for You", "Based on your watch history", False, "recs"))

    def test_recommended_with_five_or_fewer_items_repeats_them(self):
        v = browse.view({"items": [item(n) for n in range(5)]}, "all")
        self.assertEqual(ids(rows(v)["recommended"]), [0, 1, 2, 3, 4])
        v = browse.view({"items": [item(1)]}, "all")
        self.assertEqual(ids(rows(v)["recommended"]), [1])
        self.assertEqual(len(v["hero"]), 1)

    def test_six_items_recommended_is_the_sixth_only(self):
        v = browse.view({"items": [item(n) for n in range(6)]}, "all")
        self.assertEqual(ids(rows(v)["recommended"]), [5])

    def test_no_items(self):
        v = browse.view({"items": []}, "all")
        self.assertEqual((v["total"], v["hero"], v["rows"]), (0, [], []))

    def test_top10_numbered_and_needs_row_min(self):
        v = browse.view({"items": [item(n) for n in range(30)]}, "all")
        t = rows(v)["top10"]
        self.assertTrue(t["numbered"])
        self.assertEqual((ids(t), t["title"]), (list(range(10)), "Top 10 picks for you"))
        self.assertNotIn("top10", rows(browse.view({"items": [item(n) for n in range(3)]}, "all")))
        self.assertIn("top10", rows(browse.view({"items": [item(n) for n in range(4)]}, "all")))
        self.assertEqual(len(rows(browse.view({"items": [item(n) for n in range(7)]}, "all"))["top10"]["items"]), 7)


class TestBecause(unittest.TestCase):
    def test_seed_is_most_common_and_row_filters_by_it(self):
        items = [item(0, because=["A", "B"]), item(1, because=["B"]), item(2, because=["B", "A"]),
                 item(3, because=["B"]), item(4, because=["A"]), item(5, because=["B"])]
        r = rows(browse.view({"items": items}, "all"))["because"]
        self.assertEqual(r["title"], "Because you watched B")
        self.assertIsNone(r["subtitle"])
        self.assertEqual(ids(r), [0, 1, 2, 3, 5])

    def test_tie_goes_to_title_in_highest_ranked_item(self):
        items = [item(0, because=["Y"]), item(1, because=["X", "Y"]), item(2, because=["X"]),
                 item(3, because=["X"]), item(4, because=["Y"]), item(5, because=["Y", "X"])]
        # X: 4 items (1,2,3,5), Y: 4 items (0,1,4,5) -> Y is in the top-ranked item
        r = rows(browse.view({"items": items}, "all"))["because"]
        self.assertEqual(r["title"], "Because you watched Y")

    def test_tie_with_same_first_item_is_deterministic_by_name(self):
        items = [item(n, because=["Zed", "Abe"]) for n in range(4)]
        self.assertEqual(rows(browse.view({"items": items}, "all"))["because"]["title"], "Because you watched Abe")

    def test_omitted_below_row_min(self):
        items = [item(n, because=["A"] if n < 3 else []) for n in range(10)]
        self.assertNotIn("because", rows(browse.view({"items": items}, "all")))

    def test_counts_one_per_item_even_with_duplicate_names(self):
        items = [item(0, because=["A", "A", "A"])] + [item(n) for n in range(1, 6)]
        self.assertNotIn("because", rows(browse.view({"items": items}, "all")))

    def test_seed_is_per_kind_and_capped(self):
        items = [item(n, "movie", because=["M"]) for n in range(25)] + \
                [item(100 + n, "tv", because=["T"]) for n in range(5)]
        self.assertEqual(len(rows(browse.view({"items": items}, "movie"))["because"]["items"]), browse.ROW_MAX)
        self.assertEqual(rows(browse.view({"items": items}, "tv"))["because"]["title"], "Because you watched T")
        self.assertEqual(rows(browse.view({"items": items}, "all"))["because"]["title"], "Because you watched M")


class TestTrending(unittest.TestCase):
    def result(self, items, genre=None):
        return {"items": items, "profile": {"genre": genre or {"Drama": 0.9, "Comedy": 0.5, "Horror": 0.4, "Western": 0.3}}}

    def test_first_genre_with_enough_flagged_items(self):
        items = [item(n, genres=["Drama"], trending=n < 2) for n in range(6)] + \
                [item(10 + n, genres=["Comedy"], new=True) for n in range(4)]
        r = rows(browse.view(self.result(items), "all"))["trending"]
        self.assertEqual(r["title"], "Trending in Comedy")  # Drama has only 2 flagged
        self.assertEqual(ids(r), [10, 11, 12, 13])

    def test_trending_first_then_score_and_new_counts(self):
        items = [item(0, genres=["Drama"], new=True), item(1, genres=["Drama"], new=True),
                 item(2, genres=["Drama"], trending=True), item(3, genres=["Drama"], new=True),
                 item(4, genres=["Drama"], trending=True), item(5, genres=["Drama"])]
        r = rows(browse.view(self.result(items), "all"))["trending"]
        self.assertEqual(ids(r), [2, 4, 0, 1, 3])

    def test_only_top_three_genres_are_tried(self):
        items = [item(n, genres=["Western"], trending=True) for n in range(6)]
        self.assertNotIn("trending", rows(browse.view(self.result(items), "all")))

    def test_genre_ties_break_by_name(self):
        items = [item(n, genres=["B", "A"], trending=True) for n in range(5)]
        r = rows(browse.view(self.result(items, {"B": 0.5, "A": 0.5}), "all"))["trending"]
        self.assertEqual(r["title"], "Trending in A")

    def test_omitted_without_profile_or_flags(self):
        items = [item(n, genres=["Drama"]) for n in range(8)]
        self.assertNotIn("trending", rows(browse.view(self.result(items), "all")))
        self.assertNotIn("trending", rows(browse.view({"items": [item(n, genres=["Drama"], trending=True)
                                                                 for n in range(8)]}, "all")))

    def test_capped(self):
        items = [item(n, genres=["Drama"], trending=True) for n in range(30)]
        self.assertEqual(len(rows(browse.view(self.result(items), "all"))["trending"]["items"]), browse.ROW_MAX)


class TestGems(unittest.TestCase):
    def test_gems_rules(self):
        # 14 movies; votes: ranks 0-9 are the top 10 (excluded even if obscure)
        votes = [5000] * 10 + [100, 200, 9000, 150]
        matches = [95] * 10 + [80, 70, 90, 50]
        items = [item(n, votes=votes[n], match=matches[n]) for n in range(14)]
        r = rows(browse.view({"items": items}, "movie"))
        self.assertNotIn("gems", r)  # only 2 qualify: 10 (100 votes), 11 (200 votes); 12 too popular, 13 low match
        more = items + [item(20 + n, votes=50, match=75) for n in range(3)]
        r = rows(browse.view({"items": more}, "movie"))["gems"]
        self.assertEqual(ids(r), [10, 11, 20, 21, 22])
        self.assertEqual((r["title"], r["subtitle"]), ("Hidden Gems", "High match, less well known"))

    def test_median_is_per_media_type(self):
        movies = [item(n, votes=100000, match=99) for n in range(10)] + \
                 [item(10 + n, votes=2000, match=70) for n in range(6)]
        shows = [item(100 + n, "tv", votes=300, match=99) for n in range(10)] + \
                [item(110 + n, "tv", votes=900, match=70) for n in range(6)]
        # movie median_low of 16 votes: sorted 6x2000,10x100000 -> 8th lowest = 100000 -> every obscure movie qualifies
        # tv median_low: 10x300 then 6x900 -> 8th lowest = 300 -> the 900-vote shows do NOT qualify
        v = browse.view({"items": movies + shows}, "all")
        gems = ids(rows(v)["gems"])
        self.assertTrue(set(gems) >= set(range(10, 16)))
        self.assertFalse(set(gems) & set(range(110, 116)))

    def test_threshold_boundary(self):
        items = [item(n, votes=1000, match=99) for n in range(10)] + \
                [item(10, votes=500, match=60), item(11, votes=500, match=59)] + \
                [item(12 + n, votes=500, match=60) for n in range(3)]
        r = rows(browse.view({"items": items}, "movie"))["gems"]
        self.assertEqual(ids(r), [10, 12, 13, 14])

    def test_no_gems_with_only_ten_items(self):
        self.assertNotIn("gems", rows(browse.view({"items": [item(n, votes=1, match=99) for n in range(10)]}, "all")))


class TestLibraryRow(unittest.TestCase):
    LIB = [{"media_type": "movie", "tmdb_id": 1, "title": "Old", "added_at": "2025-01-01T00:00:00+00:00"},
           {"media_type": "tv", "tmdb_id": 2, "title": "Show", "added_at": "2026-02-01T00:00:00+00:00"},
           {"media_type": "movie", "tmdb_id": 3, "title": "New", "added_at": "2026-03-01T00:00:00+00:00"},
           {"media_type": "movie", "tmdb_id": None, "title": "Undated", "added_at": None}]

    def row(self, kind, library=None):
        result = {"items": [], "library": self.LIB if library is None else library}
        return rows(browse.view(result, kind)).get("library_new")

    def test_sorted_desc_none_last_and_flagged(self):
        r = self.row("all")
        self.assertEqual([i["title"] for i in r["items"]], ["New", "Show", "Old", "Undated"])
        self.assertTrue(all(i["in_library"] for i in r["items"]))
        self.assertEqual((r["title"], r["source"], r["numbered"]), ("New in your library", "library", False))

    def test_filtered_by_kind(self):
        self.assertEqual([i["title"] for i in self.row("movie")["items"]], ["New", "Old", "Undated"])
        self.assertEqual([i["title"] for i in self.row("tv")["items"]], ["Show"])

    def test_omitted_when_none_or_empty_or_no_match(self):
        self.assertNotIn("library_new", rows(browse.view({"items": [item(1)], "library": None}, "all")))
        self.assertNotIn("library_new", rows(browse.view({"items": [item(1)]}, "all")))
        self.assertIsNone(self.row("all", library=[]))
        self.assertIsNone(self.row("tv", library=[self.LIB[0]]))

    def test_capped(self):
        lib = [{"media_type": "movie", "tmdb_id": n, "title": f"L{n}", "added_at": f"2026-01-{n % 28 + 1:02d}"}
               for n in range(50)]
        self.assertEqual(len(self.row("all", lib)["items"]), browse.LIBRARY_ROW_MAX)

    def test_library_works_even_with_no_recommendations(self):
        v = browse.view({"items": [], "library": self.LIB}, "all")
        self.assertEqual([r["id"] for r in v["rows"]], ["library_new"])


class TestFlagsAndPurity(unittest.TestCase):
    def test_in_library_flag(self):
        items = [item(n) for n in range(8)]
        v = browse.view({"items": items}, "all", owned={("movie", 1), ("movie", 7), ("tv", 2)})
        flags = {i["tmdb_id"]: i["in_library"] for i in v["hero"]}
        self.assertEqual(flags, {0: False, 1: True, 2: False, 3: False, 4: False})
        self.assertEqual({i["tmdb_id"]: i["in_library"] for i in rows(v)["recommended"]["items"]},
                         {5: False, 6: False, 7: True})
        self.assertTrue(all(i["in_library"] is False for i in browse.view({"items": items}, "all")["hero"]))

    def test_input_is_never_mutated(self):
        result = {"items": [item(n, genres=["Drama"], because=["A"], trending=True) for n in range(30)],
                  "profile": {"genre": {"Drama": 1.0}},
                  "library": [{"media_type": "movie", "tmdb_id": 1, "title": "L", "added_at": None}]}
        before = copy.deepcopy(result)
        owned = frozenset({("movie", 1)})
        for kind in browse.KINDS:
            v = browse.view(result, kind, owned)
            for row in v["rows"]:
                for it in row["items"]:
                    it["poked"] = True
            for it in v["hero"]:
                it["poked"] = True
        self.assertEqual(result, before)

    def test_minimal_fake_result_works(self):
        result = {"items": [{"media_type": "movie", "tmdb_id": n, "title": f"X{n}"} for n in range(12)]}
        v = browse.view(result, "all")
        self.assertEqual(v["total"], 12)
        self.assertEqual([r["id"] for r in v["rows"]], ["recommended", "top10"])
        self.assertEqual(browse.view({}, "tv")["rows"], [])
        self.assertEqual(browse.view(None, "all")["total"], 0)

    def test_kind_filtering_and_bad_kind(self):
        items = [item(n, "tv" if n % 2 else "movie", because=["A"]) for n in range(40)]
        v = browse.view({"items": items}, "movie")
        self.assertEqual(v["kind"], "movie")
        for it in v["hero"] + [i for r in v["rows"] for i in r["items"]]:
            self.assertEqual(it["media_type"], "movie")
        self.assertEqual(v["total"], 20)
        self.assertEqual(browse.view({"items": items}, "nope")["kind"], "all")


class TestSampleView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = sources.run(True)

    def titles(self, kind):
        v = browse.view(self.result, kind)
        return [r["title"] for r in v["rows"]], v

    def test_home_and_movies_have_because_arrival(self):
        for kind in ("all", "movie"):
            titles, v = self.titles(kind)
            self.assertEqual(len(v["hero"]), 5)
            for t in ("Recommended for You", "Because you watched Arrival", "Top 10 picks for you", "New in your library"):
                self.assertIn(t, titles)

    def test_tv_has_no_arrival_row_and_no_movies(self):
        titles, v = self.titles("tv")
        self.assertNotIn("Because you watched Arrival", titles)
        self.assertIn("Mindhunter", [i["title"] for i in v["hero"]] + [i["title"] for r in v["rows"][:1] for i in r["items"]])
        for r in v["rows"]:
            for it in r["items"]:
                self.assertEqual(it["media_type"], "tv")

    def test_movies_has_no_tv_title(self):
        v = browse.view(self.result, "movie")
        for it in v["hero"] + [i for r in v["rows"] for i in r["items"]]:
            self.assertNotEqual(it["title"], "Mindhunter")


class TestOwnedKeys(unittest.TestCase):
    LIB = {"library": [{"media_type": "movie", "tmdb_id": 1}, {"media_type": "tv", "tmdb_id": None},
                       {"media_type": "tv", "tmdb_id": 2}]}

    def setUp(self):
        old = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", old)
        db.record_added({"media_type": "movie", "tmdb_id": 50, "title": "Added Movie", "year": 2020})

    def test_live_mode_unions_library_and_added(self):
        with mock.patch.object(web, "_is_sample", return_value=False):
            self.assertEqual(web.owned_keys(self.LIB), {("movie", 1), ("tv", 2), ("movie", 50)})

    def test_sample_mode_uses_library_only(self):
        with mock.patch.object(web, "_is_sample", return_value=True):
            self.assertEqual(web.owned_keys(self.LIB), {("movie", 1), ("tv", 2)})

    def test_missing_or_none_library_and_result(self):
        with mock.patch.object(web, "_is_sample", return_value=False):
            self.assertEqual(web.owned_keys({"library": None}), {("movie", 50)})
            self.assertEqual(web.owned_keys({}), {("movie", 50)})
            self.assertEqual(web.owned_keys(None), {("movie", 50)})

    def test_browse_view_flags_owned(self):
        result = {"items": [item(1), item(50), item(3), item(4), item(5)], "library": self.LIB["library"]}
        with mock.patch.object(web, "_is_sample", return_value=False):
            v = web.browse_view(result, "all")
        self.assertEqual({i["tmdb_id"]: i["in_library"] for i in v["hero"]},
                         {1: True, 50: True, 3: False, 4: False, 5: False})
        with mock.patch.object(web, "_is_sample", return_value=True):
            v = web.browse_view(result, "all")
        self.assertEqual({i["tmdb_id"]: i["in_library"] for i in v["hero"]}[50], False)


if __name__ == "__main__":
    unittest.main()


class TestBrowseStars(unittest.TestCase):
    def setUp(self):
        old = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", old)
        db.set_rating("movie", 1, 4)
        db.set_rating("tv", 2, 5)
        self.result = {"items": [item(1), item(2, "tv"), item(3), item(4), item(5), item(6)],
                       "library": [{"media_type": "movie", "tmdb_id": 9, "title": "Lib", "new": True},
                                   {"media_type": "movie", "tmdb_id": None, "title": "NoId", "new": True}]}
        db.set_rating("movie", 9, 3)

    def all_items(self, v):
        return v["hero"] + [i for r in v["rows"] for i in r["items"]]

    def test_live_stars_on_hero_and_rows(self):
        with mock.patch.object(web, "_is_sample", return_value=False):
            v = web.browse_view(self.result, "all")
        got = self.all_items(v)
        self.assertTrue(all("stars" in i for i in got))
        self.assertEqual({i["stars"] for i in got if i["tmdb_id"] == 1}, {4})
        self.assertEqual({i["stars"] for i in got if i["media_type"] == "tv" and i["tmdb_id"] == 2}, {5})
        self.assertEqual({i["stars"] for i in got if i["tmdb_id"] == 3}, {None})
        self.assertGreater(len([i for i in got if i["tmdb_id"] == 1]), 1)  # hero + at least one row
        libs = [i for i in got if i["tmdb_id"] == 9]
        self.assertTrue(libs and all(i["stars"] == 3 for i in libs))
        self.assertTrue(all(i["stars"] is None for i in got if i["tmdb_id"] is None))

    def test_sample_mode_ignores_ratings(self):
        with mock.patch.object(web, "_is_sample", return_value=True):
            v = web.browse_view(self.result, "all")
        self.assertTrue(all(i["stars"] is None for i in self.all_items(v)))

    def test_cached_result_not_mutated(self):
        before = copy.deepcopy(self.result)
        with mock.patch.object(web, "_is_sample", return_value=False):
            web.browse_view(self.result, "all")
        self.assertEqual(self.result, before)
