# Library view + Watched & personal ratings

Status: approved (open questions answered by Hamish 2026-10-02)
Author: architect · Date: 2026-10-02

## Goal
1. **Library**: browse what's actually in Plex (movies + shows) as a poster-first grid, with search, a watched/unwatched filter, sort and pagination. Today `/library` only shows the "added via What's Next" log (`db.added_items()`); that becomes one tab of the new page.
2. **Watched + ratings**: a **Watched** tab inside Library lists the watch history the taste profile already uses. You can give each title 1-5 stars, change it or clear it. Ratings are stored locally and feed the profile: loved titles count more, disliked ones count for nothing, and titles TMDB links to a disliked one are pushed down the list.

Library tabs: **All / Movies / TV shows / Watched / Added here**. Everything works in sample mode with no tokens.

## Out of scope
- Writing anything to Plex/Tautulli. Ratings are local only, permanently (Decision 3).
- Rating titles that aren't in watch history (recommendations, unwatched library items).
- A new sidebar/bottom-nav item. The nav stays exactly as it is.
- A separate `/watched` route. It never shipped, so there's nothing to redirect; it stays a 404.
- A movie/TV filter inside the Watched tab, genre/year filters, live as-you-type filtering, infinite scroll.
- Bulk TMDB lookups for posters (Decision 5).
- Changes to "Not interested" / "Add to library".
- Exposing ratings in `/api/status` (its key set is pinned by `TestBackgroundBuild.test_api_status_shape`).

## Decisions
Hamish's answers, plus the design choices they rest on.
1. **Watched is a tab inside Library** (`/library?type=watched`), not a nav item. `NAV_SECTIONS` is unchanged, and `/watched` isn't added.
2. **Plex poster proxy: yes.** `GET /poster?key=<ratingKey>` fetches Plex's thumbnail on the server. The token and thumb path never reach the browser, and only rating keys in the current snapshot are served. Each poster is one LAN request to Plex, cached by the browser for 24h.
3. **Ratings are local only.** There is no code path that writes to Plex.
4. **Dislikes down-rank, never hide.** A candidate that TMDB links to a title rated 1★ has its score ×0.4; for 2★ it's ×0.7. It stays in the list.
5. **No bulk TMDB lookups for posters.**
   - Watched items use a TMDB poster that's already cached, then the Plex proxy, then the tinted placeholder.
   - Library items use the Plex proxy, then the placeholder.
6. **Stars are shown in sample mode.** `POST /rate` refuses with `SAMPLE_MESSAGE` and never writes. Sample mode also ignores stored ratings completely (sample ids such as 1001 are real TMDB ids).
7. **Scale is 1-5 whole stars, stored as `user_rating = stars * 2`.** That's Plex's own scale. `profile.item_weight()` already reads 0-10, so the formula is unchanged and existing Plex ratings show as stars. Thumbs were rejected because they'd lose the 0.67× ("fine") vs 1.6× ("loved") difference the formula already makes. Your rating overrides the Plex/Tautulli rating; clearing it falls back to that rating.
8. **Data comes from the background build.** `sources.run()` already reads all of Plex, so it also keeps a snapshot of the library and watch history in the result, and pages never call Plex/Tautulli. Personal ratings are read from the DB at render time, so a change shows at once.
9. **A rating doesn't trigger a rebuild.** It sets a `ratings_changed` flag. The Watched tab then shows "Refresh to update recommendations", and Refresh skips its 10-minute `REFRESH_MIN_SECONDS` guard. The hourly rebuild picks ratings up anyway.

## Contract

