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
    LIBRARY = [{"title": f"M{n}", "viewCount": n % 2, "ratingKey": str(100 + n), "thumb": f"/library/metadata/{100 + n}/thumb/1",
                "Guid": [{"id": f"tmdb://{n}"}]} for n in range(5)] + [{"title": "no id"}]

    def _get(self, path, params=None):
        if path == "/library/sections":
            return {"Directory": [{"key": "1", "type": "movie"}, {"key": "2", "type": "artist"}]}
        start = params["X-Plex-Container-Start"]
        return {"totalSize": len(self.LIBRARY), "Metadata": self.LIBRARY[start:start + 2]}


class TestPlexLoad(unittest.TestCase):
    def test_paging_music_skipping_and_watched_split(self):
        watched, library_keys, skipped, library = FakePlex("http://x", "t").load()
        self.assertEqual(library_keys, {("movie", n) for n in range(5)})
        self.assertEqual(sorted(w["tmdb_id"] for w in watched), [1, 3])
        self.assertEqual(skipped, 1)

    def test_load_is_a_4_tuple_and_library_includes_titles_without_a_tmdb_id(self):
        result = FakePlex("http://x", "t").load()
        self.assertEqual(len(result), 4)
        library = result[3]
        self.assertEqual(len(library), 6)
        self.assertEqual([i["tmdb_id"] for i in library], [0, 1, 2, 3, 4, None])
        self.assertEqual(library[-1]["title"], "no id")
        self.assertEqual(library[0]["rating_key"], 100)
        self.assertEqual(library[0]["thumb"], "/library/metadata/100/thumb/1")


class TestPlexLibraryItems(unittest.TestCase):
    def test_parse_library_item_without_tmdb_id(self):
        item = plex.parse_library_item({"title": "Old", "ratingKey": "7", "addedAt": 1750000000}, "movie")
        self.assertIsNone(item["tmdb_id"])
        self.assertEqual(item["rating_key"], 7)
        self.assertTrue(item["added_at"].startswith("2025-"))

    def test_bad_rating_key_and_missing_thumb(self):
        item = plex.parse_library_item({"title": "X", "ratingKey": "abc"}, "movie")
        self.assertEqual((item["rating_key"], item["thumb"], item["added_at"]), (None, None, None))

    def test_only_library_thumbs_are_kept(self):
        for bad in ("http://evil.example/x.jpg", "//evil/x", "/photo/:/transcode?url=x", "", 5):
            self.assertIsNone(plex.parse_library_item({"title": "X", "thumb": bad}, "movie")["thumb"], bad)
        self.assertEqual(plex.parse_library_item({"title": "X", "thumb": "/library/metadata/1/thumb/2"}, "movie")["thumb"],
                         "/library/metadata/1/thumb/2")

    def test_parse_item_gains_rating_key_and_thumb(self):
        item = plex.parse_item({"title": "A", "ratingKey": "9", "thumb": "/library/metadata/9/thumb/1",
                                "Guid": [{"id": "tmdb://5"}]}, "movie")
        self.assertEqual((item["rating_key"], item["thumb"]), (9, "/library/metadata/9/thumb/1"))
        self.assertNotIn("added_at", item)


