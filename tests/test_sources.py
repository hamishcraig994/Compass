import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
import db
import plex
import radarr
import sonarr
import sources
import tautulli
import tmdb

WATCHED_TAUTULLI = [{"media_type": "movie", "tmdb_id": 1, "title": "A", "year": 2020,
                    "last_viewed": None, "user_rating": None, "view_count": 1, "progress": None}]
WATCHED_PLEX = [{"media_type": "movie", "tmdb_id": 2, "title": "B", "year": 2021,
                "last_viewed": None, "user_rating": None, "view_count": 1, "progress": None}]


CONFIG_KEYS = ("HISTORY_SOURCE", "PLEX_TOKEN", "TAUTULLI_URL", "TAUTULLI_API_KEY",
              "RADARR_URL", "RADARR_API_KEY", "RADARR_QUALITY_PROFILE_ID", "RADARR_ROOT_FOLDER",
              "SONARR_URL", "SONARR_API_KEY", "SONARR_QUALITY_PROFILE_ID", "SONARR_ROOT_FOLDER",
              "AI_PROVIDER_URL", "AI_TOKEN", "AI_MODEL")


class TestLoadLive(unittest.TestCase):
    def setUp(self):
        self._old = {k: getattr(config, k) for k in CONFIG_KEYS}
        self.addCleanup(lambda: [setattr(config, k, v) for k, v in self._old.items()])

    def test_defaults_to_plex(self):
        config.HISTORY_SOURCE = "plex"
        config.PLEX_TOKEN = "t"
        with mock.patch.object(plex.PlexClient, "load", return_value=(WATCHED_PLEX, {("movie", 9)}, 3, [])):
            watched, library_keys, notes, _ = sources._load_live()
        self.assertEqual(watched, WATCHED_PLEX)
        self.assertEqual(library_keys, {("movie", 9)})
        self.assertTrue(any("3 Plex titles" in n for n in notes))

    def test_tautulli_with_plex_token_gets_library_keys_too(self):
        config.HISTORY_SOURCE, config.PLEX_TOKEN = "tautulli", "t"
        config.TAUTULLI_URL, config.TAUTULLI_API_KEY = "http://x", "k"
        with mock.patch.object(tautulli.TautulliClient, "load", return_value=(WATCHED_TAUTULLI, 0)), \
             mock.patch.object(plex.PlexClient, "load", return_value=([], {("movie", 9)}, 0, [])):
            watched, library_keys, notes, _ = sources._load_live()
        self.assertEqual(watched, WATCHED_TAUTULLI)
        self.assertEqual(library_keys, {("movie", 9)})
        self.assertEqual(notes, [])

    def test_tautulli_without_plex_token_has_no_library_keys_but_still_works(self):
        config.HISTORY_SOURCE, config.PLEX_TOKEN = "tautulli", ""
        config.TAUTULLI_URL, config.TAUTULLI_API_KEY = "http://x", "k"
        with mock.patch.object(tautulli.TautulliClient, "load", return_value=(WATCHED_TAUTULLI, 2)):
            watched, library_keys, notes, _ = sources._load_live()
        self.assertEqual(watched, WATCHED_TAUTULLI)
        self.assertEqual(library_keys, set())
        self.assertTrue(any("PLEX_TOKEN not set" in n for n in notes))
        self.assertTrue(any("2 Tautulli" in n for n in notes))


