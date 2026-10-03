import os
import sys
import tempfile
import unittest
from html import escape
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
import db
import plex
import radarr
import settings_page
import sonarr


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
        settings_page.apply_form("plex", {"PLEX_URL": ["http://x:32400"]})
        self.assertEqual(config.PLEX_URL, "http://x:32400")
        settings_page.apply_form("plex", {"PLEX_URL": [""]})
        self.assertEqual(config.PLEX_URL, "")  # blank clears a plain field

    def test_apply_form_leaves_blank_secret_fields_untouched(self):
        settings_page.apply_form("plex", {"PLEX_TOKEN": ["real-token"]})
        settings_page.apply_form("plex", {"PLEX_TOKEN": [""]})  # simulates resubmitting the form untouched
        self.assertEqual(config.PLEX_TOKEN, "real-token")

    def test_apply_form_overwrites_a_secret_when_a_new_value_is_given(self):
        settings_page.apply_form("plex", {"PLEX_TOKEN": ["old"]})
        settings_page.apply_form("plex", {"PLEX_TOKEN": ["new"]})
        self.assertEqual(config.PLEX_TOKEN, "new")

    def test_missing_fields_in_the_submission_are_left_alone(self):
        settings_page.apply_form("plex", {"PLEX_URL": ["http://x:32400"]})
        settings_page.apply_form("plex", {})  # e.g. a field the browser didn't send at all
        self.assertEqual(config.PLEX_URL, "http://x:32400")

    def test_apply_form_only_touches_fields_belonging_to_that_section(self):
        settings_page.apply_form("tautulli", {"PLEX_URL": ["http://sneaky:1"], "TAUTULLI_URL": ["http://t:8181"]})
        self.assertEqual(config.TAUTULLI_URL, "http://t:8181")
        self.assertNotEqual(config.PLEX_URL, "http://sneaky:1")  # not a tautulli-section field

    def test_apply_form_arr_section_covers_both_radarr_and_sonarr(self):
        settings_page.apply_form("arr", {"RADARR_URL": ["http://r:7878"], "SONARR_URL": ["http://s:8989"]})
        self.assertEqual(config.RADARR_URL, "http://r:7878")
        self.assertEqual(config.SONARR_URL, "http://s:8989")

    def test_render_does_not_leak_secret_values(self):
        settings_page.apply_form("plex", {"PLEX_TOKEN": ["super-secret-token"]})
        html = settings_page.render("plex")
        self.assertNotIn("super-secret-token", html)
        self.assertIn("leave blank to keep current", html)

    def test_render_shows_current_plain_values(self):
        settings_page.apply_form("plex", {"PLEX_URL": ["http://myplex:32400"]})
        html = settings_page.render("plex")
        self.assertIn("http://myplex:32400", html)

    def test_render_escapes_values(self):
        settings_page.apply_form("plex", {"PLEX_URL": ['http://x/"><script>alert(1)</script>']})
        html = settings_page.render("plex")
        self.assertNotIn("<script>", html)

    def test_render_marks_the_selected_history_source(self):
        settings_page.apply_form("tautulli", {"HISTORY_SOURCE": ["tautulli"]})
        html = settings_page.render("tautulli")
        self.assertIn('value="tautulli" selected', html)

    def test_render_saved_banner_only_when_asked(self):
        self.assertIn("Saved", settings_page.render("plex", saved=True))
        self.assertNotIn("Saved", settings_page.render("plex", saved=False))

    def test_render_falls_back_to_default_section_for_an_unknown_one(self):
        html = settings_page.render("not-a-real-section")
        self.assertIn("Plex URL", html)

    def test_render_has_a_subnav_for_every_section(self):
        html = settings_page.render("plex")
        for _, label in settings_page.SECTIONS:
            self.assertIn(escape(label), html)

    def test_each_section_has_its_own_test_connection_button(self):
        for section, _ in settings_page.SECTIONS:
            html = settings_page.render(section)
            if section == "arr":
                self.assertIn('value="test_radarr"', html)
                self.assertIn('value="test_sonarr"', html)
            else:
                self.assertIn(f'value="test_{section}"', html)


class TestSubtabs(unittest.TestCase):
    def test_render_has_appearance_tab_and_unchanged_section_tabs(self):
        html = settings_page.render("plex")
        self.assertIn('href="/appearance">Appearance</a>', html)
        for key, label in settings_page.SECTIONS:
            on = " on" if key == "plex" else ""
            self.assertIn(f'<a class="subtab{on}" href="/settings?section={key}">{escape(label)}</a>', html)
        self.assertEqual(html.count("subtab on"), 1)

    def test_appearance_marks_only_itself(self):
        html = settings_page.subtabs_html("appearance")
        self.assertEqual(html.count("subtab on"), 1)
        self.assertIn('<a class="subtab on" href="/appearance">Appearance</a>', html)
        self.assertTrue(html.startswith('<nav class="subtabs" aria-label="Settings sections">'))
        self.assertTrue(html.index("Appearance") > html.index("Radarr &amp; Sonarr"))

    def test_sections_whitelist_unchanged(self):
        self.assertNotIn("appearance", dict(settings_page.SECTIONS))


