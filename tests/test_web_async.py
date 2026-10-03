"""Background builds, GET /api/status, JSON mode for the POST actions, Undo, and /static/.
sources.run / generate_ai_recommendations are always mocked, and the render_* functions are mocked
wherever only the routing matters, so these don't depend on page markup."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["SAMPLE"] = "1"
import config
import db
import plex
import sources
import web

MOVIE = {"media_type": "movie", "tmdb_id": 1, "title": "M", "year": 2020, "match": 50,
         "reason": "r", "matches": [], "overview": "o", "poster_url": None, "url": None}


def item(tmdb_id, media_type="movie", title=None):
    return dict(MOVIE, tmdb_id=tmdb_id, media_type=media_type, title=title or f"T{tmdb_id}")


def fake_result(items=(), sample=False):
    return {"items": list(items), "sample": sample, "notes": [], "watched_count": 3,
            "profile": {"genre": {}, "keyword": {}, "director": {}, "actor": {}}}


def wait_idle(timeout=5):
    """Waits for any background build / AI generation to finish."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with web._status_lock:
            busy = web._build["pending"] or web._build["running"] or web._ai_state["building"]
        if not busy:
            return
        time.sleep(0.01)
    raise AssertionError("background work didn't finish")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


class AsyncCase(unittest.TestCase):
    """Fresh DB, fresh in-memory state and its own server for every test."""

    def setUp(self):
        wait_idle()
        self._old_db = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.addCleanup(setattr, db, "DB_PATH", self._old_db)
        saved = (dict(web._state), dict(web._ai_state), dict(web._build), list(web._forgotten),
                 list(web._actions))
        self.addCleanup(self._restore, saved)
        web._state.update(time=0.0, result=None)
        web._ai_state.update(time=0.0, result=None, building=False, error=None)
        web._build.update(pending=False, running=False, started=None, error=None, failed_at=0.0, ratings_changed=False)
        web._forgotten[:] = []
        web._actions[:] = []
        self.server = web.make_server("127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    @staticmethod
    def _restore(saved):
        wait_idle()
        web._state.clear(), web._state.update(saved[0])
        web._ai_state.clear(), web._ai_state.update(saved[1])
        web._build.clear(), web._build.update(saved[2])
        web._forgotten[:] = saved[3]
        web._actions[:] = saved[4]

    def live(self):
        """Pretend the app is configured (not sample mode) for this test."""
        patch = mock.patch.object(sources, "use_sample", return_value=False)
        patch.start()
        self.addCleanup(patch.stop)

    def request(self, method, path, data=None, as_json=False, extra_headers=None):
        """Returns (status, headers, body) without following redirects."""
        headers = {"Accept": "application/json"} if as_json else {}
        headers.update(extra_headers or {})
        req = urllib.request.Request(self.base + path, data=data.encode() if data is not None else None,
                                     method=method, headers=headers)
        try:
            resp = urllib.request.build_opener(NoRedirect).open(req, timeout=5)
            return resp.status, resp.headers, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read().decode()

    def post_json(self, path, data=""):
        status, headers, body = self.request("POST", path, data, as_json=True)
        self.assertEqual(headers.get("Content-Type"), "application/json")
        return status, json.loads(body)

    def location(self, path, data=""):
        status, headers, _ = self.request("POST", path, data)
        self.assertEqual(status, 303)
        return headers.get("Location")


class Gate:
    """A stand-in for sources.run that blocks until released, and counts its calls."""

    def __init__(self, result=None, error=None):
        self.release, self.entered = threading.Event(), threading.Event()
        self.calls, self.result, self.error = 0, result, error

    def __call__(self, *args, **kwargs):
        self.calls += 1
        self.entered.set()
        self.release.wait(5)
        if self.error:
            raise self.error
        return self.result if self.result is not None else fake_result([item(1)])


class TestBackgroundBuild(AsyncCase):
    def test_status_goes_building_then_ready(self):
        gate = Gate()
        with mock.patch.object(sources, "run", gate):
            self.assertEqual(web.get_result_nowait(), (None, None))
            self.assertTrue(gate.entered.wait(2))
            status = web.build_status()
            self.assertEqual(status["state"], "building")
            self.assertFalse(status["has_result"])
            self.assertIsNotNone(status["started"])
            self.assertIsNone(status["updated"])
            gate.release.set()
            wait_idle()
        status = web.build_status()
        self.assertEqual((status["state"], status["has_result"], status["error"]), ("ready", True, None))
        self.assertIsNotNone(status["updated"])
        result, age = web.get_result_nowait()
        self.assertEqual([i["tmdb_id"] for i in result["items"]], [1])
        self.assertLess(age, 5)

    def test_api_status_answers_instantly_mid_build_and_never_starts_a_second_build(self):
        gate = Gate()
        with mock.patch.object(sources, "run", gate):
            web.get_result_nowait()
            self.assertTrue(gate.entered.wait(2))
            for _ in range(5):
                started = time.time()
                status, headers, body = self.request("GET", "/api/status")
                self.assertLess(time.time() - started, 1)  # compute_lock is held right now
                self.assertEqual(status, 200)
                self.assertEqual(headers.get("Content-Type"), "application/json")
                self.assertEqual(json.loads(body)["state"], "building")
                self.assertEqual(web.get_result_nowait(), (None, None))
            gate.release.set()
            wait_idle()
        self.assertEqual(gate.calls, 1)

    def test_api_status_shape(self):
        with mock.patch.object(sources, "run", return_value=fake_result()):
            web.get_result()
            _, _, body = self.request("GET", "/api/status")
        status = json.loads(body)
        self.assertEqual(set(status), {"state", "has_result", "started", "updated", "error", "ai"})
        self.assertEqual(status["ai"], {"state": "idle", "error": None})

    def test_blocking_get_result_alongside_a_background_build_builds_once(self):
        gate = Gate()
        with mock.patch.object(sources, "run", gate):
            web.get_result_nowait()
            self.assertTrue(gate.entered.wait(2))
            out = []
            t = threading.Thread(target=lambda: out.append(web.get_result()))
            t.start()
            gate.release.set()
            t.join(5)
            wait_idle()
        self.assertEqual(gate.calls, 1)
        self.assertEqual(out[0][0]["items"][0]["tmdb_id"], 1)

    def test_stale_result_is_returned_while_a_rebuild_runs(self):
        web._state.update(result=fake_result([item(7)]), time=time.time() - web.CACHE_SECONDS - 5)
        gate = Gate()
        with mock.patch.object(sources, "run", gate):
            result, age = web.get_result_nowait()
            self.assertEqual(result["items"][0]["tmdb_id"], 7)
            self.assertGreater(age, web.CACHE_SECONDS)
            self.assertTrue(gate.entered.wait(2))
            self.assertEqual(web.build_status()["state"], "building")
            self.assertTrue(web.build_status()["has_result"])
            gate.release.set()
            wait_idle()
        self.assertEqual(web.get_result_nowait()[0]["items"][0]["tmdb_id"], 1)

    def test_fresh_result_starts_no_build(self):
        web._state.update(result=fake_result([item(7)]), time=time.time())
        with mock.patch.object(sources, "run") as run:
            web.get_result_nowait()
            wait_idle()
        run.assert_not_called()

    def test_failed_build_records_the_error_and_is_not_stuck_building(self):
        with mock.patch.object(sources, "run", side_effect=RuntimeError("Plex is down")) as run:
            self.assertEqual(web.get_result_nowait(), (None, None))
            wait_idle()
            status = web.build_status()
            self.assertEqual((status["state"], status["has_result"], status["error"]), ("error", False, "Plex is down"))
            # within ERROR_RETRY_SECONDS page loads show the error rather than retrying
            web.get_result_nowait()
            self.request("GET", "/api/status")
            wait_idle()
            self.assertEqual(run.call_count, 1)
            with mock.patch.object(web, "ERROR_RETRY_SECONDS", 0):
                web.get_result_nowait()
                wait_idle()
            self.assertEqual(run.call_count, 2)

    def test_success_after_failure_clears_the_error(self):
        with mock.patch.object(sources, "run", side_effect=RuntimeError("boom")):
            web.get_result_nowait()
            wait_idle()
        with mock.patch.object(sources, "run", return_value=fake_result()):
            web.get_result()
        self.assertEqual((web.build_status()["state"], web.build_status()["error"]), ("ready", None))

    def test_blocking_get_result_still_raises_and_records_the_error(self):
        with mock.patch.object(sources, "run", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                web.get_result()
        self.assertEqual(web.build_status()["state"], "error")

    def test_error_with_an_old_result_still_counts_as_ready(self):
        web._state.update(result=fake_result([item(7)]), time=time.time() - web.CACHE_SECONDS - 5)
        with mock.patch.object(sources, "run", side_effect=RuntimeError("boom")):
            web.get_result_nowait()
            wait_idle()
        status = web.build_status()
        self.assertEqual((status["state"], status["has_result"], status["error"]), ("ready", True, "boom"))

    def test_settings_change_mid_build_discards_the_old_result_and_rebuilds(self):
        gate = Gate(result=fake_result([item(1)]))
        with mock.patch.object(sources, "run", gate):
            web.get_result_nowait()
            self.assertTrue(gate.entered.wait(2))
            web.invalidate_cache()  # must not wait for the build
            gate.result = fake_result([item(2)])
            gate.release.set()
            wait_idle()
        self.assertEqual(gate.calls, 2)
        self.assertEqual(web._state["result"]["items"][0]["tmdb_id"], 2)

    def test_titles_dismissed_mid_build_dont_reappear(self):
        self.live()
        gate = Gate(result=fake_result([item(1), item(2)]))
        with mock.patch.object(sources, "run", gate):
            web.get_result_nowait()
            self.assertTrue(gate.entered.wait(2))
            db.dismiss("movie", 1)
            gate.release.set()
            wait_idle()
        self.assertEqual([i["tmdb_id"] for i in web._state["result"]["items"]], [2])


class TestRefresh(AsyncCase):
    def test_refresh_json_starts_a_background_build(self):
        web._state.update(result=fake_result([item(7)]), time=time.time() - web.REFRESH_MIN_SECONDS - 5)
        gate = Gate()
        with mock.patch.object(sources, "run", gate):
            status, body = self.post_json("/refresh", "return_to=/recommended")
            self.assertEqual((status, body), (200, {"ok": True, "started": True, "message": "Refreshing..."}))
            self.assertTrue(gate.entered.wait(2))
            # a second click while it's running doesn't start another
            _, body = self.post_json("/refresh")
            self.assertEqual(body, {"ok": True, "started": False, "message": "Refreshing..."})
            gate.release.set()
            wait_idle()
        self.assertEqual(gate.calls, 1)

    def test_refresh_json_respects_refresh_min_seconds(self):
        web._state.update(result=fake_result([item(7)]), time=time.time())
        with mock.patch.object(sources, "run") as run:
            _, body = self.post_json("/refresh")
            wait_idle()
        self.assertEqual(body, {"ok": True, "started": False, "message": "Already up to date"})
        run.assert_not_called()

    def test_refresh_without_js_still_redirects_straight_back(self):
        web._state.update(result=fake_result([item(7)]), time=time.time() - web.REFRESH_MIN_SECONDS - 5)
        with mock.patch.object(sources, "run", return_value=fake_result()):
            self.assertEqual(self.location("/refresh", "return_to=%2Frecommended%3Ftype%3Dtv"), "/recommended?type=tv")
            self.assertEqual(self.location("/refresh", "return_to=https%3A%2F%2Fevil.example%2F"), "/recommended")
            wait_idle()


class TestDismissAndUndo(AsyncCase):
    def setUp(self):
        super().setUp()
        self.live()
        web._state.update(result=fake_result([item(1), item(2, title="Heat"), item(3)]), time=time.time())

    def ids(self, state=None):
        return [i["tmdb_id"] for i in (state or web._state)["result"]["items"]]

    def test_dismiss_json(self):
        status, body = self.post_json("/dismiss", "type=movie&id=2&return_to=/recommended")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "message": 'Hidden "Heat"', "undo": {"type": "movie", "id": 2}})
        self.assertIn(("movie", 2), db.dismissed())
        self.assertEqual(self.ids(), [1, 3])

    def test_undismiss_json_restores_the_item_in_its_old_place(self):
        self.post_json("/dismiss", "type=movie&id=2")
        status, body = self.post_json("/undismiss", "type=movie&id=2&return_to=/recommended")
        self.assertEqual((status, body), (200, {"ok": True, "message": 'Restored "Heat"'}))
        self.assertNotIn(("movie", 2), db.dismissed())
        self.assertEqual(self.ids(), [1, 2, 3])
        self.assertEqual(web._forgotten, [])

    def test_undismiss_restores_into_the_ai_cache_it_came_from(self):
        web._ai_state.update(result=fake_result([item(8), item(9, title="Silo")]), time=time.time())
        self.post_json("/dismiss", "type=movie&id=9&return_to=/ai")
        _, body = self.post_json("/undismiss", "type=movie&id=9&return_to=/ai")
        self.assertEqual(body["message"], 'Restored "Silo"')
        self.assertEqual(self.ids(web._ai_state), [8, 9])
        self.assertEqual(self.ids(), [1, 2, 3])  # not copied into the main list

    def test_double_undo_doesnt_duplicate(self):
        self.post_json("/dismiss", "type=movie&id=2")
        self.post_json("/undismiss", "type=movie&id=2")
        _, body = self.post_json("/undismiss", "type=movie&id=2")
        self.assertTrue(body["ok"])
        self.assertEqual(self.ids(), [1, 2, 3])

    def test_undismiss_of_something_not_stashed_still_undismisses(self):
        db.dismiss("tv", 50)
        _, body = self.post_json("/undismiss", "type=tv&id=50")
        self.assertTrue(body["ok"])
        self.assertIn("next refresh", body["message"])
        self.assertNotIn(("tv", 50), db.dismissed())

    def test_stash_is_capped(self):
        web._state["result"] = fake_result([item(i) for i in range(1, 80)])
        for i in range(1, 80):
            web.forget("movie", i)
        self.assertEqual(len(web._forgotten), web.FORGET_STASH)
        self.assertIsNone(web.restore("movie", 1))       # oldest fell off
        self.assertEqual(web.restore("movie", 79)["tmdb_id"], 79)

    def test_bad_input_is_a_400_in_json_mode(self):
        for path in ("/dismiss", "/undismiss", "/add"):
            for data in ("type=movie&id=abc", "type=book&id=1", "id=1", "type=movie", "type=movie&id=%C2%B2",
                         "type=movie&id=99999999999999999999", "type=movie&id=-1"):
                status, body = self.post_json(path, data)
                self.assertEqual(status, 400, (path, data))
                self.assertFalse(body["ok"])
                self.assertTrue(body["message"])
        self.assertEqual(db.dismissed(), set())

    def test_non_js_dismiss_redirects_back_with_undo_params(self):
        self.assertEqual(self.location("/dismiss", "type=movie&id=2&return_to=%2Frecommended%3Ftype%3Dall"),
                         "/recommended?type=all&undo_type=movie&undo_id=2")
        self.assertEqual(self.location("/dismiss", "type=movie&id=3&return_to=%2Fai"),
                         "/ai?undo_type=movie&undo_id=3")
        self.assertEqual(db.dismissed(), {("movie", 2), ("movie", 3)})

    def test_non_js_dismiss_replaces_stale_undo_params(self):
        loc = self.location("/dismiss", "type=movie&id=2&return_to="
                            + urllib.parse.quote("/recommended?type=tv&undo_type=movie&undo_id=1"))
        self.assertEqual(loc, "/recommended?type=tv&undo_type=movie&undo_id=2")

    def test_non_js_dismiss_never_redirects_off_site(self):
        self.assertEqual(self.location("/dismiss", "type=movie&id=2&return_to=https%3A%2F%2Fevil.example%2F"),
                         "/recommended?undo_type=movie&undo_id=2")

    def test_non_js_bad_input_redirects_without_undo_like_before(self):
        self.assertEqual(self.location("/dismiss", "type=movie&id=abc&return_to=%2Fai"), "/ai")
        self.assertEqual(db.dismissed(), set())

    def test_non_js_undismiss_redirects_back(self):
        self.location("/dismiss", "type=movie&id=2")
        self.assertEqual(self.location("/undismiss", "type=movie&id=2&return_to=%2Frecommended%3Ftype%3Dmovie"),
                         "/recommended?type=movie")
        self.assertEqual(self.ids(), [1, 2, 3])

    def test_dismissing_mid_build_doesnt_wait_for_the_build(self):
        gate = Gate()
        web._state.update(time=time.time() - web.CACHE_SECONDS - 5)
        with mock.patch.object(sources, "run", gate):
            web.get_result_nowait()
            self.assertTrue(gate.entered.wait(2))
            started = time.time()
            status, _ = self.post_json("/dismiss", "type=movie&id=2")
            self.assertLess(time.time() - started, 1)
            self.assertEqual(status, 200)
            gate.release.set()
            wait_idle()