class TestPlexPoster(unittest.TestCase):
    def test_token_is_sent_as_a_header_not_in_the_url(self):
        calls = []

        def fake(url, headers=None, params=None, **kw):
            calls.append((url, headers, params))
            return "image/jpeg", b"JPEG"

        with mock.patch.object(plex, "get_bytes", fake):
            result = plex.PlexClient("http://plex:32400/", "SECRET").poster("/library/metadata/1/thumb/2")
        self.assertEqual(result, ("image/jpeg", b"JPEG"))
        url, headers, params = calls[0]
        self.assertEqual(url, "http://plex:32400/photo/:/transcode")
        self.assertEqual(headers, {"X-Plex-Token": "SECRET"})
        self.assertNotIn("SECRET", url + str(params))
        self.assertEqual(params["url"], "/library/metadata/1/thumb/2")
        self.assertEqual((params["width"], params["height"]), (342, 513))

    def test_non_image_raises(self):
        with mock.patch.object(plex, "get_bytes", return_value=("text/html", b"<html>")):
            with self.assertRaises(RuntimeError):
                plex.PlexClient("http://x", "t").poster("/library/x")


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

    def test_normalize_new_image_fields(self):
        d = tmdb.normalize({**RAW_MOVIE, "backdrop_path": "/b.jpg"}, "movie")
        self.assertEqual(d["backdrop_url"], "https://image.tmdb.org/t/p/w1280/b.jpg")
        self.assertEqual(d["poster_large_url"], "https://image.tmdb.org/t/p/w780/p.jpg")
        self.assertEqual(d["poster_url"], "https://image.tmdb.org/t/p/w342/p.jpg")
        d = tmdb.normalize({"id": 1}, "movie")
        for key in ("backdrop_url", "poster_large_url", "runtime", "seasons", "certification"):
            self.assertIsNone(d[key], key)

    def test_normalize_runtime_and_seasons(self):
        self.assertEqual(tmdb.normalize({"id": 1, "runtime": 166}, "movie")["runtime"], 166)
        self.assertIsNone(tmdb.normalize({"id": 1, "runtime": 0}, "movie")["runtime"])
        self.assertIsNone(tmdb.normalize({"id": 1, "runtime": 50, "number_of_seasons": 3}, "tv")["runtime"])
        tv = tmdb.normalize({"id": 1, "number_of_seasons": 3, "runtime": 50}, "tv")
        self.assertEqual(tv["seasons"], 3)
        self.assertIsNone(tmdb.normalize({"id": 1, "number_of_seasons": 0}, "tv")["seasons"])
        self.assertIsNone(tmdb.normalize({"id": 1, "number_of_seasons": 3}, "movie")["seasons"])

    def test_movie_certification_prefers_gb_then_us(self):
        def rd(country, *certs):
            return {"iso_3166_1": country, "release_dates": [{"certification": c} for c in certs]}
        both = {"release_dates": {"results": [rd("US", "R"), rd("GB", "15")]}}
        self.assertEqual(tmdb.normalize({"id": 1, **both}, "movie")["certification"], "15")
        us_only = {"release_dates": {"results": [rd("FR", "12"), rd("US", "PG-13")]}}
        self.assertEqual(tmdb.normalize({"id": 1, **us_only}, "movie")["certification"], "PG-13")
        skip_empty = {"release_dates": {"results": [rd("GB", "", ""), rd("US", "", "PG")]}}
        self.assertEqual(tmdb.normalize({"id": 1, **skip_empty}, "movie")["certification"], "PG")
        in_gb = {"release_dates": {"results": [rd("GB", "", " 12A ")]}}
        self.assertEqual(tmdb.normalize({"id": 1, **in_gb}, "movie")["certification"], "12A")
        none = {"release_dates": {"results": [rd("FR", "12")]}}
        self.assertIsNone(tmdb.normalize({"id": 1, **none}, "movie")["certification"])
        self.assertIsNone(tmdb.normalize({"id": 1, "release_dates": None}, "movie")["certification"])

    def test_tv_certification_prefers_gb_then_us(self):
        both = {"content_ratings": {"results": [{"iso_3166_1": "US", "rating": "TV-MA"},
                                                {"iso_3166_1": "GB", "rating": "18"}]}}
        self.assertEqual(tmdb.normalize({"id": 1, **both}, "tv")["certification"], "18")
        us = {"content_ratings": {"results": [{"iso_3166_1": "GB", "rating": ""},
                                              {"iso_3166_1": "US", "rating": "TV-14"}]}}
        self.assertEqual(tmdb.normalize({"id": 1, **us}, "tv")["certification"], "TV-14")
        self.assertIsNone(tmdb.normalize({"id": 1, "content_ratings": {"results": []}}, "tv")["certification"])
        # a movie-shaped field on a show (or vice versa) is ignored
        self.assertIsNone(tmdb.normalize({"id": 1, "release_dates": {"results": [
            {"iso_3166_1": "GB", "release_dates": [{"certification": "15"}]}]}}, "tv")["certification"])

    def test_details_append_to_response_per_type(self):
        client = tmdb.TmdbClient("k" * 32)
        with mock.patch.object(tmdb, "get_json", return_value=RAW_MOVIE) as fake:
            client.details("movie", 10)
        self.assertEqual(fake.call_args.kwargs["params"]["append_to_response"],
                         "keywords,credits,recommendations,release_dates")
        with mock.patch.object(tmdb, "get_json", return_value=RAW_TV) as fake:
            client.details("tv", 30)
        self.assertEqual(fake.call_args.kwargs["params"]["append_to_response"],
                         "keywords,credits,recommendations,content_ratings")

    def test_details_refresh_skips_cache_read_but_writes_it(self):
        client = tmdb.TmdbClient("k" * 32)
        with mock.patch.object(tmdb, "get_json", return_value=RAW_MOVIE) as fake:
            client.details("movie", 10)
            client.details("movie", 10, refresh=True)
            self.assertEqual(fake.call_count, 2)
            newer = {**RAW_MOVIE, "backdrop_path": "/new.jpg"}
            fake.return_value = newer
            client.details("movie", 10, refresh=True)
            self.assertEqual(fake.call_count, 3)
            self.assertIn("new.jpg", client.details("movie", 10)["backdrop_url"])  # served from the updated cache
            self.assertEqual(fake.call_count, 3)

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