### Data shapes (in-memory, on the main build result)
`sources.run()` (live and sample) adds three keys to its result. `_install()` / `_hide_dismissed()` don't touch them. Pages must use `.get()`, because test fakes (`fake_result()`, test_ui `result()`) don't have these keys.
```python
result["library"]: list[LibraryItem] | None  # None = unavailable (Tautulli without PLEX_TOKEN)
result["watched"]: list[WatchedItem]         # raw history, BEFORE personal ratings are applied
result["thumbs"]:  dict[int, str]            # Plex ratingKey -> thumb path. Server-side only, never rendered

LibraryItem = {"media_type": "movie"|"tv", "tmdb_id": int|None, "title": str, "year": int|None,
               "added_at": str|None,          # ISO-8601 UTC, from Plex addedAt
               "watched": bool, "progress": float|None,
               "poster_key": int|None,        # ratingKey, only when a thumb is in result["thumbs"]
               "url": str|None}               # https://www.themoviedb.org/{type}/{id} if tmdb_id
WatchedItem = {"media_type", "tmdb_id": int, "title", "year", "last_viewed": str|None,
               "user_rating": float|None,     # Plex/Tautulli 0-10, untouched
               "view_count": int, "progress": float|None,
               "poster_key": int|None, "poster_url": str|None, "url": str|None}  # poster_url/url from CACHED TMDB details only
```
Neither shape carries `thumb`. Plex thumbs are only kept if they start with `/library/`; anything else is dropped, so the proxy can't be pointed at another host.

