import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import db
import plex
import tmdb


class TestPlexParsing(unittest.TestCase):
    def test_watched_movie(self):
        item = plex.parse_item({"title": "Arrival", "year": 2016, "viewCount": 2, "lastViewedAt": 1750000000,
                                "userRating": 9.0, "Guid": [{"id": "imdb://tt2543164"}, {"id": "tmdb://329865"}]}, "movie")
        self.assertEqual((item["tmdb_id"], item["view_count"], item["user_rating"]), (329865, 2, 9.0))
        self.assertTrue(item["last_viewed"].startswith("2025-"))

    def test_unwatched_movie_has_zero_views(self):
        item = plex.parse_item({"title": "X", "Guid": [{"id": "tmdb://1"}]}, "movie")
        self.assertEqual(item["view_count"], 0)
        self.assertIsNone(item["last_viewed"])

    def test_show_progress(self):
        item = plex.parse_item({"title": "S", "leafCount": 10, "viewedLeafCount": 1, "Guid": [{"id": "tmdb://5"}]}, "tv")
        self.assertEqual((item["view_count"], item["progress"]), (1, 0.1))
        none = plex.parse_item({"title": "S", "leafCount": 10, "viewedLeafCount": 0, "Guid": [{"id": "tmdb://5"}]}, "tv")
        self.assertEqual(none["view_count"], 0)

    def test_no_tmdb_guid_is_skipped(self):
        self.assertIsNone(plex.parse_item({"title": "Old agent", "Guid": [{"id": "imdb://tt1"}]}, "movie"))
        self.assertIsNone(plex.parse_item({"title": "No guids"}, "movie"))
        self.assertIsNone(plex.parse_item({"title": "Junk", "Guid": [{"id": "tmdb://abc"}]}, "movie"))


class FakePlex(plex.PlexClient):
    """Serves canned Plex answers, two items per page, to exercise paging."""
    LIBRARY = [{"title": f"M{n}", "viewCount": n % 2, "Guid": [{"id": f"tmdb://{n}"}]} for n in range(5)] + [{"title": "no id"}]

    def _get(self, path, params=None):
        if path == "/library/sections":
            return {"Directory": [{"key": "1", "type": "movie"}, {"key": "2", "type": "artist"}]}
        start = params["X-Plex-Container-Start"]
        return {"totalSize": len(self.LIBRARY), "Metadata": self.LIBRARY[start:start + 2]}


class TestPlexLoad(unittest.TestCase):
    def test_paging_music_skipping_and_watched_split(self):
        watched, library_keys, skipped = FakePlex("http://x", "t").load()
        self.assertEqual(library_keys, {("movie", n) for n in range(5)})
        self.assertEqual(sorted(w["tmdb_id"] for w in watched), [1, 3])
        self.assertEqual(skipped, 1)


RAW_MOVIE = {
    "id": 10, "title": "Arrival", "release_date": "2016-11-10", "overview": "Aliens.", "poster_path": "/p.jpg",
    "vote_average": 7.6, "vote_count": 19000, "genres": [{"name": "Science Fiction"}],
    "keywords": {"keywords": [{"name": "alien contact"}]},
    "credits": {"cast": [{"name": f"Actor{n}"} for n in range(8)],
                "crew": [{"name": "Denis Villeneuve", "job": "Director"}, {"name": "Someone", "job": "Editor"}]},
    "recommendations": {"results": [{"id": 20}, {"id": 21}]},
}
RAW_TV = {
    "id": 30, "name": "Severance", "first_air_date": "2022-02-18", "poster_path": None,
    "genres": [{"name": "Sci-Fi & Fantasy"}, {"name": "Action & Adventure"}, {"name": "Drama"}],
    "keywords": {"results": [{"name": "conspiracy"}]}, "created_by": [{"name": "Dan Erickson"}],
    "credits": {"cast": []}, "recommendations": {"results": []},
}


