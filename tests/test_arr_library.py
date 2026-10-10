"""arr_library: pure helpers (poster URL rules, title matching, merging Radarr/Sonarr into the Plex
snapshot, search-result statuses). No I/O."""
import copy
from datetime import date
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import arr_library as al


def plex(tmdb_id, title="T", media_type="movie", year=2020, **extra):
    return {"media_type": media_type, "tmdb_id": tmdb_id, "title": title, "year": year,
            "added_at": "2024-01-01T00:00:00", "watched": True, "progress": None,
            "poster_key": 5, "url": "u", **extra}


def arr(tmdb_id, title="T", media_type="movie", year=2020, service=None, state="missing", **extra):
    return {"media_type": media_type, "service": service or ("radarr" if media_type == "movie" else "sonarr"),
            "tmdb_id": tmdb_id, "tvdb_id": None, "title": title, "year": year, "added_at": "2025-01-01T00:00:00Z",
            "monitored": True, "arr_state": state, "episodes": None, "poster_url": "https://image.tmdb.org/a.jpg",
            "url": "arr-url", **extra}


class TestPosterUrl(unittest.TestCase):
    def img(self, url, cover="poster"):
        return [{"coverType": "fanart", "remoteUrl": "https://image.tmdb.org/f.jpg"},
                {"coverType": cover, "remoteUrl": url, "url": "/MediaCover/1/poster.jpg"}]

    def test_tmdb_original_becomes_w342(self):
        self.assertEqual(al.poster_url(self.img("https://image.tmdb.org/t/p/original/abc.jpg")),
                         "https://image.tmdb.org/t/p/w342/abc.jpg")

    def test_tvdb_and_fanart_https_kept(self):
        for url in ("https://artworks.thetvdb.com/banners/p.jpg", "https://www.thetvdb.com/x.jpg",
                    "https://assets.fanart.tv/p.jpg", "https://thetvdb.com/p.jpg"):
            self.assertEqual(al.poster_url(self.img(url)), url)

    def test_everything_else_is_none(self):
        for url in ("http://image.tmdb.org/t/p/original/a.jpg", "javascript:alert(1)", "https://evil.example/a.jpg",
                    "https://image.tmdb.org.evil.example/a.jpg", "https://u:p@image.tmdb.org/a.jpg",
                    "/MediaCover/1/poster.jpg", "", None, 5, "https://image.tmdb.org/a b.jpg"):
            self.assertIsNone(al.poster_url(self.img(url)), repr(url))

    def test_missing_or_bad_images(self):
        for images in (None, [], "x", {"coverType": "poster"}, [None, 3], [{"coverType": "fanart", "remoteUrl": "https://image.tmdb.org/a.jpg"}]):
            self.assertIsNone(al.poster_url(images), repr(images))

    def test_relative_url_alone_is_never_used(self):
        self.assertIsNone(al.poster_url([{"coverType": "poster", "url": "/MediaCover/1/poster.jpg"}]))


class TestTitleMatching(unittest.TestCase):
    def test_title_key(self):
        self.assertEqual(al.title_key("Spider-Man: No Way Home!"), "spidermannowayhome")
        self.assertEqual(al.title_key("  ÉCOLE 2 "), al.title_key("école2"))
        self.assertEqual(al.title_key(None), "")

    def test_same_title(self):
        a = {"media_type": "movie", "title": "Dune", "year": 2021}
        self.assertTrue(al.same_title(a, {"media_type": "movie", "title": "DUNE!", "year": 2021}))
        self.assertTrue(al.same_title(a, {"media_type": "movie", "title": "dune", "year": None}))
        self.assertTrue(al.same_title({**a, "year": None}, a))
        self.assertFalse(al.same_title(a, {"media_type": "movie", "title": "Dune", "year": 1984}))
        self.assertFalse(al.same_title(a, {"media_type": "tv", "title": "Dune", "year": 2021}))
        self.assertFalse(al.same_title(a, {"media_type": "movie", "title": "Dune Part Two", "year": 2021}))