### Python interface
```python
# db.py (backend-dev)
def ratings() -> dict: ...                               # {(media_type, tmdb_id): stars}
def set_rating(media_type, tmdb_id, stars) -> None: ...  # stars int 1..5, else ValueError; INSERT OR REPLACE, rated_at = now UTC ISO
def clear_rating(media_type, tmdb_id) -> None: ...       # no-op if absent

# profile.py (backend-dev)
DISLIKE_MAX_STARS = 2
def apply_ratings(watched, ratings) -> list: ...         # copies; rated items get user_rating = stars*2 and "personal_stars" = stars
def stars_from_ten(rating) -> int|None: ...              # Plex 0-10 (float or numeric str) -> 1..5, rounding half up (int(r/2 + .5)); None/0/garbage -> None

# recommend.py (backend-dev)
DISLIKE_FACTORS = {1: 0.4, 2: 0.7}
MAX_DISLIKED = 50
def recommend(..., disliked=None, ...) -> dict: ...      # disliked: {(media_type, tmdb_id): stars}. Details are looked up for up to
    # MAX_DISLIKED of them (through the TMDB client's cache). Any candidate in a disliked title's
    # details["recommendations"] (same media_type) has its final score multiplied by the SMALLEST
    # matching factor. It's never excluded. Default None means no change in behaviour.

# plex.py (backend-dev)
def parse_library_item(meta, media_type) -> dict: ...   # always returns a dict (tmdb_id may be None), incl. "rating_key": int|None, "thumb": str|None
def parse_item(meta, media_type): ...                    # unchanged, plus "rating_key" and "thumb" keys
class PlexClient:
    def load(self): ...  # NOW returns (watched, library_keys, skipped, library_items). 4-tuple on purpose:
                         # old 3-tuple mocks fail loudly instead of silently hitting the real Plex
    def poster(self, thumb, width=342, height=513) -> tuple: ...  # (content_type, bytes) via GET {PLEX_URL}/photo/:/transcode
                         # ?width&height&minSize=1&upscale=1&url=<thumb>, X-Plex-Token header. Raises on non-image content type

# tautulli.py (backend-dev): each watched dict also gets "rating_key" (int|None, the group key) and "thumb" (meta "thumb" or None)

# http_util.py (backend-dev)
def get_bytes(url, headers=None, params=None, timeout=10, max_bytes=2_000_000) -> tuple: ...
    # (content_type, data). Same retry/error rules as get_json; RuntimeError if the body is over max_bytes

# tmdb.py (backend-dev)
class TmdbClient:
    def cached_details(self, media_type, tmdb_id) -> dict|None: ...  # db.cache_get of the details:v2 key only; never a request

# sample.py (backend-dev)
def library_items(now=None) -> list: ...                 # LibraryItem dicts for watched + _EXTRA_IN_LIBRARY; poster_key None, added_at = now - n days

# sources.py (backend-dev)
def _load_live(): ...    # NOW returns (watched, library_keys, notes, library) - library is list | None
def run(sample_mode, limit=200): ...
    # live:   ratings = db.ratings(); recommend(profile.apply_ratings(watched, ratings), ...,
    #         disliked={k: s for k, s in ratings.items() if s <= profile.DISLIKE_MAX_STARS})
    # sample: ignores ratings.
    # Both:   set result["library"], ["watched"], ["thumbs"]. Watched posters come from
    #         TmdbClient.cached_details() only - no network.
def generate_ai_recommendations(limit=50): ...  # applies ratings + disliked the same way

# web.py (backend-dev)
LIBRARY_TABS = ("all", "movie", "tv", "watched", "added")
LIST_OPTIONS = {   # tab -> (sorts, shows); the first entry of each is the default
    "all":     (("added", "title", "year"), ("all", "unwatched", "watched")),
    "movie":   (("added", "title", "year"), ("all", "unwatched", "watched")),
    "tv":      (("added", "title", "year"), ("all", "unwatched", "watched")),
    "watched": (("recent", "title", "rating"), ("all", "rated", "unrated")),
    "added":   (("added", "title", "year"), ("all",)),
}
LIST_PAGE_SIZE = 48     # divisible by the 2/3/4/6-column grids
def parse_list_query(query) -> dict: ...
    # query = parse_qs dict -> {"tab", "q", "sort", "show", "page"}. tab from `type`; any invalid value falls
    # back to its default (sort/show are validated against LIST_OPTIONS[tab]). q is stripped and cut to
    # 100 chars; page via _parse_id, min 1.
def list_view(items, tab="all", q="", sort="added", show="all", page=1, per_page=LIST_PAGE_SIZE) -> dict: ...
    # Pure. -> {"items": [...this page...], "total": int, "page": int (clamped to 1..pages), "pages": int (>= 1)}
    # tab movie/tv filters media_type (others: no type filter). q: casefold substring of title.
    # show: unwatched/watched -> item["watched"]; rated/unrated -> item["stars"] is not None.
    # sort: added -> added_at desc; title -> casefold asc; year -> year desc; recent -> last_viewed desc;
    #       rating -> (stars if not None else plex_stars) desc. Missing/None values always sort last; ties go by title.
def library_items(result, tab) -> list|None: ...
    # "added"   -> db.added_items() mapped to LibraryItem (watched False, poster_url kept). Doesn't need a result.
    # "watched" -> watched_items(result).
    # otherwise -> (result or {}).get("library").
def watched_items(result) -> list: ...
    # Copies of result["watched"], each with "stars" (personal, from db.ratings(); always None in sample
    # mode) and "plex_stars" (profile.stars_from_ten(user_rating)).
def mark_ratings_changed() -> None: ...   # _build["ratings_changed"] = True (under _status_lock)
def ratings_changed() -> bool: ...
def plex_thumb(rating_key) -> str|None: ...  # from _state["result"]["thumbs"] under _status_lock

# pages.py (frontend-dev) - replaces the current no-arg render_library(); all args have defaults
def render_library(tab="all", q="", sort=None, show="all", page=1, msg="") -> str: ...   # sort None = LIST_OPTIONS[tab] default
```
Changes to existing web.py state:
- `_build` gains `"ratings_changed": False`.
- `_needs_build(refresh)` becomes `stale or (refresh and (age > REFRESH_MIN_SECONDS or _build["ratings_changed"]))`.
- `_build_locked()` clears the flag when a job starts. If that job then raises, it restores the flag to the value it had.

pages.py reads `web.LIBRARY_TABS` / `web.LIST_OPTIONS` inside functions only, never at module level (`import web` sits at the bottom of pages.py).

