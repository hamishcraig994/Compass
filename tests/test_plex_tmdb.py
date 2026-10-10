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


class TestTmdbSearchTitles(unittest.TestCase):
    def setUp(self):
        self._old = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old)
        self.client = tmdb.TmdbClient("k" * 32)

    @staticmethod
    def movie(i, **extra):
        return {"id": i, "title": f"M{i}", "release_date": "2020-03-04", "poster_path": "/p.jpg", "overview": "o",
                "vote_average": 7.5, "vote_count": 10, **extra}

    def test_all_uses_multi_and_drops_people(self):
        raw = {"results": [self.movie(1, media_type="movie"), {"id": 2, "name": "Person", "media_type": "person"},
                           {"id": 3, "name": "S", "first_air_date": "2019-01-01", "media_type": "tv"}], "total_pages": 1}
        with mock.patch.object(tmdb, "get_json", return_value=raw) as get:
            found = self.client.search_titles("dune")
        self.assertTrue(get.call_args.args[0].endswith("/search/multi"))
        self.assertEqual([(r["media_type"], r["tmdb_id"]) for r in found["results"]], [("movie", 1), ("tv", 3)])
        self.assertFalse(found["capped"])

    def test_movie_and_tv_use_their_own_path_and_ignore_media_type_field(self):
        for kind in ("movie", "tv"):
            with mock.patch.object(tmdb, "get_json", return_value={"results": [{"id": 4, "name": "X", "title": "X"}]}) as get:
                found = self.client.search_titles("x y", kind)
            self.assertTrue(get.call_args.args[0].endswith(f"/search/{kind}"))
            self.assertEqual(found["results"][0]["media_type"], kind)

    def test_params_timeout_and_retries(self):
        with mock.patch.object(tmdb, "get_json", return_value={"results": []}) as get:
            self.client.search_titles("dune")
        params = get.call_args.kwargs["params"]
        self.assertEqual((params["query"], params["include_adult"], params["page"]), ("dune", "false", 1))
        self.assertEqual((get.call_args.kwargs["timeout"], get.call_args.kwargs["retries"]), (8, 1))

    def test_capped_at_twenty_and_flag_follows_total_pages(self):
        raw = {"results": [self.movie(i) for i in range(1, 26)], "total_pages": 3}
        with mock.patch.object(tmdb, "get_json", return_value=raw):
            found = self.client.search_titles("a b", "movie")
        self.assertEqual(len(found["results"]), 20)
        self.assertEqual(found["results"][0]["tmdb_id"], 1)  # TMDB's relevance order kept
        self.assertTrue(found["capped"])

    def test_cache_written_under_prefix_and_cached_search_never_requests(self):
        with mock.patch.object(tmdb, "get_json") as get:
            self.assertIsNone(self.client.cached_search("Dune  Part", "all"))
        get.assert_not_called()
        with mock.patch.object(tmdb, "get_json", return_value={"results": [self.movie(1)]}):
            found = self.client.search_titles("Dune  Part", "movie")
        self.assertEqual(db.cache_get("tmdbsearch:v1:movie:dune part", 60), found)
        with mock.patch.object(tmdb, "get_json") as get:
            self.assertEqual(self.client.cached_search("dune PART", "movie"), found)
            self.assertIsNone(self.client.cached_search("dune part", "tv"))  # per kind
        get.assert_not_called()

    def test_second_search_titles_call_writes_the_cache_again(self):
        with mock.patch.object(tmdb, "get_json", return_value={"results": [self.movie(1, media_type="movie")]}):
            self.client.search_titles("dune")
        with mock.patch.object(tmdb, "get_json", return_value={"results": [self.movie(2, media_type="movie")]}):
            self.client.search_titles("dune")
        self.assertEqual(self.client.cached_search("dune")["results"][0]["tmdb_id"], 2)

    def test_errors_propagate(self):
        with mock.patch.object(tmdb, "get_json", side_effect=RuntimeError("HTTP 500")):
            with self.assertRaises(RuntimeError):
                self.client.search_titles("dune")
        self.assertIsNone(self.client.cached_search("dune"))

    def test_get_passes_timeout_and_retries_through(self):
        with mock.patch.object(tmdb, "get_json", return_value={}) as get:
            self.client._get("/x")
            self.client._get("/x", timeout=3, retries=0)
        self.assertEqual((get.call_args_list[0].kwargs["timeout"], get.call_args_list[0].kwargs["retries"]), (20, 2))
        self.assertEqual((get.call_args_list[1].kwargs["timeout"], get.call_args_list[1].kwargs["retries"]), (3, 0))

    def test_existing_search_for_the_ai_resolver_is_unchanged(self):
        with mock.patch.object(tmdb, "get_json", return_value={"results": [{"id": 9, "release_date": "2020-01-01"}]}):
            self.assertEqual(self.client.search("movie", "Dune", 2021), 9)
        self.assertIsNotNone(db.cache_get("search:movie:dune:2021", 60))


