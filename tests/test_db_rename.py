"""One-time whatsnext.db -> compass.db migration in db.py."""
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import db


class TestRenameMigration(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        old = db.DB_PATH
        db.DB_PATH = os.path.join(self.dir, "compass.db")
        self.addCleanup(setattr, db, "DB_PATH", old)
        self.new = db.DB_PATH
        self.old = os.path.join(self.dir, "whatsnext.db")

    def legacy(self):
        c = sqlite3.connect(self.old)
        c.execute("CREATE TABLE dismissed (media_type TEXT NOT NULL, tmdb_id INTEGER NOT NULL, "
                  "PRIMARY KEY (media_type, tmdb_id))")
        c.execute("INSERT INTO dismissed VALUES ('movie', 7)")
        c.commit()
        c.close()

    def test_default_name_is_compass(self):
        self.assertEqual(os.path.basename(db.DB_PATH), "compass.db")

    def test_fresh_install_creates_compass_only(self):
        db.dismissed()
        self.assertEqual(os.listdir(self.dir), ["compass.db"])

    def test_old_file_is_moved_with_data_and_leaves_nothing(self):
        self.legacy()
        self.assertEqual(db.dismissed(), {("movie", 7)})
        self.assertFalse(os.path.exists(self.old))
        self.assertTrue(os.path.exists(self.new))

    def test_sidecars_move_too(self):
        self.legacy()
        for sfx in ("-wal", "-shm", "-journal"):
            open(self.old + sfx, "w").close()
        with mock.patch.object(db.sqlite3, "connect") as conn:
            db._resolve_path()
            conn.assert_not_called()
        for sfx in ("", "-wal", "-shm", "-journal"):
            self.assertTrue(os.path.exists(self.new + sfx), sfx)
            self.assertFalse(os.path.exists(self.old + sfx), sfx)

    def test_existing_compass_is_not_clobbered(self):
        self.legacy()
        with open(self.new, "wb") as f:
            f.write(b"")
        c = sqlite3.connect(self.new)
        c.execute("CREATE TABLE marker (x)")
        c.commit()
        c.close()
        db.dismissed()
        self.assertTrue(os.path.exists(self.old))
        c = sqlite3.connect(self.new)
        self.assertTrue(c.execute("SELECT name FROM sqlite_master WHERE name='marker'").fetchone())
        c.close()

    def test_failed_rename_falls_back_to_old_file_and_undoes_partial_move(self):
        self.legacy()
        open(self.old + "-wal", "w").close()
        real = os.rename

        def flaky(src, dst):
            if dst == self.new:
                raise OSError("denied")
            return real(src, dst)
        with mock.patch.object(db.os, "rename", flaky):
            self.assertEqual(db.dismissed(), {("movie", 7)})
        self.assertTrue(os.path.exists(self.old))
        self.assertTrue(os.path.exists(self.old + "-wal"))
        self.assertFalse(os.path.exists(self.new + "-wal"))

    def test_custom_path_is_untouched(self):
        self.legacy()
        db.DB_PATH = os.path.join(self.dir, "custom.db")
        db.dismissed()
        self.assertTrue(os.path.exists(self.old))
        self.assertTrue(os.path.exists(db.DB_PATH))


if __name__ == "__main__":
    unittest.main()
