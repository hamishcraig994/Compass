"""web.py helpers behind the title page, season picker, Requests tab and Lists (views, not routes):
parse_seasons, title_stub, title_data, title_view, season_choices, note_arr_item, with_user_state,
requests_items, lists_view, list_page_view. TMDB is always mocked (TmdbClient methods / web.lookup_item)."""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["SAMPLE"] = "1"
import config
import db
import sample
import sources
import tmdb
import web


def rec_item(tmdb_id, media_type="movie", match=50, **extra):
    return {"media_type": media_type, "tmdb_id": tmdb_id, "title": f"Rec{tmdb_id}", "year": 2020, "match": match,
            "reason": "Because you watched A and B", "matches": ["Genre: Drama"], "because": ["A", "B", "C", "D", "E", "F"],
            "new": False, "trending": True, "overview": "o", "poster_url": f"https://img/{tmdb_id}.jpg",
            "url": f"https://www.themoviedb.org/{media_type}/{tmdb_id}", "genres": ["Drama"], "keywords": [],
            "directors": [], "cast": [], "vote_average": 7, "vote_count": 100, **extra}


def details(tmdb_id, media_type="movie", genres=("Drama",), **extra):
    return {"media_type": media_type, "tmdb_id": tmdb_id, "title": f"D{tmdb_id}", "year": 2019, "release_date": "2019-01-01",
            "overview": "o", "poster_url": f"https://img/d{tmdb_id}.jpg", "url": f"https://www.themoviedb.org/{media_type}/{tmdb_id}",
            "genres": list(genres), "keywords": [], "directors": [], "cast": [], "vote_average": 7, "vote_count": 100, **extra}


def search_item(tmdb_id, media_type="movie"):
    return {"media_type": media_type, "tmdb_id": tmdb_id, "title": f"S{tmdb_id}", "year": 2018, "release_date": None,
            "overview": "", "poster_url": None, "url": "u", "vote_average": 6, "vote_count": 10}


def extras(media_type="movie", similar=(), seasons=(), tvdb_id=None):
    return {"media_type": media_type, "tmdb_id": 1, "tagline": "", "status": "", "cast": [], "crew": [], "trailer": None,
            "seasons": list(seasons), "networks": [], "studios": [], "tvdb_id": tvdb_id, "imdb_id": None,
            "similar": list(similar)}


def tmdb_season(n, episodes=8, air="2020-01-01"):
    return {"number": n, "name": f"Season {n}", "episodes": episodes, "air_date": air, "poster_url": None}


def arr_tv(tmdb_id, seasons, state="partial", **extra):
    return {"media_type": "tv", "service": "sonarr", "tmdb_id": tmdb_id, "tvdb_id": 700 + tmdb_id, "title": f"Show{tmdb_id}",
            "year": 2020, "added_at": "2025-01-01T00:00:00Z", "monitored": True, "arr_state": state,
            "episodes": {"have": 1, "total": 10}, "poster_url": None, "url": None, "arr_id": 5, "seasons": seasons, **extra}


def aseason(n, monitored=True, have=0, total=8):
    return {"number": n, "monitored": monitored, "have": have, "total": total}


def build_result(items=(), arr=(), library=(), watched=(), profile=None):
    return {"items": list(items), "sample": False, "notes": [], "watched_count": len(watched),
            "profile": profile if profile is not None else {c: {} for c in ("genre", "keyword", "director", "actor")},
            "library": list(library), "watched": list(watched),
            "arr": {"items": list(arr), "radarr": {"state": "ok", "count": 0}, "sonarr": {"state": "ok", "count": len(arr)}}}


class WebCase(unittest.TestCase):
    """Temp DB, restored in-memory state, live mode on request."""

    def setUp(self):
        self._old_db = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old_db)
        saved = (dict(web._state), dict(web._ai_state), list(web._budget_uses))
        self.addCleanup(self._restore, saved)
        web._state.update(time=0.0, result=None)
        web._ai_state.update(time=0.0, result=None, building=False, error=None)
        web._budget_uses[:] = []
        self._old_cfg = {k: getattr(config, k) for k in ("TMDB_TOKEN", "RADARR_URL", "RADARR_API_KEY", "SONARR_URL",
                                                         "SONARR_API_KEY")}
        self.addCleanup(lambda: [setattr(config, k, v) for k, v in self._old_cfg.items()])

    @staticmethod
    def _restore(saved):
        web._state.clear(), web._state.update(saved[0])
        web._ai_state.clear(), web._ai_state.update(saved[1])
        web._budget_uses[:] = saved[2]

    def live(self, token="t" * 32):
        patch = mock.patch.object(sources, "use_sample", return_value=False)
        patch.start()
        self.addCleanup(patch.stop)
        config.TMDB_TOKEN = token
        # a fresh, un-expired result means the page never starts a (real) background build
        web._state["time"] = time.time()

    def set_result(self, result, age=0):
        web._state.update(result=result, time=time.time() - age)

    def no_build(self):
        patch = mock.patch.object(web, "_start_build", return_value=False)
        patch.start()
        self.addCleanup(patch.stop)


class TestParseSeasons(unittest.TestCase):
    def parse(self, **fields):
        return web.parse_seasons({k: v if isinstance(v, list) else [v] for k, v in fields.items()})

    def test_absent_is_none_and_season_values_alone_do_not_count(self):
        self.assertEqual(web.parse_seasons({}), (None, None))
        self.assertEqual(self.parse(season=["1", "2"]), (None, None))

    def test_all_ignores_season_values_even_bad_ones(self):
        self.assertEqual(self.parse(seasons="all", season=["x", "0"]), ("all", None))

    def test_pick_dedupes_and_sorts(self):
        self.assertEqual(self.parse(seasons="pick", season=["3", "1", "3", "2"]), ([1, 2, 3], None))
        self.assertEqual(self.parse(seasons="pick", season=["9999"]), ([9999], None))

    def test_bad_mode(self):
        for bad in ("bogus", "", "ALL", "pick ", "1"):
            self.assertEqual(self.parse(seasons=bad), (None, "That isn't a valid season choice"), bad)

    def test_pick_needs_a_season(self):
        self.assertEqual(self.parse(seasons="pick"), (None, "Pick at least one season"))

    def test_bad_season_values(self):
        for bad in ("0", "x", "-1", "10000", "", "1.5", "²", " 1", "１", "00001", "1e3"):
            self.assertEqual(self.parse(seasons="pick", season=["1", bad]), (None, "That isn't a valid season"), bad)

    def test_too_many_values(self):
        values = [str(n) for n in range(1, 202)]
        self.assertEqual(self.parse(seasons="pick", season=values), (None, "That isn't a valid season"))
        self.assertEqual(self.parse(seasons="pick", season=values[:200])[0], list(range(1, 201)))