class TestArrExclusions(unittest.TestCase):
    def setUp(self):
        self._old = {k: getattr(config, k) for k in CONFIG_KEYS}
        self.addCleanup(lambda: [setattr(config, k, v) for k, v in self._old.items()])
        config.HISTORY_SOURCE, config.PLEX_TOKEN = "plex", "t"

    def test_merges_radarr_and_sonarr_into_library_keys(self):
        config.RADARR_URL, config.RADARR_API_KEY = "http://r", "k"
        config.SONARR_URL, config.SONARR_API_KEY = "http://s", "k"
        with mock.patch.object(plex.PlexClient, "load", return_value=([], {("movie", 1)}, 0, [])), \
             mock.patch.object(radarr.RadarrClient, "existing_tmdb_ids", return_value={10, 11}), \
             mock.patch.object(sonarr.SonarrClient, "existing_tmdb_ids", return_value={20}):
            _, library_keys, notes, _ = sources._load_live()
        self.assertEqual(library_keys, {("movie", 1), ("movie", 10), ("movie", 11), ("tv", 20)})
        self.assertEqual(notes, [])

    def test_unconfigured_arr_is_skipped_silently(self):
        config.RADARR_URL = config.SONARR_URL = ""
        with mock.patch.object(plex.PlexClient, "load", return_value=([], {("movie", 1)}, 0, [])):
            _, library_keys, notes, _ = sources._load_live()
        self.assertEqual(library_keys, {("movie", 1)})
        self.assertEqual(notes, [])

    def test_arr_failure_is_a_note_not_a_crash(self):
        config.RADARR_URL, config.RADARR_API_KEY = "http://r", "k"
        with mock.patch.object(plex.PlexClient, "load", return_value=([], set(), 0, [])), \
             mock.patch.object(radarr.RadarrClient, "existing_tmdb_ids", side_effect=RuntimeError("down")):
            _, library_keys, notes, _ = sources._load_live()
        self.assertEqual(library_keys, set())
        self.assertTrue(any("Radarr" in n and "down" in n for n in notes))


class TestAddToLibrary(unittest.TestCase):
    def setUp(self):
        self._old = {k: getattr(config, k) for k in CONFIG_KEYS + ("TMDB_TOKEN",)}
        self.addCleanup(lambda: [setattr(config, k, v) for k, v in self._old.items()])

    def test_movie_goes_straight_to_radarr(self):
        config.RADARR_URL, config.RADARR_API_KEY = "http://r", "k"
        with mock.patch.object(radarr.RadarrClient, "add", return_value=(True, "Added")) as add:
            ok, msg = sources.add_to_library("movie", 5)
        self.assertEqual((ok, msg), (True, "Added"))
        add.assert_called_once_with(5, search=True)

    def test_movie_without_radarr_configured(self):
        config.RADARR_URL = ""
        ok, msg = sources.add_to_library("movie", 5)
        self.assertEqual((ok, msg), (False, "Radarr isn't configured"))

    def test_tv_resolves_tvdb_id_via_tmdb_then_adds_to_sonarr(self):
        config.SONARR_URL, config.SONARR_API_KEY, config.TMDB_TOKEN = "http://s", "k", "t" * 32
        with mock.patch("tmdb.TmdbClient.external_ids", return_value={"tvdb_id": 77}), \
             mock.patch.object(sonarr.SonarrClient, "add", return_value=(True, "Added")) as add:
            ok, msg = sources.add_to_library("tv", 5)
        self.assertEqual((ok, msg), (True, "Added"))
        add.assert_called_once_with(77, search=True)

    def test_tv_without_tvdb_id_fails_cleanly(self):
        config.SONARR_URL, config.SONARR_API_KEY, config.TMDB_TOKEN = "http://s", "k", "t" * 32
        with mock.patch("tmdb.TmdbClient.external_ids", return_value={"tvdb_id": None}):
            ok, msg = sources.add_to_library("tv", 5)
        self.assertFalse(ok)
        self.assertIn("TVDB", msg)

    def test_tv_without_sonarr_configured(self):
        config.SONARR_URL = ""
        ok, msg = sources.add_to_library("tv", 5)
        self.assertEqual((ok, msg), (False, "Sonarr isn't configured"))