class TestSampleModeGuards(AsyncCase):
    """SAMPLE=1 is set for the whole test run, so these are in sample mode without patching."""

    def setUp(self):
        super().setUp()
        web._state.update(result=fake_result([item(1)], sample=True), time=time.time())

    def test_dismiss_undismiss_add_are_refused_in_json_mode(self):
        with mock.patch.object(sources, "add_to_library") as add:
            for path in ("/dismiss", "/undismiss", "/add"):
                status, body = self.post_json(path, "type=movie&id=1&return_to=/recommended")
                self.assertEqual((status, body), (200, {"ok": False, "message": "Sample data - not saved"}), path)
        add.assert_not_called()
        self.assertEqual(db.dismissed(), set())
        self.assertEqual(len(web._state["result"]["items"]), 1)

    def test_generate_is_refused_in_json_mode(self):
        with mock.patch.object(sources, "generate_ai_recommendations") as gen:
            _, body = self.post_json("/ai/generate", "return_to=/ai")
            wait_idle()
        self.assertFalse(body["ok"])
        self.assertFalse(body["started"])
        gen.assert_not_called()

    def test_non_js_sample_dismiss_redirects_without_undo(self):
        self.assertEqual(self.location("/dismiss", "type=movie&id=1&return_to=%2Frecommended%3Ftype%3Dall"),
                         "/recommended?type=all")
        self.assertEqual(db.dismissed(), set())


