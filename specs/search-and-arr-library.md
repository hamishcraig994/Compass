# Search any title, and Radarr/Sonarr in the Library

Status: approved with one amendment (backend phase 1 built). Hamish chose **live search as you type** (Q1); the other defaults stand.
Author: architect · Date: 2026-10-04 (amended the same day for live search: Decisions 1, 2, 4 and 13, the HTTP table, the markup contract, the phases, the acceptance criteria and the briefs)

## Goal
1. **Search.** A search box in the top bar finds *any* movie or show on TMDB, not just recommendations. Results show as poster cards, each with a status: In library, In Radarr, In Sonarr or Added. Anything not yet tracked can be sent to Radarr/Sonarr through the existing Add dialog (quality profile + "search now").
2. **Radarr/Sonarr in the Library tab.** The All / Movies / TV shows tabs list what Radarr and Sonarr already track as well as the Plex snapshot. Duplicates are merged, and each card shows source badges (Plex / Radarr / Sonarr) and a download state (Missing, Upcoming, 3/10 episodes, ...). A new **Source** filter narrows the list to Plex, Radarr/Sonarr, or Wanted (not downloaded yet).

No new per-page-view calls to Radarr/Sonarr. The build already fetches both full libraries for recommendation exclusion; this feature keeps that same data instead of throwing it away.

## Out of scope
- A JSON search API, a suggestions dropdown, and live search from the top-bar box. Live search only runs in the `/search` page's own box (Decision 13).
- Search results past TMDB's first page (top 20). People, keyword and genre search.
- Changing anything already in Radarr/Sonarr: delete, monitor/unmonitor, re-search, quality changes.
- Quality, file size, per-season detail and overviews for arr titles.
- Storing the arr snapshot in SQLite, a separate "refresh Radarr" button, and any DB schema change.
- A poster proxy for arr images (Q2). Arr-only titles on Home rows (`library_new` stays Plex-only).
- Rate or "Not interested" actions on search results. Add in sample mode (consistent with the rest of the app).
- Excluding Sonarr shows that have no `tmdbId` from recommendations. Title matching (Decision 7) is for display only.

## Decisions
1. **Search is its own page: `GET /search?q=&type=all|movie|tv`**, and it works without JS.
   - Rendered by `pages.render_search`. Data comes from `web.search_view`.
   - With JS, the page's own box searches live as you type (Decision 13). It swaps in a server-rendered fragment from `GET /search?...&partial=1`, so there is no JSON and no second copy of the card markup in JS.
2. **Where the search box lives.**
   - Desktop (>820px): an inline `form.topbar-search` in `.topbar-actions`, before Settings, on every page except `/search` itself.
   - Phones (≤820px): CSS hides the form and shows `a.search-link` (a magnifier `.nav-item`) that goes to `/search`.
   - Both are always in the markup and CSS picks one, so no JS is needed. ux may also switch to the icon at a wider breakpoint if the top bar gets tight.
   - `/search` has its own large form (with a type filter) at the top, and the top-bar form is left out there.
   - **The top-bar box never searches live.** Enter (submit) goes to `/search?q=`, where the live box takes over. A live dropdown on every page would be a new overlay component with its own focus management on the cinematic pages, and it would spend the TMDB budget from every page.
3. **TMDB calls.**
   - `type=all` uses `/search/multi`, keeping only `movie`/`tv` results and dropping people. `movie`/`tv` use `/search/movie` or `/search/tv`. One request, page 1, `include_adult=false`.
   - Results are capped at `SEARCH_MAX_RESULTS = 20`, in TMDB's relevance order.
   - Minimum query is 2 characters after trimming. Queries are cut to 100 characters (`web.MAX_QUERY_CHARS`), and internal whitespace collapses to single spaces.
4. **Cost and leak controls.**
   - The TMDB token stays server-side (TmdbClient as today).
   - Results are cached in SQLite for 24h under `tmdbsearch:v1:{kind}:{casefolded query}`. This prefix is distinct from the AI resolver's `search:` keys.
   - Uncached TMDB requests from search, the add dialog and `/add` share one process-wide budget per `TMDB_BUDGET_WINDOW = 60` seconds, guarded by a small lock of its own (never `compute_lock`). The budget matters because `/search` is a GET with no login, so any page you visit could make your browser fire requests at it.
   - **Amended for live search:**
     - `TMDB_BUDGET_MAX` goes from 30 to **60**, and search may use at most `60 - LOOKUP_RESERVE (10) = 50` of it.
     - Why: live typing spends roughly 3-6 uncached requests per title typed, so 30 would hit "limited" after about 6 searches a minute. Worse, it would then starve the Add dialog's one lookup.
     - 60 a minute is still a tiny fraction of TMDB's own rate limit (tens of requests per second), and the budget only exists to bound abuse.
     - Cached queries and the empty/short states never touch the budget.
   - When search's share is used up, the page shows `SEARCH_LIMITED`. The text changes to: "Too many new searches in the last minute - wait a moment and try again. Searches you've already made still work."
   - Requests use a short timeout (`SEARCH_TIMEOUT = 8` s, 1 retry), not the 20 s × 3 build default.
   - Upstream error text is **never** echoed into the page. A v3 TMDB key travels in the query string, so the page shows a fixed message and the error is only `print`ed.
5. **Status detection for search results** comes from the current build result (`get_result_nowait()`, which never blocks), plus `db.added_items()` and `db.dismissed()` in live mode.
   - The status keys are `plex` > `radarr`/`sonarr` > `added` > `none`, in that order of precedence.
   - Matching is by `(media_type, tmdb_id)`, with a title+year fallback (Decision 7).
   - Before the first build there's no result. The page then shows a "statuses may be missing" note, and Add stays available: Radarr/Sonarr's own `add()` already refuses duplicates.
6. **Add from search reuses the existing flow.** The Add link goes to `/add-dialog?type&id&return_to=/search?...`, which POSTs `/add` and then 303s back to the search page with `msg`.
   - Today both `render_add_dialog` and `/add` only know titles that are in the recommendation caches (`web._find_item`). A new `web.lookup_item` falls back to TMDB details: cached first, then one budgeted request.
   - That also makes search adds appear in "Added here", and the result then shows **Added**.
   - With JS, a successful add **doesn't remove** a search card (`data-keep-on-add`). Instead its Add link goes away and it gains an "Added" tag.
7. **Arr library model.**
   - `RadarrClient.library()` (`GET /api/v3/movie`) and `SonarrClient.library()` (`GET /api/v3/series`) return slim normalized items (ArrItem below).
   - These are the **same single requests** `_arr_exclusions` already makes on every live build. `sources` now keeps the items in `result["arr"]` alongside the Plex snapshot, and the exclusion keys are derived from them. Same cadence as the build: hourly, on Refresh, and after a settings change. Nothing is fetched per page view.
   - Sonarr: v4 series carry `tmdbId`, which is the key used. Series without one (Sonarr v3, or not matched yet) still show in the Library. Merge/status then falls back to `(media_type, title_key, year)`, but only when one side has no tmdb id.