class TestRunNeverUsesAi(unittest.TestCase):
    """AI is manual-only (the Generate button) - locking in that run(), used for every automatic
    cache refresh, never triggers it, so it can never rack up API calls just from someone visiting
    the page or the cache expiring."""

    def setUp(self):
        self._old = {k: getattr(config, k) for k in CONFIG_KEYS}
        self.addCleanup(lambda: [setattr(config, k, v) for k, v in self._old.items()])
        config.HISTORY_SOURCE, config.PLEX_TOKEN = "plex", "t"
        config.AI_TOKEN = "sk-configured"  # AI IS configured - run() must still never touch it

    def test_run_never_calls_ai_suggest(self):
        import ai
        with mock.patch.object(plex.PlexClient, "load", return_value=([], set(), 0, [])), \
             mock.patch.object(ai.AiClient, "suggest") as suggest:
            sources.run(sample_mode=False)
        suggest.assert_not_called()

    def test_sample_mode_never_calls_ai_suggest_either(self):
        import ai
        with mock.patch.object(ai.AiClient, "suggest") as suggest:
            sources.run(sample_mode=True)
        suggest.assert_not_called()


class TestGenerateAiRecommendations(unittest.TestCase):
    def setUp(self):
        self._old = {k: getattr(config, k) for k in CONFIG_KEYS + ("TMDB_TOKEN",)}
        self.addCleanup(lambda: [setattr(config, k, v) for k, v in self._old.items()])
        config.HISTORY_SOURCE, config.PLEX_TOKEN, config.TMDB_TOKEN = "plex", "t", "t" * 32

    def test_refuses_cleanly_when_ai_is_not_configured(self):
        config.AI_TOKEN = ""
        result = sources.generate_ai_recommendations()
        self.assertEqual(result["items"], [])
        self.assertFalse(result["sample"])
        self.assertTrue(any("AI isn't configured" in n for n in result["notes"]))

    def test_uses_ai_only_mode_when_configured(self):
        config.AI_TOKEN = "sk-configured"
        with mock.patch.object(plex.PlexClient, "load", return_value=([], set(), 0, [])), \
             mock.patch("recommend.recommend") as recommend_fn:
            recommend_fn.return_value = {"items": [], "profile": {}, "notes": []}
            sources.generate_ai_recommendations(limit=10)
        self.assertTrue(recommend_fn.call_args.kwargs["ai_only"])
        self.assertIsNotNone(recommend_fn.call_args.kwargs["ai"])
        self.assertEqual(recommend_fn.call_args.kwargs["limit"], 10)

    def test_result_is_never_marked_as_sample(self):
        import ai
        config.AI_TOKEN = "sk-configured"
        with mock.patch.object(plex.PlexClient, "load", return_value=([], set(), 0, [])), \
             mock.patch.object(ai.AiClient, "suggest", return_value=[]):
            result = sources.generate_ai_recommendations()
        self.assertFalse(result["sample"])


PLEX_LIBRARY = [
    {"media_type": "movie", "tmdb_id": 1, "title": "Seen", "year": 2020, "added_at": "2026-01-01T00:00:00+00:00",
     "view_count": 2, "progress": None, "rating_key": 10, "thumb": "/library/metadata/10/thumb/1",
     "last_viewed": None, "user_rating": 8.0},
    {"media_type": "movie", "tmdb_id": None, "title": "No id", "year": None, "added_at": None, "view_count": 0,
     "progress": None, "rating_key": 11, "thumb": "http://evil.example/x.jpg", "last_viewed": None, "user_rating": None},
    {"media_type": "tv", "tmdb_id": 3, "title": "Show", "year": 2021, "added_at": None, "view_count": 1,
     "progress": 0.5, "rating_key": 12, "thumb": None, "last_viewed": None, "user_rating": None}]
PLEX_WATCHED = [dict(PLEX_LIBRARY[0]), dict(PLEX_LIBRARY[2])]


