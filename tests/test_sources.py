import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
import plex
import radarr
import sonarr
import sources
import tautulli

WATCHED_TAUTULLI = [{"media_type": "movie", "tmdb_id": 1, "title": "A", "year": 2020,
                    "last_viewed": None, "user_rating": None, "view_count": 1, "progress": None}]
WATCHED_PLEX = [{"media_type": "movie", "tmdb_id": 2, "title": "B", "year": 2021,
                "last_viewed": None, "user_rating": None, "view_count": 1, "progress": None}]


CONFIG_KEYS = ("HISTORY_SOURCE", "PLEX_TOKEN", "TAUTULLI_URL", "TAUTULLI_API_KEY",
              "RADARR_URL", "RADARR_API_KEY", "RADARR_QUALITY_PROFILE_ID", "RADARR_ROOT_FOLDER",
              "SONARR_URL", "SONARR_API_KEY", "SONARR_QUALITY_PROFILE_ID", "SONARR_ROOT_FOLDER")


class TestLoadLive(unittest.TestCase):
    def setUp(self):
        self._old = {k: getattr(config, k) for k in CONFIG_KEYS}
        self.addCleanup(lambda: [setattr(config, k, v) for k, v in self._old.items()])

    def test_defaults_to_plex(self):
        config.HISTORY_SOURCE = "plex"
        config.PLEX_TOKEN = "t"
        with mock.patch.object(plex.PlexClient, "load", return_value=(WATCHED_PLEX, {("movie", 9)}, 3)):
            watched, library_keys, notes = sources._load_live()
        self.assertEqual(watched, WATCHED_PLEX)
        self.assertEqual(library_keys, {("movie", 9)})
        self.assertTrue(any("3 Plex titles" in n for n in notes))

    def test_tautulli_with_plex_token_gets_library_keys_too(self):
        config.HISTORY_SOURCE, config.PLEX_TOKEN = "tautulli", "t"
        config.TAUTULLI_URL, config.TAUTULLI_API_KEY = "http://x", "k"
        with mock.patch.object(tautulli.TautulliClient, "load", return_value=(WATCHED_TAUTULLI, 0)), \
             mock.patch.object(plex.PlexClient, "load", return_value=([], {("movie", 9)}, 0)):
            watched, library_keys, notes = sources._load_live()
        self.assertEqual(watched, WATCHED_TAUTULLI)
        self.assertEqual(library_keys, {("movie", 9)})
        self.assertEqual(notes, [])

    def test_tautulli_without_plex_token_has_no_library_keys_but_still_works(self):
        config.HISTORY_SOURCE, config.PLEX_TOKEN = "tautulli", ""
        config.TAUTULLI_URL, config.TAUTULLI_API_KEY = "http://x", "k"
        with mock.patch.object(tautulli.TautulliClient, "load", return_value=(WATCHED_TAUTULLI, 2)):
            watched, library_keys, notes = sources._load_live()
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
        with mock.patch.object(plex.PlexClient, "load", return_value=([], {("movie", 1)}, 0)), \
             mock.patch.object(radarr.RadarrClient, "existing_tmdb_ids", return_value={10, 11}), \
             mock.patch.object(sonarr.SonarrClient, "existing_tmdb_ids", return_value={20}):
            _, library_keys, notes = sources._load_live()
        self.assertEqual(library_keys, {("movie", 1), ("movie", 10), ("movie", 11), ("tv", 20)})
        self.assertEqual(notes, [])

    def test_unconfigured_arr_is_skipped_silently(self):
        config.RADARR_URL = config.SONARR_URL = ""
        with mock.patch.object(plex.PlexClient, "load", return_value=([], {("movie", 1)}, 0)):
            _, library_keys, notes = sources._load_live()
        self.assertEqual(library_keys, {("movie", 1)})
        self.assertEqual(notes, [])

    def test_arr_failure_is_a_note_not_a_crash(self):
        config.RADARR_URL, config.RADARR_API_KEY = "http://r", "k"
        with mock.patch.object(plex.PlexClient, "load", return_value=([], set(), 0)), \
             mock.patch.object(radarr.RadarrClient, "existing_tmdb_ids", side_effect=RuntimeError("down")):
            _, library_keys, notes = sources._load_live()
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


if __name__ == "__main__":
    unittest.main()