8. **Merging into the Library.**
   - `web.library_items(result, "all"|"movie"|"tv")` returns Plex entries merged with arr items (`arr_library.merge`). A title in both appears once, with `sources: ["plex", "radarr"]`.
   - On merged entries the Plex poster/added date win. Arr-only entries use the arr's `added` date and poster.
   - "Watched" and "Added here" tabs are unchanged. Sort, search, show and paging are unchanged.
   - New `source` filter: `all` (Everywhere) / `plex` (In Plex) / `arr` (In Radarr/Sonarr) / `wanted` (in Radarr/Sonarr with state missing, partial or upcoming). It's a **select in the existing toolbar**, not new subtabs (Q3).
9. **Arr posters use the arr's `remoteUrl` directly.** Arr API keys never reach the browser, and nothing goes through `/poster` (Q2).
   - Only `https` URLs on hosts in `arr_library.POSTER_HOSTS = ("image.tmdb.org", "artworks.thetvdb.com", "thetvdb.com", "www.thetvdb.com", "assets.fanart.tv")` are kept. Anything else becomes the tinted placeholder.
   - TMDB `/t/p/original/` is rewritten to `/t/p/w342/`, the grid size.
   - The arr's relative `url` (`/MediaCover/...`, which needs the API key) is never used.
10. **Unreachable or unconfigured arr gives partial results, never a crash.**
    - Per service, `result["arr"][svc]["state"]` is `off` (not configured), `ok` or `error`.
    - On error the build adds a note (as today: "Couldn't reach Radarr: ...") and the Library shows a fixed note that its titles are missing.
    - If Plex isn't readable (Tautulli without PLEX_TOKEN) but arr items exist, the Library shows the arr items with a note, instead of the "Connect Plex" screen.
11. **Effect on recommendations: none to scoring.** Radarr/Sonarr titles with a tmdb id were already excluded and still are. `web.owned_keys` adds arr keys, so the `in_library` flag is consistent if one slips through.
12. **Sample mode demos both features with fixtures and no requests.**
    - `sample.arr_library()` supplies the arr items, and `SampleTmdb.search_titles` searches the built-in catalogue by substring.
    - The fixtures use only titles that are owned already, filtered out of recommendations already, or new catalogue entries that can never be candidates. So sample recommendations stay byte-for-byte the same.
    - No Add buttons in sample mode, as everywhere else.
    - Live search works in sample mode too: the partial goes through the same `search_view`, which uses `SampleTmdb`.
13. **Live search (`/search` page only; progressive enhancement over Decision 1).**
    - **Fragment.** `GET /search?q=&type=&partial=1` returns only the results fragment, from `pages.render_search_results`. The full page embeds exactly the same fragment through one shared helper, `_search_fragment(view, return_to)`.
    - **Same input rules as the full page.** Bad `type` falls back to `all`, `q` is collapsed and cut, and `partial` other than `"1"` gives the full page. So it is always 200 and never 400.
      - This deliberately differs from the coordinator's "400 on bad params". `q` is free text and every other param already has a defined fallback. A 400 would make JS and no-JS behave differently for the same URL.
      - It matches `/add-dialog`'s `partial` handling.
    - **Headers.** `Content-Type: text/html; charset=utf-8` and `Cache-Control: no-store`. Statuses change after an add, and the browser must never show a cached fragment as a page.
    - **One `innerHTML`.** The add dialog's `body.innerHTML = html` becomes a shared `setFragment(node, html)` helper, which both callers use.
      - It only ever receives text fetched same-origin from our own `partial=1` endpoints.
      - It refuses (returns false) when the text contains `<html`, and the caller then falls back to a normal navigation.
      - `<script>` in innerHTML never runs, and the fragments are escaped server-side anyway.
    - **Client behaviour** (app.js, on `form[data-search-live]`):
      - **Debounce.** An `input` event (ignored while `e.isComposing`; run on `compositionend`) waits 400 ms, then normalizes `q` the way the server does: trim and collapse whitespace.
      - **What a keystroke does.**
        - Length 1: do nothing. No request, and the current results stay.
        - Length 0, or 2 and up: fetch, unless `(q, type)` equals the last key sent. So retyping the same text, or adding a trailing space, costs nothing.
      - **Type pills.** A `change` on a pill runs at once, with no debounce.
      - **Escape.** Escape in the box clears it, cancels any pending request, and fetches the empty state.
      - **Enter** is a normal form submit, so the full page loads; the query is cached server-side, so it's free.
      - **Request.** `fetch(url + "&partial=1", {credentials: "same-origin", signal})`.
        - An `AbortController` aborts the previous in-flight request. A sequence counter also drops any response that isn't the latest, so a late or stale response never overwrites a newer one.
        - While loading, the results region gets `aria-busy="true"` and `.is-loading` (CSS dims the previous results, which stay visible), and the spinner is shown.
      - **Success.** `setFragment(region, html)`, then copy the fragment root's `data-announce` into `.search-status` via `textContent`. That element is the only live region, so screen readers hear "12 matches for dune", not 20 cards.
        - Then `history.replaceState` to `/search?q=..&type=..` (q omitted when empty). Never `pushState`, so Back leaves the page and refresh re-renders the same search.
        - Focus never moves; it stays in the box.
      - **Network error, non-2xx, or a non-fragment.** Clear busy and replace the region's content with a JS-built `<p class="note error" role="alert">Couldn't reach the server - try again.</p>`.
        - No automatic retry. The next request only comes from the next user input. That holds for the `limited` state too: it's just a fragment, and typing on simply gets `limited` again for free, with no TMDB call, until the window frees up.
      - Swapped-in cards work through the existing delegated handlers (detail modal, Add dialog, poster `error` fallback, `markAdded`).

## Contract

### Data shapes
```python
# ArrItem - radarr.normalize_movie(raw) / sonarr.normalize_series(raw); all keys always present
{"media_type": "movie"|"tv", "service": "radarr"|"sonarr",
 "tmdb_id": int|None,            # raw["tmdbId"] if a positive int, else None
 "tvdb_id": int|None,            # sonarr raw["tvdbId"]; radarr: None
 "title": str,                   # raw["title"] or "?"
 "year": int|None,               # raw["year"] if a positive int
 "added_at": str|None,           # raw["added"] ISO string; None if missing or starting "0001-"
 "monitored": bool,
 "arr_state": "downloaded"|"partial"|"missing"|"upcoming"|"unmonitored",
 "episodes": {"have": int, "total": int}|None,   # tv: statistics.episodeFileCount / episodeCount (missing -> 0); movie: None
 "poster_url": str|None,         # arr_library.poster_url(raw.get("images"))
 "url": str|None}                # https://www.themoviedb.org/{movie|tv}/{tmdb_id} when tmdb_id
```
`arr_state` rules:
- **Radarr:** `hasFile` gives downloaded. Otherwise, not `monitored` gives unmonitored. Otherwise, `isAvailable is False` (or, when that key is absent, `status in ("announced", "tba")`) gives upcoming. Otherwise missing.
- **Sonarr:** `total > 0 and have >= total` gives downloaded. Otherwise `have > 0` gives partial. Otherwise not `monitored` gives unmonitored. Otherwise `status == "upcoming"` or `total == 0` gives upcoming. Otherwise missing.

```python
# result["arr"] - set by sources.run() on every build (live and sample). Old in-memory results lack it: always .get()
{"items": [ArrItem],
 "radarr": {"state": "off"|"ok"|"error", "count": int},
 "sonarr": {"state": "off"|"ok"|"error", "count": int}}