class TestAddJson(AsyncCase):
    def setUp(self):
        super().setUp()
        self.live()
        web._state.update(result=fake_result([item(1, title="M")]), time=time.time())

    def test_success(self):
        with mock.patch.object(sources, "add_to_library", return_value=(True, 'Added "M" to Radarr')) as add:
            status, body = self.post_json("/add", "type=movie&id=1&search=1&quality_profile_id=4&return_to=/recommended")
        self.assertEqual((status, body), (200, {"ok": True, "message": 'Added "M" to Radarr'}))
        add.assert_called_once_with("movie", 1, search=True, quality_profile_id=4)
        self.assertEqual(len(db.added_items()), 1)
        self.assertEqual(web._state["result"]["items"], [])

    def test_failure(self):
        with mock.patch.object(sources, "add_to_library", return_value=(False, "Radarr isn't configured")):
            _, body = self.post_json("/add", "type=movie&id=1")
        self.assertEqual(body, {"ok": False, "message": "Radarr isn't configured"})
        self.assertEqual(db.added_items(), [])

    def test_exception_becomes_a_message_not_a_500(self):
        with mock.patch.object(sources, "add_to_library", side_effect=OSError("timed out")):
            status, body = self.post_json("/add", "type=movie&id=1")
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertIn("timed out", body["message"])

    def test_non_js_add_still_redirects_with_msg(self):
        with mock.patch.object(sources, "add_to_library", return_value=(True, "Added")):
            self.assertEqual(self.location("/add", "type=movie&id=1&return_to=%2Fai"), "/ai?msg=Added")


class TestAiGenerate(AsyncCase):
    def setUp(self):
        super().setUp()
        self.live()
        old = config.AI_TOKEN
        self.addCleanup(setattr, config, "AI_TOKEN", old)
        config.AI_TOKEN = "sk-x"

    def test_generate_json_runs_in_the_background_once(self):
        gate = Gate(result=fake_result([item(5)]))
        with mock.patch.object(sources, "generate_ai_recommendations", gate):
            status, body = self.post_json("/ai/generate", "return_to=/ai")
            self.assertEqual((status, body["ok"], body["started"]), (200, True, True))
            self.assertTrue(gate.entered.wait(2))
            self.assertEqual(web.build_status()["ai"], {"state": "building", "error": None})
            _, body = self.post_json("/ai/generate")
            self.assertEqual((body["ok"], body["started"]), (True, False))
            gate.release.set()
            wait_idle()
        self.assertEqual(gate.calls, 1)
        self.assertEqual(web.build_status()["ai"], {"state": "ready", "error": None})
        self.assertEqual(web._ai_state["result"]["items"][0]["tmdb_id"], 5)

    def test_generate_error_is_recorded(self):
        with mock.patch.object(sources, "generate_ai_recommendations", side_effect=RuntimeError("401 from provider")):
            self.post_json("/ai/generate")
            wait_idle()
        self.assertEqual(web.build_status()["ai"], {"state": "error", "error": "401 from provider"})
        self.assertFalse(web._ai_state["building"])

    def test_generate_json_when_ai_isnt_configured(self):
        config.AI_TOKEN = ""
        with mock.patch.object(sources, "generate_ai_recommendations") as gen:
            _, body = self.post_json("/ai/generate")
        self.assertEqual((body["ok"], body["started"]), (False, False))
        gen.assert_not_called()

    def test_generate_without_js_redirects_straight_back(self):
        with mock.patch.object(sources, "generate_ai_recommendations", return_value=fake_result()):
            self.assertEqual(self.location("/ai/generate", "return_to=%2Fai"), "/ai")
            wait_idle()