class TestTmdb(unittest.TestCase):
    def setUp(self):
        self._old = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")

    def tearDown(self):
        db.DB_PATH = self._old

    def test_normalize_movie(self):
        d = tmdb.normalize(RAW_MOVIE, "movie")
        self.assertEqual((d["title"], d["year"], d["directors"]), ("Arrival", 2016, ["Denis Villeneuve"]))
        self.assertEqual(len(d["cast"]), 5)
        self.assertEqual(d["keywords"], ["alien contact"])
        self.assertEqual(d["recommendations"], [20, 21])
        self.assertEqual(d["poster_url"], "https://image.tmdb.org/t/p/w342/p.jpg")
        self.assertEqual(d["url"], "https://www.themoviedb.org/movie/10")

    def test_normalize_tv_folds_genre_names_and_uses_creator(self):
        d = tmdb.normalize(RAW_TV, "tv")
        self.assertEqual(d["genres"], ["Science Fiction", "Action", "Drama"])
        self.assertEqual((d["directors"], d["keywords"], d["year"], d["poster_url"]),
                         (["Dan Erickson"], ["conspiracy"], 2022, None))

    def test_normalize_keeps_full_release_date(self):
        self.assertEqual(tmdb.normalize(RAW_MOVIE, "movie")["release_date"], "2016-11-10")
        self.assertEqual(tmdb.normalize(RAW_TV, "tv")["release_date"], "2022-02-18")
        self.assertIsNone(tmdb.normalize({"id": 1}, "movie")["release_date"])

    def test_trending_and_new_releases_requests(self):
        client = tmdb.TmdbClient("k" * 32)
        calls = []

        def fake_get(url, headers=None, params=None, **kw):
            calls.append((url, params))
            if "/genre/" in url:
                return {"genres": [{"id": 878, "name": "Science Fiction"}]}
            return {"results": [{"id": 1}, {"id": 2}]}

        with mock.patch.object(tmdb, "get_json", side_effect=fake_get):
            self.assertEqual(client.trending("tv"), [1, 2])
            self.assertEqual(client.new_releases("movie", "Science Fiction", "2026-03-01", "2026-09-01"), [1, 2])
            self.assertEqual(client.new_releases("tv", "Science Fiction", "2026-03-01", "2026-09-01"), [1, 2])
            self.assertEqual(client.new_releases("movie", "Western", "2026-03-01", "2026-09-01"), [])
        self.assertTrue(calls[0][0].endswith("/trending/tv/week"))
        movie_params, tv_params = calls[2][1], calls[4][1]
        self.assertEqual((movie_params["primary_release_date.gte"], movie_params["primary_release_date.lte"]),
                         ("2026-03-01", "2026-09-01"))
        self.assertEqual(tv_params["first_air_date.gte"], "2026-03-01")
        self.assertEqual(movie_params["sort_by"], "popularity.desc")

    def test_normalize_survives_sparse_data(self):
        d = tmdb.normalize({"id": 1}, "movie")
        self.assertEqual((d["title"], d["year"], d["genres"], d["vote_count"]), ("?", None, [], 0))

    def test_details_are_cached(self):
        client = tmdb.TmdbClient("k" * 32)
        with mock.patch.object(tmdb, "get_json", return_value=RAW_MOVIE) as fake:
            first = client.details("movie", 10)
            second = client.details("movie", 10)
        self.assertEqual(first, second)
        self.assertEqual(fake.call_count, 1)

    def test_v4_token_uses_bearer_header_and_v3_key_uses_param(self):
        v4 = tmdb.TmdbClient("eyJ" + "a" * 100)
        v3 = tmdb.TmdbClient("a" * 32)
        self.assertIn("Authorization", v4.headers)
        self.assertEqual(v3.params, {"api_key": "a" * 32})

    def test_discover_maps_tv_genre_alias(self):
        client = tmdb.TmdbClient("k" * 32)
        calls = []

        def fake_get(url, headers=None, params=None, **kw):
            calls.append((url, params))
            if url.endswith("/genre/tv/list"):
                return {"genres": [{"id": 10765, "name": "Sci-Fi & Fantasy"}]}
            return {"results": [{"id": 7}, {"id": 8}]}

        with mock.patch.object(tmdb, "get_json", side_effect=fake_get):
            self.assertEqual(client.discover("tv", "Science Fiction"), [7, 8])
            self.assertEqual(client.discover("tv", "Western"), [])  # unknown genre -> nothing, no crash
        self.assertEqual(calls[1][1]["with_genres"], 10765)


if __name__ == "__main__":
    unittest.main()