class TestRunSnapshotAndRatings(unittest.TestCase):
    def setUp(self):
        self._old = {k: getattr(config, k) for k in CONFIG_KEYS + ("TMDB_TOKEN",)}
        self.addCleanup(lambda: [setattr(config, k, v) for k, v in self._old.items()])
        config.HISTORY_SOURCE, config.PLEX_TOKEN, config.TMDB_TOKEN = "plex", "t", "t" * 32
        config.RADARR_URL = config.SONARR_URL = ""
        self._db = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._db)
        for patch in (mock.patch.object(plex.PlexClient, "load",
                                        return_value=(PLEX_WATCHED, {("movie", 1), ("tv", 3)}, 1, PLEX_LIBRARY)),
                      mock.patch("tmdb.TmdbClient.cached_details", return_value=None)):
            patch.start()
            self.addCleanup(patch.stop)

    def run_live(self, **kw):
        with mock.patch("recommend.recommend", return_value={"items": [], "profile": {}, "notes": []}) as rec:
            result = sources.run(sample_mode=False, **kw)
        return result, rec

    def test_no_poster_keys_without_a_plex_token(self):
        config.PLEX_TOKEN = ""
        result, _ = self.run_live()
        self.assertEqual(result["thumbs"], {})
        for item in result["library"] + result["watched"]:
            self.assertIsNone(item["poster_key"])

    def test_fills_library_watched_and_thumbs_without_any_thumb_key(self):
        result, _ = self.run_live()
        self.assertEqual(result["thumbs"], {10: "/library/metadata/10/thumb/1"})  # the evil one is dropped
        lib = {i["title"]: i for i in result["library"]}
        self.assertEqual(len(lib), 3)
        self.assertIsNone(lib["No id"]["tmdb_id"])
        self.assertIsNone(lib["No id"]["url"])
        self.assertIsNone(lib["No id"]["poster_key"])
        self.assertEqual((lib["Seen"]["poster_key"], lib["Seen"]["watched"], lib["Seen"]["url"]),
                         (10, True, "https://www.themoviedb.org/movie/1"))
        self.assertFalse(lib["No id"]["watched"])
        self.assertEqual(lib["Show"]["progress"], 0.5)
        for item in result["library"] + result["watched"]:
            self.assertNotIn("thumb", item)
        self.assertEqual([w["poster_key"] for w in result["watched"]], [10, None])
        self.assertEqual(result["watched"][0]["user_rating"], 8.0)

    def test_watched_posters_come_from_cached_details_only(self):
        cached = {"poster_url": "https://image.tmdb.org/p.jpg", "url": "https://www.themoviedb.org/movie/1"}
        with mock.patch("tmdb.TmdbClient.cached_details", side_effect=lambda t, i: cached if i == 1 else None), \
             mock.patch("tmdb.TmdbClient.details", side_effect=AssertionError("network")):
            result, _ = self.run_live()
        self.assertEqual((result["watched"][0]["poster_url"], result["watched"][0]["url"]), (cached["poster_url"], cached["url"]))
        self.assertEqual((result["watched"][1]["poster_url"], result["watched"][1]["url"]), (None, None))

    def test_live_run_applies_ratings_and_passes_disliked(self):
        db.set_rating("movie", 1, 1)
        db.set_rating("tv", 3, 5)
        result, rec = self.run_live()
        sent = {w["tmdb_id"]: w for w in rec.call_args.args[0]}
        self.assertEqual((sent[1]["user_rating"], sent[3]["user_rating"]), (2, 10))
        self.assertEqual(rec.call_args.kwargs["disliked"], {("movie", 1): 1})
        # the snapshot stays raw; the page overlays your rating itself
        self.assertEqual(result["watched"][0]["user_rating"], 8.0)
        self.assertEqual(PLEX_WATCHED[0]["user_rating"], 8.0)  # input not mutated

    def test_sample_run_ignores_ratings_and_the_tmdb_cache(self):
        db.set_rating("movie", 1001, 1)
        with mock.patch("tmdb.TmdbClient.cached_details", side_effect=AssertionError("cache")), \
             mock.patch("db.ratings", side_effect=AssertionError("ratings")):
            result = sources.run(sample_mode=True)
        self.assertIn("Interstellar", [i["title"] for i in result["library"]])
        self.assertIn("Dune", [i["title"] for i in result["library"]])
        self.assertEqual(len(result["watched"]), 16)
        self.assertEqual(result["thumbs"], {})
        for item in result["library"] + result["watched"]:
            self.assertNotIn("thumb", item)
            self.assertIsNone(item["poster_key"])
        by_title = {w["title"]: w for w in result["watched"]}
        self.assertEqual(by_title["Interstellar"]["user_rating"], 10)  # sample's own, untouched by the DB

    def test_tautulli_without_plex_token_has_no_library_but_has_watched(self):
        config.HISTORY_SOURCE, config.PLEX_TOKEN = "tautulli", ""
        config.TAUTULLI_URL, config.TAUTULLI_API_KEY = "http://x", "k"
        w = dict(WATCHED_TAUTULLI[0], rating_key=77, thumb="/library/metadata/77/thumb/1")
        with mock.patch.object(tautulli.TautulliClient, "load", return_value=([w], 0)):
            result, _ = self.run_live()
            _, _, _, library = sources._load_live()
        self.assertIsNone(library)
        self.assertIsNone(result["library"])
        self.assertEqual(result["thumbs"], {})  # /poster needs PLEX_TOKEN, so no keys that would 404
        self.assertIsNone(result["watched"][0]["poster_key"])

    def test_ai_generation_applies_ratings_and_disliked(self):
        config.AI_TOKEN = "sk-x"
        db.set_rating("movie", 1, 2)
        with mock.patch("recommend.recommend", return_value={"items": [], "profile": {}, "notes": []}) as rec:
            sources.generate_ai_recommendations()
        self.assertEqual({w["tmdb_id"]: w["user_rating"] for w in rec.call_args.args[0]}[1], 4)
        self.assertEqual(rec.call_args.kwargs["disliked"], {("movie", 1): 2})