class TestStatic(AsyncCase):
    def test_whitelisted_files(self):
        for name, content_type in (("app.css", "text/css; charset=utf-8"), ("app.js", "text/javascript; charset=utf-8")):
            status, headers, body = self.request("GET", f"/static/{name}")
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("Content-Type"), content_type)
            self.assertEqual(headers.get("Cache-Control"), "no-cache")
            with open(os.path.join(os.path.dirname(web.__file__), "static", name), encoding="utf-8") as f:
                self.assertEqual(body, f.read())

    def test_everything_else_is_a_404(self):
        for path in ("/static/", "/static/web.py", "/static/../web.py", "/static/%2e%2e/web.py",
                     "/static/..%2Fweb.py", "/static//etc/passwd", "/static/app.css/", "/static/APP.CSS",
                     "/static/app.css%00.py", "/static/./app.css", "/static", "/static/app.js?x=../../web.py/x"):
            status, _, body = self.request("GET", path)
            if path == "/static/app.js?x=../../web.py/x":  # the query string is ignored, not a path
                self.assertEqual(status, 200)
                continue
            self.assertEqual(status, 404, path)
            self.assertNotIn("import", body)

    def test_missing_file_is_a_404_not_a_500(self):
        with mock.patch.object(web.Handler, "STATIC_DIR", tempfile.mkdtemp()):
            self.assertEqual(self.request("GET", "/static/app.js")[0], 404)


class TestGetPassesPageParams(AsyncCase):
    """Only the routing: what do_GET hands the (frontend-owned) render functions."""

    def test_browse_pages_get_msg_and_undo(self):
        with mock.patch.object(web, "render_home", return_value="page") as home, \
                mock.patch.object(web, "render_browse", return_value="page") as browse:
            self.request("GET", "/?msg=Hi&undo_type=movie&undo_id=12")
            home.assert_called_once_with(msg="Hi", undo=("movie", 12))
            self.request("GET", "/movies?msg=Hi&undo_type=tv&undo_id=3")
            browse.assert_called_once_with("movie", msg="Hi", undo=("tv", 3))
            browse.reset_mock()
            self.request("GET", "/tv?msg=Yo&undo_type=movie&undo_id=4")
            browse.assert_called_once_with("tv", msg="Yo", undo=("movie", 4))

    def test_invalid_undo_is_none(self):
        with mock.patch.object(web, "render_home", return_value="page") as home, \
                mock.patch.object(web, "render_browse", return_value="page") as browse:
            for query in ("", "&undo_type=book&undo_id=1", "&undo_type=movie&undo_id=x", "&undo_type=movie"):
                for path, render, args in (("/", home, ()), ("/movies", browse, ("movie",)),
                                           ("/tv", browse, ("tv",))):
                    render.reset_mock()
                    self.request("GET", path + "?x=1" + query)
                    render.assert_called_once_with(*args, msg="", undo=None)

    def _recommended_location(self, query):
        opener = urllib.request.build_opener(NoRedirect)
        try:
            resp = opener.open(self.base + "/recommended" + query)
            return resp.status, resp.headers.get("Location")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location")

    def test_recommended_redirects_per_type(self):
        self.assertEqual(self._recommended_location("?type=movie&msg=Hi&undo_type=movie&undo_id=5"),
                         (303, "/movies?msg=Hi&undo_type=movie&undo_id=5"))
        self.assertEqual(self._recommended_location("?type=tv"), (303, "/tv"))
        for query in ("?type=new", "?type=all", "?type=%3Cscript%3E", ""):
            self.assertEqual(self._recommended_location(query), (303, "/"), query)

    def test_recommended_redirect_drops_invalid_undo_and_keeps_crlf_encoded(self):
        self.assertEqual(self._recommended_location("?type=tv&msg=Hi&undo_type=tv&undo_id=x"), (303, "/tv?msg=Hi"))
        _, location = self._recommended_location("?msg=a%0d%0aSet-Cookie:%20x")
        self.assertTrue(location.startswith("/?msg="))
        self.assertNotIn("\r", location)
        self.assertNotIn("\n", location)
        self.assertIn("%0D%0A", location)

    def test_ai_page_gets_msg_and_undo(self):
        with mock.patch.object(web, "render_ai_page", return_value="page") as render:
            self.request("GET", "/ai?msg=Added&undo_type=tv&undo_id=3")
            render.assert_called_once_with(msg="Added", undo=("tv", 3))
            render.reset_mock()
            self.request("GET", "/ai")
            render.assert_called_once_with(msg="", undo=None)

    def test_library_gets_parsed_params_and_msg(self):
        with mock.patch.object(web, "render_library", return_value="page") as render:
            self.request("GET", "/library?type=watched&q=%20dune%20&sort=rating&show=rated&page=3&msg=Hi")
            render.assert_called_once_with(tab="watched", q="dune", sort="rating", show="rated", page=3, msg="Hi")
            render.reset_mock()
            self.request("GET", "/library")
            render.assert_called_once_with(tab="all", q="", sort="added", show="all", page=1, msg="")
            render.reset_mock()
            self.request("GET", "/library?type=%3Cscript%3E&sort=zzz&show=zzz&page=-1")
            render.assert_called_once_with(tab="all", q="", sort="added", show="all", page=1, msg="")

    def test_watched_route_stays_404(self):
        self.assertEqual(self.request("GET", "/watched")[0], 404)

    def test_add_dialog_gets_partial(self):
        with mock.patch.object(web, "render_add_dialog", return_value="page") as render:
            self.request("GET", "/add-dialog?type=movie&id=4&return_to=/ai&partial=1")
            render.assert_called_once_with("movie", 4, "/ai", partial=True)
            render.reset_mock()
            self.request("GET", "/add-dialog?type=movie&id=4")
            render.assert_called_once_with("movie", 4, "/recommended", partial=False)
            render.reset_mock()
            self.assertEqual(self.request("GET", "/add-dialog?type=movie&id=%C2%B2")[0], 404)
            render.assert_not_called()


def watched_item(tmdb_id=329865, title="Arrival", media_type="movie", user_rating=9.0, poster_key=None):
    return {"media_type": media_type, "tmdb_id": tmdb_id, "title": title, "year": 2016, "last_viewed": None,
            "user_rating": user_rating, "view_count": 1, "progress": None, "poster_key": poster_key,
            "poster_url": None, "url": None}


