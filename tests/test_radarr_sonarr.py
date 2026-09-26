import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import radarr
import sonarr
from radarr import RadarrClient
from sonarr import SonarrClient


class TestRadarrClient(unittest.TestCase):
    def setUp(self):
        self.client = RadarrClient("http://arr:7878", "key123")

    def test_existing_tmdb_ids_ignores_movies_without_one(self):
        movies = [{"tmdbId": 1}, {"tmdbId": 2}, {"title": "no id"}]
        with mock.patch.object(radarr, "get_json", return_value=movies):
            self.assertEqual(self.client.existing_tmdb_ids(), {1, 2})

    def test_add_refuses_if_already_present(self):
        with mock.patch.object(radarr, "get_json", return_value=[{"tmdbId": 5}]):
            ok, msg = self.client.add(5)
        self.assertEqual((ok, msg), (False, "Already in Radarr"))

    def test_add_fails_cleanly_when_radarr_cant_find_it(self):
        with mock.patch.object(radarr, "get_json", side_effect=[[], None]):
            ok, msg = self.client.add(5)
        self.assertEqual((ok, msg), (False, "Radarr couldn't find this movie"))

    def test_add_success_posts_with_resolved_profile_and_folder(self):
        movie = {"title": "Arrival", "tmdbId": 5}
        calls = {"get": [], "post": None}

        def fake_get(url, headers=None, params=None):
            calls["get"].append((url, params))
            if url.endswith("/api/v3/movie") and params:
                return []  # not already present
            if url.endswith("/lookup/tmdb"):
                return dict(movie)
            if url.endswith("/qualityprofile"):
                return [{"id": 7, "name": "HD"}]
            if url.endswith("/rootfolder"):
                return [{"path": "/data/movies"}]
            raise AssertionError(f"unexpected GET {url}")

        def fake_post(url, headers=None, body=None):
            calls["post"] = (url, body)
            return body

        with mock.patch.object(radarr, "get_json", side_effect=fake_get), \
             mock.patch.object(radarr, "post_json", side_effect=fake_post):
            ok, msg = self.client.add(5)

        self.assertEqual((ok, msg), (True, 'Added "Arrival" to Radarr'))
        url, body = calls["post"]
        self.assertTrue(url.endswith("/api/v3/movie"))
        self.assertEqual((body["qualityProfileId"], body["rootFolderPath"], body["monitored"]), (7, "/data/movies", True))
        self.assertTrue(body["addOptions"]["searchForMovie"])

    def test_add_uses_configured_profile_and_folder_without_fetching(self):
        client = RadarrClient("http://arr:7878", "key123", quality_profile_id=3, root_folder="/data/hd")
        calls = []

        def fake_get(url, headers=None, params=None):
            calls.append(url)
            if url.endswith("/api/v3/movie") and params:
                return []
            if url.endswith("/lookup/tmdb"):
                return {"title": "X", "tmdbId": 9}
            raise AssertionError(f"should not fetch {url}")

        with mock.patch.object(radarr, "get_json", side_effect=fake_get), \
             mock.patch.object(radarr, "post_json", return_value={}) as post:
            ok, _ = client.add(9, search=False)

        self.assertTrue(ok)
        self.assertEqual(post.call_args.kwargs["body"]["qualityProfileId"], 3)
        self.assertEqual(post.call_args.kwargs["body"]["rootFolderPath"], "/data/hd")
        self.assertFalse(post.call_args.kwargs["body"]["addOptions"]["searchForMovie"])
        self.assertFalse(post.call_args.kwargs["body"]["monitored"])  # search=False also skips monitoring -
        # otherwise Radarr's own automatic search cycle would grab it shortly after anyway
        self.assertFalse(any("qualityprofile" in c or "rootfolder" in c for c in calls))

    def test_add_fails_cleanly_with_no_profiles_or_folders_available(self):
        def fake_get(url, headers=None, params=None):
            if url.endswith("/api/v3/movie") and params:
                return []
            if url.endswith("/lookup/tmdb"):
                return {"title": "X", "tmdbId": 9}
            return []  # empty quality profiles / root folders

        with mock.patch.object(radarr, "get_json", side_effect=fake_get):
            ok, msg = self.client.add(9)
        self.assertEqual((ok, msg), (False, "Radarr has no quality profile or root folder to add into"))

    def test_add_reports_errors_instead_of_raising(self):
        with mock.patch.object(radarr, "get_json", side_effect=RuntimeError("boom")):
            ok, msg = self.client.add(1)
        self.assertFalse(ok)
        self.assertIn("boom", msg)