# Merged library entry - web.library_items(result, "all"|"movie"|"tv"); existing Plex entry keys plus:
{..., "sources": ["plex"?, "radarr"?, "sonarr"?],   # in that order, never empty
      "arr_state": str|None, "episodes": dict|None,
      "poster_url": str|None}   # arr poster only when the entry has no Plex poster_key; else absent/None
# arr-only entries: {"media_type","tmdb_id","title","year","added_at","watched": False,"progress": None,
#                    "poster_key": None,"poster_url","url","sources": [svc],"arr_state","episodes"}

# SearchItem - tmdb.normalize_search(raw, media_type)
{"media_type", "tmdb_id", "title", "year", "release_date", "overview",
 "poster_url",                   # w342 or None
 "url", "vote_average", "vote_count"}

# SearchResult - SearchItem + arr_library.annotate() fields
{..., "status": "plex"|"radarr"|"sonarr"|"added"|"none", "in_library": bool,   # status != "none"
      "sources": [...], "arr_state": str|None, "episodes": dict|None,
      "watched": bool, "dismissed": bool}

# SearchView - web.search_view(q, kind)
{"q": str, "kind": "all"|"movie"|"tv",
 "state": "empty"|"short"|"ok"|"error"|"limited",
 "message": str|None,            # fixed text for error/limited (web.SEARCH_ERROR / web.SEARCH_LIMITED), else None
 "results": [SearchResult],      # [] unless state == "ok"
 "capped": bool,                 # TMDB has more than one page
 "library_known": bool,          # a build result existed to check statuses against
 "sample": bool}
```

### Python interface
```python
# arr_library.py (NEW, backend-dev) - pure: no I/O, no db/web/sources imports, never mutates inputs
POSTER_HOSTS = ("image.tmdb.org", "artworks.thetvdb.com", "thetvdb.com", "www.thetvdb.com", "assets.fanart.tv")
WANTED_STATES = ("missing", "partial", "upcoming")
def poster_url(images) -> str|None: ...     # first coverType=="poster" remoteUrl; Decision 9 rules; bad input -> None
def title_key(title) -> str: ...            # casefold, keep only str.isalnum() chars
def same_title(a, b) -> bool: ...           # same media_type, title_key equal, years equal or either None
def merge(plex_items, arr_items) -> list: ...   # Decision 8; plex_items may be None (-> treated as [])
def annotate(items, plex_items=None, arr_items=(), watched_items=(), added_keys=frozenset(),
             dismissed=frozenset()) -> list: ...   # copies + SearchResult fields
# Match rule (merge and annotate): equal (media_type, tmdb_id) when both have one; else same_title() when
# either side lacks a tmdb_id. First match wins.

# radarr.py / sonarr.py (backend-dev)
def normalize_movie(raw) -> dict: ...       # radarr.py, module level
def normalize_series(raw) -> dict: ...      # sonarr.py, module level
class RadarrClient:
    def library(self) -> list: ...          # [normalize_movie(m) for m in GET /api/v3/movie]; raises on failure
class SonarrClient:
    def library(self) -> list: ...          # GET /api/v3/series
# existing_tmdb_ids() may stay (its tests use it); sources stops calling it.

# tmdb.py (backend-dev)
SEARCH_MAX_AGE, SEARCH_MAX_RESULTS, SEARCH_TIMEOUT = 24 * 3600, 20, 8
def normalize_search(raw, media_type) -> dict: ...
class TmdbClient:
    def _get(self, path, params=None, timeout=20, retries=2): ...   # passes both through to get_json
    def cached_search(self, query, kind="all") -> dict|None: ...    # cache only, never requests
    def search_titles(self, query, kind="all") -> dict: ...         # request (timeout=SEARCH_TIMEOUT, retries=1)
        # + cache write; -> {"results": [SearchItem] (max 20), "capped": bool}; raises on network/HTTP error
# (the existing search(media_type, title, year) for the AI resolver is unchanged)

# sample.py (backend-dev)
def arr_library(now=None) -> dict: ...      # a result["arr"] dict, both services "ok" (fixtures below)
class SampleTmdb:
    def cached_search(self, query, kind="all") -> dict: ...   # same as search_titles (all local)
    def search_titles(self, query, kind="all") -> dict: ...   # casefold substring over catalogue titles,
                                                              # kind filter, vote_count desc, max 20

# sources.py (backend-dev)
def _arr_library(notes) -> tuple: ...       # replaces _arr_exclusions -> (keys: set, arr: dict); one library()
                                            # call per configured service; exception -> state "error" + note as today
def _load_live() -> tuple: ...              # now (watched, library_keys, notes, library, arr); update both callers
# run(): result["arr"] = arr (live) / sample.arr_library() (sample). Sample also unions the sample arr keys into
# library_keys before recommend(), mirroring live.

# web.py (backend-dev)
SEARCH_KINDS, SEARCH_MIN_CHARS = ("all", "movie", "tv"), 2
TMDB_BUDGET_MAX, TMDB_BUDGET_WINDOW = 60, 60  # AMENDED (phase 3): was 30
LOOKUP_RESERVE = 10                            # NEW (phase 3): the last 10 uses are kept for lookup_item
SEARCH_ERROR = "Search isn't available right now - TMDB didn't answer. Try again in a moment."
SEARCH_LIMITED = ("Too many new searches in the last minute - wait a moment and try again. "
                  "Searches you've already made still work.")   # AMENDED (phase 3)
LIST_SOURCES = {"all": ("all", "plex", "arr", "wanted"), "movie": (same), "tv": (same),
                "watched": ("all",), "added": ("all",)}    # first = default. LIST_OPTIONS is UNCHANGED.
def _tmdb_budget(reserve=0) -> bool: ...    # True and records one use if len(uses) < TMDB_BUDGET_MAX - reserve;
                                            # own lock. AMENDED (phase 3): search_view passes reserve=LOOKUP_RESERVE;
                                            # lookup_item keeps reserve=0
def parse_search_query(query) -> dict: ...  # parse_qs dict -> {"q": collapsed, cut to 100, "kind": valid or "all"}
def search_view(q, kind) -> dict: ...       # SearchView. Sample: SampleTmdb; live: TmdbClient(config.TMDB_TOKEN).
    # empty q -> "empty"; len < 2 -> "short"; cached_search hit -> no budget used; miss -> _tmdb_budget() or
    # "limited"; exception -> "error" (print it). Statuses via arr_library.annotate with result["library"],
    # result["arr"]["items"], result["watched"], and (live only) db.added_items() keys + db.dismissed().
def lookup_item(media_type, tmdb_id) -> dict|None: ...
    # _find_item() -> else None in sample mode or without config.TMDB_TOKEN -> TmdbClient.cached_details()
    # -> else (if _tmdb_budget()) TmdbClient.details(); any exception -> None. Holds no lock while fetching.