### HTTP
| Method | Path | Request fields | JSON response (Accept: application/json) | No-JS response |
|---|---|---|---|---|
| GET | `/library` | query `type` (LIBRARY_TABS), `q`, `sort`, `show`, `page`, `msg` | n/a | 200 HTML `render_library(**parse_list_query(query), msg=msg)`. Invalid values fall back to defaults |
| GET | `/poster` | query `key` (digits) | n/a | 200 image bytes, `Content-Type` passed through only if `image/jpeg`, `image/png` or `image/webp`, plus `Cache-Control: private, max-age=86400`. Otherwise **404** `Not found` text/plain: sample mode, bad key, key not in `thumbs`, no `PLEX_TOKEN`, Plex error, non-image, too large |
| POST | `/rate` | `type`, `id`, `stars` (`1`-`5` sets, `0` clears), `return_to` (default `/library?type=watched`) | 200 `{"ok": true, "message": "Rated \"Arrival\" 4/5", "rating": {"type": "movie", "id": 329865, "stars": 4, "plex_stars": 5}}`. Clear: `stars: null`, message `Cleared your rating for "Arrival"`. Title not in snapshot: `"Rated 4/5"` / `"Rating cleared"`, `plex_stars: null` · 400 `{"ok": false, "message": "That isn't a valid title"}` (bad type/id) · 400 `{"ok": false, "message": "Pick a rating from 1 to 5"}` (stars missing/not 0-5) · 200 `{"ok": false, "message": "Sample data - not saved"}` (sample, no write) · 403 CSRF | 303 to `_safe_path(return_to)` + `msg` (same text, sample message included). Bad input: 303 back with no msg |

Order inside `/rate`:
1. CSRF (already done first in `do_POST`).
2. `_item_from(form)`.
3. Validate `stars`.
4. `_is_sample()`.
5. `db.set_rating` / `db.clear_rating`, then `mark_ratings_changed()`.

The title for the message comes from the current `result["watched"]`, not from the form.

The `/poster` handler fetches outside every lock. It never logs or echoes the Plex URL (the token is in a header, never in the URL). It needs a bytes-sending variant of `_send`.

### Data / storage
New table, created in `db._connect()` alongside the others:
```sql
CREATE TABLE IF NOT EXISTS ratings (media_type TEXT NOT NULL, tmdb_id INTEGER NOT NULL,
  stars INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5), rated_at TEXT NOT NULL,
  PRIMARY KEY (media_type, tmdb_id))
```
The change is additive and idempotent: it doesn't alter existing tables, needs no backfill, and is safe on the live `data/whatsnext.db`. Plex ratings aren't copied in; they stay a read-only fallback.

### How ratings change the profile (existing `profile.item_weight`, unchanged)
| Stars | user_rating | weight factor | also |
|---|---|---|---|
| 5 | 10 | 1.6 (cap) | |
| 4 | 8 | 1.33 | |
| 3 | 6 | 0.67 | below an unrated title (1.0), same as Plex's 6/10 today |
| 2 | 4 | 0, so no taste input and never a "Because you watched" seed | TMDB-linked candidates ×0.7 |
| 1 | 2 | 0 | TMDB-linked candidates ×0.4 |
| cleared | Plex rating or None | as before | |