class TestRate(AsyncCase):
    def setUp(self):
        super().setUp()
        self.live()
        web._state.update(result=dict(fake_result([item(1)]), watched=[watched_item()]), time=time.time())

    def test_json_set_and_clear(self):
        status, body = self.post_json("/rate", "type=movie&id=329865&stars=4&return_to=/library")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "message": 'Rated "Arrival" 4/5',
                                "rating": {"type": "movie", "id": 329865, "stars": 4, "plex_stars": 5}})
        self.assertEqual(db.ratings(), {("movie", 329865): 4})
        status, body = self.post_json("/rate", "type=movie&id=329865&stars=0")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "message": 'Cleared your rating for "Arrival"',
                                "rating": {"type": "movie", "id": 329865, "stars": None, "plex_stars": 5}})
        self.assertEqual(db.ratings(), {})

    def test_title_not_in_the_snapshot(self):
        _, body = self.post_json("/rate", "type=tv&id=5&stars=2")
        self.assertEqual(body, {"ok": True, "message": "Rated 2/5",
                                "rating": {"type": "tv", "id": 5, "stars": 2, "plex_stars": None}})
        _, body = self.post_json("/rate", "type=tv&id=5&stars=0")
        self.assertEqual(body["message"], "Rating cleared")
        self.assertEqual(db.ratings(), {})

    def test_title_in_the_message_comes_from_the_snapshot_not_the_form(self):
        _, body = self.post_json("/rate", "type=movie&id=329865&stars=3&title=Hacked")
        self.assertEqual(body["message"], 'Rated "Arrival" 3/5')

    def test_changing_a_rating_replaces_it(self):
        self.post_json("/rate", "type=movie&id=329865&stars=2")
        self.post_json("/rate", "type=movie&id=329865&stars=5")
        self.assertEqual(db.ratings(), {("movie", 329865): 5})

    def test_bad_title_is_400(self):
        for data in ("type=movie&id=abc&stars=3", "type=book&id=1&stars=3", "stars=3", "type=movie&id=%C2%B2&stars=3",
                     "type=movie&id=" + "9" * 13 + "&stars=3"):
            status, body = self.post_json("/rate", data)
            self.assertEqual((status, body), (400, {"ok": False, "message": "That isn't a valid title"}), data)
        self.assertEqual(db.ratings(), {})

    def test_bad_stars_is_400(self):
        for stars in ("&stars=6", "&stars=x", "", "&stars=", "&stars=-1", "&stars=3.5", "&stars=%C2%B2", "&stars=10", "&stars=+3"):
            status, body = self.post_json("/rate", "type=movie&id=1" + stars)
            self.assertEqual((status, body), (400, {"ok": False, "message": "Pick a rating from 1 to 5"}), stars)
        self.assertEqual(db.ratings(), {})
        self.assertFalse(web.ratings_changed())

    def test_no_js_redirects_with_the_message(self):
        loc = self.location("/rate", "type=movie&id=329865&stars=4&return_to=%2Flibrary%3Ftype%3Dwatched%26page%3D2")
        self.assertEqual(loc, "/library?type=watched&page=2&msg=Rated+%22Arrival%22+4%2F5")
        self.assertEqual(db.ratings(), {("movie", 329865): 4})

    def test_no_js_default_and_offsite_return_to(self):
        self.assertTrue(self.location("/rate", "type=movie&id=329865&stars=4").startswith("/library?type=watched&msg="))
        self.assertTrue(self.location("/rate", "type=movie&id=1&stars=4&return_to=https%3A%2F%2Fevil.example%2F")
                        .startswith("/recommended?msg="))
        self.assertTrue(self.location("/rate", "type=movie&id=1&stars=4&return_to=%2F%2Fevil.example")
                        .startswith("/recommended?msg="))

    def test_no_js_bad_input_goes_back_with_no_msg(self):
        self.assertEqual(self.location("/rate", "type=movie&id=1&stars=9&return_to=%2Flibrary%3Ftype%3Dwatched"),
                         "/library?type=watched")
        self.assertEqual(self.location("/rate", "type=x&id=1&stars=3"), "/library?type=watched")
        self.assertEqual(db.ratings(), {})

    def test_sample_mode_refuses_without_writing_in_both_modes(self):
        patch = mock.patch.object(sources, "use_sample", return_value=True)
        patch.start()
        self.addCleanup(patch.stop)
        status, body = self.post_json("/rate", "type=movie&id=1001&stars=4")
        self.assertEqual((status, body), (200, {"ok": False, "message": web.SAMPLE_MESSAGE}))
        loc = self.location("/rate", "type=movie&id=1001&stars=4")
        self.assertEqual(loc, "/library?type=watched&msg=Sample+data+-+not+saved")
        self.assertEqual(db.ratings(), {})
        self.assertFalse(web.ratings_changed())

    def test_rating_sets_the_flag_and_refresh_then_skips_the_age_guard(self):
        self.assertFalse(web.ratings_changed())
        with mock.patch.object(sources, "run") as run:
            _, body = self.post_json("/refresh")  # data is fresh: nothing to do
            self.assertEqual(body["message"], "Already up to date")
            run.assert_not_called()
            self.post_json("/rate", "type=movie&id=329865&stars=5")
            self.assertTrue(web.ratings_changed())
            run.return_value = fake_result([item(2)])
            _, body = self.post_json("/refresh")
            self.assertEqual((body["ok"], body["started"], body["message"]), (True, True, "Refreshing..."))
            wait_idle()
        self.assertEqual(run.call_count, 1)
        self.assertFalse(web.ratings_changed())  # cleared by the build that picked it up

    def test_clearing_a_rating_also_sets_the_flag(self):
        self.post_json("/rate", "type=movie&id=329865&stars=0")
        self.assertTrue(web.ratings_changed())

    def test_a_failed_build_keeps_the_flag(self):
        web.mark_ratings_changed()
        web._state.update(time=time.time())
        with mock.patch.object(sources, "run", side_effect=RuntimeError("boom")):
            self.assertTrue(web._start_build(refresh=True))
            wait_idle()
        self.assertTrue(web.ratings_changed())
        self.assertEqual(web.build_status()["error"], "boom")

    def test_flag_survives_a_discarded_attempt_followed_by_a_failed_one(self):
        web.mark_ratings_changed()
        web._state.update(time=time.time())
        calls = []

        def run(sample):
            calls.append(1)
            if len(calls) == 1:
                web.invalidate_cache()  # settings changed mid-build: this result gets discarded
                return fake_result([item(2)])
            raise RuntimeError("boom")

        with mock.patch.object(sources, "run", side_effect=run):
            self.assertTrue(web._start_build(refresh=True))
            wait_idle()
        self.assertEqual(len(calls), 2)
        self.assertTrue(web.ratings_changed())

    def test_a_rating_made_mid_build_survives_the_build(self):
        web.mark_ratings_changed()
        gate = Gate()
        with mock.patch.object(sources, "run", gate):
            web._start_build(refresh=True)
            self.assertTrue(gate.entered.wait(2))
            self.assertFalse(web.ratings_changed())  # cleared when the job started
            self.post_json("/rate", "type=movie&id=329865&stars=1")
            gate.release.set()
            wait_idle()
        self.assertTrue(web.ratings_changed())

    def test_ratings_never_touch_plex(self):
        with mock.patch.object(plex.PlexClient, "_get", side_effect=AssertionError("Plex")), \
             mock.patch("http_util.urllib.request.urlopen", side_effect=AssertionError("network")):
            self.post_json("/rate", "type=movie&id=329865&stars=5")
            self.post_json("/rate", "type=movie&id=329865&stars=0")

    def test_status_shape_is_unchanged(self):
        self.assertEqual(set(web.build_status()), {"state", "has_result", "started", "updated", "error", "ai"})


