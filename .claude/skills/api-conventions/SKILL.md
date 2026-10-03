---
name: api-conventions
description: What's Next's HTTP conventions - the route table, the JSON-vs-redirect response pattern, error format and status codes, CSRF protection, input validation helpers, naming and locking rules (stdlib http.server in web.py). Use when adding or changing a route, a form, a JSON response or anything app.js calls, and when reviewing those changes.
---

# What's Next API conventions

Server: stdlib `http.server` - `Handler` in `web.py` (backend-owned). Pages are rendered by `pages.py` (frontend-owned). No framework, no external dependencies.

## Routes (current)
| Method | Path | Notes |
|---|---|---|
| GET | `/?msg=&undo_type=&undo_id=` | Home (cinematic hero + rows, movies and TV mixed) |
| GET | `/movies?...`, `/tv?...` | same layout, one kind; same `msg`/undo params |
| GET | `/recommended?type=movie\|tv&msg=&undo_type=&undo_id=` | legacy: 303 to `/movies`, `/tv` or `/` (any other `type`); keeps `msg` and a valid undo |
| GET | `/library` | |
| GET | `/appearance?msg=` | theme picker (Settings tab); cookie `wn_theme` selects the theme on every page |
| GET | `/ai?msg=&undo_type=&undo_id=` | |
| GET | `/add-dialog?type=&id=&return_to=&partial=1` | `partial=1` returns just the fragment for the JS modal; bad type/id gives 404 |
| GET | `/settings?section=<settings_page.SECTIONS>` | |
| GET | `/api/status` | `build_status()` JSON, always fast |
| GET | `/health` | `ok`, text/plain (Docker healthcheck) |
| GET | `/static/app.css`, `/static/app.js` | whitelist `Handler.STATIC_FILES` only; anything else gives 404 |
| POST | `/refresh`, `/dismiss`, `/undismiss`, `/add`, `/ai/generate` | item routes take form fields `type`, `id`, `return_to` |
| POST | `/theme` | `theme` (whitelist in `themes.py`), `return_to` (default `/appearance`); sets cookie `wn_theme` (Path=/, SameSite=Lax, 1 year), JSON or 303; works in sample mode; never touches db/cache |
| POST | `/rate` | `type`, `id`, `stars` (0 clears, 1-5), `return_to`; works for any title incl. unwatched recommendations; sample mode answers "not saved" |
| GET | `/poster?...` | Plex poster proxy (token stays server-side) |
| POST | `/settings?section=` | `action=save` or `action=test_<app>`; unknown section redirects to `/settings` |

Anything else: `404 Not found` text/plain.

## Response pattern: one route, two modes
- **JSON mode** only when the request's `Accept` header contains `application/json` (`Handler._wants_json()`; app.js always sends it). Respond with `self._json(payload, status)` (adds `Cache-Control: no-store`).
- **No-JS mode** (plain form post): `self._redirect(return_to, msg=None, extra=None)`, a **303** to a validated internal path, with `msg` (and `undo_type`/`undo_id` for dismiss) as query params.
- Every new POST must support both modes and behave identically apart from the transport.

### JSON shapes
- Item actions: `{"ok": bool, "message": str}`, plus extras: `/dismiss` adds `"undo": {"type": "movie"|"tv", "id": int}`; `/refresh` and `/ai/generate` add `"started": bool`.
- Errors use the same shape with `"ok": false` and a human-readable `message` (shown in a toast):
  - **400** bad input (e.g. `"That isn't a valid title"`),
  - **403** cross-site request (`"Blocked: request came from another site"`),
  - **200 with ok=false** for expected refusals (sample mode: `SAMPLE_MESSAGE` = `"Sample data - not saved"`; AI not configured).
- `/api/status`: `{"state": "ready"|"building"|"error", "has_result", "started", "updated", "error", "ai": {"state": "idle"|"building"|"ready"|"error", "error"}}`.

## Auth and security
- **No login** - home network only. Instead, **every POST** first passes `_cross_site(self.headers)` (CSRF): `Sec-Fetch-Site` must be `same-origin`/`none`; otherwise `Origin` must match `Host`; neither header (curl) is allowed. Behind a reverse proxy the Host header must be forwarded.
- Redirect targets only via `_safe_path(path, default="/recommended" (redirects to a browse page))` (internal, printable ASCII, no `//`, `\`, `://`). Never interpolate unvalidated input into a header (e.g. whitelist `section` against `settings_page.SECTIONS`).
- Validate ids with `_parse_id()` / `_item_from(fields, type_key, id_key)` (ASCII digits, max 12, type in `movie|tv`).
- Sample mode (`_is_sample()`) must never write to the DB or call Radarr/Sonarr.
- Secrets (tokens, API keys) never appear in responses or rendered settings fields (blank = keep current).
- Error text from upstream services can reach `/api/status` - never include URLs with API keys in exception messages.

## Naming
- Form/query fields: short lowercase (`type`, `id`, `return_to`, `msg`, `section`, `action`, `partial`). Item identity is `(media_type, tmdb_id)`; on the wire it's `type` + `id`.
- JSON keys: snake_case. Route paths: lowercase, `/noun` or `/noun/verb` (`/ai/generate`), JSON-only endpoints under `/api/`.
- **Versioning: none** - single first-party client (app.js) shipped together with the server, so change both in one go instead of versioning.

## State and locking (web.py)
- `compute_lock` is held for a whole build or AI generation; **request handlers must never wait on it**. Use `_status_lock` (held for a few lines only) for reading/writing `_state`, `_ai_state`, `_build`.
- Long work runs in a background thread (`_start_build`, `start_ai_generation`); the request returns immediately and the UI polls `/api/status`.
- User actions during a build are logged (`_log_action`) and replayed onto the new result in `_install()` - keep that path when adding actions that change the list.
