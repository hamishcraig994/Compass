import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import db
import radarr
import sonarr
from radarr import RadarrClient
from sonarr import SonarrClient


class TestRadarrClient(unittest.TestCase):
    def setUp(self):
        self._old_db = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old_db)
        self.client = RadarrClient("http://arr:7878", "key123")

    def test_existing_tmdb_ids_ignores_movies_without_one(self):
        movies = [{"tmdbId": 1}, {"tmdbId": 2}, {"title": "no id"}]
        with mock.patch.object(radarr, "get_json", return_value=movies):
            self.assertEqual(self.client.existing_tmdb_ids(), {1, 2})

    def test_quality_profiles_are_cached_across_calls(self):
        with mock.patch.object(radarr, "get_json", return_value=[{"id": 1, "name": "HD"}]) as get:
            first = self.client.quality_profiles()
            second = self.client.quality_profiles()
        self.assertEqual(first, second)
        self.assertEqual(get.call_count, 1)  # the second call was a page render, not a network round-trip

    def test_root_folders_are_cached_across_calls(self):
        with mock.patch.object(radarr, "get_json", return_value=[{"path": "/data/movies"}]) as get:
            self.client.root_folders()
            self.client.root_folders()
        self.assertEqual(get.call_count, 1)

    def test_quality_profile_cache_is_shared_across_separate_client_instances(self):
        """sources.radarr_client() builds a fresh RadarrClient on every page render - the cache
        has to be keyed by URL, not tied to one Python object, or every render pays the network
        cost again anyway."""
        with mock.patch.object(radarr, "get_json", return_value=[{"id": 1, "name": "HD"}]) as get:
            RadarrClient("http://arr:7878", "key123").quality_profiles()
            RadarrClient("http://arr:7878", "key123").quality_profiles()
        self.assertEqual(get.call_count, 1)

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
        self._old_db = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old_db)
        self.client = SonarrClient("http://arr:8989", "key456")

    def test_existing_tmdb_ids_only_counts_shows_sonarr_has_matched(self):
        series = [{"tvdbId": 1, "tmdbId": 100}, {"tvdbId": 2}, {"tvdbId": 3, "tmdbId": 300}]
        with mock.patch.object(sonarr, "get_json", return_value=series):
            self.assertEqual(self.client.existing_tmdb_ids(), {100, 300})

    def test_quality_profiles_are_cached_across_calls(self):
        with mock.patch.object(sonarr, "get_json", return_value=[{"id": 1, "name": "WEB-1080p"}]) as get:
            self.client.quality_profiles()
            self.client.quality_profiles()
        self.assertEqual(get.call_count, 1)

    def test_root_folders_are_cached_across_calls(self):
        with mock.patch.object(sonarr, "get_json", return_value=[{"path": "/data/tv"}]) as get:
            self.client.root_folders()
            self.client.root_folders()
        self.assertEqual(get.call_count, 1)

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


class TestRadarrNormalize(unittest.TestCase):
    def test_every_state_branch(self):
        n = radarr.normalize_movie
        self.assertEqual(n({"hasFile": True, "monitored": False})["arr_state"], "downloaded")
        self.assertEqual(n({"monitored": False})["arr_state"], "unmonitored")
        self.assertEqual(n({"monitored": True, "isAvailable": False})["arr_state"], "upcoming")
        self.assertEqual(n({"monitored": True, "isAvailable": True, "status": "announced"})["arr_state"], "missing")
        self.assertEqual(n({"monitored": True, "status": "announced"})["arr_state"], "upcoming")
        self.assertEqual(n({"monitored": True, "status": "tba"})["arr_state"], "upcoming")
        self.assertEqual(n({"monitored": True, "status": "released"})["arr_state"], "missing")
        self.assertEqual(n({"monitored": True})["arr_state"], "missing")

    def test_fields(self):
        item = radarr.normalize_movie({"tmdbId": 11, "title": "Dune", "year": 2021, "monitored": True, "hasFile": True,
                                       "added": "2024-05-01T10:00:00Z"})
        self.assertEqual(item, {
            "media_type": "movie", "service": "radarr", "tmdb_id": 11, "tvdb_id": None, "title": "Dune",
            "year": 2021, "added_at": "2024-05-01T10:00:00Z", "monitored": True, "arr_state": "downloaded",
            "episodes": None, "poster_url": None, "url": "https://www.themoviedb.org/movie/11"})

    def test_missing_or_zero_values(self):
        for raw in ({}, {"tmdbId": 0, "year": 0, "added": "0001-01-01T00:00:00Z", "title": ""}):
            item = radarr.normalize_movie(raw)
            self.assertEqual((item["tmdb_id"], item["year"], item["added_at"], item["title"], item["url"]),
                             (None, None, None, "?", None))

    def test_poster_is_filtered(self):
        raw = {"images": [{"coverType": "poster", "remoteUrl": "https://image.tmdb.org/t/p/original/p.jpg",
                           "url": "/MediaCover/1/poster.jpg"}]}
        self.assertEqual(radarr.normalize_movie(raw)["poster_url"], "https://image.tmdb.org/t/p/w342/p.jpg")
        raw["images"][0]["remoteUrl"] = "http://evil.example/p.jpg"
        self.assertIsNone(radarr.normalize_movie(raw)["poster_url"])

    def test_library_makes_one_get_with_the_key_and_leaks_nothing(self):
        client = RadarrClient("http://arr:7878", "secretkey")
        with mock.patch.object(radarr, "get_json", return_value=[{"tmdbId": 1, "title": "A"}, {"tmdbId": 2}]) as get:
            items = client.library()
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.args[0], "http://arr:7878/api/v3/movie")
        self.assertEqual(get.call_args.kwargs["headers"], {"X-Api-Key": "secretkey"})
        self.assertEqual([i["tmdb_id"] for i in items], [1, 2])
        self.assertNotIn("secretkey", repr(items))

    def test_library_raises_on_failure(self):
        with mock.patch.object(radarr, "get_json", side_effect=RuntimeError("down")):
            with self.assertRaises(RuntimeError):
                RadarrClient("http://arr", "k").library()