class TestPoster(AsyncCase):
    THUMB = "/library/metadata/10/thumb/1"

    def setUp(self):
        super().setUp()
        self.live()
        old = config.PLEX_TOKEN
        self.addCleanup(setattr, config, "PLEX_TOKEN", old)
        config.PLEX_TOKEN = "SECRET-TOKEN"
        web._state.update(result=dict(fake_result(), thumbs={10: self.THUMB}), time=time.time())

    def fetch(self, path="/poster?key=10"):
        req = urllib.request.Request(self.base + path)
        try:
            resp = urllib.request.build_opener(NoRedirect).open(req, timeout=5)
            return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_serves_the_image_with_cache_header(self):
        for content_type in ("image/jpeg", "image/png", "image/webp"):
            with mock.patch.object(plex.PlexClient, "poster", return_value=(content_type, b"BYTES")) as poster:
                status, headers, body = self.fetch()
            self.assertEqual((status, body), (200, b"BYTES"))
            self.assertEqual(headers["Content-Type"], content_type)
            self.assertEqual(headers["Cache-Control"], "private, max-age=86400")
            poster.assert_called_once_with(self.THUMB)
            self.assertNotIn("SECRET-TOKEN", str(headers))

    def test_404s(self):
        cases = {"bad key": "/poster?key=abc", "no key": "/poster", "unknown key": "/poster?key=11",
                 "unicode digit": "/poster?key=%C2%B2", "huge": "/poster?key=" + "1" * 13}
        for name, path in cases.items():
            with mock.patch.object(plex.PlexClient, "poster", return_value=("image/jpeg", b"x")) as poster:
                status, headers, body = self.fetch(path)
            self.assertEqual((status, body), (404, b"Not found"), name)
            poster.assert_not_called()

    def test_non_image_and_errors_are_404(self):
        for kind in (("text/html", b"<html>"), ("image/svg+xml", b"<svg>"), ("", b"x")):
            with mock.patch.object(plex.PlexClient, "poster", return_value=kind):
                status, _, body = self.fetch()
            self.assertEqual((status, body), (404, b"Not found"), kind)
        with mock.patch.object(plex.PlexClient, "poster", side_effect=RuntimeError("HTTP 500 http://plex/?X-Plex-Token=SECRET-TOKEN")):
            status, _, body = self.fetch()
        self.assertEqual((status, body), (404, b"Not found"))
        self.assertNotIn(b"SECRET", body)

    def test_sample_mode_and_missing_token_are_404(self):
        with mock.patch.object(plex.PlexClient, "poster", return_value=("image/jpeg", b"x")) as poster:
            config.PLEX_TOKEN = ""
            self.assertEqual(self.fetch()[0], 404)
            config.PLEX_TOKEN = "SECRET-TOKEN"
            with mock.patch.object(sources, "use_sample", return_value=True):
                self.assertEqual(self.fetch()[0], 404)
        poster.assert_not_called()

    def test_no_build_yet_is_404(self):
        web._state.update(result=None, time=0.0)
        with mock.patch.object(plex.PlexClient, "poster") as poster:
            self.assertEqual(self.fetch()[0], 404)
        poster.assert_not_called()

    def test_plex_thumb_helper(self):
        self.assertEqual(web.plex_thumb(10), self.THUMB)
        self.assertIsNone(web.plex_thumb(11))
        web._state.update(result=fake_result())  # fakes without "thumbs"
        self.assertIsNone(web.plex_thumb(10))


class TestReview1HeaderInjection(AsyncCase):
    """Review items 1/2 over HTTP: nothing from a request may reach the Location header raw."""

    def test_crlf_in_return_to_is_not_a_header(self):
        status, headers, _ = self.request("POST", "/refresh",
                                          "return_to=%2Fx%0d%0aSet-Cookie%3A%20pwn%3D1")
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/recommended")
        self.assertIsNone(headers.get("Set-Cookie"))

    def test_non_ascii_return_to_is_a_redirect_not_a_500(self):
        for path in ("/refresh", "/dismiss", "/ai/generate"):
            status, headers, _ = self.request("POST", path, "return_to=%2F%E2%98%83")
            self.assertEqual(status, 303, path)
            self.assertEqual(headers.get("Location"), "/recommended", path)

    def test_backslash_and_tab_open_redirects(self):
        for raw in ("%2F%5Cevil.example", "%2F%09%2Fevil.example"):
            status, headers, _ = self.request("POST", "/refresh", "return_to=" + raw)
            self.assertEqual((status, headers.get("Location")), (303, "/recommended"), raw)

    def test_settings_section_is_whitelisted_before_it_reaches_location(self):
        self.addCleanup(config._apply)
        for section in ("x%0d%0aSet-Cookie:%20pwn=1", "%E2%98%83", "nope"):
            for action in ("save", ""):
                status, headers, _ = self.request("POST", f"/settings?section={section}",
                                                  f"action={action}&PLEX_URL=http%3A%2F%2Fevil")
                self.assertEqual(status, 303)
                self.assertEqual(headers.get("Location"), "/settings")
                self.assertIsNone(headers.get("Set-Cookie"))
        self.assertIsNone(db.get_setting("PLEX_URL"))  # an unknown section saves nothing

    def test_known_settings_section_still_works(self):
        self.addCleanup(config._apply)
        status, headers, _ = self.request("POST", "/settings?section=tmdb", "action=save")
        self.assertEqual((status, headers.get("Location")), (303, "/settings?section=tmdb&saved=1"))


class TestReview2SafePath(unittest.TestCase):
    """Review item 2 (and item 1's control characters) at the function level."""

    def test_rejected(self):
        for path in ("/\\evil.example", "/\t/evil.example", "\\\\evil.example", "//evil.example",
                     "/x\r\nSet-Cookie: a=1", "/x\n", "/☃", "/a b", "/x\x00", "/x\x7f",
                     "https://evil.example/", "/redirect?to=https://evil.example", "recommended", "", None):
            self.assertEqual(web._safe_path(path), "/recommended", repr(path))

    def test_accepted(self):
        for path in ("/", "/recommended?type=tv", "/ai?msg=Hidden%20%22M%22&undo_type=movie&undo_id=1",
                     "/recommended?type=all#top", "/library"):
            self.assertEqual(web._safe_path(path), path)

    def test_add_dialog_back_link_uses_it(self):
        html = web.render_add_dialog("movie", 1, "/\\evil.example")
        self.assertNotIn("evil.example", html)


class FailOnce:
    """sources.run stand-in: the first call blocks until released and then fails."""

    def __init__(self):
        self.release, self.entered, self.calls = threading.Event(), threading.Event(), 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            self.release.wait(5)
            raise RuntimeError("old settings were wrong")
        return fake_result([item(4)])


class TestReview3FailureAfterInvalidate(AsyncCase):
    def test_a_failure_with_old_settings_rebuilds_instead_of_recording_the_error(self):
        run = FailOnce()
        with mock.patch.object(sources, "run", run):
            web.get_result_nowait()
            self.assertTrue(run.entered.wait(2))
            web.invalidate_cache()
            run.release.set()
            wait_idle()
        self.assertEqual(run.calls, 2)
        status = web.build_status()
        self.assertEqual((status["state"], status["error"]), ("ready", None))
        self.assertEqual(web._state["result"]["items"][0]["tmdb_id"], 4)
        self.assertEqual(web._build["failed_at"], 0.0)  # no back-off from the stale failure

    def test_a_failure_without_a_settings_change_is_still_recorded(self):
        run = FailOnce()
        with mock.patch.object(sources, "run", run):
            web.get_result_nowait()
            run.release.set()
            wait_idle()
        self.assertEqual(run.calls, 1)
        self.assertEqual(web.build_status()["state"], "error")


class TestReview4And5ActionsDuringABuild(AsyncCase):
    """Items 4/5: clicks made while a build runs must survive it finishing."""

    def setUp(self):
        super().setUp()
        self.live()
        web._state.update(result=fake_result([item(1), item(2, title="Heat"), item(3)]),
                          time=time.time() - web.CACHE_SECONDS - 5)

    def ids(self, state=None):
        return [i["tmdb_id"] for i in (state or web._state)["result"]["items"]]

    def build_with(self, result, during):
        gate = Gate(result=result)
        with mock.patch.object(sources, "run", gate):
            web.get_result_nowait()
            self.assertTrue(gate.entered.wait(2))
            during()
            gate.release.set()
            wait_idle()

    def test_undo_during_a_build_survives_it(self):
        self.post_json("/dismiss", "type=movie&id=2")  # before the build: it'll leave 2 out
        self.build_with(fake_result([item(1), item(3), item(9)]),
                        lambda: self.post_json("/undismiss", "type=movie&id=2"))
        self.assertEqual(self.ids(), [1, 2, 3, 9])

    def test_undo_then_dismiss_again_during_a_build_stays_hidden(self):
        self.post_json("/dismiss", "type=movie&id=2")

        def clicks():
            self.post_json("/undismiss", "type=movie&id=2")
            self.post_json("/dismiss", "type=movie&id=2")
        self.build_with(fake_result([item(1), item(3)]), clicks)
        self.assertEqual(self.ids(), [1, 3])

    def test_undo_during_a_build_isnt_duplicated_if_the_build_has_it_too(self):
        self.post_json("/dismiss", "type=movie&id=2")
        self.build_with(fake_result([item(1), item(2), item(3)]),
                        lambda: self.post_json("/undismiss", "type=movie&id=2"))
        self.assertEqual(self.ids(), [1, 2, 3])

    def test_forget_mid_build_is_applied_even_without_the_db(self):
        """What a dismiss landing after the DB re-check but before the install looks like: only
        the in-memory forget() is there to go on."""
        self.build_with(fake_result([item(1), item(2), item(3)]), lambda: web.forget("movie", 2))
        self.assertEqual(self.ids(), [1, 3])

    def test_add_mid_build_doesnt_reappear(self):
        def add():
            with mock.patch.object(sources, "add_to_library", return_value=(True, "Added")):
                self.post_json("/add", "type=movie&id=3")
        self.build_with(fake_result([item(1), item(2), item(3)]), add)
        self.assertEqual(self.ids(), [1, 2])

    def test_old_added_log_doesnt_hide_titles_from_later_builds(self):
        """The added log is display-only - only Radarr/Sonarr's library (checked by
        sources.run) keeps a title out, e.g. if you've since deleted it there."""
        db.record_added(item(3))
        with mock.patch.object(sources, "run", return_value=fake_result([item(1), item(3)])):
            web.get_result(refresh=True)
        self.assertEqual(self.ids(), [1, 3])