class TestTitleStub(WebCase):
    def test_sources_in_order_and_no_request_without_fetch(self):
        self.live()
        client = mock.Mock()
        client.cached_details.return_value = None
        with mock.patch.object(tmdb, "TmdbClient", return_value=client), \
             mock.patch.object(web, "lookup_item", side_effect=AssertionError("no fetch")):
            self.assertIsNone(web.title_stub("movie", 1))
            # 5. cached details
            client.cached_details.return_value = details(1)
            self.assertEqual(web.title_stub("movie", 1)["title"], "D1")
            # 4. arr item beats cached details
            self.set_result(build_result(arr=[arr_tv(1, [])]))
            self.assertEqual(web.title_stub("tv", 1)["title"], "Show1")
            # 3. library, 2. watched, 1. recommendation - each beats the later ones
            lib = {"media_type": "movie", "tmdb_id": 1, "title": "Lib", "year": 1999, "url": "lu"}
            self.set_result(build_result(arr=[dict(arr_tv(1, []), media_type="movie", title="Arr")], library=[lib]))
            self.assertEqual(web.title_stub("movie", 1)["title"], "Lib")
            watched = dict(lib, title="Watched", poster_url="https://img/w.jpg")
            self.set_result(build_result(library=[lib], watched=[watched]))
            stub = web.title_stub("movie", 1)
            self.assertEqual((stub["title"], stub["poster_url"]), ("Watched", "https://img/w.jpg"))
            self.set_result(build_result(items=[rec_item(1)], library=[lib], watched=[watched]))
            self.assertEqual(web.title_stub("movie", 1)["title"], "Rec1")

    def test_fills_gaps_from_later_sources_and_always_has_a_url(self):
        self.live()
        self.set_result(build_result(library=[{"media_type": "movie", "tmdb_id": 1, "title": "Lib", "year": None}]))
        with mock.patch.object(tmdb.TmdbClient, "cached_details", return_value=details(1)):
            stub = web.title_stub("movie", 1)
        self.assertEqual((stub["title"], stub["year"], stub["poster_url"]), ("Lib", 2019, "https://img/d1.jpg"))
        self.assertEqual(set(stub), {"media_type", "tmdb_id", "title", "year", "poster_url", "url"})
        self.assertEqual(stub["url"], "https://www.themoviedb.org/movie/1")

    def test_fetch_uses_lookup_item_only_live_and_only_when_nothing_else_knows_it(self):
        self.live()
        with mock.patch.object(tmdb.TmdbClient, "cached_details", return_value=None), \
             mock.patch.object(web, "lookup_item", return_value=details(7)) as lookup:
            self.assertIsNone(web.title_stub("movie", 7, fetch=False))
            lookup.assert_not_called()
            self.assertEqual(web.title_stub("movie", 7, fetch=True)["title"], "D7")
            lookup.assert_called_once_with("movie", 7)
        with mock.patch.object(tmdb.TmdbClient, "cached_details", return_value=details(7)), \
             mock.patch.object(web, "lookup_item") as lookup:
            web.title_stub("movie", 7, fetch=True)
            lookup.assert_not_called()

    def test_lookup_failure_is_none(self):
        self.live()
        with mock.patch.object(tmdb.TmdbClient, "cached_details", return_value=None), \
             mock.patch.object(web, "lookup_item", return_value=None):
            self.assertIsNone(web.title_stub("movie", 7, fetch=True))

    def test_sample_mode_uses_the_catalogue_and_never_fetches(self):
        with mock.patch.object(web, "lookup_item", side_effect=AssertionError("no fetch")), \
             mock.patch.object(db, "cache_get", side_effect=AssertionError("no db")):
            self.assertEqual(web.title_stub("movie", 1005, fetch=True)["title"], "Dune")
            self.assertIsNone(web.title_stub("movie", 424242, fetch=True))


class TestTitleStubOfflineFallback(WebCase):
    def setUp(self):
        super().setUp()
        self.live()
        self.no_build()
        p = mock.patch.object(tmdb.TmdbClient, "cached_details", return_value=None)
        p.start()
        self.addCleanup(p.stop)
        self.set_result(build_result())

    def test_list_snapshot_names_the_title_without_a_request(self):
        lid = db.create_list("L")
        db.add_to_list(lid, {"media_type": "movie", "tmdb_id": 7, "title": "Listed", "year": 1999, "poster_url": "https://p/7"})
        with mock.patch.object(web, "lookup_item", side_effect=AssertionError("fetch")):
            stub = web.title_stub("movie", 7, fetch=True)
        self.assertEqual((stub["title"], stub["year"], stub["poster_url"]), ("Listed", 1999, "https://p/7"))
        self.assertIsNone(web.title_stub("tv", 7))

    def test_requests_snapshot_names_the_title(self):
        db.record_added({"media_type": "tv", "tmdb_id": 8, "title": "Requested", "year": 2020, "poster_url": "https://p/8", "url": "u"})
        stub = web.title_stub("tv", 8)
        self.assertEqual((stub["title"], stub["poster_url"]), ("Requested", "https://p/8"))

    def test_existing_sources_win(self):
        lid = db.create_list("L")
        db.add_to_list(lid, {"media_type": "movie", "tmdb_id": 7, "title": "Listed", "year": None, "poster_url": None})
        self.set_result(build_result(library=[{"media_type": "movie", "tmdb_id": 7, "title": "Lib", "year": 2000}]))
        self.assertEqual(web.title_stub("movie", 7)["title"], "Lib")

    def test_title_view_is_partial_not_unavailable_when_tmdb_is_down(self):
        lid = db.create_list("L")
        db.add_to_list(lid, {"media_type": "movie", "tmdb_id": 7, "title": "Listed", "year": 1999, "poster_url": None})
        db.record_added({"media_type": "tv", "tmdb_id": 8, "title": "Requested", "year": 2020, "poster_url": None, "url": "u"})
        with mock.patch.object(web, "title_data", return_value={"details": None, "extras": None, "state": "error"}):
            for media_type, tmdb_id, title in (("movie", 7, "Listed"), ("tv", 8, "Requested")):
                v = web.title_view(media_type, tmdb_id)
                self.assertEqual((v["state"], v["message"], v["item"]["title"]), ("partial", web.TITLE_PARTIAL, title))
            self.assertEqual(web.title_view("movie", 99)["state"], "unavailable")