def arr_status(result) -> dict: ...         # {"radarr": state, "sonarr": state}; "off" when missing
def library_items(result, tab): ...         # all/movie/tv: arr_library.merge(result.get("library"), arr items);
    # returns None only when result["library"] is None AND there are no arr items. watched/added unchanged.
def list_view(items, tab="all", q="", sort="added", show="all", page=1, per_page=LIST_PAGE_SIZE,
              source="all"): ...            # source: plex -> "plex" in sources; arr -> radarr/sonarr in sources;
                                            # wanted -> arr_state in WANTED_STATES; ignored for watched/added
def owned_keys(result): ...                 # + {(type, tmdb_id)} of result["arr"]["items"] with a tmdb_id
def parse_list_query(query): ...            # PHASE 3 ONLY: adds "source" (validated against LIST_SOURCES[tab])
# /add: on success only, item = lookup_item(type, id); if item: db.record_added(item). (Calling it only on
# success keeps failed adds request-free.) Everything else in /add is unchanged.

# pages.py (frontend-dev)
def render_search(q="", kind="all", msg="") -> str: ...      # full page; calls web.search_view once
def render_search_results(q="", kind="all") -> str: ...     # the fragment only (no _shell); calls web.search_view once
def _search_fragment(view, return_to) -> str: ...           # the ONE renderer of the fragment, used by both above
def _search_url(q, kind) -> str: ...                        # "/search?q=..&type=.." (never partial); the return_to
                                                            # for Add links in BOTH full page and fragment
def render_library(tab="all", q="", sort=None, show="all", page=1, msg="", source="all") -> str: ...
def _shell(...): ...                        # signature unchanged; section "search" -> heading "Search", no nav
                                            # item active except .search-link; omits .topbar-search
# render_add_dialog: web._find_item -> web.lookup_item (still None in sample mode); "/search" return_to -> section "search"
```

### Sample fixtures (backend-dev, `sample.py`)
New `_CATALOGUE` entries are never candidates. They have `vote_count < 1000`, `recommendations=[]`, are not in `_RELEASED_DAYS_AGO` and are not in `_TRENDING`:
- `1040` "Paper Moons" (movie, 2025, Drama, 800 votes)
- `1041` "Glass Harbour" (movie, 2024, Thriller, 600)
- `2040` "Night Shift Diaries" (tv, 2023, Drama/Comedy, 400)
- `2041` "Low Tide" (tv, 2025, Crime/Drama, 300)

`arr_library()`:

| Service | Title (id) | State | Notes |
|---|---|---|---|
| Radarr | Interstellar (1001), Dune (1005) | downloaded | in Plex too: merged |
| Radarr | Coming Soon (1034) | upcoming | |
| Radarr | Tiny New Thing (1035) | missing | |
| Radarr | Paper Moons (1040) | downloaded | not in Plex |
| Radarr | Glass Harbour (1041) | unmonitored | |
| Sonarr | Black Mirror (2003) | downloaded | in Plex too |
| Sonarr | Night Shift Diaries (2040) | partial, 5/10 | |
| Sonarr | Low Tide (2041) | missing | `tmdb_id=None`, exercises the title fallback |

`poster_url` is None throughout. `added_at` is a few days ago. **backend-dev must prove sample recommendations are unchanged** (same item keys, same order, with and without the arr keys in `library_keys`). If 1034/1035 turn out to be candidates, swap in other non-candidates and report.

### HTTP
| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/search` | query `q`, `type` (`all`\|`movie`\|`tv`; else `all`), `msg` | 200 `render_search(**parse_search_query(query), msg=msg)`. Always 200: bad input falls back, it never 400s. HTML only, no JSON mode |
| GET | `/search?…&partial=1` | the same `q`, `type`; `msg` ignored | 200 `render_search_results(**parse_search_query(query))`, with `Content-Type: text/html; charset=utf-8` and `Cache-Control: no-store`. No `<html>` and no shell. Any other `partial` value gives the full page. Same fallbacks, never 400 (Decision 13) |
| GET | `/library` | + query `source` (phase 3) | unchanged otherwise |
| GET | `/add-dialog` | unchanged | Now also works for any TMDB title via `lookup_item`. Unknown or unavailable gives the existing "Can't add this one" body (200) |
| POST | `/add` | unchanged fields, CSRF and JSON/303 contract | Unchanged, except that success records the title via `lookup_item` |

Unknown routes still give 404. No new POST routes, so the CSRF route lists don't change. The orchestrator adds `GET /search?q=&type=&msg=&partial=1` and the `/library` `source` param to api-conventions after review. Design-system: the single `innerHTML` now lives in `setFragment`, which is used for the add-dialog and search fragments.

### Markup contract (pages.py / app.js / app.css share these)
Every untrusted string (titles, q, overview, arr titles) goes through `escape`. Every URL goes through `_web_url`. Arr poster hosts are already filtered server-side, but `_web_url` still applies. No new inline `style` except `--h:{int}` via `_poster_html`.

**Top bar** (`_shell`, every page). `.topbar-actions` becomes:
```html
<div class="topbar-actions">
  <!-- omitted when section == "search" -->
  <form class="topbar-search" method="get" action="/search" role="search">
    <label class="visually-hidden" for="topbar-q">Search all movies and TV</label>
    <input class="search-input" id="topbar-q" type="search" name="q" maxlength="100"
           placeholder="Search movies &amp; TV" autocomplete="off" enterkeyhint="search">
    <button type="submit" class="search-submit" aria-label="Search">{magnifier svg, aria-hidden}</button>
  </form>
  <a class="nav-item search-link[ active]" href="/search"[ aria-current="page"]>{magnifier svg}<span>Search</span></a>
  {settings .nav-item as today}
</div>
```
- `_NAV_ICONS` gains `search` (fixed SVG). `.search-link` isn't in `NAV_SECTIONS` and not in the bottom nav.
- CSS shows `.topbar-search` and hides `.search-link` on wide screens, and the reverse at ≤820px (ux may move that switch wider). The accessible name stays "Search" either way.