class TestFillHeroDetails(unittest.TestCase):
    HERO = {"backdrop_url": "https://image.tmdb.org/b.jpg", "poster_large_url": "https://image.tmdb.org/l.jpg",
            "runtime": 120, "seasons": None, "certification": "15"}

    def items(self, n_movie, n_tv, with_fields=()):
        out = [{"media_type": "movie", "tmdb_id": i, "title": f"M{i}"} for i in range(n_movie)]
        out += [{"media_type": "tv", "tmdb_id": 1000 + i, "title": f"T{i}"} for i in range(n_tv)]
        for item in out:
            if item["tmdb_id"] in with_fields:
                item["backdrop_url"] = None
        return sorted(out, key=lambda i: (i["tmdb_id"] % 2, i["tmdb_id"]))  # interleave-ish; order is kept

    def test_refreshes_only_hero_picks_missing_the_field_at_most_15(self):
        items = [{"media_type": "movie" if i % 2 else "tv", "tmdb_id": i, "title": f"X{i}"} for i in range(40)]
        client = mock.Mock()
        client.details.side_effect = lambda t, i, refresh=False: {**self.HERO, "title": "ignored"}
        n = sources._fill_hero_details(items, client)
        self.assertLessEqual(n, 15)
        self.assertEqual(n, client.details.call_count)
        # kind all: first 5; movie: first 5 movies; tv: first 5 tvs; deduplicated
        wanted = {(i["media_type"], i["tmdb_id"]) for kind in ("all", "movie", "tv")
                  for i in sources.browse.hero_picks(items, kind)}
        called = {(c.args[0], c.args[1]) for c in client.details.call_args_list}
        self.assertEqual(called, wanted)
        self.assertEqual(len(wanted), n)
        for c in client.details.call_args_list:
            self.assertIs(c.kwargs["refresh"], True)

    def test_dedupes_across_kinds(self):
        items = [{"media_type": "movie", "tmdb_id": i, "title": f"M{i}"} for i in range(8)]
        client = mock.Mock()
        client.details.return_value = dict(self.HERO)
        self.assertEqual(sources._fill_hero_details(items, client), 5)  # all == movie; tv is empty

    def test_updates_the_five_fields_and_nothing_else(self):
        items = [{"media_type": "movie", "tmdb_id": 1, "title": "Keep", "reason": "r", "match": 90}]
        client = mock.Mock()
        client.details.return_value = {**self.HERO, "title": "Other", "reason": "x", "match": 1}
        sources._fill_hero_details(items, client)
        self.assertEqual(items[0]["title"], "Keep")
        self.assertEqual((items[0]["reason"], items[0]["match"]), ("r", 90))
        for key, value in self.HERO.items():
            self.assertEqual(items[0][key], value)

    def test_missing_keys_in_fresh_details_become_none(self):
        items = [{"media_type": "movie", "tmdb_id": 1, "title": "A"}]
        client = mock.Mock()
        client.details.return_value = {"backdrop_url": None}
        sources._fill_hero_details(items, client)
        self.assertEqual(items[0]["runtime"], None)
        self.assertIn("backdrop_url", items[0])
        self.assertEqual(sources._fill_hero_details(items, client), 0)  # now has the key: skipped

    def test_skips_items_that_already_have_the_key_even_if_none(self):
        items = [{"media_type": "movie", "tmdb_id": 1, "title": "A", "backdrop_url": None},
                 {"media_type": "movie", "tmdb_id": 2, "title": "B"}]
        client = mock.Mock()
        client.details.return_value = dict(self.HERO)
        self.assertEqual(sources._fill_hero_details(items, client), 1)
        client.details.assert_called_once_with("movie", 2, refresh=True)

    def test_one_failure_does_not_abort_the_rest(self):
        items = [{"media_type": "movie", "tmdb_id": i, "title": f"M{i}"} for i in range(3)]
        client = mock.Mock()

        def details(t, i, refresh=False):
            if i == 1:
                raise RuntimeError("boom")
            return dict(self.HERO)
        client.details.side_effect = details
        self.assertEqual(sources._fill_hero_details(items, client), 2)
        self.assertNotIn("backdrop_url", items[1])
        self.assertIn("backdrop_url", items[0])
        self.assertIn("backdrop_url", items[2])

    def test_empty_items(self):
        self.assertEqual(sources._fill_hero_details([], mock.Mock()), 0)