class TestMerge(unittest.TestCase):
    def test_dedupes_by_tmdb_id_and_orders_sources(self):
        (entry,) = al.merge([plex(1, "Dune")], [arr(1, "Dune", state="downloaded")])
        self.assertEqual(entry["sources"], ["plex", "radarr"])
        self.assertEqual(entry["arr_state"], "downloaded")

    def test_plex_poster_and_added_at_win(self):
        (entry,) = al.merge([plex(1)], [arr(1)])
        self.assertEqual((entry["poster_key"], entry["added_at"]), (5, "2024-01-01T00:00:00"))
        self.assertIsNone(entry["poster_url"])  # arr poster only when there is no Plex poster

    def test_arr_poster_used_when_plex_has_none(self):
        (entry,) = al.merge([plex(1, poster_key=None)], [arr(1)])
        self.assertEqual(entry["poster_url"], "https://image.tmdb.org/a.jpg")

    def test_different_ids_do_not_merge_even_with_same_title(self):
        merged = al.merge([plex(1, "Dune")], [arr(2, "Dune")])
        self.assertEqual(len(merged), 2)

    def test_title_fallback_only_when_one_side_lacks_an_id(self):
        merged = al.merge([plex(None, "Low Tide", "tv", 2025)], [arr(7, "Low Tide!", "tv", 2025)])
        self.assertEqual([e["sources"] for e in merged], [["plex", "sonarr"]])
        merged = al.merge([plex(3, "Low Tide", "tv", 2025)], [arr(None, "Low Tide", "tv", 2025)])
        self.assertEqual(len(merged), 1)
        merged = al.merge([plex(3, "Low Tide", "tv", 2025)], [arr(None, "Low Tide", "tv", 2019)])
        self.assertEqual(len(merged), 2)  # years differ

    def test_arr_only_entry_shape(self):
        (entry,) = al.merge([], [arr(9, "Solo", state="partial", episodes={"have": 1, "total": 4})])
        self.assertEqual(entry, {
            "media_type": "movie", "tmdb_id": 9, "title": "Solo", "year": 2020, "added_at": "2025-01-01T00:00:00Z",
            "watched": False, "progress": None, "poster_key": None, "poster_url": "https://image.tmdb.org/a.jpg",
            "url": "arr-url", "sources": ["radarr"], "arr_state": "partial", "episodes": {"have": 1, "total": 4}})

    def test_none_plex_input(self):
        self.assertEqual(len(al.merge(None, [arr(1)])), 1)
        self.assertEqual(al.merge(None, None), [])

    def test_plex_only_entries_get_plex_source(self):
        (entry,) = al.merge([plex(1)], [])
        self.assertEqual((entry["sources"], entry["arr_state"], entry["episodes"]), (["plex"], None, None))

    def test_first_match_wins(self):
        merged = al.merge([plex(None, "Dup", year=None, poster_key=1), plex(None, "Dup", year=None, poster_key=2)],
                          [arr(5, "Dup", state="missing")])
        self.assertEqual([e["sources"] for e in merged], [["plex", "radarr"], ["plex"]])

    def test_two_arr_items_for_one_title_do_not_duplicate(self):
        self.assertEqual(len(al.merge([], [arr(1), arr(1)])), 1)

    def test_inputs_not_mutated(self):
        p, a = [plex(1), plex(2)], [arr(1), arr(3)]
        before = copy.deepcopy((p, a))
        al.merge(p, a)
        self.assertEqual((p, a), before)


class TestAnnotate(unittest.TestCase):
    def result(self, tmdb_id=1, title="T"):
        return {"media_type": "movie", "tmdb_id": tmdb_id, "title": title, "year": 2020}

    def status(self, **kw):
        (out,) = al.annotate([self.result()], **kw)
        return out

    def test_none(self):
        out = self.status()
        self.assertEqual((out["status"], out["in_library"], out["sources"], out["watched"], out["dismissed"]),
                         ("none", False, [], False, False))

    def test_precedence_plex_over_arr_over_added(self):
        both = self.status(plex_items=[plex(1)], arr_items=[arr(1)], added_keys={("movie", 1)})
        self.assertEqual((both["status"], both["sources"], both["in_library"]), ("plex", ["plex", "radarr"], True))
        radarr_only = self.status(arr_items=[arr(1, state="missing")], added_keys={("movie", 1)})
        self.assertEqual((radarr_only["status"], radarr_only["arr_state"]), ("radarr", "missing"))
        self.assertEqual(self.status(added_keys={("movie", 1)})["status"], "added")

    def test_sonarr_status_and_episodes(self):
        (out,) = al.annotate([{"media_type": "tv", "tmdb_id": 4, "title": "S", "year": 2020}],
                             arr_items=[arr(4, "S", "tv", state="partial", episodes={"have": 1, "total": 2})])
        self.assertEqual((out["status"], out["episodes"]), ("sonarr", {"have": 1, "total": 2}))

    def test_watched_and_dismissed_flags(self):
        out = self.status(watched_items=[plex(1)], dismissed={("movie", 1)})
        self.assertTrue(out["watched"] and out["dismissed"])
        self.assertEqual(out["status"], "none")

    def test_title_fallback(self):
        (out,) = al.annotate([self.result(1, "Low Tide")], arr_items=[arr(None, "low tide")])
        self.assertEqual(out["status"], "radarr")

    def test_copies_and_no_mutation(self):
        items = [self.result()]
        before = copy.deepcopy(items)
        out = al.annotate(items, plex_items=[plex(1)])
        self.assertEqual(items, before)
        self.assertIsNot(out[0], items[0])


