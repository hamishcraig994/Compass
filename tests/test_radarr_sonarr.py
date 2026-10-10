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
            "episodes": None, "arr_id": None, "seasons": None, "poster_url": None,
            "url": "https://www.themoviedb.org/movie/11"})

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


class TestNormalizeSeasons(unittest.TestCase):
    def test_arr_id_and_movie_seasons(self):
        item = radarr.normalize_movie({"id": 7, "tmdbId": 1})
        self.assertEqual((item["arr_id"], item["seasons"]), (7, None))
        self.assertIsNone(radarr.normalize_movie({"id": 0})["arr_id"])
        self.assertIsNone(radarr.normalize_movie({"id": True})["arr_id"])

    def test_series_seasons_sorted_with_statistics_fallbacks(self):
        raw = {"id": 3, "monitored": True, "seasons": [
            {"seasonNumber": 2, "monitored": False, "statistics": {"episodeFileCount": 1, "episodeCount": 6}},
            {"seasonNumber": 1, "monitored": True, "statistics": {"episodeFileCount": 8, "totalEpisodeCount": 8,
                                                                  "episodeCount": 5}},
            {"seasonNumber": 0, "monitored": False},
            {"seasonNumber": "x"}, {"seasonNumber": -1}, "junk"]}
        item = sonarr.normalize_series(raw)
        self.assertEqual(item["arr_id"], 3)
        self.assertEqual(item["seasons"], [
            {"number": 0, "monitored": False, "have": 0, "total": 0},      # missing statistics -> 0/0
            {"number": 1, "monitored": True, "have": 8, "total": 8},       # totalEpisodeCount wins
            {"number": 2, "monitored": False, "have": 1, "total": 6}])     # falls back to episodeCount

    def test_series_without_seasons_gives_an_empty_list(self):
        self.assertEqual(sonarr.normalize_series({})["seasons"], [])
        self.assertEqual(sonarr.normalize_series({"seasons": "x"})["seasons"], [])


def lookup_show():
    return {"title": "Severance", "tvdbId": 77, "someKey": 1,
            "seasons": [{"seasonNumber": 0, "monitored": False, "extra": "keep"},
                        {"seasonNumber": 1, "monitored": True}, {"seasonNumber": 2, "monitored": True},
                        {"seasonNumber": 3, "monitored": True}]}


