import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import profile
import recommend
import sample


def run(**kw):
    watched, library = sample.load()
    return recommend.recommend(watched, library, sample.SampleTmdb(), **kw)


class TestRecommend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run()
        cls.items = cls.result["items"]
        cls.titles = [i["title"] for i in cls.items]

    def test_never_suggests_anything_already_in_the_library(self):
        watched, library = sample.load()
        for item in self.items:
            self.assertNotIn((item["media_type"], item["tmdb_id"]), library, item["title"])
        self.assertNotIn("Dune", self.titles)         # in Plex but unwatched
        self.assertNotIn("Black Mirror", self.titles)

    def test_low_vote_titles_are_filtered_out(self):
        self.assertNotIn("Space Cheese Attack", self.titles)  # 9.4 rating from 30 votes

    def test_sorted_best_first_and_match_scores_are_sane(self):
        matches = [i["match"] for i in self.items]
        self.assertEqual(matches, sorted(matches, reverse=True))
        self.assertEqual(matches[0], 99)
        self.assertTrue(all(1 <= m <= 99 for m in matches))

    def test_taste_drives_the_ranking(self):
        # The viewer likes sci-fi and crime; a baking show should rank below or be missing.
        top = set(self.titles[:8])
        self.assertTrue({"Gone Girl", "Gravity"} <= top, self.titles)
        if "The Great British Bake Off" in self.titles:
            self.assertGreater(self.titles.index("The Great British Bake Off"), 8)

    def test_both_movies_and_shows_are_suggested(self):
        self.assertEqual({i["media_type"] for i in self.items}, {"movie", "tv"})

    def test_every_suggestion_explains_itself(self):
        for item in self.items:
            self.assertTrue(item["reason"])
        gone_girl = next(i for i in self.items if i["title"] == "Gone Girl")
        self.assertTrue(gone_girl["reason"].startswith("Because you watched"))
        self.assertIn("Se7en", gone_girl["reason"])

    def test_dismissed_titles_are_excluded(self):
        result = run(dismissed={("movie", 1017)})  # Gone Girl
        self.assertNotIn("Gone Girl", [i["title"] for i in result["items"]])

    def test_limit(self):
        self.assertEqual(len(run(limit=5)["items"]), 5)

    def test_profile_reflects_history(self):
        genres = self.result["profile"]["genre"]
        self.assertGreater(genres["Science Fiction"], genres.get("Comedy", 0))
        self.assertIn("Denis Villeneuve", self.result["profile"]["director"])

    def test_a_disliked_show_doesnt_drive_suggestions(self):
        # Ted Lasso was abandoned at 10%, so The Bear/Bake Off (its links) shouldn't beat the sci-fi/crime picks.
        for title in ("The Bear", "The Great British Bake Off"):
            if title in self.titles:
                self.assertGreater(self.titles.index(title), 10)

    def test_empty_history_returns_nothing_gracefully(self):
        result = recommend.recommend([], set(), sample.SampleTmdb())
        self.assertEqual(result["items"], [])
        self.assertTrue(result["notes"])

    def test_tmdb_failures_are_skipped_not_fatal(self):
        class Flaky(sample.SampleTmdb):
            def details(self, media_type, tmdb_id):
                if tmdb_id in (1001, 1017):
                    raise RuntimeError("TMDB down")
                return super().details(media_type, tmdb_id)

        watched, library = sample.load()
        result = recommend.recommend(watched, library, Flaky())
        self.assertNotIn("Gone Girl", [i["title"] for i in result["items"]])
        self.assertTrue(any("Couldn't look up" in n for n in result["notes"]))
        self.assertTrue(result["items"])