class TestReview6AiFiltering(AsyncCase):
    def setUp(self):
        super().setUp()
        self.live()
        old = config.AI_TOKEN
        self.addCleanup(setattr, config, "AI_TOKEN", old)
        config.AI_TOKEN = "sk-x"
        web._state.update(result=fake_result([item(5), item(6)]), time=time.time())

    def test_dismiss_or_add_during_generation_is_applied_to_the_new_batch(self):
        gate = Gate(result=fake_result([item(5), item(6), item(7)]))
        with mock.patch.object(sources, "generate_ai_recommendations", gate):
            self.post_json("/ai/generate")
            self.assertTrue(gate.entered.wait(2))
            self.post_json("/dismiss", "type=movie&id=5")
            with mock.patch.object(sources, "add_to_library", return_value=(True, "Added")):
                self.post_json("/add", "type=movie&id=6")
            gate.release.set()
            wait_idle()
        self.assertEqual([i["tmdb_id"] for i in web._ai_state["result"]["items"]], [7])

    def test_titles_already_dismissed_are_filtered_from_the_batch(self):
        db.dismiss("movie", 7)
        with mock.patch.object(sources, "generate_ai_recommendations",
                               return_value=fake_result([item(7), item(8)])):
            self.post_json("/ai/generate")
            wait_idle()
        self.assertEqual([i["tmdb_id"] for i in web._ai_state["result"]["items"]], [8])


class Boom(BaseException):
    """Not an Exception - the kind of thing only a finally: block cleans up after."""


class TestReview7Hardening(AsyncCase):
    def test_thread_start_failure_resets_pending(self):
        with mock.patch.object(threading.Thread, "start", side_effect=RuntimeError("can't start new thread")):
            with self.assertRaises(RuntimeError):
                web._start_build()
            with self.assertRaises(RuntimeError):
                web.start_ai_generation()
        self.assertFalse(web._build["pending"])
        self.assertFalse(web._ai_state["building"])
        self.assertNotEqual(web.build_status()["state"], "building")

    def test_base_exception_in_a_build_doesnt_pin_running(self):
        with mock.patch.object(sources, "run", side_effect=Boom()):
            with self.assertRaises(Boom):
                web.get_result()
        self.assertFalse(web._build["running"])

    def test_base_exception_in_ai_generation_doesnt_pin_building(self):
        web._ai_state["building"] = True  # as start_ai_generation() leaves it
        with mock.patch.object(sources, "generate_ai_recommendations", side_effect=Boom()):
            with self.assertRaises(Boom):
                web._ai_generate()
        self.assertFalse(web._ai_state["building"])

    def test_non_js_generate_doesnt_start_when_ai_isnt_configured(self):
        self.live()
        old = config.AI_TOKEN
        self.addCleanup(setattr, config, "AI_TOKEN", old)
        config.AI_TOKEN = ""
        with mock.patch.object(sources, "generate_ai_recommendations") as gen, \
             mock.patch.object(web, "start_ai_generation") as start:
            self.assertEqual(self.location("/ai/generate", "return_to=%2Fai"), "/ai")
        start.assert_not_called()
        gen.assert_not_called()


POST_ROUTES = (("/settings?section=arr", "action=save&RADARR_URL=http%3A%2F%2Fevil"),
               ("/rate", "type=movie&id=1&stars=3"), ("/add", "type=movie&id=1"), ("/dismiss", "type=movie&id=1"), ("/undismiss", "type=movie&id=1"),
               ("/refresh", ""), ("/ai/generate", ""), ("/theme", "theme=crimson"))