class TestNormalizeSearch(unittest.TestCase):
    def test_movie(self):
        item = tmdb.normalize_search({"id": 1, "title": "Dune", "release_date": "2021-10-22", "poster_path": "/p.jpg",
                                      "overview": "o", "vote_average": 7.8, "vote_count": 5}, "movie")
        self.assertEqual(item, {"media_type": "movie", "tmdb_id": 1, "title": "Dune", "year": 2021,
                                "release_date": "2021-10-22", "overview": "o",
                                "poster_url": "https://image.tmdb.org/t/p/w342/p.jpg",
                                "url": "https://www.themoviedb.org/movie/1", "vote_average": 7.8, "vote_count": 5})

    def test_tv_uses_name_and_first_air_date(self):
        item = tmdb.normalize_search({"id": 2, "name": "Dark", "first_air_date": "2017-12-01"}, "tv")
        self.assertEqual((item["title"], item["year"], item["release_date"]), ("Dark", 2017, "2017-12-01"))

    def test_missing_poster_date_and_title(self):
        item = tmdb.normalize_search({"id": 3, "release_date": ""}, "movie")
        self.assertEqual((item["title"], item["year"], item["release_date"], item["poster_url"], item["overview"],
                          item["vote_count"]), ("?", None, None, None, "", 0))


class TestSearchMalformedRows(unittest.TestCase):
    def setUp(self):
        self._old = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old)
        self.client = tmdb.TmdbClient("k" * 32)

    BAD = [
        {"id": 2, "media_type": "movie", "title": 5, "overview": 5, "release_date": "²０２¹4-01-01",
         "poster_path": 7, "vote_average": "high", "vote_count": [1]},
        {"media_type": "movie", "title": "No id"},
        {"id": "3", "media_type": "movie", "title": "String id"},
        {"id": True, "media_type": "movie", "title": "Bool id"},
        {"id": 4, "media_type": ["movie"], "title": "Bad type"},
        "not a dict", None, 12,
        {"id": 5, "media_type": "movie", "title": "Poster", "poster_path": "/a b\n.jpg", "release_date": {"x": 1}},
        {"id": 6, "media_type": "movie", "title": {"a": 1}, "overview": None, "release_date": "٢٠٢١-01-01"},
    ]

    def test_bad_rows_degrade_or_are_skipped_and_good_rows_survive(self):
        good = {"id": 1, "media_type": "movie", "title": "Good", "release_date": "2020-01-02", "overview": "ok"}
        with mock.patch.object(tmdb, "get_json", return_value={"results": self.BAD + [good]}):
            found = self.client.search_titles("x y")
        by_id = {r["tmdb_id"]: r for r in found["results"]}
        self.assertEqual(set(by_id), {1, 2, 5, 6})
        self.assertEqual(by_id[1]["title"], "Good")
        two = by_id[2]
        self.assertEqual((two["title"], two["overview"], two["year"], two["release_date"], two["poster_url"],
                          two["vote_average"], two["vote_count"]), ("?", "", None, None, None, 0, 0))
        self.assertIsNone(by_id[5]["poster_url"])
        self.assertEqual((by_id[6]["title"], by_id[6]["year"]), ("?", None))
        for r in found["results"]:
            for key in ("title", "overview", "url"):
                self.assertIsInstance(r[key], str)
            self.assertIsInstance(r["tmdb_id"], int)
        import json
        json.dumps(found)
        self.assertEqual(self.client.cached_search("x y"), found)

    def test_malformed_results_container(self):
        for raw in ({"results": None}, {"results": "x"}, {}):
            with mock.patch.object(tmdb, "get_json", return_value=raw):
                try:
                    found = self.client.search_titles("x y", "movie")
                except TypeError:
                    self.fail("results container of wrong type should degrade")
            self.assertEqual(found["results"], [])

    def test_ascii_year_parse(self):
        self.assertEqual(tmdb._year("2020-01-01"), 2020)
        self.assertIsNone(tmdb._year("²²²²"))
        self.assertIsNone(tmdb._year(None))
        self.assertIsNone(tmdb._year("202"))

    def test_details_accepts_timeout_and_retries(self):
        with mock.patch.object(tmdb, "get_json", return_value={"id": 1, "title": "T"}) as get:
            self.client.details("movie", 1, timeout=8, retries=1)
        self.assertEqual((get.call_args.kwargs["timeout"], get.call_args.kwargs["retries"]), (8, 1))


