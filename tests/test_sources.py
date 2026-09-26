import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
import plex
import sources
import tautulli

WATCHED_TAUTULLI = [{"media_type": "movie", "tmdb_id": 1, "title": "A", "year": 2020,
                    "last_viewed": None, "user_rating": None, "view_count": 1, "progress": None}]
WATCHED_PLEX = [{"media_type": "movie", "tmdb_id": 2, "title": "B", "year": 2021,
                "last_viewed": None, "user_rating": None, "view_count": 1, "progress": None}]


class TestLoadLive(unittest.TestCase):
    def setUp(self):
        self._old = {k: getattr(config, k) for k in
                     ("HISTORY_SOURCE", "PLEX_TOKEN", "TAUTULLI_URL", "TAUTULLI_API_KEY")}
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


if __name__ == "__main__":
    unittest.main()