class TestFind(unittest.TestCase):
    def test_tmdb_match_first(self):
        items = [arr(None, "Dune", tvdb_id=None), arr(5, "Dune"), arr(6, "Other")]
        self.assertIs(al.find(items, "movie", 5), items[1])

    def test_tvdb_match_for_tv_only(self):
        show = arr(None, "Show", "tv", tvdb_id=77)
        self.assertIs(al.find([show], "tv", 999, tvdb_id=77), show)
        movie = arr(None, "Film", tvdb_id=77)
        self.assertIsNone(al.find([movie], "movie", 999, tvdb_id=77))

    def test_title_fallback_only_for_arr_items_without_a_tmdb_id(self):
        keyless = arr(None, "Low Tide", "tv", 2025)
        self.assertIs(al.find([keyless], "tv", 1, title="low tide!", year=2025), keyless)
        self.assertIsNone(al.find([keyless], "tv", 1, title="Low Tide", year=1999))
        self.assertIsNone(al.find([arr(9, "Low Tide", "tv", 2025)], "tv", 1, title="Low Tide", year=2025))
        self.assertIsNone(al.find([keyless], "movie", 1, title="Low Tide"))
        self.assertIsNone(al.find([keyless], "tv", 1))
        self.assertIsNone(al.find(None, "tv", 1))


def aseason(n, monitored=True, have=0, total=0):
    return {"number": n, "monitored": monitored, "have": have, "total": total}


class TestSeasonRows(unittest.TestCase):
    today = date(2026, 10, 10)

    def tm(self, n, episodes=8, air="2020-01-01", name=None):
        return {"number": n, "name": name or f"S{n}", "episodes": episodes, "air_date": air, "poster_url": None}

    def rows(self, tmdb, seasons=None, tracked=True):
        item = arr(1, "Show", "tv", seasons=seasons) if tracked else None
        return {r["number"]: r for r in al.season_rows(tmdb, item, self.today)}

    def test_untracked_series_has_no_arr_data_and_everything_is_selectable(self):
        rows = self.rows([self.tm(0), self.tm(1), self.tm(2)], tracked=False)
        self.assertEqual(sorted(rows), [1, 2])          # specials hidden
        r = rows[1]
        self.assertEqual((r["monitored"], r["have"], r["total"], r["state"], r["requested"], r["selectable"]),
                         (None, None, None, None, False, True))
        self.assertEqual((r["name"], r["episodes"], r["air_date"]), ("S1", 8, "2020-01-01"))

    def test_every_state(self):
        seasons = [aseason(1, True, 8, 8), aseason(2, True, 3, 8), aseason(3, False, 0, 8), aseason(4, True, 0, 8),
                   aseason(5, True, 0, 8), aseason(6, True, 0, 8)]
        tmdb = [self.tm(1), self.tm(2), self.tm(3), self.tm(4), self.tm(5, air="2027-01-01"), self.tm(6, air=None)]
        rows = self.rows(tmdb, seasons)
        self.assertEqual({n: rows[n]["state"] for n in rows},
                         {1: "available", 2: "partial", 3: "unmonitored", 4: "missing", 5: "upcoming", 6: "upcoming"})
        self.assertEqual((rows[1]["requested"], rows[1]["selectable"]), (True, False))
        self.assertEqual((rows[3]["requested"], rows[3]["selectable"], rows[3]["monitored"]), (False, True, False))
        self.assertEqual((rows[2]["have"], rows[2]["total"]), (3, 8))

    def test_complete_beats_unmonitored_and_partial_beats_unmonitored(self):
        rows = self.rows([self.tm(1), self.tm(2)], [aseason(1, False, 8, 8), aseason(2, False, 2, 8)])
        self.assertEqual((rows[1]["state"], rows[2]["state"]), ("available", "partial"))

    def test_union_of_tmdb_and_arr_numbers(self):
        rows = self.rows([self.tm(1), self.tm(2)], [aseason(2, True, 0, 6), aseason(3, True, 0, 4), aseason(0)])
        self.assertEqual(sorted(rows), [1, 2, 3])
        self.assertIsNone(rows[1]["state"])             # TMDB only: not in the arr data
        self.assertIsNone(rows[1]["monitored"])
        self.assertTrue(rows[1]["selectable"])          # tracked series, but this season isn't requested
        self.assertEqual((rows[3]["name"], rows[3]["episodes"], rows[3]["air_date"]), ("Season 3", 4, None))
        self.assertEqual(rows[3]["state"], "upcoming")  # no air date known
        self.assertEqual(rows[2]["episodes"], 8)        # TMDB's count wins

    def test_episode_fallback_when_tmdb_has_none(self):
        rows = self.rows([self.tm(1, episodes=0)], [aseason(1, True, 0, 5)])
        self.assertEqual(rows[1]["episodes"], 5)
        self.assertIsNone(self.rows([self.tm(1, episodes=0)], tracked=False)[1]["episodes"])

    def test_arr_without_a_seasons_list_and_inputs_untouched(self):
        tmdb = [self.tm(1)]
        before = copy.deepcopy(tmdb)
        rows = al.season_rows(tmdb, arr(1, "Show", "tv", seasons=None), self.today)
        self.assertEqual(rows[0]["state"], None)
        self.assertEqual(tmdb, before)
        self.assertEqual(al.season_rows(None, None, self.today), [])