def raw_title(media_type="movie", **extra):
    base = {"id": 42, "overview": "o", "genres": [{"name": "Drama"}], "vote_average": 7, "vote_count": 100,
            "poster_path": "/p.jpg"}
    if media_type == "movie":
        base.update(title="Dune", release_date="2021-09-15")
    else:
        base.update(name="Severance", first_air_date="2022-02-18")
    return {**base, **extra}


class TestNormalizeTitle(unittest.TestCase):
    def video(self, key, kind="Trailer", official=True, site="YouTube", name="v"):
        return {"key": key, "type": kind, "official": official, "site": site, "name": name}

    def trailer(self, videos):
        return tmdb.normalize_title(raw_title(videos={"results": videos}), "movie")["trailer"]

    def test_trailer_preference_order(self):
        teaser = self.video("teaser1", "Teaser", name="teaser")
        unofficial = self.video("unoff12", official=False, name="unofficial")
        official = self.video("offic12", name="official")
        self.assertEqual(self.trailer([teaser, unofficial, official])["name"], "official")
        self.assertEqual(self.trailer([teaser, unofficial])["name"], "unofficial")
        self.assertEqual(self.trailer([teaser])["name"], "teaser")
        self.assertEqual(self.trailer([self.video("aaaaaa1", name="first"), self.video("bbbbbb1", name="second")])["name"],
                         "first")
        self.assertEqual(self.trailer([official])["url"], "https://www.youtube.com/watch?v=offic12")

    def test_unusable_videos_give_none(self):
        self.assertIsNone(self.trailer([]))
        self.assertIsNone(self.trailer([self.video("abcdef1", site="Vimeo")]))
        self.assertIsNone(self.trailer([self.video("javascript:alert(1)")]))
        self.assertIsNone(self.trailer([self.video("a b c d e f")]))
        self.assertIsNone(self.trailer([self.video("ab")]))
        self.assertIsNone(self.trailer([self.video(None), "junk", self.video("abcdef1", "Clip")]))
        self.assertIsNone(tmdb.normalize_title(raw_title(videos="x"), "movie")["trailer"])

    def test_cast_is_capped_with_characters_and_valid_profile_urls_only(self):
        cast = [{"name": f"A{n}", "character": f"C{n}", "profile_path": "/f.jpg"} for n in range(20)]
        cast[1]["profile_path"] = "javascript:alert(1)"
        cast[2]["profile_path"] = "no-slash.jpg"
        cast[3]["profile_path"] = None
        got = tmdb.normalize_title(raw_title(credits={"cast": cast + [{"name": ""}, "junk"]}), "movie")["cast"]
        self.assertEqual(len(got), tmdb.CAST_MAX)
        self.assertEqual(got[0], {"name": "A0", "character": "C0", "profile_url": "https://image.tmdb.org/t/p/w185/f.jpg"})
        self.assertEqual([c["profile_url"] for c in got[1:4]], [None, None, None])

    def test_movie_crew_priority_and_unique_names(self):
        crew = [{"name": "W", "job": "Writer"}, {"name": "S", "job": "Screenplay"}, {"name": "D", "job": "Director"},
                {"name": "S", "job": "Writer"}, {"name": "N", "job": "Novel"}, {"name": "X", "job": "Gaffer"},
                {"name": "Q", "job": "Story"}]
        got = tmdb.normalize_title(raw_title(credits={"crew": crew}), "movie")["crew"]
        self.assertEqual([(c["name"], c["job"]) for c in got],
                         [("D", "Director"), ("S", "Screenplay"), ("W", "Writer"), ("Q", "Story"), ("N", "Novel")])

    def test_crew_is_capped_at_six(self):
        crew = [{"name": f"D{n}", "job": "Director"} for n in range(9)]
        self.assertEqual(len(tmdb.normalize_title(raw_title(credits={"crew": crew}), "movie")["crew"]), 6)

    def test_tv_creators(self):
        raw = raw_title("tv", created_by=[{"name": "Dan"}, {"name": "Dan"}, {"name": "Ben"}, {"name": 5}],
                        credits={"crew": [{"name": "Z", "job": "Director"}]})
        self.assertEqual(tmdb.normalize_title(raw, "tv")["crew"],
                         [{"name": "Dan", "job": "Creator"}, {"name": "Ben", "job": "Creator"}])

    def test_seasons_sorted_specials_kept_missing_episode_count_zero(self):
        raw = raw_title("tv", seasons=[
            {"season_number": 2, "name": "Two", "episode_count": 10, "air_date": "2025-01-17", "poster_path": "/s2.jpg"},
            {"season_number": 0, "name": "Specials", "episode_count": 3},
            {"season_number": 1, "name": "", "air_date": None},
            {"season_number": "x"}, {"season_number": -1}, "junk"])
        got = tmdb.normalize_title(raw, "tv")["seasons"]
        self.assertEqual([s["number"] for s in got], [0, 1, 2])
        self.assertEqual(got[1], {"number": 1, "name": "Season 1", "episodes": 0, "air_date": None, "poster_url": None})
        self.assertEqual(got[2], {"number": 2, "name": "Two", "episodes": 10, "air_date": "2025-01-17",
                                  "poster_url": "https://image.tmdb.org/t/p/w342/s2.jpg"})
        self.assertEqual(tmdb.normalize_title(raw_title(seasons=[{"season_number": 1}]), "movie")["seasons"], [])

    def test_similar_fills_up_from_similar_deduped_capped_and_skips_malformed(self):
        def rows(start, n, bad=()):
            return [{"id": i, "title": f"M{i}", "release_date": "2020-01-01"} for i in range(start, start + n)] + list(bad)
        raw = raw_title(recommendations={"results": rows(100, 15, [{"title": "no id"}, {"id": -3}, "junk", {"id": 42}])},
                        similar={"results": rows(110, 15)})   # 110-114 repeat recommendations; 42 is the title itself
        got = tmdb.normalize_title(raw, "movie")["similar"]
        ids = [s["tmdb_id"] for s in got]
        self.assertEqual(len(ids), tmdb.SIMILAR_MAX)
        self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(ids[:15], list(range(100, 115)))
        self.assertEqual(ids[15:], list(range(115, 120)))
        self.assertNotIn(42, ids)
        self.assertEqual(got[0]["media_type"], "movie")

    def test_bad_imdb_id_gives_none_and_tvdb_comes_through(self):
        for bad in ("tt", "nm123", "tt12345678901", "tt1x", 5, None, "<script>"):
            self.assertIsNone(tmdb.normalize_title(raw_title(external_ids={"imdb_id": bad}), "movie")["imdb_id"])
        good = tmdb.normalize_title(raw_title("tv", external_ids={"imdb_id": "tt0123456", "tvdb_id": 99}), "tv")
        self.assertEqual((good["imdb_id"], good["tvdb_id"]), ("tt0123456", 99))
        self.assertIsNone(tmdb.normalize_title(raw_title(external_ids={"tvdb_id": "99"}), "movie")["tvdb_id"])

    def test_hostile_string_types_give_defaults(self):
        extras = tmdb.normalize_title(raw_title(tagline={"x": 1}, status=["a"], networks=[{"name": 5}, {"name": "HBO"}],
                                                production_companies=[{"name": None}, {"name": "A24"}]), "movie")
        self.assertEqual((extras["tagline"], extras["status"]), ("", ""))
        self.assertEqual(extras["studios"], ["A24"])
        tv = tmdb.normalize_title(raw_title("tv", networks=[{"name": n} for n in "ABCDE"]), "tv")
        self.assertEqual(tv["networks"], ["A", "B", "C"])
        self.assertEqual(tmdb.normalize_title(raw_title(networks=[{"name": "HBO"}]), "movie")["networks"], [])

    def test_missing_blocks_are_fine(self):
        extras = tmdb.normalize_title({"id": 1}, "tv")
        self.assertEqual((extras["cast"], extras["crew"], extras["trailer"], extras["seasons"], extras["similar"]),
                         ([], [], None, [], []))


