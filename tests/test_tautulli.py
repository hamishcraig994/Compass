"""Tests use response shapes based on Tautulli's documented API - not verified against a live
server (see the caveat in tautulli.py's docstring and the --tautulli-probe command)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tautulli
from tautulli import TautulliClient, _tmdb_id_from_metadata

MOVIE_META = {"rating_key": "100", "media_type": "movie", "title": "Arrival", "year": 2016,
             "guids": [{"id": "imdb://tt2543164"}, {"id": "tmdb://329865"}], "user_rating": 9.0}
SHOW_META = {"rating_key": "200", "media_type": "show", "title": "Severance", "year": 2022,
            "guids": [{"id": "tmdb://95396"}], "episode_count": 10}
OLD_AGENT_META = {"rating_key": "300", "media_type": "movie", "title": "Old", "year": 2001,
                  "guid": "com.plexapp.agents.themoviedb://603?lang=en"}


def history_response(rows, filtered=None):
    return {"response": {"result": "success",
                         "data": {"data": rows, "recordsFiltered": filtered if filtered is not None else len(rows)}}}


def metadata_response(meta):
    return {"response": {"result": "success", "data": meta}}


class TestTmdbIdFromMetadata(unittest.TestCase):
    def test_guids_list(self):
        self.assertEqual(_tmdb_id_from_metadata(MOVIE_META), 329865)

    def test_old_agent_single_guid_string(self):
        self.assertEqual(_tmdb_id_from_metadata(OLD_AGENT_META), 603)

    def test_no_tmdb_anywhere(self):
        self.assertIsNone(_tmdb_id_from_metadata({"guids": [{"id": "imdb://tt1"}]}))
        self.assertIsNone(_tmdb_id_from_metadata({}))

    def test_non_ascii_digit_in_legacy_guid_does_not_raise(self):
        meta = {"guid": "com.plexapp.agents.themoviedb://12\u00b2?lang=en"}
        self.assertEqual(_tmdb_id_from_metadata(meta), 12)
        self.assertIsNone(_tmdb_id_from_metadata({"guid": "com.plexapp.agents.themoviedb://\u00b2"}))


class TestTautulliClient(unittest.TestCase):
    def setUp(self):
        self.client = TautulliClient("http://tautulli:8181", "key123", user="hamish")

    def test_call_raises_on_failure_response(self):
        with mock.patch.object(tautulli, "get_json", return_value={"response": {"result": "error", "message": "bad key"}}):
            with self.assertRaises(RuntimeError):
                self.client._call("get_history")

    def test_call_includes_api_key_and_user_filter(self):
        seen = {}

        def fake_get_json(url, headers=None, params=None, **kw):
            seen.update(params)
            return history_response([])

        with mock.patch.object(tautulli, "get_json", side_effect=fake_get_json):
            self.client._history_rows()
        self.assertEqual(seen["apikey"], "key123")
        self.assertEqual(seen["user"], "hamish")
        self.assertEqual(seen["grouping"], 1)

    def test_history_pages_until_exhausted(self):
        pages = [history_response([{"n": n} for n in range(2)], filtered=5),
                 history_response([{"n": n} for n in range(2, 4)], filtered=5),
                 history_response([{"n": 4}], filtered=5)]

        with mock.patch.object(tautulli, "get_json", side_effect=pages):
            rows = self.client._history_rows()
        self.assertEqual([r["n"] for r in rows], [0, 1, 2, 3, 4])

    def test_load_builds_a_movie_from_one_history_row(self):
        row = {"media_type": "movie", "rating_key": "100", "date": 1750000000, "group_count": 2, "watched_status": 1}

        def fake_get_json(url, headers=None, params=None, **kw):
            if params.get("cmd") == "get_history":
                return history_response([row])
            return metadata_response(MOVIE_META)

        with mock.patch.object(tautulli, "get_json", side_effect=fake_get_json):
            watched, skipped = self.client.load()
        self.assertEqual(skipped, 0)
        self.assertEqual(len(watched), 1)
        item = watched[0]
        self.assertEqual((item["media_type"], item["tmdb_id"], item["title"], item["view_count"], item["user_rating"]),
                         ("movie", 329865, "Arrival", 2, 9.0))
        self.assertTrue(item["last_viewed"].startswith("2025-"))
        self.assertIsNone(item["progress"])

    def test_load_carries_rating_key_and_thumb(self):
        row = {"media_type": "movie", "rating_key": "100", "date": 1750000000, "watched_status": 1}
        meta = dict(MOVIE_META, thumb="/library/metadata/100/thumb/5")

        def fake_get_json(url, headers=None, params=None, **kw):
            return history_response([row]) if params.get("cmd") == "get_history" else metadata_response(meta)

        with mock.patch.object(tautulli, "get_json", side_effect=fake_get_json):
            watched, _ = self.client.load()
        self.assertEqual((watched[0]["rating_key"], watched[0]["thumb"]), (100, "/library/metadata/100/thumb/5"))
        meta.pop("thumb")
        with mock.patch.object(tautulli, "get_json", side_effect=fake_get_json):
            watched, _ = self.client.load()
        self.assertEqual((watched[0]["rating_key"], watched[0]["thumb"]), (100, None))

    def test_non_ascii_digit_rating_key_and_year_do_not_crash(self):
        row = {"media_type": "movie", "rating_key": "\u00b2", "date": 1750000000, "watched_status": 1}
        meta = dict(MOVIE_META, year="\u00b2")

        def fake_get_json(url, headers=None, params=None, **kw):
            return history_response([row]) if params.get("cmd") == "get_history" else metadata_response(meta)

        with mock.patch.object(tautulli, "get_json", side_effect=fake_get_json):
            watched, _ = self.client.load()
        self.assertEqual((watched[0]["rating_key"], watched[0]["year"]), (None, None))

    def test_show_rating_key_is_the_group_key(self):
        episodes = [{"media_type": "episode", "rating_key": "201", "grandparent_rating_key": "200",
                     "date": 1750000000, "watched_status": 1}]

        def fake_get_json(url, headers=None, params=None, **kw):
            return history_response(episodes) if params.get("cmd") == "get_history" else metadata_response(SHOW_META)

        with mock.patch.object(tautulli, "get_json", side_effect=fake_get_json):
            watched, _ = self.client.load()
        self.assertEqual(watched[0]["rating_key"], 200)

    def test_load_groups_episodes_under_the_show_and_computes_progress(self):
        episodes = [
            {"media_type": "episode", "rating_key": "201", "grandparent_rating_key": "200",
             "date": 1750000000, "watched_status": 1},
            {"media_type": "episode", "rating_key": "202", "grandparent_rating_key": "200",
             "date": 1750001000, "watched_status": 1},
        ]

        def fake_get_json(url, headers=None, params=None, **kw):
            if params.get("cmd") == "get_history":
                return history_response(episodes)
            return metadata_response(SHOW_META)

        with mock.patch.object(tautulli, "get_json", side_effect=fake_get_json):
            watched, skipped = self.client.load()
        self.assertEqual(len(watched), 1)
        item = watched[0]
        self.assertEqual((item["media_type"], item["tmdb_id"], item["progress"], item["view_count"]),
                         ("tv", 95396, 0.2, 2))  # 2 of 10 episodes

    def test_partial_watches_are_ignored(self):
        row = {"media_type": "movie", "rating_key": "100", "date": 1750000000, "watched_status": 0.5}
        with mock.patch.object(tautulli, "get_json", return_value=history_response([row])):
            watched, skipped = self.client.load()
        self.assertEqual((watched, skipped), ([], 0))

    def test_items_with_no_tmdb_id_are_skipped_not_fatal(self):
        row = {"media_type": "movie", "rating_key": "300", "date": 1750000000, "watched_status": 1}

        def fake_get_json(url, headers=None, params=None, **kw):
            if params.get("cmd") == "get_history":
                return history_response([row])
            return metadata_response({"rating_key": "300", "title": "No guid at all"})

        with mock.patch.object(tautulli, "get_json", side_effect=fake_get_json):
            watched, skipped = self.client.load()
        self.assertEqual((watched, skipped), ([], 1))

    def test_metadata_lookup_failure_is_skipped_not_fatal(self):
        rows = [{"media_type": "movie", "rating_key": "100", "date": 1750000000, "watched_status": 1},
                {"media_type": "movie", "rating_key": "300", "date": 1750000000, "watched_status": 1}]

        def fake_get_json(url, headers=None, params=None, **kw):
            if params.get("cmd") == "get_history":
                return history_response(rows)
            if params.get("rating_key") == "300":
                raise RuntimeError("gone")
            return metadata_response(MOVIE_META)

        with mock.patch.object(tautulli, "get_json", side_effect=fake_get_json):
            watched, skipped = self.client.load()
        self.assertEqual(skipped, 1)
        self.assertEqual(len(watched), 1)

    def test_raw_probe_returns_samples_without_crashing_on_empty_history(self):
        with mock.patch.object(tautulli, "get_json", return_value=history_response([])):
            result = self.client.raw_probe()
        self.assertEqual(result, {"history_sample": [], "metadata_sample": None})


if __name__ == "__main__":
    unittest.main()