### Markup contract (pages.py / app.js / app.css share these class names)
- **Nav**: unchanged. Every `/library?...` URL highlights the existing Library item.
- **Subtabs**: `.subtabs` / `.subtab` with All / Movies / TV shows / Watched / Added here, linking to `/library?type=<tab>`. They carry `q` and nothing else; sort/show/page reset to the new tab's defaults. The active tab gets `.on`.
- **Toolbar** (no JS needed): `<form class="toolbar" method="get" action="/library" role="search">`. Contents:
  - hidden `type`;
  - `<label>` + `<input type="search" name="q">`;
  - `<select name="sort">` and `<select name="show">`, each labelled, with options from `web.LIST_OPTIONS[tab]` (labels are pages' choice);
  - `<button type="submit" class="btn-ghost">Apply</button>`.

  The `show` select is omitted when the tab has a single show option (Added here).
- **Pager**: `<nav class="pager" aria-label="Pages">` with Previous/Next links (or a `span.muted` when disabled) and `<span class="pager-status">Page 2 of 7</span>`. Links keep all params. Omit it when `pages == 1`.
- **Library card** (`_library_card`, replaces the current one; used for All / Movies / TV shows / Added here):
  - `.card` > `.card-poster` (poster + `.kind`, plus `.poster-top` > `.badges` with `.badge` "Watched", or "Started" when `0 < progress < 1`);
  - `.card-info` > `.title` (TMDB link if `url`) and `.card-sub` (year, or "Added YYYY-MM-DD" on Added here - keep the text `Added `).
  - No actions.
- **Poster helper**: `_poster_html(item)` precedence is `poster_url` (via `_web_url`), then `<img class="poster" src="/poster?key={int(poster_key)}" alt="" loading="lazy">`, then `.poster-empty`. The placeholder hue must not crash when `tmdb_id` is None (use poster_key or a title hash).
- **Watched card** (Watched tab, `_watched_card`):
  - `<article class="card" data-card="{type}-{id}">` > poster > `.card-info` > `.title`, `.card-sub` ("Watched YYYY-MM-DD", plus "· 3 plays" if view_count > 1).
  - Then `<form class="rate" method="post" action="/rate" data-enhance="rate">`, containing:
    - hidden `type`, `id`, `return_to` (the current `/library?type=watched&...` URL);
    - `<div class="stars" role="group" aria-label="Your rating for {title}">` with 5 `<button type="submit" name="stars" value="N" class="star[ on][ from-plex]" aria-pressed="true|false" aria-label="Rate N out of 5">★</button>`. `on` is set for 1..effective rating; `from-plex` is set when that rating comes from Plex; `aria-pressed="true"` goes only on the button equal to the *personal* rating;
    - `<p class="rating-text">` reading "Your rating: 4/5", "From Plex: 5/5" or "Not rated";
    - `<button type="submit" name="stars" value="0" class="link-btn star-clear">Clear rating</button>`, only rendered when a personal rating exists.
- **Watched tab notes**:
  - `_message_notes(msg, None, return_to)`;
  - if `web.ratings_changed()`: `<p class="note">Your ratings changed - refresh to update your recommendations.</p>` plus `_refresh_form(return_to, "Refresh recommendations")`;
  - in sample mode, "Sample data - ratings aren't saved."
- **States**: when `get_result_nowait()` returns `None`, every tab except Added here uses `_waiting_screen("recs")` / `_error_screen` with `auto_refresh`. Added here renders straight from the DB. `library is None` shows an `.empty` "Connect Plex to browse your library" linking to `/settings?section=plex`. An empty watch history shows an `.empty` "Nothing watched yet". No matches shows an `.empty` "Nothing matches" with a link that clears the filters.
- **Home**: the "Added to library" tile's href becomes `/library?type=added`.
- **app.js**: new `handlers.rate(form, submitter)`. The submit listener passes `e.submitter`; if it's missing (old Safari), use the last clicked `button[name=stars]` in that form. Post `FormData(form)` plus `stars=<submitter.value>`.
  - On `ok`: update that form in place from `data.rating` (`on`/`from-plex` classes, `aria-pressed`, `.rating-text`, add/remove the Clear button), then `toast(message, {action: {label: "Update recommendations", run: post /refresh}})`.
  - On `!ok`: show an error toast (this is the sample-mode path).
  - On non-JSON: fall back via `failed()`.
  - Keep focus on the pressed star, or on the first star after a clear.
  - Optional: a capture-phase `error` listener on `img.poster` that swaps in a `.poster-empty` built with `textContent`.

## File ownership
| Agent | Files | Task |
|---|---|---|
| backend-dev | `db.py`, `profile.py`, `recommend.py`, `plex.py`, `tautulli.py`, `tmdb.py`, `http_util.py`, `sample.py`, `sources.py`, `web.py`, `tests/test_library.py` (new), `tests/test_web_async.py`, `tests/test_web.py`, `tests/test_profile.py`, `tests/test_recommend.py`, `tests/test_plex_tmdb.py`, `tests/test_tautulli.py`, `tests/test_sources.py`, `tests/test_http_util.py` | Storage, profile/recommend, snapshot data, helpers, `/rate`, `/poster`, GET `/library` wiring |
| frontend-dev | `pages.py`, `static/app.js`, `tests/test_ui.py` | Library page incl. Watched tab, poster helper, rate enhancement |
| ux-designer | `static/app.css` | Style only the new classes: `.toolbar` (+ `input[type="search"]`), `.pager`, `.pager-status`, `.rate`, `.stars`, `.star`, `.star.on`, `.star.from-plex`, `.star-clear`, `.rating-text` |
| orchestrator | `README.md` | Document Library tabs, Watched, ratings and the poster proxy after review |

**Phases: two.** Watched now lives inside `render_library`, so web.py needs no new import from pages.py and there's only one shared function, `render_library`.
1. **Phase 1: backend-dev ‖ ux-designer.** Backend does everything, including wiring GET `/library` to `render_library(**parse_list_query(query), msg=msg)` and writing the updated `tests/test_web.py`. Until phase 2, the current no-arg `render_library()` raises TypeError, so the `/library` tests in `tests.test_web` are **expected red**. They aren't in the after-edit hook's fast set, and `TestGetPassesPageParams` mocks `render_library`, so it's unaffected. Backend reports which tests are red.
2. **Phase 2: frontend-dev**, against the real web helpers. Phase 2 ends with the full suite green. Then the reviewer.

## Acceptance criteria
- [ ] Migration is idempotent: `_connect()` twice on the same file works, and set/clear/ratings round-trip; `set_rating(..., 6)` raises. Proof: `python3 -m unittest tests.test_library`
- [ ] `apply_ratings` maps stars to `stars*2` without mutating its input; `stars_from_ten(7.0) == 4`, `stars_from_ten("8") == 4`, `stars_from_ten(None) is None`. Proof: `python3 -m unittest tests.test_profile`
- [ ] Loving a sample title raises its genres' profile weight. A candidate linked from a 1★ title scores exactly ×0.4 against the same run without `disliked`, and is still in `items`. `disliked=None` gives identical output to today. Proof: `python3 -m unittest tests.test_recommend`
- [ ] `PlexClient.load()` returns a 4-tuple with library items, including ones with no TMDB id. Thumbs not under `/library/` are dropped. `poster()` sends the token in a header, not the URL. Proof: `python3 -m unittest tests.test_plex_tmdb`
- [ ] The Tautulli watched dict carries `rating_key`/`thumb`. Proof: `python3 -m unittest tests.test_tautulli`
- [ ] `get_bytes` returns `(type, data)`, enforces `max_bytes`, and has the same HTTP error message as `get_json`. Proof: `python3 -m unittest tests.test_http_util`
- [ ] `run()` fills `library`/`watched`/`thumbs`, and no item dict contains `thumb`. Live run applies DB ratings and passes `disliked`; sample run ignores them. `_load_live` returns `library=None` for Tautulli without PLEX_TOKEN. Every `PlexClient.load` mock is a 4-tuple, and no test reaches the network. Proof: `python3 -m unittest tests.test_sources`
- [ ] `parse_list_query` falls back on every bad value (`type=<script>`, `type=watched&sort=added` gives `recent`, `page=-1`, `page=²`, 500-char `q`). `list_view` filters, sorts (None last) and clamps pages. `watched_items` overlays stars only in live mode. `library_items(None, "added")` works with no build. Proof: `python3 -m unittest tests.test_library`
- [ ] `/rate` tests in `tests.test_web_async.TestRate`:
  - JSON set + clear (shape exactly as in the contract);
  - 400 for bad id and for `stars=6`/`stars=x`/missing;
  - no-JS 303 with `msg` to `return_to`, defaulting to `/library?type=watched`;
  - off-site `return_to` goes to `/recommended`;
  - sample refusal with no DB write, in both modes;
  - `ratings_changed` is set, and `/refresh` then starts a build even when the data is newer than `REFRESH_MIN_SECONDS`.

  Proof: `python3 -m unittest tests.test_web_async.TestRate`
- [ ] `/rate` added to `POST_ROUTES` and blocked cross-site. Proof: `python3 -m unittest tests.test_web_async.TestCsrf`
- [ ] `/poster` returns 200 with a mocked `PlexClient.poster`, and 404 for sample, bad key, unknown key, non-image and exception. The response never contains the token. Proof: `python3 -m unittest tests.test_web_async.TestPoster`
- [ ] GET `/library` passes parsed params + `msg` through to `render_library`; `/watched` stays 404. Proof: `python3 -m unittest tests.test_web_async.TestGetPassesPageParams`
- [ ] Sample-mode end to end:
  - `/library` lists "Interstellar" and "Dune" (owned, unwatched);
  - `/library?type=watched` lists "Severance" with 5 star buttons;
  - `/library?type=added` shows the old empty state "Nothing added yet" and the post-add "M (2020)" / "Added ";
  - the Library nav item is active on every tab;
  - the nav has no "Watched" item.

  Proof: `python3 -m unittest tests.test_web`
- [ ] Markup checks:
  - hostile titles are escaped on every tab;
  - `poster_key` renders `/poster?key=N` and no `thumb`/token appears;
  - `tmdb_id=None` renders a placeholder without crashing;
  - subtab links carry only `type` + `q`;
  - pager links keep params;
  - the toolbar is a GET form whose options follow the tab;
  - rate form: 5 `name="stars"` submit buttons, `aria-pressed` only on the personal rating, Clear only when rated, "From Plex" fallback;
  - the ratings-changed note and refresh form appear;
  - building/error/`library None`/no-match states render.

  Proof: `python3 -m unittest tests.test_ui`
- [ ] app.js: read-through check plus curl of `/rate` in JSON mode. Hamish clicks through `SAMPLE=1 PORT=8099 python3 web.py`: stars toast "Sample data - not saved", and tabs, search and paging work.
- [ ] Works without JS (GET filter form, rate POST + 303 + note) and with JS (in-place stars + toast).
- [ ] At 375px there's no horizontal scroll (5 stars fit a 2-column card: star buttons ≥28px wide, Clear on its own line; 5 subtabs scroll horizontally as `.subtabs` already does). Light and dark mode both pass AA. `:focus-visible` is kept on stars.
- [ ] Full suite green: `python3 -m unittest discover -s tests`

## Live-request budget
None. Fixtures and mocks only (`mock.patch.object(plex.PlexClient, "load"/"poster")`, `mock.patch("urllib.request.urlopen")` in test_http_util).

## Open questions
1. The Plex `/photo/:/transcode` endpoint behind `/poster` hasn't been checked against Hamish's server, and it can't be in tests. After deploy (with his OK), he opens the Library once. If posters are broken, the fallback is fetching `{PLEX_URL}{thumb}` directly, which works but sends full-size images. Not blocking.

---

## Task briefs

### backend-dev (phase 1, parallel with ux-designer)
Spec: `specs/library-and-ratings.md` (the Contract is fixed; report back if anything in it can't work). Live-request budget: none.

Own and edit: `db.py`, `profile.py`, `recommend.py`, `plex.py`, `tautulli.py`, `tmdb.py`, `http_util.py`, `sample.py`, `sources.py`, `web.py`, and tests `test_library.py` (new), `test_web_async.py`, `test_web.py`, `test_profile.py`, `test_recommend.py`, `test_plex_tmdb.py`, `test_tautulli.py`, `test_sources.py`, `test_http_util.py`.
1. Add the `ratings` table and functions (`db.py`), `apply_ratings`/`stars_from_ten` (`profile.py`) and `disliked` (`recommend.py`).
2. Change `PlexClient.load()` to a 4-tuple and add `parse_library_item`/`poster`. Add `rating_key`/`thumb` to Tautulli items, then `get_bytes`, `cached_details` and `sample.library_items`.
3. Change `_load_live()` to a 4-tuple, have `run()`/`generate_ai_recommendations()` fill `library`/`watched`/`thumbs` and apply ratings, live mode only.
4. In web.py: `LIBRARY_TABS`, `LIST_OPTIONS`, `parse_list_query`, `list_view`, `library_items`, `watched_items`, `plex_thumb`, the ratings-changed flag with its `_needs_build`/`_build_locked` changes, POST `/rate`, GET `/poster` (bytes `_send`), GET `/library` → `render_library(**parse_list_query(query), msg=msg)`, and `/rate` in `POST_ROUTES`.
5. Update every `PlexClient.load` mock to a 4-tuple; no test may reach the network.
6. Update `tests/test_web.py`: `/library?type=added` for the two existing library tests, sample `/library` and `?type=watched` content, nav checks.

The `/library` rendering tests in `tests.test_web` stay red until frontend-dev's phase 2. Everything else in `python3 -m unittest discover -s tests` must be green. Report files changed, test results (name the expected-red tests) and any contract deviations.

### frontend-dev (phase 2, after backend)
Spec: `specs/library-and-ratings.md`, especially "Markup contract". Own: `pages.py`, `static/app.js`, `tests/test_ui.py`. Live-request budget: none.
1. Replace `render_library()` with `render_library(tab, q, sort, show, page, msg)`, covering the five tabs including Watched, using `web.get_result_nowait`, `web.library_items`, `web.list_view`, `web.ratings_changed`, `web._is_sample` and `web.LIST_OPTIONS`. Read the `web.*` names inside functions.
2. Add the subtabs, toolbar GET form, pager, `_library_card`, `_watched_card` with the rate form, and all states. Extend `_poster_html` (poster_key proxy, no-tmdb_id placeholder). Point the Home "Added" tile at `/library?type=added`. Leave `NAV_SECTIONS` unchanged.
3. app.js: `handlers.rate` with submitter handling, in-place update, and a toast with an "Update recommendations" action. Keep the createElement/textContent rule; no innerHTML.
4. Add the test_ui tests listed in the acceptance criteria.

Use only existing classes plus the new ones named in the spec. Don't edit app.css; ask via the orchestrator if a class is missing.

Run `python3 -m unittest tests.test_ui`, then the full suite, which must be fully green including `tests.test_web`. Report files changed, test results, and anything in app.js that needs checking by hand.

### ux-designer (phase 1, parallel with backend)
Spec: `specs/library-and-ratings.md` ("Markup contract"). Own: `static/app.css` only. Style just these new classes:
- `.toolbar` (a wrapping flex row; `input[type="search"]` gets the same rule as `input[type="text"]`);
- `.pager`, `.pager-status`;
- `.rate`, `.stars` (inline row, no wrap at 375px in a 2-column card);
- `.star` (min 28×32px touch target; transparent button; off = `--muted`, `.on` = `--accent` colour, `.from-plex` dimmer with AA contrast kept);
- `.star-clear`, `.rating-text`.

Watched cards are not inside `.card-actions`, so the full-width button rules don't apply; make sure stars aren't stretched. Requirements: tokens only (add a token rather than hard-coding a colour), dark + light themes, focus outline kept, reduced-motion respected, no external assets. Run `python3 -m unittest tests.test_ui` and report the classes styled and any tokens added.