class TestCsrf(AsyncCase):
    """Cross-site browser POSTs are refused with a 403 before anything happens."""

    def setUp(self):
        super().setUp()
        self.live()
        self.addCleanup(config._apply)
        web._state.update(result=fake_result([item(1)]), time=time.time() - web.REFRESH_MIN_SECONDS - 5)
        old = config.AI_TOKEN
        self.addCleanup(setattr, config, "AI_TOKEN", old)
        config.AI_TOKEN = "sk-x"
        # anything that would do real work, so a test that wrongly gets through can't
        for target, value in ((sources, "run"), (sources, "generate_ai_recommendations"), (sources, "add_to_library")):
            patch = mock.patch.object(target, value, return_value=(True, "Added") if value == "add_to_library"
                                      else fake_result([item(1)]))
            self.addCleanup(patch.stop)
            setattr(self, "mock_" + value, patch.start())

    def own_origin(self):
        return self.base  # http://127.0.0.1:<port>, which is what urllib sends as Host

    def post(self, path, data, headers, as_json=False):
        status, resp_headers, body = self.request("POST", path, data, as_json=as_json, extra_headers=headers)
        wait_idle()
        return status, resp_headers, body

    def assert_blocked_everywhere(self, headers):
        for path, data in POST_ROUTES:
            status, resp_headers, body = self.post(path, data, headers)
            self.assertEqual(status, 403, (path, headers))
            self.assertIn("another site", body)
            self.assertIsNone(resp_headers.get("Location"))
            self.assertIsNone(resp_headers.get("Set-Cookie"))
        self.assertIsNone(db.get_setting("RADARR_URL"))
        self.assertEqual(db.dismissed(), set())
        self.assertEqual(db.ratings(), {})
        self.mock_add_to_library.assert_not_called()
        self.mock_generate_ai_recommendations.assert_not_called()
        self.mock_run.assert_not_called()

    def assert_allowed_everywhere(self, headers):
        for path, data in POST_ROUTES:
            status, _, _ = self.post(path, data, headers)
            self.assertEqual(status, 303, (path, headers))

    def test_cross_site_fetch_is_blocked_on_every_route(self):
        self.assert_blocked_everywhere({"Sec-Fetch-Site": "cross-site"})

    def test_same_site_fetch_is_blocked_too(self):
        """e.g. another app on the same box at a different port"""
        self.assert_blocked_everywhere({"Sec-Fetch-Site": "same-site"})

    def test_same_origin_and_none_are_allowed(self):
        for value in ("same-origin", "none"):
            self.assert_allowed_everywhere({"Sec-Fetch-Site": value})

    def test_sec_fetch_site_wins_over_origin(self):
        self.assert_blocked_everywhere({"Sec-Fetch-Site": "cross-site", "Origin": self.own_origin()})

    def test_mismatched_origin_is_blocked(self):
        port = self.server.server_address[1]
        for origin in ("http://evil.example", f"http://127.0.0.1:{port + 1}", f"https://127.0.0.1",
                       "http://localhost:" + str(port)):
            self.assert_blocked_everywhere({"Origin": origin})

    def test_null_origin_is_blocked(self):
        self.assert_blocked_everywhere({"Origin": "null"})

    def test_matching_origin_is_allowed(self):
        self.assert_allowed_everywhere({"Origin": self.own_origin()})

    def test_no_headers_at_all_is_allowed(self):
        self.assert_allowed_everywhere({})

    def test_json_403_has_a_json_body(self):
        status, headers, body = self.post("/dismiss", "type=movie&id=1", {"Sec-Fetch-Site": "cross-site"}, as_json=True)
        self.assertEqual(status, 403)
        self.assertEqual(headers.get("Content-Type"), "application/json")
        self.assertEqual(json.loads(body), {"ok": False, "message": "Blocked: request came from another site"})

    def test_cross_site_settings_save_doesnt_save_a_malicious_radarr_url(self):
        status, _, _ = self.post("/settings?section=arr", "action=save&RADARR_URL=http%3A%2F%2Fevil.example%3A7878",
                                 {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        self.assertIsNone(db.get_setting("RADARR_URL"))
        self.assertNotEqual(config.RADARR_URL, "http://evil.example:7878")
        # and the same form from the app itself does save
        self.post("/settings?section=arr", "action=save&RADARR_URL=http%3A%2F%2Fradarr%3A7878",
                  {"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(db.get_setting("RADARR_URL"), "http://radarr:7878")


class TestCrossSiteHelper(unittest.TestCase):
    def check(self, headers):
        return web._cross_site(headers)

    def test_default_ports(self):
        self.assertFalse(self.check({"Origin": "http://arr", "Host": "arr:80"}))
        self.assertFalse(self.check({"Origin": "http://arr:80", "Host": "arr"}))
        self.assertFalse(self.check({"Origin": "https://arr", "Host": "arr:443"}))
        self.assertFalse(self.check({"Origin": "HTTP://ARR:8091", "Host": "arr:8091"}))
        self.assertTrue(self.check({"Origin": "http://arr", "Host": "arr:8091"}))

    def test_scheme_only_picks_the_default_port(self):
        """This server only speaks plain HTTP, so an https Origin at its exact host:port can only be
        the app itself behind a TLS proxy - allowed rather than breaking that setup."""
        self.assertFalse(self.check({"Origin": "https://arr", "Host": "arr"}))
        self.assertFalse(self.check({"Origin": "https://arr:8091", "Host": "arr:8091"}))
        self.assertTrue(self.check({"Origin": "https://arr", "Host": "arr:80"}))

    def test_ipv6(self):
        self.assertFalse(self.check({"Origin": "http://[::1]:8091", "Host": "[::1]:8091"}))
        self.assertTrue(self.check({"Origin": "http://[::1]:8092", "Host": "[::1]:8091"}))

    def test_garbage_is_blocked(self):
        for origin in ("null", "", "file://", "ftp://arr:8091", "http://arr:notaport", "http://"):
            self.assertTrue(self.check({"Origin": origin, "Host": "arr:8091"}), origin)
        self.assertTrue(self.check({"Origin": "http://arr:8091"}))  # no Host to compare with
        self.assertTrue(self.check({"Origin": "http://arr:8091", "Host": "arr:bad"}))

    def test_neither_header(self):
        self.assertFalse(self.check({}))
        self.assertFalse(self.check({"Host": "arr:8091"}))


class TestTheme(AsyncCase):
    COOKIE = "wn_theme=crimson; Path=/; Max-Age=34560000; SameSite=Lax"

    def post_form(self, data, json_mode=False):
        return self.request("POST", "/theme", data, as_json=json_mode)

    def test_json_sets_cookie(self):
        status, headers, body = self.post_form("theme=crimson", True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True, "message": "Theme set to Crimson", "theme": "crimson"})
        self.assertEqual(headers.get("Set-Cookie"), self.COOKIE)
        self.assertEqual(headers.get("Content-Type"), "application/json")

    def test_no_js_redirects_with_cookie(self):
        status, headers, _ = self.post_form("theme=crimson")
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/appearance?msg=Theme+set+to+Crimson")
        self.assertEqual(headers.get("Set-Cookie"), self.COOKIE)

    def test_return_to(self):
        _, headers, _ = self.post_form("theme=teal&return_to=/library")
        self.assertEqual(headers.get("Location"), "/library?msg=Theme+set+to+Teal+Night")
        for bad in ("//evil.example", "http://evil.example", "/a\\b"):
            _, headers, _ = self.post_form("theme=teal&return_to=" + urllib.parse.quote(bad))
            self.assertTrue(headers.get("Location").startswith("/appearance?msg="), bad)

    def test_invalid_theme(self):
        for data in ("theme=Crimson", "theme=", "", "theme=evil", "theme=%3Cscript%3E", "other=crimson"):
            status, headers, body = self.post_form(data, True)
            self.assertEqual(status, 400, data)
            self.assertEqual(json.loads(body), {"ok": False, "message": "That isn't a valid theme"})
            self.assertIsNone(headers.get("Set-Cookie"))
            status, headers, _ = self.post_form(data)
            self.assertEqual(status, 303)
            self.assertEqual(headers.get("Location"), "/appearance?msg=That+isn%27t+a+valid+theme")
            self.assertIsNone(headers.get("Set-Cookie"))

    def test_sample_and_live_both_set_cookie(self):
        self.assertTrue(web._is_sample())
        self.assertEqual(self.post_form("theme=crimson", True)[1].get("Set-Cookie"), self.COOKIE)
        self.live()
        self.assertFalse(web._is_sample())
        self.assertEqual(self.post_form("theme=crimson", True)[1].get("Set-Cookie"), self.COOKIE)

    def test_touches_no_state(self):
        with mock.patch.object(web, "invalidate_cache") as inv, \
                mock.patch.object(web, "_log_action") as log, \
                mock.patch.object(db, "set_setting") as set_setting:
            self.post_form("theme=ocean", True)
            self.post_form("theme=ocean")
        inv.assert_not_called()
        log.assert_not_called()
        set_setting.assert_not_called()
        self.assertEqual(db.dismissed(), set())
        self.assertEqual(db.ratings(), {})
        self.assertIsNone(db.get_setting("RADARR_URL"))
        self.assertFalse(web._actions)

    def test_current_theme_outside_request(self):
        self.assertEqual(web.current_theme(), "amber")

    def test_current_theme_per_request(self):
        with mock.patch.object(web, "render_home", lambda **kw: web.current_theme()):
            for cookie, want in (("wn_theme=crimson", "crimson"), (None, "amber"), ("wn_theme=bad", "amber"),
                                 ("a=1; wn_theme=lime", "lime"), (None, "amber")):
                headers = {"Cookie": cookie} if cookie else {}
                status, _, body = self.request("GET", "/", extra_headers=headers)
                self.assertEqual((status, body), (200, want), cookie)

    def test_post_sets_thread_local_too(self):
        seen = []
        with mock.patch.object(web.Handler, "_post_theme",
                               lambda self, form, as_json: (seen.append(web.current_theme()), self._json({}))):
            self.request("POST", "/theme", "theme=lime", extra_headers={"Cookie": "wn_theme=mono"})
        self.assertEqual(seen, ["mono"])


if __name__ == "__main__":
    unittest.main()