**Search page** (`render_search`, `_shell(body, "search", subtitle)`, `return_to = _search_url(q, kind)`):
```html
<div class="search-page">
  {_message_notes(msg, None, return_to)}
  [<p class="note">Sample data - searching the built-in sample catalogue.</p>   when view["sample"]]
  <form class="search-form" method="get" action="/search" role="search" data-search-live>
    <label class="search-label" for="search-q">Search all movies and TV</label>
    <div class="search-row">
      <input class="search-input" id="search-q" type="search" name="q" value="{q}" maxlength="100" minlength="2"
             required autocomplete="off" enterkeyhint="search" aria-controls="search-results"[ autofocus  - only when q is empty]>
      <span class="spinner sm search-spinner" data-search-spinner aria-hidden="true" hidden></span>
      <button type="submit" class="btn-add">Search</button>
    </div>
    <fieldset class="search-types"><legend class="visually-hidden">Show</legend>
      <label class="search-type"><input type="radio" name="type" value="all"[ checked]> All</label>
      <label class="search-type"><input type="radio" name="type" value="movie"> Movies</label>
      <label class="search-type"><input type="radio" name="type" value="tv"> TV shows</label>
    </fieldset>
  </form>
  <p class="search-status visually-hidden" role="status" aria-live="polite" data-search-status></p>  <!-- empty; JS fills it -->
  <div class="search-results-region" id="search-results" data-search-results aria-busy="false">
    {_search_fragment(view, return_to)}
  </div>
</div>
```
**Fragment** (`_search_fragment`, the only renderer; it's also the whole `partial=1` body):
```html
<div class="search-fragment" data-search-fragment data-search-state="{state}" data-announce="{announce}">
  {state block}
</div>
```
`{announce}` is a plain-text summary, escaped with `quote=True`:
- empty → "";
- short → "Type at least 2 characters";
- error/limited → the message;
- ok with no results → `No matches for "{q}"`;
- ok → `{n} match(es) for "{q}"`.

State blocks. None of them carries `role="status"`/`role="alert"` any more, because `.search-status` is the one live region and an alert inside swapped content would announce twice:
- `empty`: `<p class="muted search-hint">Find any movie or show and send it to Radarr or Sonarr.</p>` (wording is frontend's call).
- `short`: `<p class="note">Type at least 2 characters.</p>`.
- `error` / `limited`: `<p class="note error">{message}</p>`.
- `ok` with 0 results: `<div class="empty"><h3>No matches for &ldquo;{q}&rdquo;</h3><p>Check the spelling or try the original title.</p></div>`.
- `ok`:
  - `<p class="search-summary muted">{n} match(es) for &ldquo;{q}&rdquo;[ - showing the top 20. Add a year or more words to narrow it down.]</p>`
  - then `[<p class="note muted-note">Your library is still loading, so some "In library" labels may be missing.</p>  - when not library_known]`
  - then `<div class="grid search-results" data-grid>{cards}</div>`.

Other rules:
- Subtitle (full page only): `Results for "{q}"` when there is a q, else "". With JS it isn't updated live, so frontend may drop it if it looks stale.
- Add links in the fragment use `return_to = _search_url(q, kind)`, never a URL with `partial`.

**Search card** (`_search_card(item, return_to, can_act)`):
```html
<article class="card search-card" data-card="{type}-{id}" data-keep-on-add>
  <div class="card-poster" data-open-detail>{_poster_html(item)}
    <div class="poster-top"><span class="badges">
      [<span class="lib-tag status-tag status-{status}">{label}</span>]   <!-- not for "none" -->
      [<span class="badge">Watched</span>][<span class="badge">Not interested</span>]
    </span></div>
    <span class="kind">{Movie|TV}</span></div>
  <div class="card-info"><h3 class="title">{Title (Year)}</h3>
    [<p class="card-sub arr-line">{arr state label}</p>]                  <!-- when arr_state -->
    <details class="card-details"><summary>Details<span class="visually-hidden">: {title}</span></summary>
      <div class="detail-body"><p class="card-meta"><span>{Movie|TV}</span>[<span>{year}</span>][<span>TMDB {vote_average:.1f}</span> when vote_count]</p>
        <p class="overview">{overview or "No overview available."}</p>[ext-link "More on TMDB"]</div></details>
    [<div class="card-actions">                                         <!-- omitted when empty -->
      [{_add_link(item, return_to)}  when status == "none" and _can_add(item, can_act)]
      [<form class="inline" method="post" action="/undismiss" data-enhance="undismiss">{_hidden_fields}
         <button type="submit" class="link-btn">Show in recommendations again</button></form>  when dismissed and can_act]
    </div>]
  </div>
</article>
```
- Status labels: plex "In library", radarr "In Radarr", sonarr "In Sonarr", added "Added".
- `can_act = not view["sample"]`.

**Arr state labels** (shared by search and library, frontend constant): downloaded "Downloaded", partial "{have}/{total} episodes" (falling back to "Partly downloaded"), missing "Missing", upcoming "Upcoming", unmonitored "Unmonitored".

**Library** (`render_library`, tabs all/movie/tv only):
- **Card** (`_library_card`):
  - In `.poster-top .badges`, after the existing Watched/Started badge, add `<span class="badge arr-state state-{arr_state}">{label}</span>`. Show it when `arr_state` is set, **except** downloaded titles that are also in Plex (noise).
  - After `.card-sub`, add `<p class="card-sources"><span class="visually-hidden">In </span>{<span class="src src-{key}">{Plex|Radarr|Sonarr}</span> per sources}</p>`.
  - Text labels everywhere, so nothing is colour-only.
- **Toolbar:** add `<label>Source<select name="source">` with options all "Everywhere", plex "In Plex", arr ("In Radarr" on movie, "In Sonarr" on tv, "In Radarr or Sonarr" on all), wanted "Wanted (not downloaded)". Show it only when `LIST_SOURCES[tab]` has more than one option and `web.arr_status(result)` has any service that isn't `off`.
- **`_library_url`** gains `source`, omitted when it's `all`. Pager links and `return_to` keep it. Subtabs keep only `q`, as today.
- **Notes** above the toolbar:
  - per service in state `error`: `<p class="note">Couldn't reach {Radarr|Sonarr} at the last refresh, so its titles aren't shown.</p>`;
  - when `result["library"] is None` but items exist: `<p class="note">Plex isn't connected, so only Radarr and Sonarr titles are shown.</p>`.
- **Empty states:** the unfiltered text becomes "Nothing in Plex, Radarr or Sonarr yet." The filtered empty state with a `q` adds `<a class="btn-ghost" href="/search?q={q}">Search all movies and TV for "{q}"</a>`.

**app.js** (frontend-dev). Keep the createElement/textContent rule. Exactly one `innerHTML` stays in the file, now inside `setFragment`.
- **`setFragment(node, html)`.** The only `innerHTML`.
  - It returns false without touching `node` if `/<html[\s>]/i` matches.
  - Its callers are `openAddDialog`, which replaces its current `body.innerHTML = html` and keeps its existing full-page fallback, and the live search.
  - A comment states that it only ever receives our own server's escaped `partial=1` fragments.
- `handlers.add` on success: for each `findCards(type, id)` card:
  - if it has `data-keep-on-add`, call `markAdded(card)`: remove every `a[data-add-dialog]` in it, and then add a `<span class="lib-tag status-tag status-added">Added</span>` to its `.poster-top .badges`, or relabel an existing `.status-tag` to "Added";
  - otherwise `removeCard` as today.
- **Live search** on `form[data-search-live]`, exactly per Decision 13.
  - Elements: `[data-search-results]` (the region; toggles `aria-busy` and `.is-loading`), `[data-search-spinner]` (toggles `hidden`), `[data-search-status]` (`textContent` set from `[data-search-fragment]`'s `data-announce`).
  - Constants: `SEARCH_DEBOUNCE_MS = 400`, `SEARCH_MIN = 2`.
  - The pieces: `AbortController` when available, a sequence counter, the last-key check, `isComposing`/`compositionend`, Escape to clear, pills on `change`, `history.replaceState` (guarded by `window.history && history.replaceState`), and the error note built with `el()`.
  - Do nothing when the page loads: the server already rendered the current results.
- The detail modal on swapped-in cards works through the existing delegation (`[data-card]`, `[data-open-detail]`, `.card-details > summary`).

**app.css** (ux-designer): see the brief. Tokens only, both light/dark, all six themes. The new states are:
- `.search-results-region.is-loading` dims its content, at about 0.55 opacity, with `pointer-events: none`;
- `[aria-busy="true"]` gets no other visual;
- `.search-spinner` sits inside `.search-row`;
- reduced motion means no fade transition, and the spinner still shows its static state.

## File ownership
| Agent | Files | Task |
|---|---|---|
| backend-dev | `arr_library.py` (new), `radarr.py`, `sonarr.py`, `tmdb.py`, `sample.py`, `sources.py`, `web.py`; tests `test_arr_library.py` (new), `test_radarr_sonarr.py`, `test_plex_tmdb.py`, `test_sources.py`, `test_library.py`, `test_web_async.py`, `test_web.py` | Data layer, search, lookup, merge, then routes |
| frontend-dev | `pages.py`, `static/app.js`, `tests/test_ui.py` | Top-bar search, search page, fragment and cards, library badges/filter/notes, add-dialog lookup, `markAdded`, `setFragment`, live search JS |
| ux-designer | `static/app.css` | Style the new classes, including the loading/dimmed state |
| orchestrator | `README.md`, `.claude/skills/api-conventions` (routes), `.claude/skills/design-system` (components) | After review. Also the optional mockup (see report) |

`db.py`, `config.py`, `browse.py`, `recommend.py`, `profile.py`, `plex.py`, `tautulli.py`, `ai.py`, `themes.py` and `settings_page.py` don't change. No file has two owners.

**Phases.** web.py imports page names from pages.py at load time, and the Handler calls `render_library(**parse_list_query(query))`. So:
1. **Phase 1: backend-dev ‖ ux-designer.** Backend: **DONE and green (685 tests)** before the live-search amendment. ux: if it has already run, resume it for the live-state classes only (`.search-results-region(.is-loading)`, `.search-spinner`); otherwise brief it with the amended brief.
   - Backend does everything in the Python interface except the `/search` route, the `render_search` import and the `source` key in `parse_list_query`. If `parse_list_query` returned `source` before `render_library` accepts it, the Handler would raise TypeError.
   - The `/add` change is in phase 1.
   - The full suite stays green. Because sample Library counts change, backend updates `test_web` / `test_library` as needed.
   - ux works from this spec (and the mockup if one is made).
2. **Phase 2: frontend-dev**, after backend phase 1. It needs `web.search_view`, `lookup_item`, `arr_status`, `LIST_SOURCES` and `list_view(source=)`.
   - It also builds the fragment helpers, `render_search_results`, `setFragment` and the live-search JS.
   - Test `render_search` and `render_search_results` directly, because the route isn't there yet.
   - test_ui tests that patched `web._find_item` for the add dialog switch to `web.lookup_item`.
   - Until phase 3, the JS's `partial=1` request gets the full page. `setFragment` refuses it, so the page falls back to navigating. That's harmless, but live search can only be checked by hand after phase 3.
3. **Phase 3: backend-dev** (resume, about 15 minutes).
   - Add `GET /search`, including the `partial=1` branch, and `render_search` + `render_search_results` to `from pages import`.
   - Add `source` to `parse_list_query`.
   - Budget amendment: `TMDB_BUDGET_MAX = 60`, `LOOKUP_RESERVE = 10`, `_tmdb_budget(reserve=0)`, `search_view` passing `reserve=LOOKUP_RESERVE`, and the new `SEARCH_LIMITED` text. Update the existing limited test (31st becomes 51st).
   - Add the end-to-end tests. The full suite goes green.

Then the reviewer. One agent can't do all of this because of ownership. ux is worth a separate run, because there are about 18 new classes and states across the top bar, search page and library cards.

## Acceptance criteria
- [ ] **arr_library** (`python3 -m unittest tests.test_arr_library`):
  - `poster_url`: TMDB `original` becomes `w342`; a TVDB https URL is kept; `http://`, `javascript:`, an unknown host, a relative `/MediaCover` URL, missing images and non-list input all give None.
  - `title_key`/`same_title`: case, punctuation, year equal or None.
  - `merge`:
    - dedupe by tmdb id, and the title fallback only when one side lacks an id;
    - the order of `sources`;
    - Plex poster_key and added_at win;
    - arr-only entry shape;
    - `None` Plex input;
    - first match wins;
    - inputs not mutated.
  - `annotate`: status precedence plex > radarr/sonarr > added > none; watched and dismissed flags; `in_library`.
- [ ] **Radarr/Sonarr** (`python3 -m unittest tests.test_radarr_sonarr`, `mock.patch.object(radarr, "get_json")` / `sonarr`):
  - every `arr_state` branch for both;
  - `added` `0001-01-01T00:00:00Z` becomes None;
  - `tmdbId` 0 or missing becomes None;
  - missing `statistics` gives `{"have": 0, "total": 0}`;
  - `library()` makes exactly one GET to `/api/v3/movie` / `/api/v3/series` with the `X-Api-Key` header, and the API key appears in no returned value.
- [ ] **TMDB search** (`python3 -m unittest tests.test_plex_tmdb`, `mock.patch.object(tmdb, "get_json")`):
  - `all` hits `/search/multi` and drops `person`; `movie`/`tv` hit `/search/{kind}`;
  - `include_adult=false`, timeout 8 and retries 1 are passed;
  - capped at 20, and `capped` follows `total_pages > 1`;
  - `normalize_search` handles movie and tv dates/names and a missing poster;
  - the cache key is `tmdbsearch:v1:`; `cached_search` never requests; a second `search_titles` call with the same query writes the cache;
  - the existing `search()` behaviour is unchanged.
- [ ] **sources** (`python3 -m unittest tests.test_sources`, `library()` mocked):
  - one `library()` call per configured service per `_load_live`;
  - `library_keys` includes arr tmdb ids;
  - `result["arr"]` shape with off/ok/error;
  - a failure gives a note and state `error`, never a crash;
  - `generate_ai_recommendations` still works with the 5-tuple;
  - sample `run()` has `result["arr"] == sample.arr_library()`-shaped data, and **the sample recommendation keys and order are unchanged**.
- [ ] **web data** (`python3 -m unittest tests.test_library tests.test_web_async`):
  - `library_items` merges for all/movie/tv and returns None only when there's no Plex and no arr items;
  - `list_view(source=...)` for each source, ignored on watched/added;
  - `owned_keys` includes arr keys;
  - `arr_status` defaults to `off`;
  - `parse_search_query` collapses, cuts and defaults;
  - `search_view` covers empty / short / ok / error (exception text not in `message`) / limited (31st uncached search in the window as built in phase 1; 51st after the phase 3 amendment; cached searches don't count) / `library_known` False / sample;
  - `lookup_item` checks the cache first, then cached details, then one budgeted `details()`; returns None in sample mode, without a token, or on an exception;
  - `/add` success for a title in no cache records it in `added` (with `TmdbClient.details` mocked), and a failed add makes no TMDB call.
- [ ] **Phase 3 routes** (`python3 -m unittest tests.test_web_async tests.test_web`):
  - `GET /search` passes q/kind/msg through (mocked `render_search`);
  - `type=bogus` becomes `all`;
  - a 300-character q is cut to 100;
  - `GET /library?source=wanted&type=tv` reaches `render_library(source="wanted")`, and a bad source becomes `all`.
  - **Partial route** (mocked renderers):
    - `/search?q=dune&type=movie&partial=1` calls `render_search_results(q="dune", kind="movie")` and not `render_search`;
    - the response has `Content-Type: text/html; charset=utf-8` and `Cache-Control: no-store`;
    - `partial=0`, `partial=yes` and no `partial` give the full page;
    - `type=bogus&partial=1` gives kind `all` and status 200;
    - `msg` is ignored on the partial.
  - **Budget** (`web._tmdb_budget` with a cleared `_budget_uses`):
    - search's 51st uncached request in the window is `limited`, while `lookup_item`'s `_tmdb_budget()` still succeeds up to 60;
    - cached searches and short/empty queries never record a use;
    - `SEARCH_LIMITED` equals the new text.
  - **Sample end to end:**
    - `/search?q=dune` shows Dune with "In library";
    - `/search?q=low+tide` shows "In Sonarr" and "Missing";
    - `/search?q=paper` shows "In Radarr";
    - `/search?q=x` shows the 2-character note;
    - `/search?q=zzzz` shows "No matches";
    - `/search?q=<script>` is escaped;
    - no "Add to library" anywhere in sample mode;
    - `/library?type=movie&source=arr` lists Paper Moons and Dune but not Arrival;
    - `/library?source=wanted` lists Tiny New Thing, Coming Soon, Night Shift Diaries and Low Tide;
    - Dune appears once on `/library?type=movie`.
    - `/search?q=dune&partial=1` starts with `<div class="search-fragment"`, contains no `<html`, `topbar` or `search-form`, and has `data-search-state="ok"` and Dune + "In library";
    - `/search?q=x&partial=1` gives `data-search-state="short"`;
    - `/search?partial=1` gives `data-search-state="empty"`;
    - `/search?q=%3Cscript%3E&partial=1` is escaped in both the body and `data-announce`;
    - the fragment inside the full `/search?q=dune` page equals the partial body byte for byte (same sample data).
- [ ] **Markup** (`python3 -m unittest tests.test_ui`):
  - `.topbar-search` and `.search-link` on Home/Library/Settings; no `.topbar-search` on `/search`, where `.search-link` has `aria-current="page"`;
  - every search state renders;
  - a hostile title, overview or q is escaped; a `javascript:` poster is dropped;
  - status tag labels for each status, and none for `none`;
  - Add only when status is `none`, the service is configured and it's not sample;
  - `data-keep-on-add` on search cards;
  - the undismiss form only when dismissed;
  - `autofocus` only when q is empty;
  - library cards show source badges and state badges (no "Downloaded" when in Plex);
  - the Source select appears only when an arr service isn't `off`;
  - arr error notes and the no-Plex note;
  - pager links keep `source`;
  - the filtered empty state links to `/search?q=`;
  - `render_add_dialog` uses `lookup_item` and maps `/search` to section `search`.
  - Live-search markup with a patched `web.search_view`:
    - `render_search` contains `form ... data-search-live`, `[data-search-results]` with `id="search-results"` and `aria-busy="false"`, an empty `.search-status` with `role="status"` and `aria-live="polite"`, and `[data-search-spinner]` with `hidden`;
    - the input has `aria-controls="search-results"`;
    - `render_search_results` equals the fragment embedded in `render_search` for the same view;
    - the fragment has no `<html`, no `_shell` markup and no `role="status"`/`role="alert"`;
    - `data-search-state` and `data-announce` are correct for each state ("2 matches for "dune"", "No matches for ...", the short text, the error/limited message);
    - a hostile q is escaped inside `data-announce`;
    - Add links in the fragment have `return_to=/search?q=...&type=...` without `partial`.
- [ ] **Static and JS hooks** (`python3 -m unittest tests.test_ui.TestStaticAssets`):
  - no external URLs;
  - **exactly one `innerHTML`**, and it sits inside `function setFragment`;
  - app.js contains `data-keep-on-add`, `data-search-live`, `partial=1`, `AbortController`, `replaceState`, `isComposing`, `data-announce` and `setFragment(`, used at least twice: add dialog and search.
- [ ] **Manual** (no browser here): Hamish clicks through `SAMPLE=1 PORT=8099 python3 web.py`.
  - The top-bar search works at desktop width and through the phone icon at 375px, and it's readable on the transparent cinematic top bar.
  - Results, statuses and the More info modal work.
  - **Live search:**
    - typing updates the results after a short pause, with the old results dimmed meanwhile;
    - typing fast never ends on stale results;
    - the type pills re-run the search at once;
    - Escape clears;
    - Enter loads the full page;
    - the URL follows the query, and refresh/Back behave;
    - a screen reader announces only the summary;
    - with the server stopped mid-typing, the error note shows and nothing loops;
    - Add from a live result opens the dialog.
  - The Library Source filter and badges work.
  - With JS off, search and the filters still work.
  - Every theme, light and dark; focus rings visible.
  - The live Add-from-search flow can only be checked on arr after deploy (needs Hamish's OK).
- [ ] **Full suite** green after phase 3: `python3 -m unittest discover -s tests`

## Live-request budget
**Zero** for every implementer and the reviewer. Mock at the boundary: `tmdb.get_json`, `radarr.get_json`, `sonarr.get_json`, `RadarrClient.library`, `SonarrClient.library`, `TmdbClient.details`, `TmdbClient.search_titles`, `sources.add_to_library`. Any test that drives a live-mode `/add` or `/add-dialog` for an uncached title must patch `TmdbClient.details` or `web.lookup_item`, because `lookup_item` would otherwise make a request when a `TMDB_TOKEN` is set.

What this adds at runtime in production:
- one TMDB request per *uncached* debounced search query, at most 50 per minute; plus lookups up to a shared total of 60 per minute, with 10 always left for lookups. Typing a title typically costs 3-6 requests the first time and nothing after that (24h cache);
- at most one TMDB details request when adding a title that isn't a recommendation (usually cached);
- **no new Radarr/Sonarr requests**, since the build's existing `/movie` and `/series` fetches are reused.

## Open questions
Q1-Q4 are answered: live search (Decision 13), and the other defaults stand.
1. ~~Live-as-you-type search.~~ **Answered: live**, on the `/search` page's own box only, via the server-rendered fragment (Decision 13).
2. **Arr posters.** Answered: the default stands. The default: the arr's remote URLs (TMDB/TVDB/fanart over https, allowlisted), loaded by the browser like recommendation posters today. The alternative is a `/arr-poster` proxy through Radarr/Sonarr's `/MediaCover`, which keeps image traffic on the LAN and covers custom art, but costs a new route and server bandwidth.
3. **Library filter shape.** Answered: the default stands. The default: one **Source** select (Everywhere / In Plex / In Radarr or Sonarr / Wanted). The alternative is extra subtabs "Radarr" and "Sonarr" next to Watched/Added here.
4. **Smaller defaults.** Answered: they stand, except (e), which the amendment changes.
   - (a) Untracked search results get no "Not added" tag; the Add button says it.
   - (b) No Add for titles already in Plex but not in Radarr/Sonarr.
   - (c) No Add buttons in sample mode.
   - (d) "Downloaded" is hidden on cards that are also in Plex.
   - (e) ~~A budget of 30 TMDB requests per minute~~ → 60 per minute, with 10 reserved for Add lookups (Decision 4). Not blocking: if Hamish prefers to keep 30, set `TMDB_BUDGET_MAX = 30` and `LOOKUP_RESERVE = 5`, and expect "limited" after about 5 new titles typed in a minute.
5. **Open, not blocking:** should the top-bar box also search live, with a dropdown on every page? Default: **no** (Decision 2). Enter takes you to the live `/search` page.

---

## Task briefs

### backend-dev: phase 1 (DONE), then phase 3
Spec: `specs/search-and-arr-library.md`. The Contract is fixed; report back if any of it can't work. Live-request budget: **none**. Own: `arr_library.py` (new), `radarr.py`, `sonarr.py`, `tmdb.py`, `sample.py`, `sources.py`, `web.py`, and tests `test_arr_library.py` (new), `test_radarr_sonarr.py`, `test_plex_tmdb.py`, `test_sources.py`, `test_library.py`, `test_web_async.py`, `test_web.py`.

**Phase 1.** No `/search` route, no `from pages import` change, and no `source` in `parse_list_query`. The suite must stay green.
1. `arr_library.py`, pure, exactly as in the Contract.
2. `normalize_movie` / `normalize_series` and `library()` on both clients.
3. In tmdb: `_get(timeout, retries)`, `normalize_search`, `cached_search`, `search_titles` and the constants.
4. In sample: the four catalogue entries, `arr_library()`, and `SampleTmdb.cached_search` / `search_titles`. Prove the sample recommendations are unchanged.
5. In sources: `_arr_library`, the 5-tuple `_load_live` (update `generate_ai_recommendations`), `result["arr"]` in `run()`, and the sample exclusion union.
6. In web: the constants, `_tmdb_budget` (own lock), `parse_search_query`, `search_view`, `lookup_item`, `arr_status`, `LIST_SOURCES`, merged `library_items`, `list_view(source=)`, `owned_keys` + arr, and `/add` recording through `lookup_item` on success only.
7. Tests per the acceptance criteria. Fix any `test_web`/`test_library` assertion that the merged sample Library changes, and say which.

**Phase 3** (after frontend phase 2; phase 1 is already done):
1. Add `GET /search`. With `partial == "1"`, send `render_search_results(**parse_search_query(query))` with `headers={"Cache-Control": "no-store"}` (default text/html Content-Type). Otherwise send `render_search(..., msg=msg)`. Import both.
2. Add `source` to `parse_list_query` (update the exact-dict test).
3. Budget amendment (Decision 4): `TMDB_BUDGET_MAX = 60`, `LOOKUP_RESERVE = 10`, `_tmdb_budget(reserve=0)`, `search_view` using `reserve=LOOKUP_RESERVE`, the new `SEARCH_LIMITED` text, and update the limited test.
4. Add the route, partial, budget and sample end-to-end tests per the acceptance criteria.

Run `python3 -m unittest discover -s tests`. Report the files changed, test results, the real sample row/title output you verified, and any deviations.

### frontend-dev: phase 2
Spec: `specs/search-and-arr-library.md`, especially "Markup contract". Own: `pages.py`, `static/app.js`, `tests/test_ui.py`. Live-request budget: **none**. Start after backend phase 1.
1. In `_shell`: the top-bar search form plus `.search-link`, the `search` icon, and section `search` (heading "Search", no `.topbar-search`).
2. `render_search`, `render_search_results`, `_search_fragment` (the single renderer used by both), `_search_url`, `_search_card` with every state, and `_search_meta`.
   - Follow the live-search markup: `data-search-live`, the region, `.search-status`, the spinner, `aria-controls`, and the fragment root with `data-search-state`/`data-announce`.
   - There is no `role` on the state blocks.
3. In `render_library`: the `source` param, the Source select (only when arr isn't `off`), `_library_url(source)`, the card source and state badges, the notes and the empty-state copy and link.
4. `render_add_dialog` switches to `web.lookup_item` and maps `/search` to section `search`.
5. app.js:
   - `setFragment(node, html)` as the one `innerHTML`; `openAddDialog` uses it;
   - `markAdded` for `data-keep-on-add` cards in `handlers.add`;
   - the live search, exactly per Decision 13 (debounce 400 ms, the 1-character no-op, the last-key check, abort plus sequence counter, busy/spinner/dim, the announce copy, `replaceState`, pills, Escape, IME, the error note, no retry, nothing on load).
6. test_ui per the acceptance criteria. Patch `web.search_view`, `web.get_result_nowait`, `web.lookup_item` and `config` as needed. Call `render_search` / `render_search_results` directly, because the route arrives in phase 3.

Use only the classes named in the spec. Don't edit app.css; ask via the orchestrator if a class is missing. Run `python3 -m unittest tests.test_ui`, then the full suite. List any expected-red tests, and everything in app.js that needs a hand check.

### ux-designer: now (backend phase 1 is done); can run alongside frontend phase 2
Spec: `specs/search-and-arr-library.md` ("Markup contract"). Own: `static/app.css` only. Live-request budget: **none**.
Style these:
- **Top bar:**
  - `.topbar-search`, `.topbar-search .search-input`, `.search-submit`: compact; legible on the transparent cinematic top bar and on the solid one.
  - `.search-link`: hidden on wide screens. At ≤820px, `.topbar-search` is hidden and `.search-link` is shown as an icon (a visually-hidden label is fine). Switch to the icon at a wider breakpoint if the top bar overflows at 821px.
- **Live state:** `.search-results-region` and `.search-results-region.is-loading` (dim the previous results to about 0.55 opacity, `pointer-events: none`; no transition under reduced motion), and `.search-spinner` (the existing `.spinner.sm`, placed inside `.search-row` next to the input).
- **Search page:** `.search-page`, `.search-form`, `.search-label`, `.search-row`, `.search-input` (large), `.search-types`, `.search-type` (pill radios with a visible checked state that isn't colour-only and a focus ring via `:has(:focus-visible)`), `.search-hint`, `.search-summary`, `.search-results`, `.search-card`, `.arr-line`.
- **Status:** `.status-tag` with `.status-plex`, `.status-radarr`, `.status-sonarr` and `.status-added` (variants of `.lib-tag`; text carries the meaning).
- **Library:** `.badge.arr-state` with `.state-missing`, `.state-partial`, `.state-upcoming`, `.state-unmonitored` and `.state-downloaded`, plus `.card-sources` and `.src` (`.src-plex`, `.src-radarr`, `.src-sonarr`). Make the toolbar's third select wrap cleanly at 375px.

Rules:
- Tokens only; add a token rather than hard-coding a colour, and add it to **all six theme blocks** if it's a theme token (`TestThemeCss` enforces the exact token set, so prefer non-theme tokens or existing ones).
- AA contrast in light and dark for every theme.
- `:focus-visible` kept.
- No horizontal scroll at 375px.
- `prefers-reduced-motion` respected.
- No external assets.

Run `python3 -m unittest tests.test_ui`. Report the classes styled, any tokens added, and any class you needed that the spec doesn't name.