class TestTitleData(WebCase):
    def setUp(self):
        super().setUp()
        self.live()
        self.client_patches = {}

    def patch_client(self, cached_details=None, cached_title=None, title=None):
        for name, value in (("cached_details", cached_details), ("cached_title", cached_title)):
            p = mock.patch.object(tmdb.TmdbClient, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(tmdb.TmdbClient, "title", **({"side_effect": title} if isinstance(title, Exception)
                                                           else {"return_value": title}))
        self.title_mock = p.start()
        self.addCleanup(p.stop)

    def test_both_cached_is_ok_and_uses_no_budget(self):
        self.patch_client(details(1), extras())
        with mock.patch.object(web, "_tmdb_budget", side_effect=AssertionError("budget")):
            got = web.title_data("movie", 1)
        self.assertEqual(got["state"], "ok")
        self.title_mock.assert_not_called()

    def test_uncached_makes_one_budgeted_request_with_the_lookup_reserve(self):
        self.patch_client(title=(details(1), extras()))
        with mock.patch.object(web, "_tmdb_budget", return_value=True) as budget:
            got = web.title_data("movie", 1)
        budget.assert_called_once_with(reserve=web.LOOKUP_RESERVE)
        self.assertEqual((got["state"], got["details"]["title"]), ("ok", "D1"))
        self.title_mock.assert_called_once_with("movie", 1)

    def test_details_cached_but_extras_expired_fetches(self):
        self.patch_client(details(1), None, title=(details(1, year=2000), extras()))
        got = web.title_data("movie", 1)
        self.assertEqual((got["state"], got["details"]["year"]), ("ok", 2000))

    def test_budget_used_up_is_limited_and_keeps_what_is_cached(self):
        self.patch_client(details(1), None)
        with mock.patch.object(web, "_tmdb_budget", return_value=False):
            got = web.title_data("movie", 1)
        self.assertEqual((got["state"], got["details"]["title"], got["extras"]), ("limited", "D1", None))
        self.title_mock.assert_not_called()

    def test_the_lookup_reserve_is_really_held_back(self):
        self.patch_client(title=(details(1), extras()))
        web._budget_uses[:] = [time.monotonic()] * (web.TMDB_BUDGET_MAX - web.LOOKUP_RESERVE)
        self.assertEqual(web.title_data("movie", 1)["state"], "limited")

    def test_error_returns_the_state_without_exception_text(self):
        self.patch_client(title=RuntimeError("HTTP 500: https://api?api_key=SECRET"))
        with mock.patch("builtins.print") as printed:
            got = web.title_data("movie", 1)
        self.assertEqual((got["state"], got["details"], got["extras"]), ("error", None, None))
        self.assertNotIn("SECRET", repr(printed.call_args_list))
        self.assertIn("RuntimeError", repr(printed.call_args_list))

    def test_network_errors_are_errors(self):
        self.patch_client(title=TimeoutError("slow"))
        self.assertEqual(web.title_data("movie", 1)["state"], "error")

    def test_http_404_is_not_found(self):
        self.patch_client(title=RuntimeError("HTTP 404: not found"))
        self.assertEqual(web.title_data("movie", 1)["state"], "not_found")
        self.patch_client(title=RuntimeError("something HTTP 404"))
        self.assertEqual(web.title_data("movie", 1)["state"], "error")

    def test_no_token(self):
        config.TMDB_TOKEN = ""
        with mock.patch.object(tmdb.TmdbClient, "title", side_effect=AssertionError("no request")):
            got = web.title_data("movie", 1)
        self.assertEqual((got["state"], got["details"], got["extras"]), ("error", None, None))

    def test_sample_mode(self):
        patch = mock.patch.object(sources, "use_sample", return_value=True)
        patch.start()
        self.addCleanup(patch.stop)
        got = web.title_data("movie", 1005)
        self.assertEqual((got["state"], got["details"]["title"], got["extras"]["cast"][0]["name"]),
                         ("ok", "Dune", "Timothée Chalamet"))
        self.assertEqual(web.title_data("movie", 424242), {"details": None, "extras": None, "state": "not_found"})
        self.assertEqual(web.title_data("tv", 1005)["state"], "not_found")   # wrong type


class TestTitleView(WebCase):
    def setUp(self):
        super().setUp()
        self.live()
        self.no_build()
        self.data = {"details": details(1), "extras": extras(), "state": "ok"}
        p = mock.patch.object(web, "title_data", side_effect=lambda t, i: self.data)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(tmdb.TmdbClient, "cached_details", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def view(self, media_type="movie", tmdb_id=1):
        return web.title_view(media_type, tmdb_id)

    def test_a_recommendation_gets_the_why_section_and_no_fit(self):
        self.set_result(build_result([rec_item(1, match=88)], profile={"genre": {"Drama": 1.0}, "keyword": {}, "director": {}, "actor": {}}))
        v = self.view()
        self.assertEqual(v["state"], "ok")
        self.assertIsNone(v["message"])
        self.assertEqual(v["rec"], {"match": 88, "reason": "Because you watched A and B", "matches": ["Genre: Drama"],
                                    "because": ["A", "B", "C", "D", "E"], "new": False, "trending": True, "source": "main"})
        self.assertIsNone(v["fit"])
        self.assertEqual(v["item"]["title"], "Rec1")   # the recommendation wins over details

    def test_an_ai_recommendation_is_marked_as_ai(self):
        self.set_result(build_result())
        web._ai_state["result"] = build_result([rec_item(1)])
        self.assertEqual(self.view()["rec"]["source"], "ai")

    def test_a_non_recommendation_with_a_profile_gets_the_fit_section(self):
        self.set_result(build_result(profile={"genre": {"Drama": 1.0}, "keyword": {}, "director": {}, "actor": {}}))
        v = self.view()
        self.assertIsNone(v["rec"])
        self.assertEqual(v["fit"], {"matches": ["Genre: Drama"]})
        self.assertEqual(v["item"]["title"], "D1")

    def test_no_fit_without_matches_or_without_a_profile(self):
        self.set_result(build_result(profile={"genre": {"Comedy": 1.0}, "keyword": {}, "director": {}, "actor": {}}))
        self.assertIsNone(self.view()["fit"])
        self.set_result(build_result(profile={}))
        self.assertIsNone(self.view()["fit"])

    def test_status_arr_and_watched_come_from_the_snapshot(self):
        show_arr = arr_tv(1, [aseason(1, True, 3), aseason(2, False, 0)])
        self.data = {"details": details(1, "tv"), "extras": extras("tv", seasons=[tmdb_season(1), tmdb_season(2)]), "state": "ok"}
        self.set_result(build_result(arr=[show_arr], watched=[{"media_type": "tv", "tmdb_id": 1, "title": "Show1", "year": 2020}]))
        v = self.view("tv", 1)
        self.assertEqual(v["status"]["status"], "sonarr")
        self.assertTrue(v["status"]["in_library"] and v["status"]["watched"])
        self.assertEqual(v["arr"]["arr_id"], 5)
        self.assertEqual([(s["number"], s["state"], s["requested"]) for s in v["seasons"]],
                         [(1, "partial", True), (2, "unmonitored", False)])
        self.assertFalse(v["can_add"])

    def test_untracked_title_can_be_added_only_when_the_service_is_configured(self):
        self.set_result(build_result())
        config.RADARR_URL = ""
        self.assertFalse(self.view()["can_add"])
        config.RADARR_URL, config.RADARR_API_KEY = "http://r", "k"
        v = self.view()
        self.assertEqual(v["status"]["status"], "none")
        self.assertTrue(v["can_add"])
        self.assertEqual(v["seasons"], [])
        self.assertFalse(v["sample"])
        self.assertTrue(v["library_known"])

    def test_arr_found_by_tvdb_id_from_the_extras(self):
        self.data = {"details": details(1, "tv"), "extras": extras("tv", tvdb_id=777), "state": "ok"}
        self.set_result(build_result(arr=[dict(arr_tv(99, []), tmdb_id=None, tvdb_id=777)]))
        self.assertEqual(self.view("tv", 1)["arr"]["tvdb_id"], 777)

    def test_user_state(self):
        watch = db.ensure_watchlist()
        custom = db.create_list("Horror night")
        for lid in (custom, watch):
            db.add_to_list(lid, {"media_type": "movie", "tmdb_id": 1, "title": "X", "year": None, "poster_url": None})
        db.set_rating("movie", 1, 4)
        self.set_result(build_result())
        v = self.view()
        self.assertEqual((v["stars"], v["on_watchlist"], sorted(v["lists"])), (4, True, sorted([watch, custom])))
        self.assertEqual([n["name"] for n in v["list_names"]], ["Watchlist", "Horror night"])

    def test_dismissed_is_reported(self):
        db.dismiss("movie", 1)
        self.set_result(build_result())
        self.assertTrue(self.view()["status"]["dismissed"])

    def test_partial_keeps_the_recommendation_as_the_base_item(self):
        self.data = {"details": None, "extras": None, "state": "error"}
        self.set_result(build_result([rec_item(1)]))
        v = self.view()
        self.assertEqual((v["state"], v["message"], v["item"]["title"]), ("partial", web.TITLE_PARTIAL, "Rec1"))
        self.assertIsNone(v["extras"])
        self.assertIsNotNone(v["rec"])

    def test_partial_with_cached_details_only(self):
        self.data = {"details": details(1), "extras": None, "state": "limited"}
        self.set_result(build_result())
        v = self.view()
        self.assertEqual((v["state"], v["item"]["title"]), ("partial", "D1"))

    def test_partial_from_a_stub_alone(self):
        self.data = {"details": None, "extras": None, "state": "error"}
        self.set_result(build_result(library=[{"media_type": "movie", "tmdb_id": 1, "title": "Lib", "year": 1999}]))
        v = self.view()
        self.assertEqual((v["state"], v["item"]["title"]), ("partial", "Lib"))

    def test_unavailable_when_we_have_nothing(self):
        self.data = {"details": None, "extras": None, "state": "error"}
        self.set_result(build_result())
        v = self.view()
        self.assertEqual((v["state"], v["message"], v["item"], v["status"]), ("unavailable", web.TITLE_UNAVAILABLE, None, None))

    def test_not_found(self):
        self.data = {"details": None, "extras": None, "state": "not_found"}
        self.set_result(build_result([rec_item(1)]))
        v = self.view()
        self.assertEqual((v["state"], v["message"], v["item"]), ("not_found", web.TITLE_NOT_FOUND, None))

    def test_no_build_result_yet_still_renders(self):
        v = self.view()
        self.assertEqual(v["state"], "ok")
        self.assertFalse(v["library_known"])
        self.assertEqual(v["status"]["status"], "none")

    def test_title_view_never_waits_for_a_build(self):
        self.set_result(None)
        with web._status_lock:   # a held status lock would block; compute_lock must not be touched
            pass
        with web.compute_lock:
            started = time.time()
            self.view()
            self.assertLess(time.time() - started, 1)


class TestSimilarOrdering(WebCase):
    def setUp(self):
        super().setUp()
        self.live()
        self.no_build()
        self.cached = {}
        p = mock.patch.object(tmdb.TmdbClient, "cached_details", side_effect=lambda t, i: self.cached.get((t, i)))
        p.start()
        self.addCleanup(p.stop)
        self.profile = {"genre": {"Drama": 1.0, "Comedy": 0.2}, "keyword": {}, "director": {}, "actor": {}}

    def similar_ids(self, similar, **result_kw):
        data = {"details": details(1), "extras": extras(similar=similar), "state": "ok"}
        self.set_result(build_result(profile=self.profile, **result_kw))
        with mock.patch.object(web, "title_data", return_value=data):
            return web.title_view("movie", 1)["similar"]

    def test_recs_by_match_then_cached_by_content_score_then_tmdb_order(self):
        self.cached[("movie", 12)] = details(12, genres=["Comedy"])
        self.cached[("movie", 13)] = details(13, genres=["Drama"])
        similar = [search_item(n) for n in (10, 11, 12, 13, 14, 15)]
        got = self.similar_ids(similar, items=[rec_item(15, match=40), rec_item(11, match=90)])
        self.assertEqual([s["tmdb_id"] for s in got], [11, 15, 13, 12, 10, 14])
        self.assertEqual([s["match"] for s in got], [90, 40, None, None, None, None])

    def test_dismissed_titles_are_dropped(self):
        db.dismiss("movie", 11)
        got = self.similar_ids([search_item(10), search_item(11)])
        self.assertEqual([s["tmdb_id"] for s in got], [10])

    def test_owned_titles_stay_with_their_status(self):
        got = self.similar_ids([search_item(10)], library=[{"media_type": "movie", "tmdb_id": 10, "title": "S10", "year": 2018}])
        self.assertEqual((got[0]["status"], got[0]["in_library"]), ("plex", True))

    def test_each_has_user_state_and_no_requests_are_made(self):
        db.set_rating("movie", 10, 5)
        with mock.patch.object(tmdb.TmdbClient, "title", side_effect=AssertionError("request")), \
             mock.patch.object(tmdb.TmdbClient, "details", side_effect=AssertionError("request")):
            got = self.similar_ids([search_item(10), search_item(11)])
        self.assertEqual([(s["stars"], s["lists"], s["on_watchlist"]) for s in got], [(5, [], False), (None, [], False)])

    def test_without_a_profile_the_tmdb_order_is_kept(self):
        self.profile = None
        self.cached[("movie", 11)] = details(11)
        data = {"details": details(1), "extras": extras(similar=[search_item(10), search_item(11)]), "state": "ok"}
        self.set_result(build_result(profile={}))
        with mock.patch.object(web, "title_data", return_value=data):
            got = web.title_view("movie", 1)["similar"]
        self.assertEqual([s["tmdb_id"] for s in got], [10, 11])


class TestTitleViewSample(WebCase):
    def test_dune_and_the_sample_seasons(self):
        self.no_build()
        sample_result = sources.run(True)
        web._state.update(result=sample_result, time=time.time())
        v = web.title_view("movie", 1005)
        self.assertEqual((v["state"], v["sample"], v["can_add"]), ("ok", True, False))
        self.assertTrue(v["status"]["in_library"])
        self.assertEqual(v["status"]["sources"], ["plex", "radarr"])
        self.assertEqual(v["extras"]["cast"][0]["name"], "Timothée Chalamet")
        self.assertEqual((v["stars"], v["lists"], v["on_watchlist"]), (None, [], False))
        tv = web.title_view("tv", 2040)
        self.assertEqual([(s["number"], s["state"], s["have"], s["total"]) for s in tv["seasons"]],
                         [(1, "available", 5, 5), (2, "missing", 0, 5)])
        self.assertEqual(web.title_view("movie", 999999)["state"], "not_found")


class TestSeasonChoices(WebCase):
    def setUp(self):
        super().setUp()
        self.live()

    def choices(self, data, arr=()):
        self.set_result(build_result(arr=arr))
        with mock.patch.object(web, "title_data", return_value=data):
            return web.season_choices(5)

    def test_untracked_series_lists_the_tmdb_seasons_as_selectable(self):
        got = self.choices({"details": details(5, "tv"), "extras": extras("tv", seasons=[tmdb_season(0), tmdb_season(1), tmdb_season(2)]),
                            "state": "ok"})
        self.assertEqual((got["tracked"], got["known"]), (False, True))
        self.assertEqual([(s["number"], s["selectable"]) for s in got["seasons"]], [(1, True), (2, True)])

    def test_tracked_series_marks_requested_seasons(self):
        got = self.choices({"details": None, "extras": extras("tv", seasons=[tmdb_season(1), tmdb_season(2)]), "state": "ok"},
                           arr=[arr_tv(5, [aseason(1, True, 8), aseason(2, False, 0)])])
        self.assertTrue(got["tracked"])
        self.assertEqual([(s["number"], s["requested"], s["selectable"]) for s in got["seasons"]], [(1, True, False), (2, False, True)])

    def test_unknown_when_tmdb_failed_and_not_tracked(self):
        got = self.choices({"details": None, "extras": None, "state": "error"})
        self.assertEqual(got, {"seasons": [], "tracked": False, "known": False})

    def test_tracked_but_tmdb_failed_falls_back_to_the_arr_seasons(self):
        got = self.choices({"details": None, "extras": None, "state": "limited"}, arr=[arr_tv(5, [aseason(1, True, 3)])])
        self.assertEqual((got["tracked"], got["known"], [s["number"] for s in got["seasons"]]), (True, True, [1]))

    def test_sample(self):
        patch = mock.patch.object(sources, "use_sample", return_value=True)
        patch.start()
        self.addCleanup(patch.stop)
        got = web.season_choices(2040)
        self.assertEqual([s["number"] for s in got["seasons"]], [1, 2])
        self.assertFalse(web.season_choices(999999)["known"])


class TestNoteArrItem(WebCase):
    def test_inserts_then_replaces_by_service_and_id(self):
        self.set_result(build_result(arr=[arr_tv(5, [])]))
        new = arr_tv(6, [aseason(1)])
        web.note_arr_item(new)
        items = web._state["result"]["arr"]["items"]
        self.assertEqual([i["tmdb_id"] for i in items], [5, 6])
        self.assertEqual(web._state["result"]["arr"]["sonarr"]["count"], 2)
        web.note_arr_item(dict(new, arr_state="downloaded"))
        items = web._state["result"]["arr"]["items"]
        self.assertEqual([(i["tmdb_id"], i["arr_state"]) for i in items], [(5, "partial"), (6, "downloaded")])
        self.assertEqual(web._state["result"]["arr"]["sonarr"]["count"], 2)

    def test_replaces_by_tvdb_id_when_the_tmdb_id_is_missing(self):
        self.set_result(build_result(arr=[arr_tv(5, [])]))
        web.note_arr_item(dict(arr_tv(5, []), tmdb_id=None, arr_state="missing"))
        items = web._state["result"]["arr"]["items"]
        self.assertEqual([(i["tmdb_id"], i["arr_state"]) for i in items], [(None, "missing")])

    def test_a_radarr_item_with_the_same_id_does_not_replace_a_sonarr_one(self):
        self.set_result(build_result(arr=[arr_tv(5, [])]))
        web.note_arr_item(dict(arr_tv(5, []), service="radarr", media_type="movie", tvdb_id=None))
        self.assertEqual(len(web._state["result"]["arr"]["items"]), 2)

    def test_replaces_the_list_object_so_readers_keep_a_consistent_snapshot(self):
        self.set_result(build_result(arr=[arr_tv(5, [])]))
        before = web._state["result"]["arr"]["items"]
        web.note_arr_item(arr_tv(6, []))
        self.assertEqual(len(before), 1)

    def test_nothing_to_do_without_a_result_or_item(self):
        web.note_arr_item(arr_tv(5, []))
        web.note_arr_item(None)
        self.assertIsNone(web._state["result"])

    def test_holds_only_the_status_lock_and_does_not_wait_for_a_build(self):
        self.set_result(build_result())
        with web.compute_lock:
            web.note_arr_item(arr_tv(5, []))
        self.assertEqual(len(web._state["result"]["arr"]["items"]), 1)


class TestWithUserState(WebCase):
    def test_live_adds_stars_lists_and_watchlist(self):
        watch = db.ensure_watchlist()
        lid = db.create_list("L")
        db.add_to_list(watch, {"media_type": "movie", "tmdb_id": 1, "title": "x", "year": None, "poster_url": None})
        db.add_to_list(lid, {"media_type": "movie", "tmdb_id": 1, "title": "x", "year": None, "poster_url": None})
        db.set_rating("movie", 1, 3)
        self.live()
        src = [{"media_type": "movie", "tmdb_id": 1}, {"media_type": "tv", "tmdb_id": 1}, {"media_type": "movie", "tmdb_id": None}]
        out = web.with_user_state(src)
        self.assertEqual([(o["stars"], o["lists"], o["on_watchlist"]) for o in out],
                         [(3, [watch, lid], True), (None, [], False), (None, [], False)])
        self.assertNotIn("stars", src[0])   # copies

    def test_one_ratings_and_one_memberships_query_per_call(self):
        self.live()
        with mock.patch.object(db, "ratings", return_value={}) as ratings, \
             mock.patch.object(db, "memberships", return_value={}) as member:
            web.with_user_state([{"media_type": "movie", "tmdb_id": n} for n in range(30)])
        self.assertEqual((ratings.call_count, member.call_count), (1, 1))

    def test_sample_gives_none_empty_false_and_never_touches_the_db(self):
        with mock.patch.object(db, "ratings", side_effect=AssertionError), mock.patch.object(db, "lists", side_effect=AssertionError):
            out = web.with_user_state([{"media_type": "movie", "tmdb_id": 1005}])
        self.assertEqual((out[0]["stars"], out[0]["lists"], out[0]["on_watchlist"]), (None, [], False))

    def test_a_list_without_a_watchlist_row_reports_false(self):
        lid = db.create_list("L")
        db.add_to_list(lid, {"media_type": "movie", "tmdb_id": 1, "title": "x", "year": None, "poster_url": None})
        self.live()
        self.assertFalse(web.with_user_state([{"media_type": "movie", "tmdb_id": 1}])[0]["on_watchlist"])


class TestRequestsItems(WebCase):
    def add(self, n, media_type="tv", seasons=None, title=None):
        db.record_added({"media_type": media_type, "tmdb_id": n, "title": title or f"Show{n}", "year": 2020,
                         "poster_url": None, "url": "u"}, seasons)

    def test_entry_shape_and_states(self):
        self.live()
        self.add(1, seasons=[1])
        self.add(2, "movie")
        self.add(3, "movie")
        arr_movie = dict(arr_tv(2, []), media_type="movie", service="radarr", arr_state="downloaded", seasons=None, episodes=None)
        plex_movie = {"media_type": "movie", "tmdb_id": 3, "title": "Show3", "year": 2020, "watched": False}
        self.set_result(build_result(arr=[arr_tv(1, [aseason(1, True, 8, 8), aseason(2, True, 0, 8)]), arr_movie], library=[plex_movie]))
        got = {e["tmdb_id"]: e for e in web.requests_items(web._state["result"])}
        self.assertEqual(set(got[1]), {"media_type", "tmdb_id", "title", "year", "added_at", "watched", "progress", "poster_key",
                                       "poster_url", "url", "seasons", "request_state", "episodes", "sources", "stars", "lists",
                                       "on_watchlist"})
        self.assertEqual((got[1]["request_state"], got[1]["seasons"], got[1]["sources"]), ("available", [1], ["sonarr"]))
        self.assertEqual(got[1]["episodes"], {"have": 8, "total": 8})
        self.assertEqual((got[2]["request_state"], got[2]["sources"]), ("available", ["radarr"]))
        self.assertEqual((got[3]["request_state"], got[3]["sources"]), ("available", ["plex"]))   # no arr item, in Plex
        self.assertFalse(got[1]["watched"])

    def test_requested_when_neither_arr_nor_plex_has_it(self):
        self.live()
        self.add(1, "movie")
        self.set_result(build_result())
        self.assertEqual(web.requests_items(web._state["result"])[0]["request_state"], "requested")

    def test_no_state_before_the_first_build(self):
        self.live()
        self.add(1, "movie")
        got = web.requests_items(None)
        self.assertEqual((got[0]["request_state"], got[0]["episodes"], got[0]["sources"]), (None, None, []))

    def test_empty_and_user_state(self):
        self.live()
        self.assertEqual(web.requests_items(None), [])
        self.add(1, "movie")
        db.set_rating("movie", 1, 5)
        self.assertEqual(web.requests_items(None)[0]["stars"], 5)

    def test_library_items_added_tab_uses_it(self):
        self.live()
        self.add(1, "movie")
        got = web.library_items(None, "added")
        self.assertEqual([e["tmdb_id"] for e in got], [1])
        self.assertIn("request_state", got[0])

    def test_list_view_show_open_and_available(self):
        items = [{"title": "A", "request_state": "available"}, {"title": "B", "request_state": "processing"},
                 {"title": "C", "request_state": None}, {"title": "D", "request_state": "requested"}]
        def titles(show):
            return [i["title"] for i in web.list_view(items, tab="added", show=show)["items"]]
        self.assertEqual(titles("all"), ["A", "B", "C", "D"])
        self.assertEqual(sorted(titles("open")), ["B", "C", "D"])
        self.assertEqual(titles("available"), ["A"])

    def test_query_validation_for_the_added_tab(self):
        self.assertEqual(web.parse_list_query({"type": ["added"], "show": ["open"]})["show"], "open")
        self.assertEqual(web.parse_list_query({"type": ["added"], "show": ["available"]})["show"], "available")
        self.assertEqual(web.parse_list_query({"type": ["added"], "show": ["bogus"]})["show"], "all")
        self.assertEqual(web.parse_list_query({"type": ["movie"], "show": ["open"]})["show"], "all")


class TestListsView(WebCase):
    def test_sample_is_a_virtual_watchlist_and_writes_nothing(self):
        with mock.patch.object(db, "ensure_watchlist", side_effect=AssertionError("write")), \
             mock.patch.object(db, "create_list", side_effect=AssertionError("write")):
            v = web.lists_view()
            self.assertIsNone(web.watchlist_id())
        self.assertEqual(v, {"lists": [{"id": None, "name": "Watchlist", "description": "", "kind": "watchlist", "count": 0,
                                        "url": None, "posters": []}], "sample": True, "can_create": False, "max_lists": 50})

    def test_live_creates_the_watchlist_and_summarises_every_list(self):
        self.live()
        self.assertEqual(db.lists(), [])
        lid = db.create_list("Zed", "desc")
        for n in range(6):
            db.add_to_list(lid, {"media_type": "movie", "tmdb_id": n, "title": f"T{n}", "year": None,
                                 "poster_url": None if n == 5 else f"https://p/{n}"})
        v = web.lists_view()
        self.assertEqual([(l["name"], l["kind"], l["count"]) for l in v["lists"]], [("Watchlist", "watchlist", 0), ("Zed", "custom", 6)])
        watch, zed = v["lists"]
        self.assertEqual((watch["url"], zed["url"], zed["description"]), (f"/lists/{watch['id']}", f"/lists/{lid}", "desc"))
        self.assertEqual(zed["posters"], ["https://p/4", "https://p/3", "https://p/2", "https://p/1"])   # first 4 in manual order
        self.assertEqual(watch["posters"], [])
        self.assertTrue(v["can_create"] and not v["sample"])
        self.assertEqual(web.watchlist_id(), watch["id"])

    def test_can_create_is_false_at_the_limit(self):
        self.live()
        for n in range(web.MAX_LISTS):
            db.create_list(f"L{n}")
        self.assertFalse(web.lists_view()["can_create"])


class TestListPageView(WebCase):
    def setUp(self):
        super().setUp()
        self.live()
        self.no_build()
        self.lid = db.create_list("Mine")
        # inserted bottom-up so manual order is C, B, A, D
        self.add(4, "D", 2001)
        self.add(1, "A", 2020)
        self.add(2, "b", None)
        self.add(3, "C", 2010)
        self.set_result(build_result())

    def add(self, n, name, year):
        db.add_to_list(self.lid, {"media_type": "movie", "tmdb_id": n, "title": name, "year": year, "poster_url": None})

    def ids(self, sort="manual", page=1):
        return [i["tmdb_id"] for i in web.list_page_view(self.lid, sort, page)["items"]]

    def test_shape(self):
        v = web.list_page_view(self.lid)
        self.assertEqual(set(v), {"list", "sort", "sorts", "page", "pages", "total", "items", "sample", "can_move"})
        self.assertEqual((v["sort"], v["sorts"], v["page"], v["pages"], v["total"], v["sample"], v["can_move"]),
                         ("manual", web.LIST_SORTS, 1, 1, 4, False, True))
        self.assertEqual((v["list"]["name"], v["list"]["count"]), ("Mine", 4))
        first = v["items"][0]
        for key in ("title", "poster_url", "added_at", "position", "status", "in_library", "match", "stars", "lists", "on_watchlist"):
            self.assertIn(key, first)

    def test_every_sort(self):
        self.assertEqual(self.ids("manual"), [3, 2, 1, 4])
        self.assertEqual(self.ids("title"), [1, 2, 3, 4])            # casefolded: A b C D
        self.assertEqual(self.ids("year"), [1, 3, 4, 2])             # newest first, None last
        db.set_rating("movie", 4, 2)
        db.set_rating("movie", 1, 5)
        self.assertEqual(self.ids("rating"), [1, 4, 2, 3])           # None last, title tie-break (b before C)

    def test_recently_added_is_newest_first(self):
        with mock.patch.object(db, "_now", side_effect=lambda: f"2026-01-01T00:00:0{next(self.tick)}+00:00"):
            self.tick = iter(range(9))
            other = db.create_list("Other")
            for n in (7, 8, 9):
                db.add_to_list(other, {"media_type": "movie", "tmdb_id": n, "title": f"T{n}", "year": None, "poster_url": None})
        self.assertEqual([i["tmdb_id"] for i in web.list_page_view(other, "added")["items"]], [9, 8, 7])

    def test_match_sort_uses_the_current_recommendations_and_none_goes_last(self):
        self.set_result(build_result([rec_item(1, match=40), rec_item(4, match=90)]))
        v = web.list_page_view(self.lid, "match")
        self.assertEqual([(i["tmdb_id"], i["match"]) for i in v["items"]], [(4, 90), (1, 40), (2, None), (3, None)])

    def test_can_move_only_for_manual(self):
        self.assertTrue(web.list_page_view(self.lid, "manual")["can_move"])
        self.assertFalse(web.list_page_view(self.lid, "title")["can_move"])
        self.assertEqual(web.list_page_view(self.lid, "bogus")["sort"], "manual")

    def test_paging(self):
        big = db.create_list("Big")
        for n in range(web.LIST_PAGE_SIZE + 5):
            db.add_to_list(big, {"media_type": "movie", "tmdb_id": n + 1, "title": f"T{n:03d}", "year": None, "poster_url": None})
        v = web.list_page_view(big, "title", 2)
        self.assertEqual((v["page"], v["pages"], v["total"], len(v["items"])), (2, 2, web.LIST_PAGE_SIZE + 5, 5))
        self.assertEqual(web.list_page_view(big, "title", 99)["page"], 2)
        self.assertEqual(web.list_page_view(big, "title", 0)["page"], 1)

    def test_status_comes_from_the_snapshot(self):
        self.set_result(build_result(library=[{"media_type": "movie", "tmdb_id": 3, "title": "C", "year": 2010}]))
        by_id = {i["tmdb_id"]: i for i in web.list_page_view(self.lid)["items"]}
        self.assertEqual((by_id[3]["status"], by_id[1]["status"]), ("plex", "none"))

    def test_missing_list_and_sample_mode_give_none(self):
        self.assertIsNone(web.list_page_view(9999))
        self.assertIsNone(web.list_page_view("1"))
        patch = mock.patch.object(sources, "use_sample", return_value=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.assertIsNone(web.list_page_view(self.lid))

    def test_empty_list(self):
        empty = db.create_list("Empty")
        v = web.list_page_view(empty)
        self.assertEqual((v["items"], v["total"], v["pages"]), ([], 0, 1))

    def test_parse_list_page_query(self):
        self.assertEqual(web.parse_list_page_query({}), {"sort": "manual", "page": 1})
        self.assertEqual(web.parse_list_page_query({"sort": ["year"], "page": ["3"]}), {"sort": "year", "page": 3})
        self.assertEqual(web.parse_list_page_query({"sort": ["x"], "page": ["0"]}), {"sort": "manual", "page": 1})
        self.assertEqual(web.parse_list_page_query({"page": ["abc"]})["page"], 1)


class TestSampleCatalogue(unittest.TestCase):
    def test_sample_title_extras_and_cached_title(self):
        client = sample.SampleTmdb()
        details_, extras_ = client.title("tv", 2003)
        self.assertEqual([(s["number"], s["episodes"]) for s in extras_["seasons"]], [(1, 3), (2, 3), (3, 4)])
        self.assertEqual([s["number"] for s in client.title("tv", 2001)[1]["seasons"]], [1, 2])   # default (1, 8), (2, 8)
        self.assertEqual(client.cached_title("movie", 1005)["crew"], [{"name": "Denis Villeneuve", "job": "Director"}])
        self.assertEqual(client.cached_title("tv", 2001)["crew"], [{"name": "Dan Erickson", "job": "Creator"}])
        self.assertIsNone(client.cached_title("movie", 424242))
        with self.assertRaises(KeyError):
            client.title("movie", 424242)
        self.assertTrue(all(s["media_type"] == "movie" for s in client.title("movie", 1001)[1]["similar"]))

    def test_sample_arr_fixtures_carry_ids_and_seasons(self):
        items = sample.arr_library()["items"]
        self.assertEqual([i["arr_id"] for i in items], list(range(501, 510)))
        by_title = {i["title"]: i for i in items}
        self.assertIsNone(by_title["Dune"]["seasons"])
        self.assertEqual([(s["number"], s["have"], s["total"]) for s in by_title["Black Mirror"]["seasons"]],
                         [(1, 3, 3), (2, 3, 3), (3, 4, 4)])
        self.assertEqual([(s["number"], s["have"], s["total"]) for s in by_title["Night Shift Diaries"]["seasons"]],
                         [(1, 5, 5), (2, 0, 5)])
        self.assertEqual([(s["number"], s["have"], s["total"]) for s in by_title["Low Tide"]["seasons"]], [(1, 0, 8)])
        self.assertTrue(all(s["monitored"] for i in items if i["seasons"] for s in i["seasons"]))


if __name__ == "__main__":
    unittest.main()