class TestArrDropdowns(unittest.TestCase):
    def setUp(self):
        self._old_db = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old_db)
        self.addCleanup(config._apply)

    def test_falls_back_to_text_input_when_radarr_is_not_configured(self):
        html = settings_page.render("arr")
        self.assertIn('name="RADARR_QUALITY_PROFILE_ID" value=""', html)

    def test_shows_a_dropdown_when_radarr_is_reachable(self):
        config.set_setting("RADARR_URL", "http://r:7878")
        config.set_setting("RADARR_API_KEY", "k")
        config.set_setting("RADARR_QUALITY_PROFILE_ID", "7")
        with mock.patch.object(radarr.RadarrClient, "quality_profiles", return_value=[{"id": 4, "name": "SD"}, {"id": 7, "name": "HD-1080p"}]), \
             mock.patch.object(radarr.RadarrClient, "root_folders", return_value=[{"path": "/data/movies"}]):
            html = settings_page.render("arr")
        self.assertIn('<option value="7" selected>HD-1080p</option>', html)
        self.assertIn('<option value="/data/movies"', html)

    def test_falls_back_to_text_input_when_radarr_fetch_fails(self):
        config.set_setting("RADARR_URL", "http://r:7878")
        config.set_setting("RADARR_API_KEY", "k")
        with mock.patch.object(radarr.RadarrClient, "quality_profiles", side_effect=RuntimeError("down")):
            html = settings_page.render("arr")
        self.assertIn('placeholder="connect it below first, then reload this page"', html)

    def test_sonarr_dropdown_independent_of_radarr(self):
        config.set_setting("SONARR_URL", "http://s:8989")
        config.set_setting("SONARR_API_KEY", "k")
        with mock.patch.object(sonarr.SonarrClient, "quality_profiles", return_value=[{"id": 2, "name": "WEB-1080p"}]), \
             mock.patch.object(sonarr.SonarrClient, "root_folders", return_value=[{"path": "/data/tv"}]):
            html = settings_page.render("arr")
        self.assertIn('<option value="2">WEB-1080p</option>', html)
        # Radarr not configured -> still a text input for Radarr specifically
        self.assertIn('name="RADARR_QUALITY_PROFILE_ID" value=""', html)


class TestRunTest(unittest.TestCase):
    def setUp(self):
        self._old_db = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old_db)
        self.addCleanup(config._apply)

    def test_tests_with_the_submitted_value_not_the_saved_one(self):
        config.set_setting("PLEX_TOKEN", "old-saved-token")
        with mock.patch.object(plex, "PlexClient") as MockClient:
            MockClient.return_value.test_connection.return_value = (True, "ok")
            settings_page.run_test("plex", {"PLEX_URL": ["http://new:32400"], "PLEX_TOKEN": ["new-typed-token"]})
        MockClient.assert_called_once_with("http://new:32400", "new-typed-token")

    def test_blank_secret_falls_back_to_the_saved_value(self):
        config.set_setting("PLEX_TOKEN", "already-saved")
        with mock.patch.object(plex, "PlexClient") as MockClient:
            MockClient.return_value.test_connection.return_value = (True, "ok")
            settings_page.run_test("plex", {"PLEX_URL": ["http://x:32400"], "PLEX_TOKEN": [""]})
        MockClient.assert_called_once_with("http://x:32400", "already-saved")

    def test_returns_ok_and_message_on_success(self):
        with mock.patch.object(plex.PlexClient, "test_connection", return_value=(True, "Connected")):
            result = settings_page.run_test("plex", {"PLEX_URL": ["http://x"], "PLEX_TOKEN": ["t"]})
        self.assertEqual(result, {"app": "plex", "ok": True, "message": "Connected"})

    def test_returns_failure_message_without_raising(self):
        with mock.patch.object(plex.PlexClient, "test_connection", return_value=(False, "401 Unauthorized")):
            result = settings_page.run_test("plex", {"PLEX_URL": ["http://x"], "PLEX_TOKEN": ["t"]})
        self.assertEqual(result, {"app": "plex", "ok": False, "message": "401 Unauthorized"})

    def test_radarr_and_sonarr_are_independently_testable(self):
        with mock.patch.object(radarr.RadarrClient, "test_connection", return_value=(True, "Connected to Radarr v5")):
            result = settings_page.run_test("radarr", {"RADARR_URL": ["http://r"], "RADARR_API_KEY": ["k"]})
        self.assertEqual(result["app"], "radarr")
        self.assertTrue(result["ok"])

    def test_ai_is_testable_with_submitted_values(self):
        import ai
        with mock.patch.object(ai, "AiClient") as MockClient:
            MockClient.return_value.test_connection.return_value = (True, "Connected - gpt-4o-mini responded")
            result = settings_page.run_test("ai", {"AI_TOKEN": ["sk-test"], "AI_PROVIDER_URL": ["http://x"],
                                                   "AI_MODEL": ["gpt-4o-mini"]})
        MockClient.assert_called_once_with("sk-test", "http://x", "gpt-4o-mini")
        self.assertEqual(result, {"app": "ai", "ok": True, "message": "Connected - gpt-4o-mini responded"})

    def test_unknown_app_fails_gracefully(self):
        result = settings_page.run_test("not-a-real-app", {})
        self.assertFalse(result["ok"])


class TestFormOverrides(unittest.TestCase):
    def test_only_includes_non_secret_fields_that_were_submitted(self):
        overrides = settings_page.form_overrides("plex", {"PLEX_URL": ["http://x:32400"], "PLEX_TOKEN": ["secret"]})
        self.assertEqual(overrides, {"PLEX_URL": "http://x:32400"})

    def test_ignores_fields_outside_the_section(self):
        overrides = settings_page.form_overrides("plex", {"TAUTULLI_URL": ["http://t"]})
        self.assertEqual(overrides, {})

    def test_render_uses_overrides_instead_of_saved_config(self):
        html = settings_page.render("plex", overrides={"PLEX_URL": "http://not-yet-saved:32400"})
        self.assertIn("http://not-yet-saved:32400", html)


if __name__ == "__main__":
    unittest.main()