class TestSonarrSeasonAdd(unittest.TestCase):
    def setUp(self):
        self._old_db = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old_db)
        self.client = SonarrClient("http://arr:8989", "key456")
        self.existing = None  # what GET /series?tvdbId= answers
        self.posts, self.puts = [], []

    def fake_get(self, url, headers=None, params=None):
        if url.endswith("/api/v3/series") and params and "tvdbId" in params:
            return [] if self.existing is None else [self.existing]
        if url.endswith("/lookup"):
            return [lookup_show()]
        if url.endswith("/qualityprofile"):
            return [{"id": 4}]
        if url.endswith("/rootfolder"):
            return [{"path": "/data/tv"}]
        raise AssertionError(f"unexpected GET {url} {params}")

    def run_add(self, *args, post_result=None, put_result=None, **kwargs):
        def post(url, headers=None, body=None):
            self.posts.append((url, body))
            return post_result

        def put(url, headers=None, body=None):
            self.puts.append((url, body))
            return put_result

        with mock.patch.object(sonarr, "get_json", side_effect=self.fake_get), \
             mock.patch.object(sonarr, "post_json", side_effect=post), \
             mock.patch.object(sonarr, "put_json", side_effect=put):
            return self.client.add(*args, **kwargs)

    def flags(self, body):
        return {s["seasonNumber"]: s["monitored"] for s in body["seasons"]}

    def test_new_series_pick_payload(self):
        ok, msg = self.run_add(77, True, seasons=[1, 3])
        self.assertEqual((ok, msg), (True, 'Added "Severance" to Sonarr'))
        (url, body), = self.posts
        self.assertEqual(url, "http://arr:8989/api/v3/series")
        self.assertEqual(self.flags(body), {0: False, 1: True, 2: False, 3: True})
        self.assertEqual(body["seasons"][0]["extra"], "keep")
        self.assertEqual(body["monitorNewItems"], "none")
        self.assertEqual(body["addOptions"], {"searchForMissingEpisodes": True, "searchForCutoffUnmetEpisodes": False,
                                              "ignoreEpisodesWithFiles": False})
        self.assertNotIn("monitor", body["addOptions"])
        self.assertEqual((body["qualityProfileId"], body["rootFolderPath"], body["monitored"], body["someKey"]),
                         (4, "/data/tv", True, 1))
        self.assertEqual(self.puts, [])

    def test_new_series_all_and_none_monitor_every_season_but_specials(self):
        for seasons in ("all", None):
            self.posts.clear()
            self.run_add(77, True, seasons=seasons)
            body = self.posts[0][1]
            self.assertEqual(self.flags(body), {0: False, 1: True, 2: True, 3: True})
            self.assertEqual(body["monitorNewItems"], "all")

    def test_search_false_adds_unmonitored_but_keeps_the_season_flags(self):
        self.run_add(77, False, seasons=[2])
        body = self.posts[0][1]
        self.assertFalse(body["monitored"])
        self.assertFalse(body["addOptions"]["searchForMissingEpisodes"])
        self.assertEqual(self.flags(body), {0: False, 1: False, 2: True, 3: False})

    def test_season_not_in_the_lookup_is_refused_without_posting(self):
        ok, msg = self.run_add(77, True, seasons=[1, 7])
        self.assertEqual((ok, msg), (False, 'Season 7 isn\'t listed for "Severance" in Sonarr'))
        self.assertEqual(self.posts, [])
        self.assertIsNone(self.client.last_item)

    def test_last_item_comes_from_the_response_and_is_none_on_failure(self):
        self.run_add(77, True, seasons="all", post_result={"id": 9, "tvdbId": 77, "tmdbId": 5, "title": "Severance",
                                                          "monitored": True})
        self.assertEqual((self.client.last_item["arr_id"], self.client.last_item["service"]), (9, "sonarr"))
        self.run_add(77, True, seasons="all", post_result=None)
        self.assertIsNone(self.client.last_item)
        with mock.patch.object(sonarr, "get_json", side_effect=RuntimeError("down")):
            self.client.add(77)
        self.assertIsNone(self.client.last_item)

    def make_existing(self, monitored=(1,)):
        self.existing = {"id": 12, "title": "Severance", "tvdbId": 77, "monitored": False, "seasons": [
            {"seasonNumber": n, "monitored": n in monitored} for n in (0, 1, 2, 3)]}

    def test_existing_series_without_seasons_is_already_in_sonarr(self):
        self.make_existing()
        ok, msg = self.run_add(77, True)
        self.assertEqual((ok, msg), (False, "Already in Sonarr"))
        self.assertEqual((self.puts, self.posts), ([], []))

    def test_existing_series_pick_puts_additively_and_searches_each_new_season(self):
        self.make_existing(monitored=(1,))
        ok, msg = self.run_add(77, True, seasons=[1, 2, 3], put_result={"id": 12, "tvdbId": 77, "monitored": True})
        self.assertEqual((ok, msg), (True, 'Now monitoring season 2 and 3 of "Severance" in Sonarr - searching now'))
        (url, body), = self.puts
        self.assertEqual(url, "http://arr:8989/api/v3/series/12")
        self.assertTrue(body["monitored"])
        self.assertEqual(self.flags(body), {0: False, 1: True, 2: True, 3: True})
        self.assertEqual(self.posts, [
            ("http://arr:8989/api/v3/command", {"name": "SeasonSearch", "seriesId": 12, "seasonNumber": 2}),
            ("http://arr:8989/api/v3/command", {"name": "SeasonSearch", "seriesId": 12, "seasonNumber": 3})])
        self.assertEqual(self.client.last_item["arr_id"], 12)

    def test_existing_series_never_unmonitors(self):
        self.make_existing(monitored=(1, 3))
        self.run_add(77, True, seasons=[2])
        self.assertEqual(self.flags(self.puts[0][1]), {0: False, 1: True, 2: True, 3: True})

    def test_existing_series_without_search_sends_no_commands(self):
        self.make_existing()
        ok, msg = self.run_add(77, False, seasons=[2])
        self.assertEqual((ok, msg), (True, 'Now monitoring season 2 of "Severance" in Sonarr'))
        self.assertEqual((len(self.puts), self.posts), (1, []))

    def test_existing_series_all_sends_one_series_search(self):
        self.make_existing(monitored=(1,))
        ok, msg = self.run_add(77, True, seasons="all")
        self.assertEqual((ok, msg), (True, 'Now monitoring season 2 and 3 of "Severance" in Sonarr - searching now'))
        self.assertEqual(self.posts, [("http://arr:8989/api/v3/command", {"name": "SeriesSearch", "seriesId": 12})])
        self.assertEqual(self.flags(self.puts[0][1])[0], False)

    def test_nothing_new_to_monitor(self):
        self.make_existing(monitored=(1, 2, 3))
        ok, msg = self.run_add(77, True, seasons=[1, 2])
        self.assertEqual((ok, msg), (False, 'Already monitoring those seasons of "Severance" in Sonarr'))
        self.assertEqual((self.puts, self.posts), ([], []))

    def test_existing_series_season_not_listed(self):
        self.make_existing()
        ok, msg = self.run_add(77, True, seasons=[9])
        self.assertEqual((ok, msg), (False, 'Season 9 isn\'t listed for "Severance" in Sonarr'))
        self.assertEqual(self.puts, [])

    def test_a_failed_search_command_does_not_fail_the_request(self):
        self.make_existing()
        with mock.patch.object(sonarr, "get_json", side_effect=self.fake_get), \
             mock.patch.object(sonarr, "put_json", return_value=None), \
             mock.patch.object(sonarr, "post_json", side_effect=RuntimeError("busy")):
            ok, msg = self.client.add(77, True, seasons=[2])
        self.assertEqual((ok, msg), (True, 'Now monitoring season 2 of "Severance" in Sonarr'))

    def test_find_filters_on_tvdb_id_client_side(self):
        rows = [{"tvdbId": 1, "title": "A"}, {"tvdbId": 77, "title": "B"}]
        with mock.patch.object(sonarr, "get_json", return_value=rows) as get:
            self.assertEqual(self.client._find(77)["title"], "B")
            self.assertIsNone(self.client._find(5))
        self.assertEqual(get.call_args.args[0], "http://arr:8989/api/v3/series")
        self.assertEqual(get.call_args.kwargs["params"], {"tvdbId": 5})

    def test_find_error_is_treated_as_not_found_by_add(self):
        def fail_find(url, headers=None, params=None):
            if params and "tvdbId" in params:
                raise RuntimeError("boom")
            return self.fake_get(url, headers, params)
        with mock.patch.object(sonarr, "get_json", side_effect=fail_find), \
             mock.patch.object(sonarr, "post_json", return_value=None) as post:
            ok, _ = self.client.add(77, True, seasons="all")
        self.assertTrue(ok)
        post.assert_called_once()

    def test_put_sends_the_api_key(self):
        with mock.patch.object(sonarr, "put_json", return_value=None) as put:
            self.client._put("/api/v3/series/1", {"a": 1})
        self.assertEqual(put.call_args.args[0], "http://arr:8989/api/v3/series/1")
        self.assertEqual(put.call_args.kwargs["headers"], {"X-Api-Key": "key456"})

    def test_the_api_key_is_in_no_message(self):
        with mock.patch.object(sonarr, "get_json", side_effect=RuntimeError("HTTP 500: nope")):
            _, msg = self.client.add(77, True, seasons=[1])
        self.assertNotIn("key456", msg)


