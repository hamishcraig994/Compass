"""db.py: the schema migrations, the Requests seasons column, ratings rows and every list function.
Web-level list behaviour (views and POST routes) is in test_title.py and test_web_async.py."""
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import db


def title(n, media_type="movie", **extra):
    return {"media_type": media_type, "tmdb_id": n, "title": f"T{n}", "year": 2000 + n, "poster_url": f"https://p/{n}",
            **extra}


class DbCase(unittest.TestCase):
    def setUp(self):
        self._old = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old)


class TestMigration(DbCase):
    OLD_ADDED = ("CREATE TABLE added (media_type TEXT NOT NULL, tmdb_id INTEGER NOT NULL, title TEXT NOT NULL, "
                 "year INTEGER, poster_url TEXT, url TEXT, added_at TEXT NOT NULL, PRIMARY KEY (media_type, tmdb_id))")

    def make_old_db(self):
        conn = sqlite3.connect(db.DB_PATH)
        conn.execute(self.OLD_ADDED)
        conn.execute("INSERT INTO added VALUES ('movie', 5, 'Dune', 2021, NULL, 'u', '2024-01-01T00:00:00+00:00')")
        conn.execute("CREATE TABLE ratings (media_type TEXT NOT NULL, tmdb_id INTEGER NOT NULL, stars INTEGER NOT NULL "
                     "CHECK (stars BETWEEN 1 AND 5), rated_at TEXT NOT NULL, PRIMARY KEY (media_type, tmdb_id))")
        conn.execute("INSERT INTO ratings VALUES ('movie', 5, 4, '2024-02-01T00:00:00+00:00')")
        conn.commit()
        conn.close()

    def test_old_schema_is_upgraded_twice_and_keeps_its_rows(self):
        self.make_old_db()
        for _ in range(2):
            db._connect().close()
        conn = sqlite3.connect(db.DB_PATH)
        columns = [r[1] for r in conn.execute("PRAGMA table_info(added)")]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        conn.close()
        self.assertEqual(columns.count("seasons"), 1)
        self.assertLessEqual({"lists", "list_items"}, tables)
        self.assertLessEqual({"lists_one_watchlist", "list_items_title"}, indexes)
        items = db.added_items()
        self.assertEqual([(i["title"], i["seasons"]) for i in items], [("Dune", None)])
        self.assertEqual(db.ratings(), {("movie", 5): 4})

    def test_fresh_database_has_the_seasons_column(self):
        db._connect().close()
        conn = sqlite3.connect(db.DB_PATH)
        self.assertIn("seasons", [r[1] for r in conn.execute("PRAGMA table_info(added)")])
        conn.close()

    def test_a_lost_duplicate_column_race_is_tolerated(self):
        """Two threads both see no column; the loser's ALTER fails with 'duplicate column name'."""
        self.make_old_db()
        real_connect = sqlite3.connect
        barrier = threading.Barrier(2)

        class SlowConn(sqlite3.Connection):
            def execute(self, sql, *args):
                result = super().execute(sql, *args)
                if sql.startswith("PRAGMA table_info(added)"):
                    rows = result.fetchall()
                    try:
                        barrier.wait(2)   # both threads have now looked, neither has altered
                    except threading.BrokenBarrierError:
                        pass
                    return _Replay(rows)
                return result

        class _Replay:
            def __init__(self, rows):
                self.rows = rows

            def __iter__(self):
                return iter(self.rows)

        errors = []

        def connect(path, *a, **kw):
            return real_connect(path, *a, factory=SlowConn, **kw)

        def worker():
            try:
                db._connect().close()
            except Exception as e:   # pragma: no cover - the failure we are guarding against
                errors.append(e)

        with mock.patch.object(sqlite3, "connect", connect):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            [t.start() for t in threads]
            [t.join(10) for t in threads]
        self.assertEqual(errors, [])
        conn = real_connect(db.DB_PATH)
        self.assertEqual([r[1] for r in conn.execute("PRAGMA table_info(added)")].count("seasons"), 1)
        conn.close()

    def test_other_operational_errors_are_not_swallowed(self):
        self.make_old_db()
        real = sqlite3.connect

        class Boom(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql.startswith("ALTER TABLE added"):
                    raise sqlite3.OperationalError("disk I/O error")
                return super().execute(sql, *args)

        with mock.patch.object(sqlite3, "connect", lambda p, *a, **kw: real(p, *a, factory=Boom, **kw)):
            with self.assertRaises(sqlite3.OperationalError):
                db._connect()


class TestRequestsSeasons(DbCase):
    def added(self, n=5):
        return next(i for i in db.added_items() if i["tmdb_id"] == n)

    def test_round_trip(self):
        db.record_added(title(5, "tv"), [3, 1, 3])
        self.assertEqual(self.added()["seasons"], [1, 3])
        db.record_added(title(6, "tv"), "all")
        self.assertEqual(self.added(6)["seasons"], "all")
        db.record_added(title(7))
        self.assertIsNone(self.added(7)["seasons"])

    def test_merge_is_a_union_and_all_wins(self):
        db.record_added(title(5, "tv"), [1])
        db.record_added(title(5, "tv"), [3, 1])
        self.assertEqual(self.added()["seasons"], [1, 3])
        db.record_added(title(5, "tv"), "all")
        self.assertEqual(self.added()["seasons"], "all")
        db.record_added(title(5, "tv"), [2])
        self.assertEqual(self.added()["seasons"], "all")

    def test_none_never_erases_a_stored_value(self):
        db.record_added(title(5, "tv"), [2])
        db.record_added(title(5, "tv"), None)
        self.assertEqual(self.added()["seasons"], [2])
        db.record_added(title(5, "tv"))
        self.assertEqual(self.added()["seasons"], [2])

    def test_corrupt_values_read_as_none(self):
        db.record_added(title(5, "tv"))
        conn = sqlite3.connect(db.DB_PATH)
        for bad in ("garbage", "{}", '["a"]', "[-1]", "[true]", "5", "null"):
            conn.execute("UPDATE added SET seasons = ?", (bad,))
            conn.commit()
            self.assertIsNone(self.added()["seasons"], bad)
        conn.close()

    def test_a_repeat_request_refreshes_the_row(self):
        db.record_added(title(5, "tv"), [1])
        db.record_added(dict(title(5, "tv"), title="Renamed"), [2])
        self.assertEqual(len(db.added_items()), 1)
        self.assertEqual(self.added()["title"], "Renamed")

    def test_added_count_unchanged_by_the_new_column(self):
        db.record_added(title(5))
        self.assertEqual(db.added_count(), 1)


class TestRatingRows(DbCase):
    def test_rows_carry_stars_and_timestamp(self):
        self.assertEqual(db.rating_rows(), [])
        db.set_rating("movie", 5, 4)
        db.set_rating("tv", 9, 2)
        rows = db.rating_rows()
        self.assertEqual({(r["media_type"], r["tmdb_id"], r["stars"]) for r in rows}, {("movie", 5, 4), ("tv", 9, 2)})
        self.assertTrue(all(r["rated_at"].startswith("20") for r in rows))
        db.clear_rating("movie", 5)
        self.assertEqual([r["tmdb_id"] for r in db.rating_rows()], [9])


class TestWatchlist(DbCase):
    def test_lists_never_creates(self):
        self.assertEqual(db.lists(), [])

    def test_ensure_is_idempotent(self):
        first = db.ensure_watchlist()
        self.assertEqual(db.ensure_watchlist(), first)
        rows = db.lists()
        self.assertEqual([(r["id"], r["name"], r["kind"], r["count"]) for r in rows], [(first, "Watchlist", "watchlist", 0)])

    def test_concurrent_first_use_makes_one_row(self):
        ids, errors = [], []

        def worker():
            try:
                ids.append(db.ensure_watchlist())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        [t.start() for t in threads]
        [t.join(10) for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(db.lists()), 1)

    def test_a_second_watchlist_row_is_refused_by_the_index(self):
        db.ensure_watchlist()
        conn = sqlite3.connect(db.DB_PATH)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO lists (name, kind, created_at, updated_at) VALUES ('W2', 'watchlist', 'x', 'x')")
        conn.close()


class TestListCrud(DbCase):
    def test_order_counts_and_get(self):
        z, a = db.create_list("zeta", "last"), db.create_list("Alpha")
        watch = db.ensure_watchlist()
        db.add_to_list(a, title(1))
        db.add_to_list(a, title(2))
        rows = db.lists()
        self.assertEqual([r["name"] for r in rows], ["Watchlist", "Alpha", "zeta"])   # watchlist, then casefolded name
        self.assertEqual([r["count"] for r in rows], [0, 2, 0])
        self.assertEqual(db.get_list(z)["description"], "last")
        self.assertEqual(db.get_list(watch)["kind"], "watchlist")
        self.assertEqual(set(db.get_list(a)), {"id", "name", "description", "kind", "created_at", "updated_at", "count"})
        self.assertIsNone(db.get_list(999))
        self.assertEqual(db.get_list(a)["kind"], "custom")

    def test_update(self):
        lid = db.create_list("A", "x")
        before = db.get_list(lid)["updated_at"]
        self.assertTrue(db.update_list(lid, "B", "y"))
        got = db.get_list(lid)
        self.assertEqual((got["name"], got["description"]), ("B", "y"))
        self.assertGreaterEqual(got["updated_at"], before)
        self.assertFalse(db.update_list(999, "B", ""))

    def test_watchlist_cannot_be_renamed_or_deleted(self):
        watch = db.ensure_watchlist()
        db.add_to_list(watch, title(1))
        self.assertFalse(db.update_list(watch, "Mine", ""))
        self.assertFalse(db.delete_list(watch))
        self.assertEqual((db.get_list(watch)["name"], db.get_list(watch)["count"]), ("Watchlist", 1))

    def test_delete_removes_the_items_too(self):
        keep, gone = db.create_list("keep"), db.create_list("gone")
        db.add_to_list(keep, title(1))
        db.add_to_list(gone, title(1))
        db.add_to_list(gone, title(2))
        self.assertTrue(db.delete_list(gone))
        self.assertIsNone(db.get_list(gone))
        self.assertEqual(db.list_items(gone), [])
        self.assertEqual(db.memberships(), {("movie", 1): [keep]})
        self.assertFalse(db.delete_list(gone))


class TestListItems(DbCase):
    def setUp(self):
        super().setUp()
        self.lid = db.create_list("L")

    def order(self):
        return [i["tmdb_id"] for i in db.list_items(self.lid)]

    def test_new_items_go_to_the_top(self):
        for n in (1, 2, 3):
            self.assertTrue(db.add_to_list(self.lid, title(n)))
        self.assertEqual(self.order(), [3, 2, 1])
        positions = [i["position"] for i in db.list_items(self.lid)]
        self.assertEqual(positions, [-2, -1, 0])

    def test_item_fields(self):
        db.add_to_list(self.lid, {"media_type": "tv", "tmdb_id": 8, "title": "S"})
        item = db.list_items(self.lid)[0]
        self.assertEqual({k: item[k] for k in ("media_type", "tmdb_id", "title", "year", "poster_url")},
                         {"media_type": "tv", "tmdb_id": 8, "title": "S", "year": None, "poster_url": None})
        self.assertTrue(item["added_at"])

    def test_duplicate_add_is_false_and_the_same_id_can_be_movie_and_tv(self):
        self.assertTrue(db.add_to_list(self.lid, title(1)))
        self.assertFalse(db.add_to_list(self.lid, title(1)))
        self.assertTrue(db.add_to_list(self.lid, title(1, "tv")))
        self.assertEqual(len(db.list_items(self.lid)), 2)

    def test_add_to_a_missing_list_is_false(self):
        self.assertFalse(db.add_to_list(999, title(1)))
        self.assertEqual(db.memberships(), {})

    def test_remove(self):
        db.add_to_list(self.lid, title(1))
        self.assertTrue(db.remove_from_list(self.lid, "movie", 1))
        self.assertFalse(db.remove_from_list(self.lid, "movie", 1))
        self.assertFalse(db.remove_from_list(self.lid, "tv", 1))
        self.assertEqual(self.order(), [])

    def test_add_after_removing_the_top_still_goes_on_top(self):
        for n in (1, 2, 3):
            db.add_to_list(self.lid, title(n))
        db.remove_from_list(self.lid, "movie", 3)
        db.add_to_list(self.lid, title(4))
        self.assertEqual(self.order(), [4, 2, 1])

    def fill(self):
        for n in (4, 3, 2, 1):
            db.add_to_list(self.lid, title(n))      # order is now 1, 2, 3, 4
        self.assertEqual(self.order(), [1, 2, 3, 4])

    def test_move_down_and_up_swap_with_the_neighbour(self):
        self.fill()
        self.assertTrue(db.move_in_list(self.lid, "movie", 2, "down"))
        self.assertEqual(self.order(), [1, 3, 2, 4])
        self.assertTrue(db.move_in_list(self.lid, "movie", 2, "up"))
        self.assertEqual(self.order(), [1, 2, 3, 4])

    def test_move_top_and_bottom(self):
        self.fill()
        self.assertTrue(db.move_in_list(self.lid, "movie", 3, "top"))
        self.assertEqual(self.order(), [3, 1, 2, 4])
        self.assertTrue(db.move_in_list(self.lid, "movie", 1, "bottom"))
        self.assertEqual(self.order(), [3, 2, 4, 1])

    def test_moves_at_the_ends_and_bad_input_are_false_and_change_nothing(self):
        self.fill()
        for args in ((1, "up"), (1, "top"), (4, "down"), (4, "bottom"), (99, "up")):
            self.assertFalse(db.move_in_list(self.lid, "movie", *args), args)
        self.assertFalse(db.move_in_list(self.lid, "movie", 2, "sideways"))
        self.assertFalse(db.move_in_list(self.lid, "tv", 2, "up"))
        self.assertFalse(db.move_in_list(999, "movie", 2, "up"))
        self.assertEqual(self.order(), [1, 2, 3, 4])

    def test_repeated_top_moves_keep_working(self):
        self.fill()
        db.move_in_list(self.lid, "movie", 4, "top")
        db.move_in_list(self.lid, "movie", 3, "top")
        self.assertEqual(self.order(), [3, 4, 1, 2])

    def test_memberships(self):
        other = db.create_list("O")
        db.add_to_list(self.lid, title(1))
        db.add_to_list(other, title(1))
        db.add_to_list(other, title(2, "tv"))
        self.assertEqual(db.memberships(), {("movie", 1): sorted([self.lid, other]), ("tv", 2): [other]})

    def test_counts_and_updated_at_follow_changes(self):
        before = db.get_list(self.lid)["updated_at"]
        db.add_to_list(self.lid, title(1))
        got = db.get_list(self.lid)
        self.assertEqual(got["count"], 1)
        self.assertGreaterEqual(got["updated_at"], before)

    def test_an_unknown_media_type_is_refused_by_the_schema(self):
        self.assertFalse(db.add_to_list(self.lid, title(1, "book")))
        self.assertEqual(db.list_items(self.lid), [])


if __name__ == "__main__":
    unittest.main()