class TestTmdbTitle(unittest.TestCase):
    def setUp(self):
        self._old = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old)
        self.client = tmdb.TmdbClient("k" * 32)

    def test_one_request_with_the_append_list_and_three_cache_writes(self):
        raw = raw_title("tv", external_ids={"tvdb_id": 77, "imdb_id": "tt1"}, seasons=[{"season_number": 1}])
        with mock.patch.object(tmdb, "get_json", return_value=raw) as get:
            details, extras = self.client.title("tv", 42)
        get.assert_called_once()
        self.assertTrue(get.call_args.args[0].endswith("/tv/42"))
        self.assertEqual(get.call_args.kwargs["params"]["append_to_response"],
                         "keywords,credits,recommendations,similar,videos,external_ids,content_ratings")
        self.assertEqual((get.call_args.kwargs["timeout"], get.call_args.kwargs["retries"]), (8, 1))
        self.assertEqual(details["title"], "Severance")
        self.assertEqual(extras["tvdb_id"], 77)
        with mock.patch.object(tmdb, "get_json") as none:
            self.assertEqual(self.client.cached_details("tv", 42), details)
            self.assertEqual(self.client.cached_title("tv", 42), extras)
            self.assertEqual(self.client.external_ids("tv", 42), {"tvdb_id": 77})
            self.assertEqual(self.client.details("tv", 42), details)
        none.assert_not_called()

    def test_movie_uses_release_dates(self):
        with mock.patch.object(tmdb, "get_json", return_value=raw_title()) as get:
            self.client.title("movie", 42)
        self.assertTrue(get.call_args.kwargs["params"]["append_to_response"].endswith(",release_dates"))

    def test_without_external_ids_the_external_ids_cache_is_left_alone(self):
        with mock.patch.object(tmdb, "get_json", return_value=raw_title()):
            self.client.title("movie", 42)
        self.assertIsNone(db.cache_get("external_ids:movie:42", 1e9))

    def test_cached_title_never_requests_and_expires(self):
        with mock.patch.object(tmdb, "get_json") as get:
            self.assertIsNone(self.client.cached_title("movie", 1))
        get.assert_not_called()
        db.cache_put("title:v1:movie:1", {"x": 1})
        self.assertEqual(self.client.cached_title("movie", 1), {"x": 1})
        with mock.patch("time.time", return_value=__import__("time").time() + tmdb.TITLE_MAX_AGE + 5):
            self.assertIsNone(self.client.cached_title("movie", 1))

    def test_errors_propagate_and_write_nothing(self):
        with mock.patch.object(tmdb, "get_json", side_effect=RuntimeError("HTTP 404: nope")):
            with self.assertRaises(RuntimeError):
                self.client.title("movie", 7)
        self.assertIsNone(self.client.cached_title("movie", 7))
        self.assertIsNone(self.client.cached_details("movie", 7))