class TestRadarrLastItem(unittest.TestCase):
    def setUp(self):
        self._old_db = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old_db)
        self.client = RadarrClient("http://arr:7878", "key123")

    def add(self, post_result):
        def fake_get(url, headers=None, params=None):
            if url.endswith("/movie") and params:
                return []
            if url.endswith("/lookup/tmdb"):
                return {"title": "Arrival", "tmdbId": 5}
            return [{"id": 1, "path": "/m"}]
        with mock.patch.object(radarr, "get_json", side_effect=fake_get), \
             mock.patch.object(radarr, "post_json", return_value=post_result):
            return self.client.add(5)

    def test_last_item_set_from_the_response(self):
        ok, _ = self.add({"id": 31, "tmdbId": 5, "title": "Arrival", "monitored": True})
        self.assertTrue(ok)
        self.assertEqual((self.client.last_item["arr_id"], self.client.last_item["tmdb_id"]), (31, 5))

    def test_last_item_none_for_a_non_dict_response_and_on_failure(self):
        self.add(None)
        self.assertIsNone(self.client.last_item)
        self.client.last_item = {"stale": 1}
        with mock.patch.object(radarr, "get_json", return_value=[{"tmdbId": 5}]):
            ok, _ = self.client.add(5)
        self.assertFalse(ok)
        self.assertIsNone(self.client.last_item)


if __name__ == "__main__":
    unittest.main()
