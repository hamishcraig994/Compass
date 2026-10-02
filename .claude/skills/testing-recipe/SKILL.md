---
name: testing-recipe
description: How to run and write tests for What's Next - exact unittest commands and timings, the fast vs full suite, fixtures and mocking patterns, threading helpers, sample-mode smoke testing, and what can't be tested here. Use before running or writing tests, before reporting work as done, and when reviewing test coverage.
---

# What's Next testing recipe

Stdlib `unittest` only - no pytest, no pip, no third-party packages. Run everything from the repo root (`/home/ops/whatsnext`).

## Commands
| What | Command | Time |
|---|---|---|
| Full suite (run before reporting done) | `python3 -m unittest discover -s tests` | ~53s, 333 tests |
| One module | `python3 -m unittest tests.test_ui` | |
| One test | `python3 -m unittest tests.test_web_async.TestCsrf.test_cross_site_fetch_is_blocked_on_every_route` (pattern: `tests.<module>.<Class>.<method>`) | |
| Fast set (what the after-edit hook runs) | every module except `test_web` and `test_web_async` | ~5s |
| Syntax only | `python3 -m py_compile <file>.py` | |

Slow modules: `test_web_async` (~39s, a real server per test) and `test_web` (~10s). `test_settings` takes ~3s. Everything else is under 1s.

The project's `.claude/settings.json` hook already runs py_compile + the fast set after every `.py` edit, and `tests.test_ui` after `static/*` edits; a failure comes back to you as hook output - fix it before moving on.

## Where tests live (ownership in .claude/ownership.json)
- `tests/test_ui.py` - frontend-owned: page markup, escaping, static files, building/undo screens.
- `tests/test_web_async.py` - background builds, JSON endpoints, CSRF, static whitelist, undo/race handling.
- `tests/test_web.py` - pages + handler end to end in sample mode.
- One module per integration: `test_plex_tmdb`, `test_tautulli`, `test_radarr_sonarr`, `test_ai`, `test_http_util`, plus `test_recommend`, `test_profile`, `test_sources`, `test_settings`.

## Patterns to copy
- **Sample mode for the whole module:** `os.environ["SAMPLE"] = "1"` *before* `import web` (see top of test_web_async.py / test_ui.py).
- **Temp DB per test:** `db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")` with `self.addCleanup(setattr, db, "DB_PATH", old)`.
- **Mock at the module boundary**, never real network: `mock.patch.object(sources, "run", ...)`, `mock.patch.object(tmdb, "get_json", ...)`, `mock.patch.object(radarr, "post_json", ...)`, `mock.patch.object(ai, "post_json", ...)`. `tests/test_http_util.py` is the one place the real urllib error path is exercised.
- **Rendering with fake state:** `mock.patch.object(web, "get_result_nowait", ...)` / `"build_status"` - pages.py reads state through the `web` module, so these patches reach the pages.
- **Background threads:** never `time.sleep()` and hope. Use `threading.Event` gates (`self.release`, `self.entered`) to hold a fake build mid-flight, and `wait_idle()` (test_web_async.py) to wait for builds/AI generation to finish, with a deadline.
- **Fixtures:** helpers `item()` / `fake_result()` in test_web_async.py build fake recommendation results.
- **Redirect assertions:** `NoRedirect` opener (test_web_async.py) to inspect a 303's `Location`.
- **Every new route needs:** JSON-mode test, no-JS redirect test, bad-input (400) test, sample-mode test, CSRF (403) test.

## Smoke test in a real server
```bash
SAMPLE=1 PORT=8099 python3 web.py        # run in the background
curl -s http://127.0.0.1:8099/api/status  # wait for "state": "ready"
curl -s -X POST -H 'Accept: application/json' -d 'type=movie&id=1&return_to=/recommended' http://127.0.0.1:8099/dismiss
```
Stop it **by PID** (`ss -ltnp | grep :8099` -> `kill <pid>`), never `pkill -f` (it can match your own shell). Port 8099 is the convention for local demos; 8090/8091 are taken on arr.

## Rules
- **No live requests** to Plex, TMDB, Radarr, Sonarr, Tautulli or AI providers unless your brief gives an explicit budget.
- **TODO:** no browser (and no node) on this machine, so `static/app.js` has no automated test - check it by reading, by curl-ing every endpoint it calls, and by asking Hamish to click through a sample-mode demo.
- **TODO:** no linter or type checker installed (no ruff/pyflakes/mypy) - watch for unused imports and misspelled names yourself.