class TestSearch(unittest.TestCase):
    """search() is what stands between an AI-suggested title (a name, never a trustworthy id) and
    treating it as a real candidate - it has to pick the right movie, not just the first result."""

    def setUp(self):
        self._old = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old)
        self.client = tmdb.TmdbClient("k" * 32)

    def test_picks_the_result_matching_the_given_year(self):
        results = {"results": [{"id": 1, "release_date": "2001-01-01"}, {"id": 2, "release_date": "2016-11-10"}]}
        with mock.patch.object(tmdb, "get_json", return_value=results):
            self.assertEqual(self.client.search("movie", "Arrival", 2016), 2)

    def test_accepts_a_result_one_year_off(self):
        results = {"results": [{"id": 1, "release_date": "2017-01-01"}]}
        with mock.patch.object(tmdb, "get_json", return_value=results):
            self.assertEqual(self.client.search("movie", "Arrival", 2016), 1)

    def test_refuses_a_result_more_than_a_year_off_rather_than_guessing(self):
        results = {"results": [{"id": 1, "release_date": "2001-01-01"}]}
        with mock.patch.object(tmdb, "get_json", return_value=results):
            self.assertIsNone(self.client.search("movie", "Arrival", 2016))

    def test_picks_the_most_voted_among_several_close_years(self):
        results = {"results": [{"id": 1, "release_date": "2016-01-01", "vote_count": 10},
                               {"id": 2, "release_date": "2017-01-01", "vote_count": 500}]}
        with mock.patch.object(tmdb, "get_json", return_value=results):
            self.assertEqual(self.client.search("movie", "Arrival", 2016), 2)

    def test_uses_first_air_date_for_tv(self):
        results = {"results": [{"id": 9, "first_air_date": "2022-02-18"}]}
        with mock.patch.object(tmdb, "get_json", return_value=results):
            self.assertEqual(self.client.search("tv", "Severance", 2022), 9)

    def test_no_year_given_takes_the_first_result(self):
        results = {"results": [{"id": 3}, {"id": 4}]}
        with mock.patch.object(tmdb, "get_json", return_value=results):
            self.assertEqual(self.client.search("movie", "Arrival"), 3)

    def test_no_results_at_all_returns_none(self):
        with mock.patch.object(tmdb, "get_json", return_value={"results": []}):
            self.assertIsNone(self.client.search("movie", "Not A Real Movie", 2016))

    def test_results_are_cached_including_a_no_match(self):
        with mock.patch.object(tmdb, "get_json", return_value={"results": []}) as get:
            self.client.search("movie", "Not A Real Movie", 2016)
            result = self.client.search("movie", "Not A Real Movie", 2016)
        self.assertIsNone(result)
        self.assertEqual(get.call_count, 1)  # the "no match" itself was cached, not just misses retried

    def test_a_match_is_also_cached(self):
        results = {"results": [{"id": 2, "release_date": "2016-11-10"}]}
        with mock.patch.object(tmdb, "get_json", return_value=results) as get:
            first = self.client.search("movie", "Arrival", 2016)
            second = self.client.search("movie", "Arrival", 2016)
        self.assertEqual((first, second), (2, 2))
        self.assertEqual(get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