class TestRunHeroDetails(unittest.TestCase):
    def test_sample_run_never_fills_and_sample_items_carry_the_five_keys(self):
        with mock.patch.object(sources, "_fill_hero_details", side_effect=AssertionError("live only")), \
             mock.patch("tmdb.TmdbClient.details", side_effect=AssertionError("network")):
            result = sources.run(sample_mode=True)
        self.assertTrue(result["items"])
        for item in result["items"]:
            for key in sources.browse.HERO_FIELDS:
                self.assertIn(key, item)
                self.assertIsNone(item[key])

    def test_live_run_fills_hero_picks_with_the_client(self):
        old = {k: getattr(config, k) for k in CONFIG_KEYS + ("TMDB_TOKEN",)}
        self.addCleanup(lambda: [setattr(config, k, v) for k, v in old.items()])
        config.HISTORY_SOURCE, config.PLEX_TOKEN, config.TMDB_TOKEN = "plex", "t", "t" * 32
        config.RADARR_URL = config.SONARR_URL = ""
        olddb = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", olddb)
        recs = {"items": [{"media_type": "movie", "tmdb_id": i, "title": f"M{i}"} for i in range(8)],
                "profile": {}, "notes": []}
        fresh = {"backdrop_url": "https://image.tmdb.org/b.jpg", "poster_large_url": None, "runtime": 99,
                 "seasons": None, "certification": "PG"}
        with mock.patch.object(plex.PlexClient, "load", return_value=([], set(), 0, [])), \
             mock.patch("tmdb.TmdbClient.cached_details", return_value=None), \
             mock.patch("recommend.recommend", return_value=recs), \
             mock.patch.object(tmdb.TmdbClient, "details", return_value=fresh) as details:
            result = sources.run(sample_mode=False)
        self.assertEqual(details.call_count, 5)
        self.assertEqual([i.get("runtime") for i in result["items"]], [99] * 5 + [None] * 3)


if __name__ == "__main__":
    unittest.main()