class TestNewAndTrending(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = {i["title"]: i for i in run()["items"]}

    def test_new_and_trending_titles_that_fit_your_taste_are_suggested(self):
        for title in ("The Long Signal", "Harbour Lights", "Orbit Nine"):
            self.assertIn(title, self.items)

    def test_flags(self):
        signal, harbour, orbit, heat, gone = (self.items[t] for t in
                                              ("The Long Signal", "Harbour Lights", "Orbit Nine", "Heat", "Gone Girl"))
        self.assertTrue(signal["new"] and signal["trending"])
        self.assertTrue(harbour["new"] and harbour["trending"])
        self.assertTrue(orbit["new"] and not orbit["trending"])       # new only, found via new releases
        self.assertTrue(heat["trending"] and not heat["new"])         # old film, trending, also linked to favourites
        self.assertFalse(gone["new"] or gone["trending"])

    def test_buzz_that_doesnt_fit_your_taste_is_left_out(self):
        self.assertNotIn("Sunday Best", self.items)    # new + trending comedy
        self.assertNotIn("Cooking Rivals", self.items)  # trending reality show

    def test_unreleased_and_barely_voted_titles_are_left_out(self):
        self.assertNotIn("Coming Soon", self.items)     # trending, but not out yet
        self.assertNotIn("Tiny New Thing", self.items)  # new, but 10 votes

    def test_taste_still_beats_buzz(self):
        ranked = [i["title"] for i in run()["items"]]
        self.assertEqual(ranked[0], "Gone Girl")
        # a strong linked match outranks the merely-new title
        self.assertLess(ranked.index("Gravity"), ranked.index("Orbit Nine"))

    def test_trending_boosts_rank(self):
        # Heat is linked to the same favourites either way; being trending should move it up.
        class NotTrending(sample.SampleTmdb):
            def trending(self, media_type):
                return []

        watched, library = sample.load()
        before = {i["title"]: i["match"] for i in recommend.recommend(watched, library, NotTrending())["items"]}
        after = {i["title"]: i["match"] for i in run()["items"]}
        self.assertGreater(after["Heat"], before["Heat"])

    def test_reasons_mention_why_for_buzz_titles(self):
        self.assertEqual(self.items["Orbit Nine"]["reason"], "New release that fits your taste")
        self.assertTrue(self.items["Harbour Lights"]["reason"].startswith("Trending"))

    def test_failure_to_fetch_trending_is_a_note_not_a_crash(self):
        class Broken(sample.SampleTmdb):
            def trending(self, media_type):
                raise RuntimeError("TMDB down")

        watched, library = sample.load()
        result = recommend.recommend(watched, library, Broken())
        self.assertTrue(any("trending" in n for n in result["notes"]))
        self.assertIn("Gone Girl", [i["title"] for i in result["items"]])


class TestBuzzScore(unittest.TestCase):
    def test_trending_is_full_marks(self):
        self.assertEqual(recommend.buzz_score(5000, True), 1.0)

    def test_fresh_beats_old_and_fades_to_zero(self):
        self.assertGreater(recommend.buzz_score(10, False), recommend.buzz_score(300, False))
        self.assertEqual(recommend.buzz_score(recommend.FRESH_SPAN_DAYS + 1, False), 0.0)
        self.assertEqual(recommend.buzz_score(None, False), 0.0)

    def test_release_age(self):
        from datetime import date
        self.assertEqual(recommend.release_age_days({"release_date": "2026-09-01"}, date(2026, 9, 21)), 20)
        self.assertLess(recommend.release_age_days({"release_date": "2026-10-01"}, date(2026, 9, 21)), 0)
        self.assertIsNone(recommend.release_age_days({"release_date": None}, date(2026, 9, 21)))
        self.assertIsNone(recommend.release_age_days({"release_date": "garbage"}, date(2026, 9, 21)))


def _details(media_type, tmdb_id, title, genres=(), vote_count=500, vote_average=8.0,
            release_date="2020-06-15", recs=()):
    return {"media_type": media_type, "tmdb_id": tmdb_id, "title": title,
           "year": int(release_date[:4]), "release_date": release_date, "overview": "",
           "poster_url": None, "url": None, "genres": list(genres), "keywords": [], "directors": [],
           "cast": [], "vote_average": vote_average, "vote_count": vote_count, "recommendations": list(recs)}


class FakeTmdb:
    """Deliberately not sample.py's shared catalogue, so these tests don't have to reason about
    cross-path leakage from it - full control over exactly what's reachable and how."""

    def __init__(self, catalogue, search_results=None):
        self.catalogue = catalogue  # {(media_type, id): details}
        self.search_results = search_results or {}  # {(media_type, title, year): id_or_None}

    def details(self, media_type, tmdb_id):
        return self.catalogue[(media_type, tmdb_id)]

    def discover(self, media_type, genre_name):
        return []

    def trending(self, media_type):
        return []

    def new_releases(self, media_type, genre_name, since, until):
        return []

    def search(self, media_type, title, year=None):
        return self.search_results.get((media_type, title, year))


WATCHED = [{"media_type": "movie", "tmdb_id": 1, "title": "Watched Movie", "last_viewed": None,
           "user_rating": None, "view_count": 1, "progress": None}]


class TestAiIntegration(unittest.TestCase):
    def _catalogue(self, extra=None):
        return {("movie", 1): _details("movie", 1, "Watched Movie", genres=["Science Fiction"]), **(extra or {})}

    def test_resolved_suggestion_appears_with_its_own_reason(self):
        catalogue = self._catalogue({("movie", 2): _details("movie", 2, "AI Movie", genres=["Science Fiction"])})
        tmdb = FakeTmdb(catalogue, {("movie", "AI Movie", 2020): 2})
        ai_client = mock.Mock()
        ai_client.suggest.return_value = [{"title": "AI Movie", "year": 2020, "media_type": "movie",
                                          "reason": "great pacing"}]
        result = recommend.recommend(WATCHED, set(), tmdb, ai=ai_client)
        items = {i["title"]: i for i in result["items"]}
        self.assertIn("AI Movie", items)
        self.assertEqual(items["AI Movie"]["reason"], "AI pick: great pacing")

    def test_missing_reason_falls_back_to_a_generic_one(self):
        catalogue = self._catalogue({("movie", 2): _details("movie", 2, "AI Movie", genres=["Science Fiction"])})
        tmdb = FakeTmdb(catalogue, {("movie", "AI Movie", 2020): 2})
        ai_client = mock.Mock()
        ai_client.suggest.return_value = [{"title": "AI Movie", "year": 2020, "media_type": "movie"}]
        result = recommend.recommend(WATCHED, set(), tmdb, ai=ai_client)
        items = {i["title"]: i for i in result["items"]}
        self.assertEqual(items["AI Movie"]["reason"], "AI pick based on your taste profile")

    def test_unresolvable_title_is_skipped_not_a_crash(self):
        tmdb = FakeTmdb(self._catalogue(), search_results={})  # search() returns None for anything
        ai_client = mock.Mock()
        ai_client.suggest.return_value = [{"title": "Nonexistent Movie", "year": 2020, "media_type": "movie"}]
        result = recommend.recommend(WATCHED, set(), tmdb, ai=ai_client)
        self.assertNotIn("Nonexistent Movie", [i["title"] for i in result["items"]])

    def test_already_owned_or_watched_suggestion_is_excluded(self):
        catalogue = self._catalogue({("movie", 2): _details("movie", 2, "AI Movie", genres=["Science Fiction"])})
        tmdb = FakeTmdb(catalogue, {("movie", "AI Movie", 2020): 2})
        ai_client = mock.Mock()
        ai_client.suggest.return_value = [{"title": "AI Movie", "year": 2020, "media_type": "movie"}]
        result = recommend.recommend(WATCHED, {("movie", 2)}, tmdb, ai=ai_client)
        self.assertNotIn("AI Movie", [i["title"] for i in result["items"]])

    def test_ai_failure_is_a_note_not_a_crash(self):
        tmdb = FakeTmdb(self._catalogue())
        ai_client = mock.Mock()
        ai_client.suggest.side_effect = RuntimeError("API down")
        result = recommend.recommend(WATCHED, set(), tmdb, ai=ai_client)
        self.assertTrue(any("AI" in n for n in result["notes"]))

    def test_low_vote_ai_suggestion_is_still_filtered_by_min_votes(self):
        catalogue = self._catalogue({("movie", 2): _details("movie", 2, "Obscure AI Movie",
                                                            genres=["Science Fiction"], vote_count=3)})
        tmdb = FakeTmdb(catalogue, {("movie", "Obscure AI Movie", 2020): 2})
        ai_client = mock.Mock()
        ai_client.suggest.return_value = [{"title": "Obscure AI Movie", "year": 2020, "media_type": "movie"}]
        result = recommend.recommend(WATCHED, set(), tmdb, ai=ai_client)
        self.assertNotIn("Obscure AI Movie", [i["title"] for i in result["items"]])

    def test_duplicate_suggestions_are_not_added_twice(self):
        catalogue = self._catalogue({("movie", 2): _details("movie", 2, "AI Movie", genres=["Science Fiction"])})
        tmdb = FakeTmdb(catalogue, {("movie", "AI Movie", 2020): 2})
        ai_client = mock.Mock()
        ai_client.suggest.return_value = [{"title": "AI Movie", "year": 2020, "media_type": "movie"}] * 2
        result = recommend.recommend(WATCHED, set(), tmdb, ai=ai_client)
        self.assertEqual([i["title"] for i in result["items"]].count("AI Movie"), 1)

    def test_invalid_media_type_from_the_ai_is_skipped(self):
        tmdb = FakeTmdb(self._catalogue())
        ai_client = mock.Mock()
        ai_client.suggest.return_value = [{"title": "Some Podcast", "year": 2020, "media_type": "podcast"}]
        result = recommend.recommend(WATCHED, set(), tmdb, ai=ai_client)  # must not raise
        self.assertNotIn("Some Podcast", [i["title"] for i in result["items"]])

    def test_no_ai_client_never_calls_anything_ai_related(self):
        tmdb = FakeTmdb(self._catalogue())
        result = recommend.recommend(WATCHED, set(), tmdb)  # ai=None by default
        self.assertEqual(result["notes"], [])


class StrictFakeTmdb(FakeTmdb):
    """Raises if any TMDB-discovery method is called - proves ai_only=True doesn't touch them at
    all (not just that they return nothing), since every one of those calls has a real cost."""

    def discover(self, media_type, genre_name):
        raise AssertionError("ai_only must not call discover()")

    def trending(self, media_type):
        raise AssertionError("ai_only must not call trending()")

    def new_releases(self, media_type, genre_name, since, until):
        raise AssertionError("ai_only must not call new_releases()")


class TestAiOnly(unittest.TestCase):
    def _catalogue(self, extra=None):
        return {("movie", 1): _details("movie", 1, "Watched Movie", genres=["Science Fiction"]), **(extra or {})}

    def test_ai_only_never_calls_tmdb_discovery_methods(self):
        catalogue = self._catalogue({("movie", 2): _details("movie", 2, "AI Movie", genres=["Science Fiction"])})
        tmdb = StrictFakeTmdb(catalogue, {("movie", "AI Movie", 2020): 2})
        ai_client = mock.Mock()
        ai_client.suggest.return_value = [{"title": "AI Movie", "year": 2020, "media_type": "movie", "reason": "x"}]
        result = recommend.recommend(WATCHED, set(), tmdb, ai=ai_client, ai_only=True)  # must not raise
        self.assertIn("AI Movie", [i["title"] for i in result["items"]])

    def test_ai_only_with_no_ai_client_returns_nothing(self):
        tmdb = StrictFakeTmdb(self._catalogue())
        result = recommend.recommend(WATCHED, set(), tmdb, ai_only=True)  # ai=None
        self.assertEqual(result["items"], [])

    def test_ai_only_still_applies_the_same_scoring_and_min_votes(self):
        catalogue = self._catalogue({("movie", 2): _details("movie", 2, "Obscure", genres=["Science Fiction"],
                                                            vote_count=3)})
        tmdb = StrictFakeTmdb(catalogue, {("movie", "Obscure", 2020): 2})
        ai_client = mock.Mock()
        ai_client.suggest.return_value = [{"title": "Obscure", "year": 2020, "media_type": "movie"}]
        result = recommend.recommend(WATCHED, set(), tmdb, ai=ai_client, ai_only=True)
        self.assertNotIn("Obscure", [i["title"] for i in result["items"]])

    def test_ai_only_excludes_linked_and_genre_candidates_even_though_ai_found_nothing(self):
        # Without ai_only, this catalogue's recommendations link would normally surface "Linked
        # Movie" - ai_only must not include it, since that's a non-AI candidate source.
        catalogue = self._catalogue({("movie", 2): _details("movie", 2, "Linked Movie",
                                                            genres=["Science Fiction"])})
        catalogue[("movie", 1)] = {**catalogue[("movie", 1)], "recommendations": [2]}
        tmdb = FakeTmdb(catalogue)
        ai_client = mock.Mock()
        ai_client.suggest.return_value = []
        result = recommend.recommend(WATCHED, set(), tmdb, ai=ai_client, ai_only=True)
        self.assertEqual(result["items"], [])


class TestScoring(unittest.TestCase):
    FEATURES = {"genre": {"Sci-Fi": 1.0}, "keyword": {"time loop": 1.0}, "director": {"D": 1.0}, "actor": {"A": 1.0}}
    BLANK = {"genres": [], "keywords": [], "directors": [], "cast": []}

    def test_content_score_grows_with_overlap(self):
        none = recommend.content_score(self.FEATURES, {**self.BLANK, "genres": ["Comedy"]})
        some = recommend.content_score(self.FEATURES, {**self.BLANK, "genres": ["Sci-Fi"]})
        more = recommend.content_score(self.FEATURES, {**self.BLANK, "genres": ["Sci-Fi"], "directors": ["D"]})
        self.assertEqual(none, 0)
        self.assertLess(some, more)
        self.assertLessEqual(more, 1)

    def test_quality_is_shrunk_when_votes_are_few(self):
        few = recommend.quality_score({"vote_average": 10, "vote_count": 5})
        many = recommend.quality_score({"vote_average": 8.5, "vote_count": 20000})
        self.assertGreater(many, few)

    def test_quality_is_bounded(self):
        for avg in (0, 5, 10):
            q = recommend.quality_score({"vote_average": avg, "vote_count": 100000})
            self.assertTrue(0 <= q <= 1)


class TestRatingsInRecommend(unittest.TestCase):
    def test_loving_a_title_raises_its_genre_weight(self):
        watched, library = sample.load()
        before = recommend.recommend(watched, library, sample.SampleTmdb())["profile"]["genre"]["Comedy"]
        loved = profile.apply_ratings(watched, {("movie", 1015): 5})  # Superbad
        after = recommend.recommend(loved, library, sample.SampleTmdb())["profile"]["genre"]["Comedy"]
        self.assertGreater(after, before)

    def test_disliked_none_or_empty_changes_nothing(self):
        base = run()
        self.assertEqual(run(disliked=None), base)
        self.assertEqual(run(disliked={}), base)

    def _matches(self, **kw):
        # unfloored "match", so the ratio between two runs is exact
        with mock.patch.object(recommend.math, "floor", lambda x: x):
            return {(i["media_type"], i["tmdb_id"]): i["match"] for i in run(**kw)["items"]}

    def test_candidate_linked_from_a_one_star_title_scores_exactly_0_4(self):
        base = self._matches()
        after = self._matches(disliked={("movie", 1001): 1})  # Interstellar -> Gravity, Moon, Edge of Tomorrow
        penalised = {("movie", 1019), ("movie", 1009), ("movie", 1008)}
        self.assertIn(("movie", 1019), after)  # still listed, never excluded
        self.assertEqual(set(base), set(after))
        best = max(base, key=base.get)
        self.assertNotIn(best, penalised)
        for key in penalised & set(base):
            self.assertAlmostEqual(after[key], base[key] * 0.4, places=6, msg=key)
        for key in set(base) - penalised:
            self.assertAlmostEqual(after[key], base[key], places=6, msg=key)

    def test_two_stars_is_0_7_and_the_smallest_factor_wins(self):
        base = self._matches()
        two = self._matches(disliked={("movie", 1001): 2})
        self.assertAlmostEqual(two[("movie", 1019)], base[("movie", 1019)] * 0.7, places=6)
        both = self._matches(disliked={("movie", 1001): 2, ("movie", 1002): 1})  # both link Moon (1009)
        self.assertAlmostEqual(both[("movie", 1009)], base[("movie", 1009)] * 0.4, places=6)

    def test_other_star_values_and_unknown_titles_are_ignored(self):
        base = self._matches()
        self.assertEqual(self._matches(disliked={("movie", 1001): 3, ("movie", 99999): 1}), base)

    def test_only_max_disliked_titles_are_looked_up(self):
        looked_up = []
        real = sample.SampleTmdb()

        class Spy:
            def __getattr__(self, name):
                return getattr(real, name)

            def details(self, media_type, tmdb_id):
                looked_up.append(tmdb_id)
                return real.details(media_type, tmdb_id)

        watched, library = sample.load()
        many = {("movie", 5000 + n): 1 for n in range(recommend.MAX_DISLIKED + 20)}
        recommend.recommend(watched, library, Spy(), disliked=many)
        self.assertEqual(len([i for i in looked_up if i >= 5000]), recommend.MAX_DISLIKED)


if __name__ == "__main__":
    unittest.main()