class TestRequestState(unittest.TestCase):
    def entry(self, seasons=None):
        return {"media_type": "tv", "tmdb_id": 1, "title": "Show", "seasons": seasons}

    def test_no_arr_item(self):
        self.assertEqual(al.request_state(self.entry(), None, False), ("requested", None))
        self.assertEqual(al.request_state(self.entry(), None, True), ("available", None))

    def test_movie_states(self):
        for arr_state, want in (("downloaded", "available"), ("missing", "processing"), ("upcoming", "upcoming"),
                                ("unmonitored", "unmonitored")):
            got = al.request_state({"media_type": "movie"}, arr(1, state=arr_state), False)
            self.assertEqual(got, (want, None))

    def test_in_plex_does_not_override_an_arr_item(self):
        self.assertEqual(al.request_state({"media_type": "movie"}, arr(1, state="missing"), True)[0], "processing")

    def test_tv_whole_series_states(self):
        eps = {"have": 3, "total": 10}
        show = lambda state: arr(1, "Show", "tv", state=state, episodes=eps)
        self.assertEqual(al.request_state(self.entry(), show("partial"), False), ("partial", eps))
        self.assertEqual(al.request_state(self.entry("all"), show("downloaded"), False), ("available", eps))
        self.assertEqual(al.request_state(self.entry(), show("missing"), False)[0], "processing")
        self.assertEqual(al.request_state(self.entry(), show("upcoming"), False)[0], "upcoming")
        self.assertEqual(al.request_state(self.entry(), show("unmonitored"), False)[0], "unmonitored")

    def pick_show(self, state="partial"):
        return arr(1, "Show", "tv", state=state, episodes={"have": 8, "total": 20},
                   seasons=[aseason(1, True, 8, 8), aseason(2, True, 0, 6), aseason(3, True, 0, 6)])

    def test_tv_pick_complete_is_available_even_if_the_series_is_partial(self):
        self.assertEqual(al.request_state(self.entry([1]), self.pick_show(), False),
                         ("available", {"have": 8, "total": 8}))

    def test_tv_pick_is_summed_over_the_requested_seasons(self):
        show = self.pick_show()
        show["seasons"][1] = aseason(2, True, 2, 6)
        self.assertEqual(al.request_state(self.entry([1, 2]), show, False), ("partial", {"have": 10, "total": 14}))

    def test_tv_pick_nothing_downloaded_yet(self):
        self.assertEqual(al.request_state(self.entry([2, 3]), self.pick_show(), False),
                         ("processing", {"have": 0, "total": 12}))

    def test_tv_pick_without_matching_arr_seasons_falls_back_to_the_series_state(self):
        show = self.pick_show()
        self.assertEqual(al.request_state(self.entry([9]), show, False)[0], "partial")
        show["seasons"] = None
        self.assertEqual(al.request_state(self.entry([1]), show, False)[0], "partial")


if __name__ == "__main__":
    unittest.main()
