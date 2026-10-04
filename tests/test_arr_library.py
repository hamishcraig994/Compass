"""arr_library: pure helpers (poster URL rules, title matching, merging Radarr/Sonarr into the Plex
snapshot, search-result statuses). No I/O."""
import copy
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


if __name__ == "__main__":
    unittest.main()