class TestSonarrNormalize(unittest.TestCase):
    @staticmethod
    def raw(have=None, total=None, **extra):
        stats = {}
        if have is not None:
            stats["episodeFileCount"] = have
        if total is not None:
            stats["episodeCount"] = total
        return {"monitored": True, "statistics": stats, **extra}

    def test_every_state_branch(self):
        n = sonarr.normalize_series
        self.assertEqual(n(self.raw(10, 10, monitored=False))["arr_state"], "downloaded")
        self.assertEqual(n(self.raw(11, 10))["arr_state"], "downloaded")
        self.assertEqual(n(self.raw(5, 10))["arr_state"], "partial")
        self.assertEqual(n(self.raw(1, 0))["arr_state"], "partial")  # have > 0 but total unknown
        self.assertEqual(n(self.raw(0, 10, monitored=False))["arr_state"], "unmonitored")
        self.assertEqual(n(self.raw(0, 10, status="upcoming"))["arr_state"], "upcoming")
        self.assertEqual(n(self.raw(0, 0))["arr_state"], "upcoming")
        self.assertEqual(n(self.raw(0, 10, status="continuing"))["arr_state"], "missing")
        self.assertEqual(n(self.raw(None, 10))["arr_state"], "missing")

    def test_episodes_and_missing_statistics(self):
        self.assertEqual(sonarr.normalize_series(self.raw(3, 8))["episodes"], {"have": 3, "total": 8})
        self.assertEqual(sonarr.normalize_series({"monitored": True})["episodes"], {"have": 0, "total": 0})
        self.assertEqual(sonarr.normalize_series({"statistics": None})["episodes"], {"have": 0, "total": 0})

    def test_fields_and_empty_values(self):
        item = sonarr.normalize_series({"tmdbId": 5, "tvdbId": 99, "title": "Show", "year": 2020, "monitored": True,
                                        "added": "2023-01-01T00:00:00Z"})
        self.assertEqual((item["media_type"], item["service"], item["tmdb_id"], item["tvdb_id"], item["url"]),
                         ("tv", "sonarr", 5, 99, "https://www.themoviedb.org/tv/5"))
        empty = sonarr.normalize_series({"tmdbId": 0, "added": "0001-01-01T00:00:00Z"})
        self.assertEqual((empty["tmdb_id"], empty["added_at"], empty["title"], empty["url"]), (None, None, "?", None))

    def test_library_makes_one_get_with_the_key_and_leaks_nothing(self):
        client = SonarrClient("http://arr:8989", "secretkey")
        with mock.patch.object(sonarr, "get_json", return_value=[{"tmdbId": 1, "title": "A"}]) as get:
            items = client.library()
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.args[0], "http://arr:8989/api/v3/series")
        self.assertEqual(get.call_args.kwargs["headers"], {"X-Api-Key": "secretkey"})
        self.assertEqual(len(items), 1)
        self.assertNotIn("secretkey", repr(items))