class TestSonarrClient(unittest.TestCase):
    def setUp(self):
        self.client = SonarrClient("http://arr:8989", "key456")

    def test_existing_tmdb_ids_only_counts_shows_sonarr_has_matched(self):
        series = [{"tvdbId": 1, "tmdbId": 100}, {"tvdbId": 2}, {"tvdbId": 3, "tmdbId": 300}]
        with mock.patch.object(sonarr, "get_json", return_value=series):
            self.assertEqual(self.client.existing_tmdb_ids(), {100, 300})

    def test_add_refuses_if_already_present_by_tvdb_id(self):
        with mock.patch.object(sonarr, "get_json", return_value=[{"tvdbId": 55}]):
            ok, msg = self.client.add(55)
        self.assertEqual((ok, msg), (False, "Already in Sonarr"))

    def test_add_success_uses_tvdb_term_lookup_and_posts(self):
        show = {"title": "Severance", "tvdbId": 77}
        calls = {"post": None}

        def fake_get(url, headers=None, params=None):
            if url.endswith("/api/v3/series") and params is None:
                return []  # not already present
            if url.endswith("/lookup") and params == {"term": "tvdb:77"}:
                return [dict(show)]
            if url.endswith("/qualityprofile"):
                return [{"id": 4}]
            if url.endswith("/rootfolder"):
                return [{"path": "/data/tv"}]
            raise AssertionError(f"unexpected GET {url} {params}")

        def fake_post(url, headers=None, body=None):
            calls["post"] = (url, body)

        with mock.patch.object(sonarr, "get_json", side_effect=fake_get), \
             mock.patch.object(sonarr, "post_json", side_effect=fake_post):
            ok, msg = self.client.add(77)

        self.assertEqual((ok, msg), (True, 'Added "Severance" to Sonarr'))
        url, body = calls["post"]
        self.assertTrue(url.endswith("/api/v3/series"))
        self.assertEqual((body["qualityProfileId"], body["rootFolderPath"]), (4, "/data/tv"))
        self.assertTrue(body["addOptions"]["searchForMissingEpisodes"])
        self.assertTrue(body["monitored"])

    def test_add_with_search_false_also_leaves_it_unmonitored(self):
        show = {"title": "Severance", "tvdbId": 77}

        def fake_get(url, headers=None, params=None):
            if url.endswith("/api/v3/series") and params is None:
                return []
            if url.endswith("/lookup"):
                return [dict(show)]
            if url.endswith("/qualityprofile"):
                return [{"id": 4}]
            if url.endswith("/rootfolder"):
                return [{"path": "/data/tv"}]
            raise AssertionError(f"unexpected GET {url} {params}")

        with mock.patch.object(sonarr, "get_json", side_effect=fake_get), \
             mock.patch.object(sonarr, "post_json") as post:
            ok, _ = self.client.add(77, search=False)

        self.assertTrue(ok)
        self.assertFalse(post.call_args.kwargs["body"]["monitored"])  # otherwise Sonarr's own
        # automatic search cycle would grab it shortly after, regardless of the choice made here
        self.assertFalse(post.call_args.kwargs["body"]["addOptions"]["searchForMissingEpisodes"])

    def test_add_fails_cleanly_when_sonarr_cant_find_it(self):
        with mock.patch.object(sonarr, "get_json", side_effect=[[], []]):
            ok, msg = self.client.add(999)
        self.assertEqual((ok, msg), (False, "Sonarr couldn't find this show"))

    def test_add_reports_errors_instead_of_raising(self):
        with mock.patch.object(sonarr, "get_json", side_effect=RuntimeError("gone")):
            ok, msg = self.client.add(1)
        self.assertFalse(ok)
        self.assertIn("gone", msg)


if __name__ == "__main__":
    unittest.main()
