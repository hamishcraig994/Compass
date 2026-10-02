import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import profile

NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


def item(days_ago=0, **kw):
    return {"last_viewed": (NOW - timedelta(days=days_ago)).isoformat(), "view_count": 1,
            "user_rating": None, "progress": None, **kw}


def details(genres=(), keywords=(), directors=(), cast=()):
    return {"genres": list(genres), "keywords": list(keywords), "directors": list(directors), "cast": list(cast)}


class TestItemWeight(unittest.TestCase):
    def test_recent_beats_old(self):
        self.assertGreater(profile.item_weight(item(10), NOW), profile.item_weight(item(700), NOW))

    def test_half_life(self):
        self.assertAlmostEqual(profile.item_weight(item(365), NOW), 0.5, places=1)

    def test_old_watches_keep_a_floor(self):
        self.assertAlmostEqual(profile.item_weight(item(20000), NOW), profile.MIN_RECENCY)

    def test_rating_scales_weight(self):
        loved = profile.item_weight(item(user_rating=10), NOW)
        neutral = profile.item_weight(item(user_rating=7), NOW)
        self.assertGreater(loved, neutral)
        self.assertEqual(profile.item_weight(item(user_rating=3), NOW), 0)  # disliked = no influence

    def test_rewatch_bonus_is_capped(self):
        once, twice, many = (profile.item_weight(item(view_count=n), NOW) for n in (1, 2, 50))
        self.assertGreater(twice, once)
        self.assertAlmostEqual(many, once * 1.75)

    def test_abandoned_show_counts_less(self):
        self.assertLess(profile.item_weight(item(progress=0.1), NOW), profile.item_weight(item(progress=0.9), NOW))

    def test_missing_dates_dont_crash(self):
        self.assertEqual(profile.item_weight({"last_viewed": None, "view_count": 1}, NOW), 1.0)


class TestBuild(unittest.TestCase):
    def test_values_are_between_0_and_1_and_top_is_1(self):
        f = profile.build([(item(), details(genres=["Sci-Fi", "Thriller"])), (item(), details(genres=["Sci-Fi"]))], NOW)
        self.assertEqual(f["genre"]["Sci-Fi"], 1.0)
        self.assertTrue(0 < f["genre"]["Thriller"] < 1)

    def test_recent_taste_outweighs_old_taste(self):
        f = profile.build([(item(5), details(genres=["Sci-Fi"])), (item(900), details(genres=["Western"]))], NOW)
        self.assertGreater(f["genre"]["Sci-Fi"], f["genre"]["Western"])

    def test_one_off_people_are_ignored_but_repeats_count(self):
        f = profile.build([(item(), details(directors=["A", "B"])), (item(), details(directors=["A"]))], NOW)
        self.assertIn("A", f["director"])
        self.assertNotIn("B", f["director"])

    def test_disliked_titles_dont_shape_taste(self):
        f = profile.build([(item(user_rating=2), details(genres=["Horror"])), (item(), details(genres=["Comedy"]))], NOW)
        self.assertNotIn("Horror", f["genre"])

    def test_drama_is_damped(self):
        f = profile.build([(item(), details(genres=["Drama"])), (item(), details(genres=["Drama", "Crime"])),
                           (item(), details(genres=["Crime"]))], NOW)
        self.assertLess(f["genre"]["Drama"], f["genre"]["Crime"])

    def test_empty_history(self):
        f = profile.build([], NOW)
        self.assertEqual(f, {c: {} for c in profile.CATEGORIES})

    def test_summary_orders_by_strength(self):
        s = profile.summary({"genre": {"a": 0.2, "b": 1.0}, "keyword": {}, "director": {}, "actor": {}}, per_category=1)
        self.assertEqual(s["genre"], ["b"])


class TestRatings(unittest.TestCase):
    def test_stars_from_ten(self):
        self.assertEqual(profile.stars_from_ten(7.0), 4)
        self.assertEqual(profile.stars_from_ten("8"), 4)
        self.assertEqual(profile.stars_from_ten(10), 5)
        self.assertEqual(profile.stars_from_ten(2), 1)
        self.assertEqual(profile.stars_from_ten(1), 1)
        for bad in (None, 0, "0", "x", "", float("nan"), -3, [], float("inf")):
            self.assertIsNone(profile.stars_from_ten(bad), bad)

    def test_apply_ratings_overrides_without_mutating(self):
        a, b = item(user_rating=9.0, media_type="movie", tmdb_id=1), item(media_type="tv", tmdb_id=1)
        original = [dict(a), dict(b)]
        out = profile.apply_ratings([a, b], {("movie", 1): 4})
        self.assertEqual([a, b], original)
        self.assertEqual((out[0]["user_rating"], out[0]["personal_stars"]), (8, 4))
        self.assertEqual(out[1], b)  # same id, other media type: untouched
        self.assertNotIn("personal_stars", out[1])
        self.assertIsNot(out[1], b)

    def test_stars_scale_matches_item_weight(self):
        w = lambda stars: profile.item_weight(profile.apply_ratings([item(media_type="movie", tmdb_id=1)], {("movie", 1): stars})[0], NOW)
        self.assertEqual((w(1), w(2)), (0.0, 0.0))
        self.assertGreater(w(5), w(4))
        self.assertGreater(w(4), w(3))


if __name__ == "__main__":
    unittest.main()
