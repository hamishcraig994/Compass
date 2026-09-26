import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
import db
import settings_page


class TestDbSettings(unittest.TestCase):
    def setUp(self):
        self._old_db = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")

    def tearDown(self):
        db.DB_PATH = self._old_db

    def test_unset_setting_is_none(self):
        self.assertIsNone(db.get_setting("NOPE"))

    def test_set_then_get_round_trips(self):
        db.set_setting("PLEX_TOKEN", "abc123")
        self.assertEqual(db.get_setting("PLEX_TOKEN"), "abc123")

    def test_set_overwrites(self):
        db.set_setting("PLEX_TOKEN", "first")
        db.set_setting("PLEX_TOKEN", "second")
        self.assertEqual(db.get_setting("PLEX_TOKEN"), "second")


class TestConfigResolutionOrder(unittest.TestCase):
    """A saved setting beats an env var, which beats the hardcoded default - and set_setting()
    applies live, matching what the Settings page relies on."""

    def setUp(self):
        self._old_db = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self._old_env = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self._old_env)))
        self.addCleanup(setattr, db, "DB_PATH", self._old_db)
        self.addCleanup(config._apply)  # restore config's globals from the real env afterward

    def test_default_when_nothing_set(self):
        os.environ.pop("PLEX_URL", None)
        config._apply()
        self.assertEqual(config.PLEX_URL, "http://192.168.1.102:32400")

    def test_env_var_overrides_default(self):
        os.environ["PLEX_URL"] = "http://elsewhere:32400"
        config._apply()
        self.assertEqual(config.PLEX_URL, "http://elsewhere:32400")

    def test_saved_setting_overrides_env_var(self):
        os.environ["PLEX_URL"] = "http://elsewhere:32400"
        db.set_setting("PLEX_URL", "http://saved:32400")
        config._apply()
        self.assertEqual(config.PLEX_URL, "http://saved:32400")

    def test_set_setting_applies_immediately(self):
        config.set_setting("TMDB_TOKEN", "new-token")
        self.assertEqual(config.TMDB_TOKEN, "new-token")
        self.assertEqual(db.get_setting("TMDB_TOKEN"), "new-token")

    def test_quality_profile_id_parses_as_int_or_none(self):
        config.set_setting("RADARR_QUALITY_PROFILE_ID", "")
        self.assertIsNone(config.RADARR_QUALITY_PROFILE_ID)
        config.set_setting("RADARR_QUALITY_PROFILE_ID", "7")
        self.assertEqual(config.RADARR_QUALITY_PROFILE_ID, 7)

    def test_history_source_is_normalized(self):
        config.set_setting("HISTORY_SOURCE", "  TAUTULLI  ")
        self.assertEqual(config.HISTORY_SOURCE, "tautulli")


class TestSettingsPageForm(unittest.TestCase):
    def setUp(self):
        self._old_db = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old_db)
        self.addCleanup(config._apply)

    def test_apply_form_saves_plain_fields_including_blank(self):
        settings_page.apply_form({"PLEX_URL": ["http://x:32400"]})
        self.assertEqual(config.PLEX_URL, "http://x:32400")
        settings_page.apply_form({"PLEX_URL": [""]})
        self.assertEqual(config.PLEX_URL, "")  # blank clears a plain field

    def test_apply_form_leaves_blank_secret_fields_untouched(self):
        settings_page.apply_form({"PLEX_TOKEN": ["real-token"]})
        settings_page.apply_form({"PLEX_TOKEN": [""]})  # simulates resubmitting the form untouched
        self.assertEqual(config.PLEX_TOKEN, "real-token")

    def test_apply_form_overwrites_a_secret_when_a_new_value_is_given(self):
        settings_page.apply_form({"PLEX_TOKEN": ["old"]})
        settings_page.apply_form({"PLEX_TOKEN": ["new"]})
        self.assertEqual(config.PLEX_TOKEN, "new")

    def test_missing_fields_in_the_submission_are_left_alone(self):
        settings_page.apply_form({"PLEX_URL": ["http://x:32400"]})
        settings_page.apply_form({})  # e.g. a field the browser didn't send at all
        self.assertEqual(config.PLEX_URL, "http://x:32400")

    def test_render_does_not_leak_secret_values(self):
        settings_page.apply_form({"PLEX_TOKEN": ["super-secret-token"]})
        html = settings_page.render()
        self.assertNotIn("super-secret-token", html)
        self.assertIn("leave blank to keep current", html)

    def test_render_shows_current_plain_values(self):
        settings_page.apply_form({"PLEX_URL": ["http://myplex:32400"]})
        html = settings_page.render()
        self.assertIn("http://myplex:32400", html)

    def test_render_escapes_values(self):
        settings_page.apply_form({"PLEX_URL": ['http://x/"><script>alert(1)</script>']})
        html = settings_page.render()
        self.assertNotIn("<script>", html)

    def test_render_marks_the_selected_history_source(self):
        settings_page.apply_form({"HISTORY_SOURCE": ["tautulli"]})
        html = settings_page.render()
        self.assertIn('value="tautulli" selected', html)

    def test_render_saved_banner_only_when_asked(self):
        self.assertIn("Saved", settings_page.render(saved=True))
        self.assertNotIn("Saved", settings_page.render(saved=False))


if __name__ == "__main__":
    unittest.main()
