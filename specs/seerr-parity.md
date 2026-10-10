# Seerr parity, phase 1: title pages, season picks, rate and list anything, Requests

Status: draft
Author: architect · Date: 2026-10-10

## Goal
Hamish's ask: "very very similar to what seerr is but with an improved recommendation algorithm like ours. I also need to be able to select which season of a tv show I want or all seasons. Additionally I should be able to rate each media no matter what screen I'm on." Later he added: "Add the ability for lists like how trakt works."

Compass stays single-user. Adds still go straight to Radarr/Sonarr, with no approval queue. Our recommendation engine stays the core. Phase 1 brings in Seerr's flows and UI:
1. **Title detail page** `/title/<movie|tv>/<tmdb_id>` for every title, wherever you find it. It has a backdrop hero, poster, facts, cast, a trailer link, availability, a seasons list for TV, "Why Compass picked this", and a "More like this" row ordered by our engine. **Every poster click and every "More info" goes here.**
2. **Season choice when adding to Sonarr.** You can pick "All seasons" or tick specific seasons, both in the Add dialog and on the detail page. For a series already in Sonarr, you can request more seasons.
3. **Rate anything from any screen.** Compact 1-5 stars sit on every card surface. Rating an unwatched title feeds the taste profile.
4. **Requests.** The Library's "Added here" tab becomes **Requests**. It shows each title's live-ish status and, for TV, which seasons you asked for. `/requests` is an alias for it.
5. ~~Discover rows (Upcoming / Popular / genre).~~ **Moved to Phase 2.** With Lists added, Phase 1 is already four agent runs. These rows need new TMDB list calls in every build, plus row/cache plumbing, and they add the least value for a personal recommender. See Phase 2.
6. **Lists (Trakt-style).** A built-in **Watchlist** plus custom lists (name and description). You can add or remove titles from every surface that has stars. There's a Lists page, and each list has its own page with sorting and Up/Down manual ordering. The detail page shows which lists a title is on.

## Out of scope (Phase 2 / open questions, not designed here)
- Multi-user accounts, the approval queue, notifications, issue reporting, Plex OAuth login.
- **Trakt account sync**: OAuth, importing/exporting lists, scrobbling.
- Discover rows (Upcoming, Popular, by genre/network/studio). This is item 5.
- "Add whole list to Radarr/Sonarr". It isn't trivial: each title needs its own profile and season choice, it means N sequential arr calls, and errors have to be reported per item.
- Drag-and-drop list ordering, undo for list removals, public/shared lists, a "From your Watchlist" Home row, and a taste weight for the Watchlist (Q2).
- Per-season availability from Plex (it would need a Plex call per page view). Season state comes from Sonarr only.
- Live download progress (the Radarr/Sonarr `/queue` endpoints), unmonitoring seasons, removing titles from Radarr/Sonarr, requesting Specials (season 0).
- An embedded trailer player. The trailer is a link out.
- Season monitoring in sample mode, and any Add in sample mode (unchanged).

## Decisions

### D1. Title detail page replaces the card modal
- **Route.** `GET /title/movie/<id>` and `GET /title/tv/<id>` are real URLs: shareable, Back works, and they need no JS. Anything else under `/title/` gives a 404 text response.
- **Every poster is a link.** On every card that has a tmdb_id (rows, hero, search, library, watched, AI, list pages, the similar row), the poster becomes `<a class="poster-link" href="/title/...">`. The hover preview's "More info", its poster, and the hero's new "More info" button all navigate there.
- **The JS detail modal (`openDetail`) is retired.** Justification:
  - one rich view instead of two;
  - phones (no hover) get every action on one page: stars, Watchlist/lists, Add with seasons, Not interested;
  - it works without JS;
  - it removes JS: no more `[data-open-detail]` poster buttons or summary interception.
  - What we lose: a quick in-place view on phones. That costs a page load on the LAN, and Back returns to the row.
- **The `<details class="card-details">` blocks stay** as the no-JS inline fallback on row, search and AI cards (no test churn). With JS:
  - row cards still hide them (`html.has-dialog .row-card .card-details`);
  - on search and AI cards they now just expand natively.
- The hero's `<details class="hero-details">` is removed (D5). The `<dialog>` modal itself stays for the Add dialog and the new list dialog.
- **Data** (`web.title_view`):
  - base fields come from the recommendation item if the title is one, else the cached details, else one request;
  - extras (cast with characters, crew, trailer, seasons, networks/studios, similar, external ids) come from **one** TMDB request (`TmdbClient.title`, see Contract).
  - That request also writes `details:v2` and `external_ids`, so it keeps the build cache warm and saves the TVDB lookup at Add time.
  - Cache: the new key `title:v1:{type}:{id}`, `TITLE_MAX_AGE = 3 days`. Episode counts change weekly for airing shows; movies barely change, but one constant keeps it simple.
- **Budget.** An uncached title page costs one request through `_tmdb_budget(reserve=LOOKUP_RESERVE)`, the same share as search, because a GET with no login can be triggered by any page. Cached pages cost nothing.
  - When the budget is used up, or TMDB fails, the page still renders from what we have: `state="partial"` with a fixed note.
  - If we have nothing at all, it renders `state="unavailable"` with status 200.
  - When TMDB says 404 (`RuntimeError` text starting `"HTTP 404"`), it's `state="not_found"` with status 404 and a rendered page.
  - Upstream error text is never shown.
- **"Why Compass picked this"**, when the title is in the current recommendations (main or AI): match %, reason, match chips, and "Because you watched" (up to 5 names).
  - For any other title with details and a profile, a smaller **"How it fits your taste"** section shows `recommend.matches(profile, details)` chips (no % - it isn't comparable to a relative match %).
- **"More like this"** comes from TMDB `recommendations` (filled up from `similar`, max 20, same media type, dismissed titles dropped). Our engine orders it:
  1. titles in our current recommendations first, by their `match` (the card shows "N% match");
  2. then titles with cached `details:v2`, by `recommend.content_score(profile, details)` descending (no number shown);
  3. then TMDB's order.
  - Owned or tracked titles stay in, with their status tag, as Seerr does.
  - No extra requests: scoring only uses cache reads.
- **Availability badges** come from `arr_library.annotate` against the build snapshot: In Plex / In Radarr / In Sonarr / Added, plus the arr state (Missing, Upcoming, 5/10 episodes, ...), plus Watched and Not interested. There are no per-view Plex/arr calls.
- **Seasons list (TV)** is TMDB seasons merged with Sonarr's per-season statistics. The build's `GET /api/v3/series` already returns `seasons[].statistics`, which we now keep. Specials (season 0) are hidden.

### D2. Season selection (Sonarr)
- **Form fields** on `POST /add`, tv only (ignored for movies):
  - `seasons` = `all` | `pick`;
  - `season` = a season number, repeatable.
- **Validation** (`web.parse_seasons`):
  - Field absent gives `None`, the legacy behaviour: add everything, or "Already in Sonarr" if the series exists. That keeps old forms and old tests working.
  - `all` gives `"all"`, and any `season` values are ignored.
  - `pick` needs at least 1 and at most `MAX_SEASONS_PER_REQUEST = 200` values. Each must be ASCII digits with value 1..`SEASON_NUMBER_MAX = 9999` (year-numbered seasons exist). The values are deduped and sorted.
  - Anything else is a 400 with a message (see HTTP).
  - **Existence** is checked by Sonarr's own data inside `SonarrClient`: the lookup result's `seasons` for a new series, and the series' `seasons` for one that's already tracked. A number that isn't there gives `(False, 'Season 7 isn\'t listed for "X" in Sonarr')`, which is a 200 `ok: false`, like every other arr refusal.
  - The UI lists TMDB seasons. TMDB and TVDB numbering can differ (anime mostly), and that message covers the mismatch (Risk 2).
- **New series payload.** `POST /api/v3/series` sends the lookup object (`GET /api/v3/series/lookup?term=tvdb:{tvdb_id}`, `[0]`) with these fields updated:
  ```json
  {"qualityProfileId": 4, "rootFolderPath": "/tv", "monitored": true,
   "seasons": [{"seasonNumber": 0, "monitored": false}, {"seasonNumber": 1, "monitored": true}, {"seasonNumber": 2, "monitored": false}],
   "monitorNewItems": "none",
   "addOptions": {"searchForMissingEpisodes": true, "searchForCutoffUnmetEpisodes": false, "ignoreEpisodesWithFiles": false}}
  ```
  - `seasons` holds **every** season from the lookup. `monitored` is true when the number is chosen (`"all"`: every season > 0; specials always false). Other keys in each season dict are kept.
  - `addOptions.monitor` is **deliberately omitted**. That means `unknown`, so Sonarr keeps our per-season flags. Any explicit monitor type (`all`, `future`, ...) would override them. This is what Overseerr/Jellyseerr do, and it works on v3 and v4.
  - `monitorNewItems` is `"all"` for `"all"`/`None` and `"none"` for a pick. It's a v4 field; v3 ignores unknown fields (**verify**, Risk 1).
  - `monitored` and `searchForMissingEpisodes` follow `search`, unchanged from today. Unchecked "search now" adds the series unmonitored, with the chosen seasons already flagged for when you turn monitoring on in Sonarr. The dialog copy says so.
- **Existing series ("request more seasons")**, when `seasons` is `"all"` or a list and the series is found:
  1. `GET /api/v3/series?tvdbId={tvdb_id}` (filtered client-side by `tvdbId` too, for versions that ignore the param). The entry is the full series resource.
  2. Mark the requested seasons `monitored: true`. This is additive only: nothing is ever unmonitored. `"all"` means every season > 0. Also set `series["monitored"] = True`.
  3. If no season changed: `(False, 'Already monitoring those seasons of "X" in Sonarr')`.
  4. `PUT /api/v3/series/{id}` with the whole modified resource (new `http_util.put_json`). Sonarr's series update also flips the episodes of seasons that changed (**verify**, Risk 1).
  5. If `search`: for `"all"`, send `POST /api/v3/command` `{"name": "SeriesSearch", "seriesId": id}`. Otherwise send one `{"name": "SeasonSearch", "seriesId": id, "seasonNumber": n}` per **newly** monitored season.
  6. The message is `'Now monitoring season 3 and 4 of "X" in Sonarr'` (plus `" - searching now"` when searching).
- **`/add` for an existing series with `seasons=None`** still answers "Already in Sonarr".
- **Where seasons come from in the UI:** `web.season_choices(tmdb_id)`, which is TMDB extras (cached first, then one budgeted request) merged with the Sonarr snapshot.
  - With no season data, the picker shows only "All seasons", with a note.
  - For a tracked series, monitored seasons show as checked and disabled ("Requested"), and only the others can be picked. The quality-profile select is hidden, because the profile can't be changed here.
- **The detail page has its own season form** (D1 seasons list).
  - It shows a checkbox per selectable season, "Search now", and two buttons: "Request selected seasons" (`seasons=pick`) and "Request all seasons" (`name=seasons value=all`).
  - It uses the **Settings default profile**, so the page never fetches Radarr/Sonarr profiles. A "More options" link opens the Add dialog with the profile select.
  - Movies get only the Add-dialog link.
- **Instant status after an add.** Radarr/Sonarr answer `POST /movie|/series` and `PUT /series/{id}` with the resource. The client normalizes it (`last_item`), and `web.note_arr_item()` puts it into the cached `result["arr"]["items"]` (under `_status_lock`, replacing the same service + tmdb/tvdb id).
  - So Requests and the detail page show the new state immediately, with **zero** extra arr calls.
  - If a build is running at that moment, its result may lack the item until the next build. That's accepted and documented.

### D3. Rate anything, and what a rating on an unwatched title means
- **Stars live** in these places. A grid card gets one compact line; nothing extra goes on cinematic cards.

  | Surface | Where the stars are |
  |---|---|
  | Home/Movies/TV row cards | Hover preview (desktop, as today). Phone: the detail page (tap the poster). No-JS: inside the existing `<details>` (as today) |
  | "New in your library" row cards | They gain `data-preview` and a `.card-tools` block (hidden with JS like row details; the preview clones it). No-JS: visible |
  | Hero | Compact stars in `.hero-actions` (new `.card-tools.hero-tools`), next to Add and More info |
  | Hover preview | Cloned rate form (as today), plus the new Watchlist toggle and list link |
  | Search results, AI page, Library All/Movies/TV/Requests, list pages | `.card-tools` line at the bottom of `.card-info`: compact stars, Watchlist toggle, list link |
  | Library Watched | Unchanged full stars and text, plus a list link |
  | Detail page | Full rate form (stars, text, Clear) in `.title-actions` |
  | Similar row on the detail page | Same as row cards (preview, or tap through to its own page) |
- **Compact variant.** `_rate_form(item, return_to, compact=True)` renders `class="rate rate-compact"` with the same 5 buttons and Clear button. Its `.rating-text` also carries `visually-hidden`, so `applyRating` keeps one code path. At 375px the 5 stars fit a 2-column card, and the Watchlist/list icons wrap onto a second line.
- **`/rate` is unchanged** (fields, JSON shape, sample refusal). Only the message title changes: it now comes from `web.title_stub(type, id, fetch=False)`, which checks the watched snapshot, recommendations, library/arr items and cached details, with no request. So "Rated "Dune" 4/5" works everywhere.
- **Profile effect.** library-and-ratings.md put this out of scope. Today an unwatched title's rating only feeds `disliked`. New behaviour: **a rating means "I've seen it"**.
  - `profile.rated_entries(watched, rows)` makes one synthetic history entry per rated title that's missing from the watch history: `user_rating = stars*2`, `last_viewed = rated_at`, `view_count = 1`, `title = None`, `rated_only = True`.
  - They're appended to the history the recommender sees, in live `run()` and `generate_ai_recommendations()`. So:
    - 4-5★ seed the profile like a watched favourite;
    - 3★ counts 0.67;
    - 1-2★ give no taste input and keep the existing linked-title penalty;
    - **the rated title drops out of recommendations at the next rebuild** (it's now "have"). It stays on screen until then, with no instant removal.
  - `recommend.py` falls back to `details["title"]` when an entry's title is None, for "Because you watched" and the AI's watched list.
  - The Watched tab still shows only real Plex/Tautulli history, and `watched_count` is unchanged.
  - Sample mode ignores ratings as before, so sample recommendations stay byte-for-byte the same.
  - Q1 asks Hamish to confirm.

### D4. Requests = the Library's "Added here" tab, renamed
- There's no new top-level page. The bottom nav already has 5 items, and the "Added here" tab already lists `db.added_items()`.
  - The tab label becomes **"Requests"**, and the URL stays `/library?type=added`, so old links work.
  - New `GET /requests` gives a 303 to `/library?type=added` (keeping a `msg`).
- `db.added` gains a `seasons` column. A repeat request for the same show merges seasons: a union, and `"all"` wins.
- **Status** comes from `arr_library.request_state(entry, arr_item, in_plex)`. It uses the build snapshot plus D2's instant injection, with no per-view calls:

  | state | label | rule |
  |---|---|---|
  | `available` | Available | arr `downloaded`, or (no arr item and) in Plex. TV with a seasons list: every requested season has `total > 0 and have >= total` |
  | `partial` | `{have}/{total} episodes` (fallback "Partly available") | arr `partial`. For a TV pick, have/total are summed over the requested seasons |
  | `processing` | Searching | arr `missing` (monitored, nothing on disk yet) |
  | `upcoming` | Upcoming | arr `upcoming` |
  | `unmonitored` | Not monitored | arr `unmonitored` |
  | `requested` | Requested | not in arr and not in Plex: sent, not seen since |
  | `None` | (no badge) | no build result yet |
- The tab also gets a `show` filter: `all` (Everything), `open` (Not available yet: every state except `available`), `available`.

### D5. Hero changes (because the modal is gone)
- The actions are: primary (Add, or the "In library" tag), then `<a class="btn-ghost hero-more" href="/title/...">More info</a>`, then `.card-tools.hero-tools` (compact stars and Watchlist toggle, no list link).
- `<details class="hero-details">` and the hidden `.card-poster` copy are removed. "Not interested" is no longer on the hero; it's one click away on the detail page, in the Top 10 row and in the preview.

### D6. Lists
- **Storage.** There are two new tables, `lists` and `list_items` (Contract).
  - Each list item stores a snapshot of title/year/poster_url taken when it's added, as `added` does. List pages then never need TMDB.
  - The Watchlist is a `lists` row with `kind='watchlist'`. It's created lazily by `db.ensure_watchlist()` (live mode only), and a partial unique index allows only one.
- **Limits** (web constants):
  - `MAX_LISTS = 50` custom lists, plus the Watchlist;
  - `MAX_LIST_ITEMS = 1000` per list;
  - `LIST_NAME_MAX = 60`;
  - `LIST_DESC_MAX = 300`.
  - Names are whitespace-collapsed, 1-60 printable characters, and unique ignoring case, including "Watchlist". The description is whitespace-collapsed into a single line.
- **Ordering.** A new item goes to the **top** (`position = MIN(position) - 1`, 0 when the list is empty). So the default "Your order" starts out newest-first, and Up/Down/Top/Bottom reorder it. A move swaps with the neighbour by position across the whole list, so it works across pages.
  - List page sorts (`LIST_SORTS`): `manual` (Your order, the default), `added` (Recently added), `title` (Title A-Z), `year` (Newest year), `rating` (Your rating; None last), `match` (Match % from the current recommendations; None last). Missing values sort last, and ties go by title.
  - Move buttons appear only for `sort=manual`.
- **Titles** come from `web.title_stub(type, id, fetch=True)`: the snapshot and caches first, then one `lookup_item` (budgeted).
  - If that's None in live mode, the add is refused: 200 `{"ok": false, "message": "Couldn't look that title up right now - try again in a minute."}`.
- **Where lists live in the UI.** It's a **Library sub-area**, so the nav doesn't change:
  - the Library subtabs gain a "Lists" link to `/lists`;
  - `/lists` and `/lists/<id>` render with the Library nav item active and the "Lists" subtab on.
  - `GET /watchlist` gives a 303 to `/lists/<watchlist id>` (live) or `/lists` (sample).
  - Q3 offers a top-level nav item instead.
- **Per-title controls:**
  - a **Watchlist toggle**: a form posting `/lists/add` or `/lists/remove` with `list=watchlist`, a bookmark icon with a text label, `aria-pressed`;
  - a **list link** `a.list-link[data-list-dialog]` to `/list-dialog?type&id&return_to`. That's a page without JS and a modal fragment with JS, exactly like `/add-dialog`. Its form posts `/lists/set` with a checkbox per list (Watchlist first) and an optional "New list" name field.
- **The detail page** shows "On your lists: Watchlist, Horror night" (links), plus the toggle and the list link.
- **Taste and recommendations.** The Watchlist gives **no taste weight, no exclusion and no boost** in Phase 1:
  - Adding to a watchlist is intent, not enjoyment. People watchlist things others recommended, and ratings are the explicit signal.
  - Feeding it back would make a loop: recommended, watchlisted, more of the same.
  - Excluding watchlisted titles would hide exactly the titles you want to see. Cards just show the bookmark state instead.
  - Q2 offers a 0.25-weight seed as an alternative.
- **Sample mode.** Lists render (a virtual empty Watchlist; nothing is written), and every list POST answers `SAMPLE_MESSAGE`.
- **Undo, drag-and-drop and "add list to arr"** are Phase 2. Deleting a list asks for confirmation through a `<details>` confirm step, which works without JS.

### D7. Request body cap
`do_POST` reads at most 4096 bytes today. That is too small for a 300-character UTF-8 description (up to 3600 bytes once percent-encoded) plus the other fields, or for a long season pick. It's raised to `MAX_FORM_BYTES = 16384`.

## Contract

### Data shapes
```python
# ArrItem (radarr.normalize_movie / sonarr.normalize_series) - two NEW keys, always present:
{..., "arr_id": int|None,            # raw["id"] if positive int
      "seasons": None | [            # movie: None. tv: from raw["seasons"], sorted by number, entries with int seasonNumber >= 0
          {"number": int, "monitored": bool,
           "have": int,              # statistics.episodeFileCount (missing -> 0)
           "total": int}]}           # statistics.totalEpisodeCount, else episodeCount, else 0

# TitleExtras - tmdb.normalize_title(raw, media_type); third-party strings validated as in normalize_search
{"media_type", "tmdb_id",
 "tagline": str, "status": str,                       # "" when missing ("Returning Series", "Released", ...)
 "cast": [{"name": str, "character": str, "profile_url": str|None}],   # <= CAST_MAX (12), TMDB order; w185 profile
 "crew": [{"name": str, "job": str}],                 # <= 6. movie: jobs Director, Screenplay, Writer, Story, Novel, in that
                                                      # priority, unique names. tv: created_by as job "Creator"
 "trailer": {"name": str, "url": str} | None,         # YouTube only, key ^[A-Za-z0-9_-]{6,32}$ ->
                                                      # "https://www.youtube.com/watch?v={key}". Prefer type "Trailer" + official,
                                                      # then "Trailer", then "Teaser"; TMDB order within a tier
 "seasons": [{"number": int, "name": str, "episodes": int, "air_date": str|None, "poster_url": str|None}],
                                                      # tv: raw["seasons"], season_number int >= 0, sorted; movie: []
 "networks": [str], "studios": [str],                 # <= 3 each (tv networks / production_companies)
 "tvdb_id": int|None, "imdb_id": str|None,            # external_ids; imdb must match ^tt\d{1,10}$
 "similar": [SearchItem]}                             # recommendations.results then similar.results; same media_type;
                                                      # deduped; <= SIMILAR_MAX (20); malformed rows skipped

# SeasonRow - arr_library.season_rows(tmdb_seasons, arr_item, today)
{"number": int,                     # > 0 only (specials hidden); union of TMDB and arr numbers, sorted
 "name": str,                       # TMDB name, else f"Season {n}"
 "episodes": int|None,              # TMDB episodes, else arr total, else None
 "air_date": str|None,
 "monitored": bool|None,            # None when the series isn't in Sonarr, or this season isn't in the arr data
 "have": int|None, "total": int|None,
 "state": "available"|"partial"|"missing"|"upcoming"|"unmonitored"|None,
 "requested": bool,                 # arr season monitored
 "selectable": bool}                # not requested (a new series: every season)
# state (arr season present): total > 0 and have >= total -> available; have > 0 -> partial; not monitored -> unmonitored;
#   air_date missing or > today -> upcoming; else missing. No arr season -> None.

# TitleView - web.title_view(media_type, tmdb_id)
{"media_type", "tmdb_id",
 "state": "ok"|"partial"|"unavailable"|"not_found",
 "message": str|None,               # TITLE_PARTIAL / TITLE_UNAVAILABLE / TITLE_NOT_FOUND (fixed text) or None
 "item": dict|None,                 # normalize() shape (title, year, release_date, overview, poster_url, poster_large_url,
                                    # backdrop_url, url, genres, directors, cast, vote_average, vote_count, runtime, seasons,
                                    # certification); rec item > details > stub. None only for unavailable/not_found
 "extras": TitleExtras|None,
 "rec": {"match": int, "reason": str, "matches": [str], "because": [str], "new": bool, "trending": bool,
         "source": "main"|"ai"}|None,
 "fit": {"matches": [str]}|None,    # non-recs only, when details and result["profile"] exist and matches is non-empty
 "status": {"status", "in_library", "sources", "arr_state", "episodes", "watched", "dismissed"},   # annotate() fields
 "arr": ArrItem|None,               # arr_library.find(...)
 "seasons": [SeasonRow],            # [] for movies
 "stars": int|None, "lists": [int], "on_watchlist": bool,
 "list_names": [{"id": int, "name": str}],   # lists containing it, Watchlist first
 "similar": [dict],                 # SearchResult + "match": int|None + "stars", "lists", "on_watchlist"
 "can_add": bool,                   # status == "none" and service configured and not sample (pages may still use _can_add)
 "sample": bool, "library_known": bool}

# Request entry - web.library_items(result, "added") (was a LibraryItem built from db.added_items())
{"media_type", "tmdb_id", "title", "year", "added_at", "watched": False, "progress": None, "poster_key": None,
 "poster_url", "url",
 "seasons": None|"all"|[int],       # NEW
 "request_state": str|None, "episodes": dict|None,   # NEW (D4)
 "sources": [...], "stars": int|None, "lists": [int], "on_watchlist": bool}   # NEW

# List summary - web.lists_view()["lists"][n]
{"id": int|None,                    # None only for sample mode's virtual Watchlist
 "name": str, "description": str, "kind": "watchlist"|"custom", "count": int,
 "url": str|None,                   # "/lists/{id}"; None for the virtual one
 "posters": [str]}                  # <= 4 poster_urls of the first items in manual order (may be [])

# ListsView - web.lists_view()
{"lists": [ListSummary], "sample": bool, "can_create": bool,   # can_create: not sample and custom count < MAX_LISTS
 "max_lists": MAX_LISTS}

# ListPageView - web.list_page_view(list_id, sort, page) -> None if the list doesn't exist (or sample mode with any id)
{"list": ListSummary, "sort": str, "sorts": LIST_SORTS, "page": int, "pages": int, "total": int,
 "items": [dict],                   # this page: list item snapshot + annotate() status fields + "match": int|None
                                    # + "stars", "lists", "on_watchlist", "position"
 "sample": bool, "can_move": bool}  # sort == "manual" and not sample
```

### Python interface
```python
# http_util.py (backend-dev)
def put_json(url, headers=None, body=None, timeout=20): ...   # like post_json, method PUT, no retries

# tmdb.py (backend-dev)
TITLE_MAX_AGE, CAST_MAX, SIMILAR_MAX = 3 * 24 * 3600, 12, 20
def normalize_title(raw, media_type) -> dict: ...             # TitleExtras
class TmdbClient:
    def cached_title(self, media_type, tmdb_id) -> dict|None: ...   # cache key title:v1:{type}:{id} only; never requests
    def title(self, media_type, tmdb_id) -> tuple: ...              # (details, extras). ONE GET /{type}/{id} with
        # append_to_response=keywords,credits,recommendations,similar,videos,external_ids,{release_dates|content_ratings}
        # timeout=SEARCH_TIMEOUT, retries=1. Writes details:v2 (normalize(raw)), title:v1 (normalize_title(raw)) and
        # external_ids:{type}:{id} ({"tvdb_id": ...}, the existing external_ids() shape). Raises on network/HTTP error.

# sample.py (backend-dev)
class SampleTmdb:
    def cached_title(self, media_type, tmdb_id) -> dict|None: ...   # = title()[1], None if not in catalogue
    def title(self, media_type, tmdb_id) -> tuple: ...              # (details, extras) from the catalogue; KeyError if unknown
        # extras: cast = catalogue cast names (character ""), crew from directors (Director / Creator), trailer None,
        # seasons from _SEASONS (below; other tv ids: [(1, 8), (2, 8)]), similar = the catalogue recs as SearchItems
# _SEASONS = {2003: [(1, 3), (2, 3), (3, 4)], 2040: [(1, 5), (2, 5)], 2041: [(1, 8)]}  # (number, episodes)
# arr_library(): every item gains "arr_id" (fixed fake ints 501..509) and "seasons": movies None;
#   2003: [{1, True, 3, 3}, {2, True, 3, 3}, {3, True, 4, 4}]; 2040: [{1, True, 5, 5}, {2, True, 0, 5}];
#   Low Tide: [{1, True, 0, 8}]. Sample recommendations must stay byte-for-byte identical.

# radarr.py (backend-dev)
class RadarrClient:
    last_item: dict|None   # set by add(): normalize_movie(POST response) when it's a dict, else None
# sonarr.py (backend-dev)
class SonarrClient:
    last_item: dict|None   # set by add(): normalize_series(POST or PUT response) when it's a dict, else None
    def _find(self, tvdb_id) -> dict|None: ...      # GET /api/v3/series?tvdbId=; first entry whose tvdbId matches; raises
    def _put(self, path, body): ...                 # put_json with X-Api-Key
    def add(self, tvdb_id, search=True, seasons=None) -> tuple: ...   # (ok, message); D2. Never raises.
        # seasons None: today's behaviour + seasons flags all True for > 0, monitorNewItems "all".
        # Existence check order: _find(); exception -> treat as not found (Sonarr refuses duplicates itself, as today).

# arr_library.py (backend-dev) - stays pure
REQUEST_STATES = ("requested", "processing", "upcoming", "partial", "available", "unmonitored")
def find(arr_items, media_type, tmdb_id, tvdb_id=None, title=None, year=None) -> dict|None: ...
    # tmdb match; else tvdb match (tv); else same_title() only when the arr item has no tmdb_id
def season_rows(tmdb_seasons, arr_item, today) -> list: ...      # SeasonRow; today is a date
def request_state(entry, arr_item, in_plex) -> tuple: ...        # (state, episodes|None) per D4

# db.py (backend-dev)
def record_added(item, seasons=None) -> None: ...   # seasons None|"all"|[int]; merges with an existing row (union; "all" wins;
                                                    # None never erases a stored value)
def added_items() -> list: ...                      # + "seasons": None|"all"|[int] (corrupt value -> None)
def rating_rows() -> list: ...                      # [{"media_type", "tmdb_id", "stars", "rated_at"}]
def ensure_watchlist() -> int: ...                  # creates it if missing (INSERT OR IGNORE), returns its id
def lists() -> list: ...                            # [{"id","name","description","kind","created_at","updated_at","count"}],
                                                    # Watchlist first, then custom by casefolded name. Never creates.
def get_list(list_id) -> dict|None: ...             # same keys
def create_list(name, description="") -> int: ...   # caller validates; kind "custom"
def update_list(list_id, name, description) -> bool: ...   # False if missing or watchlist
def delete_list(list_id) -> bool: ...               # False if missing or watchlist; deletes its items in the same transaction
def list_items(list_id) -> list: ...                # [{"media_type","tmdb_id","title","year","poster_url","added_at","position"}]
                                                    # by position ascending
def add_to_list(list_id, item) -> bool: ...         # item needs media_type, tmdb_id, title, year, poster_url; inserts at the top
                                                    # (MIN(position) - 1, or 0); False if already there. Bumps updated_at
def remove_from_list(list_id, media_type, tmdb_id) -> bool: ...
def move_in_list(list_id, media_type, tmdb_id, direction) -> bool: ...  # "up"/"down" swap with the neighbour by position;
                                                    # "top"/"bottom" = MIN-1 / MAX+1. False if absent or already at the end
def memberships() -> dict: ...                      # {(media_type, tmdb_id): [list_id, ...]} - one query

# profile.py (backend-dev)
def rated_entries(watched, rating_rows) -> list: ...    # D3: synthetic entries for rated titles not in watched; never mutates

# recommend.py (backend-dev): item.get("title") or details.get("title") or "?" wherever a watched item's title is read
#   (the "because" sources and the AI's watched_titles). Nothing else changes.

# sources.py (backend-dev)
def add_to_library(media_type, tmdb_id, search=True, quality_profile_id=None, seasons=None) -> tuple: ...
    # NOW a 3-tuple (ok, message, arr_item|None). arr_item = client.last_item on success. Movies ignore seasons.
    # TV: the TVDB id comes from TmdbClient.external_ids (usually cached by the title page).
# run(live) and generate_ai_recommendations(): rows = db.rating_rows(); ratings = {(m, i): s ...};
#   history = profile.apply_ratings(watched, ratings) + profile.rated_entries(watched, rows); recommend(history, ...).
#   disliked unchanged. Sample unchanged.

# web.py (backend-dev)
MAX_FORM_BYTES = 16384
SEASON_NUMBER_MAX, MAX_SEASONS_PER_REQUEST = 9999, 200
MAX_LISTS, MAX_LIST_ITEMS, LIST_NAME_MAX, LIST_DESC_MAX = 50, 1000, 60, 300
LIST_SORTS = ("manual", "added", "title", "year", "rating", "match")
LIST_OPTIONS["added"] = (("added", "title", "year"), ("all", "open", "available"))   # only "added" changes
TITLE_PARTIAL = "Some details (cast, trailer, seasons) couldn't load right now - try again in a minute."
TITLE_UNAVAILABLE = "Couldn't load this title right now - TMDB didn't answer. Try again in a minute."
TITLE_NOT_FOUND = "TMDB doesn't know this title."
def parse_seasons(form) -> tuple: ...           # (None|"all"|[int], error_message|None) per D2
def title_stub(media_type, tmdb_id, fetch=False) -> dict|None: ...
    # {"media_type","tmdb_id","title","year","poster_url","url"} from: _find_item, result["watched"], result["library"],
    # arr items (tmdb match), TmdbClient.cached_details (live) / SampleTmdb.details (sample); then, only if fetch and
    # live, lookup_item(). Never holds a lock while fetching.
def title_data(media_type, tmdb_id) -> dict: ...
    # {"details": dict|None, "extras": dict|None, "state": "ok"|"partial"|"limited"|"error"|"not_found"}.
    # sample: SampleTmdb.title (KeyError -> not_found). live: cached_details + cached_title; both cached -> ok;
    # else no token -> partial/error; else _tmdb_budget(reserve=LOOKUP_RESERVE) or "limited"; TmdbClient.title();
    # RuntimeError starting "HTTP 404" -> not_found; any other exception -> "error" (print the type name only).
def title_view(media_type, tmdb_id) -> dict: ...  # TitleView (D1). Uses get_result_nowait() (never blocks)
def season_choices(tmdb_id) -> dict: ...          # {"seasons": [SeasonRow], "tracked": bool, "known": bool}
                                                  # known: there's TMDB or arr season data
def note_arr_item(item) -> None: ...              # D2 injection into _state["result"]["arr"]["items"] under _status_lock
def with_user_state(items) -> list: ...           # copies + "stars", "lists", "on_watchlist" (sample: None, [], False).
                                                  # One db.ratings() + one db.memberships() per call
def requests_items(result) -> list: ...           # Request entries (D4); result may be None
def lists_view() -> dict: ...                     # ListsView; live mode calls db.ensure_watchlist() first
def list_page_view(list_id, sort="manual", page=1) -> dict|None: ...   # ListPageView; per_page LIST_PAGE_SIZE
def parse_list_page_query(query) -> dict: ...     # {"sort": valid or "manual", "page": int >= 1}
def watchlist_id() -> int|None: ...               # db.ensure_watchlist() in live mode, None in sample
# Changed: library_items(result, "added") -> requests_items(result); search_view results and browse_view items go
#   through with_user_state (browse_view keeps its "stars" key; it also gains lists/on_watchlist);
#   list_view(show="open"|"available") filters on item["request_state"] (open = not "available").
# /rate: message title from title_stub(fetch=False) (falls back to the existing "Rated N/5" text).

# pages.py (frontend-dev)
def render_title(view, msg="", undo=None) -> str: ...        # view = web.title_view(...) (the handler computes it)
def render_lists(msg="") -> str: ...                          # calls web.lists_view()
def render_list(view, msg="") -> str: ...                     # view = web.list_page_view(...)
def render_list_dialog(media_type, tmdb_id, return_to, partial=False) -> str: ...
def _title_url(item) -> str: ...                              # "/title/{movie|tv}/{int id}"
def _rate_form(item, return_to, compact=False) -> str: ...
def _card_tools(item, return_to, stars=True, lists=True) -> str: ...   # "" when the item has no tmdb_id
def _season_picker(choices, form_id) -> str: ...              # shared by the add dialog and the detail page
# render_add_dialog: tv adds the season picker from web.season_choices(id); a tracked series hides the profile select and
#   says "Request more seasons"; the existing search checkbox stays. render_library("added"): label "Requests", state badge,
#   seasons line, show filter labels. Every card renderer: poster-link + card tools per D3.
```
pages.py reads `web.*` inside functions only, as today.

### HTTP
All POSTs pass CSRF first (403 JSON `{"ok": false, "message": "Blocked: request came from another site"}`, or text/plain without JSON). Every JSON error uses `{"ok": false, "message": str}`. "No-JS" means a 303 to `_safe_path(return_to)` plus `msg`, the same text as JSON; bad input 303s back without a msg, as `/rate` does today. In sample mode every write route below answers 200 `{"ok": false, "message": "Sample data - not saved"}` (303 + that msg) **after** input validation and before any db or arr access. Validation order: CSRF, parse (400), sample, existence (400), limits (200 ok:false), write.

| Method | Path | Request | JSON response | No-JS |
|---|---|---|---|---|
| GET | `/title/movie/<id>`, `/title/tv/<id>` | path parts validated with `_parse_id`; query `msg`, `undo_type`, `undo_id` | n/a (HTML) | 200 `render_title(title_view(t, id), msg, undo)`; **404 rendered page** when `state == "not_found"`; any other path under `/title/` (bad type, bad id, extra parts) gives the 404 text |
| POST | `/add` | existing fields + `seasons` (`all`\|`pick`, tv only) + `season` (repeatable) | unchanged shape `{"ok", "message"}`. 400 `"That isn't a valid season choice"` (bad `seasons` value), `"Pick at least one season"`, `"That isn't a valid season"` (non-digit, 0, > 9999, > 200 values) | unchanged. On success: `db.record_added(item, seasons)` and `note_arr_item(arr_item)` |
| GET | `/add-dialog` | unchanged | | tv: includes the season picker (`web.season_choices`) |
| GET | `/requests` | `msg` | | 303 to `/library?type=added` (+ `msg` if set) |
| GET | `/library?type=added&show=open\|available` | | | `show` validated against the new `LIST_OPTIONS["added"]` |
| GET | `/lists` | `msg` | | 200 `render_lists(msg)` |
| GET | `/lists/<id>` | `sort`, `page`, `msg` | | 200 `render_list(view, msg)`; 404 text when the id isn't digits or `list_page_view` is None |
| GET | `/watchlist` | | | 303 to `/lists/<id>` (live) or `/lists` (sample) |
| GET | `/list-dialog` | `type`, `id`, `return_to`, `partial=1` | | like `/add-dialog`: bad type/id gives 404; `partial=1` gives just the fragment (`Cache-Control: no-store`) |
| POST | `/lists/create` | `name`, `description`, optional `type`+`id` (also add that title), `return_to` (default `/lists/{new id}`) | 200 `{"ok": true, "message": "Created \"Horror night\"", "list": ListSummary}`. 400 `"Give the list a name"`, `"List names can be up to 60 characters"`, `"Descriptions can be up to 300 characters"`, `"You already have a list called \"X\""`, `"That isn't a valid title"`. 200 ok:false `"You can have up to 50 lists"` | 303 |
| POST | `/lists/update` | `list_id`, `name`, `description`, `return_to` (default `/lists/{id}`) | 200 `{"ok": true, "message": "Saved \"X\"", "list": ListSummary}`. 400 `"That list doesn't exist"`, `"The Watchlist can't be renamed or deleted"` + the name/description errors | 303 |
| POST | `/lists/delete` | `list_id`, `return_to` (default `/lists`) | 200 `{"ok": true, "message": "Deleted \"X\""}`. 400 as update | 303 |
| POST | `/lists/add` | `list_id` (digits) **or** `list=watchlist`, `type`, `id`, `return_to` | 200 `{"ok": true, "message": "Added \"Dune\" to Watchlist", "list": {"id", "name", "kind"}, "item": {"type", "id"}, "in_list": true, "lists": [int], "on_watchlist": bool}`. Already there: ok true, `"Already on Watchlist"`. 400 `"That list doesn't exist"` / `"That isn't a valid title"`. 200 ok:false `"That list is full (1000 titles)"` / the lookup failure (D6) | 303 |
| POST | `/lists/remove` | same fields | same shape with `"in_list": false`, `"Removed \"Dune\" from Watchlist"`; not there: ok true, `"Not on Watchlist"` | 303 |
| POST | `/lists/set` | `type`, `id`, `list_id` (repeatable: the full set that should contain the title, Watchlist included), `new_list` (optional name: create it and include it), `return_to` | 200 `{"ok": true, "message": "Saved - on 2 lists" \| "Saved - not on any list", "item", "lists": [int], "on_watchlist": bool, "created": {"id", "name"}\|null}`. 400 for an unknown list id, a bad title or a bad `new_list` name (same messages as create). Limits as add/create | 303 |
| POST | `/lists/move` | `list_id`, `type`, `id`, `direction` (`up`\|`down`\|`top`\|`bottom`), `return_to` | 200 `{"ok": true, "message": "Moved", "moved": bool}`. 400 `"That isn't a valid move"`, `"That list doesn't exist"`, `"That isn't a valid title"` | 303 |

- `/lists/set` resolves the title stub (fetch=True) only when it actually adds the title somewhere.
- The list-page `return_to` keeps `sort`/`page`.
- `/lists/*` and `/add` all live under `do_POST`. Every new POST joins `POST_ROUTES` in `tests/test_web_async.py`.
- The orchestrator updates api-conventions and design-system after review.

### Data / storage (db.py `_connect()`; additive, idempotent, safe on the live `compass.db`)
```sql
CREATE TABLE IF NOT EXISTS lists (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'custom' CHECK (kind IN ('watchlist','custom')),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS lists_one_watchlist ON lists(kind) WHERE kind = 'watchlist';
CREATE TABLE IF NOT EXISTS list_items (list_id INTEGER NOT NULL, media_type TEXT NOT NULL CHECK (media_type IN ('movie','tv')),
  tmdb_id INTEGER NOT NULL, title TEXT NOT NULL, year INTEGER, poster_url TEXT, added_at TEXT NOT NULL,
  position INTEGER NOT NULL, PRIMARY KEY (list_id, media_type, tmdb_id));
CREATE INDEX IF NOT EXISTS list_items_title ON list_items(media_type, tmdb_id);
```
- No foreign keys: SQLite needs `PRAGMA foreign_keys` on every connection. `delete_list` removes the items itself, in one transaction.
- `added.seasons TEXT` (NULL, `'all'`, or a JSON list such as `'[1,3]'`): add it only if `PRAGMA table_info(added)` lacks it. Catch `sqlite3.OperationalError` containing "duplicate column", because two threads can race on first connect.
- Nothing is backfilled. Old rows read as `seasons=None`.
- The test proves `_connect()` twice on an old-schema file (made without the column or tables) works and keeps the existing rows.

### Markup contract (pages.py / app.js / app.css share these)
Every untrusted string goes through `escape`, and every URL through `_web_url` (TMDB images, the YouTube trailer, the TMDB/IMDb links: `https://www.imdb.com/title/{imdb_id}/`). No new inline `style` except `--h:{int}`.

**Poster link** (every card with a tmdb_id). Inside `.card-poster`, the poster image/placeholder is wrapped:
`<a class="poster-link" href="/title/{type}/{id}" aria-label="More info: {Title (Year)}">{_poster_html}</a>`.
- `data-open-detail` is removed everywhere.
- Badges, kind, rank and match stay siblings, and CSS gives them `pointer-events: none`.
- Library, watched and lib-card titles link to the detail page (same tab) instead of TMDB, when there's a tmdb_id. Items without a tmdb_id stay unlinked.

**Card tools** (`_card_tools`):
```html
<div class="card-tools">
  <form class="rate rate-compact" method="post" action="/rate" data-enhance="rate">{hidden type/id/return_to}
    <div class="stars" role="group" aria-label="Your rating for {title}">{5 .star buttons as today}</div>
    <p class="rating-text visually-hidden">{text}</p>[clear button as today]</form>
  <form class="inline list-toggle-form" method="post" action="/lists/{add|remove}" data-enhance="list">
    {hidden type/id/return_to}<input type="hidden" name="list" value="watchlist">
    <button type="submit" class="list-toggle[ on]" aria-pressed="{true|false}">
      {bookmark svg .list-icon aria-hidden}<span class="list-toggle-text">{Watchlist|On Watchlist}</span></button></form>
  <a class="list-link" href="/list-dialog?type=..&id=..&return_to=.." data-list-dialog aria-label="Add {title} to a list">
    {list svg .list-icon aria-hidden}<span class="visually-hidden">Lists</span></a>
</div>
```
- `stars=False` drops the rate form (Watched cards keep their full form).
- `lists=False` drops the toggle and the link.
- In sample mode the tools still render; the POSTs refuse with a toast.

**Hero** (`_hero_slide`): `.hero-actions` holds `{primary}<a class="btn-ghost hero-more" href="{title url}">More info</a>{_card_tools(compact, lists=False) with extra class hero-tools}`. There's no `<details>` and no hidden `.card-poster`.

**Detail page** (`render_title`): `_shell(body, section, cinematic=True)`, with section `movies` or `tv` from the type and the visually-hidden `h2` = the title (frontend adds a `heading` param to `_shell`).
```html
<article class="title-page" data-card="{type}-{id}" data-keep-on-add>
  <section class="title-hero" style="--h:N">[<img class="title-backdrop" src alt="" >]
    <div class="title-hero-inner">
      <div class="title-poster">{poster img w780 / w342 / placeholder}</div>
      <div class="title-head">
        <p class="kicker">{Movie|TV series}</p><h1 class="title title-name">{title}</h1>
        [<p class="title-tagline">{tagline}</p>]
        <p class="card-meta title-meta">[match][year][cert][runtime/seasons]<span class="title-genres">{genres}</span></p>
        <p class="title-status badges">{status tag, arr-state badge, Watched, Not interested, On Watchlist}</p>
        <div class="title-actions">{Add link (data-add-dialog) | "Request more seasons" link | In library tag}
          [<a class="btn-ghost trailer-link" href="{youtube}" target="_blank" rel="noopener noreferrer">Watch trailer
             <span class="visually-hidden">(opens YouTube in a new tab)</span></a>]
          {dismiss form | undismiss form (not sample)}
          {_rate_form(full)} {Watchlist toggle} {list link (labelled "Add to list")}</div>
        [<p class="in-lists">On your lists: <a href="/lists/1">Watchlist</a>, ...</p>]
      </div></div></section>
  {_message_notes(msg, undo, return_to)} [<p class="note">{view.message}</p>]
  <section class="title-section title-overview"><h2>Overview</h2><p class="overview">..</p>
    <dl class="title-facts">{Status, Release / First aired, Runtime / Seasons, Network(s) / Studio(s), Director / Creator, TMDB x.x}</dl>
    {TMDB link .ext-link, IMDb link .ext-link}</section>
  [<section class="title-section why"><h2>Why Compass picked this</h2><p class="why-match match">{n}% match</p>
     <p class="reason">..</p><div class="chips">..</div>[<p class="muted">Because you watched A, B</p>]</section>]
  [<section class="title-section why fit"><h2>How it fits your taste</h2><div class="chips">..</div></section>]
  [<section class="title-section seasons"><h2>Seasons</h2>
     <form class="season-form" method="post" action="/add" data-enhance="add" data-after="reload">{hidden type/id/return_to}
       <ul class="season-list">{<li class="season-row">
         [<input type="checkbox" class="season-check" name="season" value="N" id="s-N">  (only if selectable and can_add)]
         <label for="s-N" class="season-name">{name}</label><span class="season-eps">{n} episodes · {air year}</span>
         [<span class="badge arr-state season-state state-{state}">{label}</span>]</li>}</ul>
       [<div class="season-actions">  (only when the service is configured, not sample, and something is selectable)
         <label class="checkbox-label"><input type="checkbox" name="search" value="1" checked> Search now</label>
         <button type="submit" name="seasons" value="pick" class="btn-add">Request selected seasons</button>
         <button type="submit" name="seasons" value="all" class="btn-ghost">Request all seasons</button>
         <a class="link-btn" href="/add-dialog?..." data-add-dialog>More options</a></div>]</form></section>]
  [<section class="title-section cast"><h2>Cast</h2><ul class="cast-list">{<li class="cast-card">
     <span class="cast-photo">{img w185 | initial placeholder}</span><span class="cast-name">..</span>
     <span class="cast-role">..</span></li>}</ul></section>]
  [<section class="row title-similar">..same markup as _row_html: row-head h3 "More like this", .track-wrap/.track of _row_card..</section>]
</article>
```
- Season state labels: available "Available", partial "{have}/{total} episodes", missing "Wanted", upcoming "Upcoming", unmonitored "Not requested" when the series isn't monitoring it. Requested and complete rows show no checkbox.
- `state="unavailable"`/`"not_found"` render inside the shell: `<div class="empty"><h3>{message}</h3><a class="btn-ghost" href="/search">Search instead</a></div>`.
- The return_to for every form on the page is `/title/{type}/{id}`.

**Season picker** (`_season_picker`, in the add dialog; the detail page uses its own season form above):
```html
<fieldset class="season-picker"><legend>Seasons</legend>
  <label class="season-mode"><input type="radio" name="seasons" value="all" checked> All seasons</label>
  [<label class="season-mode"><input type="radio" name="seasons" value="pick"> Choose seasons</label>
   <ul class="season-list">{<li class="season-row"><label class="checkbox-label"><input type="checkbox" class="season-check"
       name="season" value="N"[ checked disabled - requested]> {name} <span class="season-eps">{n} episodes</span>
       [<span class="badge arr-state season-state state-..">..</span>]</label></li>}</ul>]
  [<p class="muted">Season list unavailable right now - you can still add all seasons.</p>  (not known)]
</fieldset>
```
For a tracked series the "All seasons" label reads "All remaining seasons" and the profile select is omitted.

**List dialog** (`render_list_dialog`; page or fragment, `_safe_path(return_to)`):
```html
<div class="dialog-box list-dialog"><div class="dialog-head"><div><h3 id="list-dialog-title">Add "{title}" to lists</h3></div></div>
  <form method="post" action="/lists/set" data-enhance="lists">{hidden type/id/return_to}
    <fieldset class="list-choices"><legend class="visually-hidden">Lists</legend>
      {<label class="checkbox-label list-choice"><input type="checkbox" name="list_id" value="{id}"[ checked]> {name}
        <span class="muted">{count}</span></label>}</fieldset>
    [<label class="list-new">New list<input type="text" name="new_list" maxlength="60" placeholder="Name"></label> (when can_create)]
    <div class="card-actions"><a class="btn-ghost" href="{return_to}" data-close>Cancel</a><button type="submit" class="btn-add">Save</button></div>
  </form></div>
```
- In live mode the Watchlist is created first (`web.lists_view()`), so it always has an id.
- In sample mode the box shows a note and no form.
- A title that can't be looked up gets the "Can't add this one" body, mirroring the add dialog.

**Lists page** (`render_lists`): the Library subtabs (with Lists on), then:
`<div class="lists-page">{notes}<div class="lists-grid">{<a class="list-tile" href="{url}"><span class="list-tile-posters">{up to 4 <img> or placeholders}</span><span class="list-tile-name">{name}</span><span class="list-tile-meta">{count} titles</span>[<span class="list-tile-desc">{desc}</span>]</a>}</div>`, then
`<form class="list-create" method="post" action="/lists/create"><label>Name<input name="name" maxlength="60" required></label><label>Description<input name="description" maxlength="300"></label><button class="btn-add">Create list</button></form>` (shown when `can_create`).
- Sample mode adds a note "Sample data - lists aren't saved." and leaves out the create form.
- The virtual Watchlist renders as a `div.list-tile`, not a link.

**List page** (`render_list`): subtabs, `<header class="list-head"><h3>{name}</h3>[<p class="muted">{desc}</p>]`, then, for custom lists only:
- `<details class="list-edit"><summary>Edit</summary>` with an update form;
- `<details class="list-delete"><summary>Delete list</summary><p>Delete "{name}"? This can't be undone.</p><form ... action="/lists/delete"><button class="btn-ghost danger">Delete</button></form></details>`;

then `</header>`, then:
- a `.toolbar` GET form with a `sort` select (labels: Your order, Recently added, Title A-Z, Newest year, Your rating, Match %);
- the grid `<div class="grid" data-grid>` of `_list_card` cards (search-card-like: poster-link, status tag, title, `.card-tools`, plus `<div class="list-move">` with Up/Down/Top forms posting `/lists/move` with `data-enhance="list-move"` when `can_move`, plus a "Remove" form `/lists/remove` with `data-enhance="list"` and `data-remove-card`);
- the `.pager`.

The empty state is `.empty` "Nothing on this list yet" with a "Browse recommendations" link.

**Requests tab** (`render_library("added")`): the subtab label is "Requests".
- Cards add `<span class="badge req-state req-{state}">{label}</span>` in `.poster-top .badges`, and `<p class="card-sub req-seasons">{All seasons | Seasons 1, 3 | (nothing for movies/None)}</p>`.
- The show select labels are Everything / Not available yet / Available.
- Card tools as D3.
- The empty-state copy changes "Added here" to Requests and keeps the text `Added `.

**app.js** (frontend-dev). Keep the createElement/textContent rule. `setFragment` stays the only `innerHTML` (now 3 callers).
1. **Remove** `openDetail`, the `[data-open-detail]` role=button setup and keydown, and the summary/poster click interception. Keep the delegated `a[data-add-dialog]` and `[data-close]` handling.
2. **Preview.**
   - "More info" and the preview poster navigate to the card's `a.poster-link` href.
   - The library branch's "Open" link points at the poster-link href instead of TMDB.
   - Both branches clone `.list-toggle-form` (icon-only, `tabindex=-1`) and `a.list-link`.
   - `openAddDialog`'s return-focus fallback uses `.poster-link`.
3. **Dialog fragments.** Generalize `openAddDialog(link)` into `openFragmentDialog(link, focusSelector)` for `a[data-add-dialog]` and `a[data-list-dialog]` (fetch `href + &partial=1`; fall back to navigating).
4. **`handlers.list`** (`/lists/add|remove` forms):
   - post; on `ok`, update **every** `.list-toggle-form` for that type/id from `on_watchlist` (only if the form's `list` is watchlist): flip the `action`, `.on`, `aria-pressed` and the label text;
   - if the form has `data-remove-card` and `in_list` is false, `removeCard` its card;
   - toast the message; on `!ok`, an error toast.
5. **`handlers.lists`** (dialog `/lists/set`): on `ok`, close the modal, toast, and sync the watchlist toggles from `on_watchlist` and the detail page's `.in-lists` (or `location.reload()` when `created` is set on a detail page; frontend's call).
6. **`handlers["list-move"]`**: on `ok` and `moved`, move the card's DOM node (`up`: before its previous sibling; `down`: after its next; `top`/`bottom`: first/last in the grid), keep focus on the pressed button, toast.
7. **`handlers.rate`**: unchanged logic. `applyRating` already copes with compact forms; the created Clear button gets `tabindex=-1` only inside `.preview`.
8. **`handlers.add`**: a form with `data-after="reload"` (detail page) navigates on `ok` to `return_to` + `msg=` the message (replacing any existing `msg`). Otherwise it behaves as today.
9. **Season picker**: a `change` on `.season-check` inside a form with `input[name=seasons][value=pick]` checks that radio. The detail page's season form needs no JS.
10. **Static check**: still no external URLs (the SVG namespace stays split).

## File ownership
| Agent | Files | Task |
|---|---|---|
| backend-dev | `http_util.py`, `tmdb.py`, `radarr.py`, `sonarr.py`, `arr_library.py`, `db.py`, `profile.py`, `recommend.py`, `sources.py`, `sample.py`, `web.py`; tests `test_http_util.py`, `test_plex_tmdb.py`, `test_radarr_sonarr.py`, `test_arr_library.py`, `test_library.py`, `test_profile.py`, `test_recommend.py`, `test_sources.py`, `test_web_async.py`, `test_web.py`, plus new `tests/test_lists.py` and `tests/test_title.py` | Data, clients, db, profile, web helpers, all POST routes (phase A), GET routes + end-to-end (phase C) |
| frontend-dev | `pages.py`, `static/app.js`, `tests/test_ui.py` | Detail page, poster links, card tools everywhere, hero change, season picker, list dialog + pages, Requests tab, JS (phase B) |
| ux-designer | `static/app.css` | Style every class in the brief (phase A, in parallel) |
| orchestrator | `README.md`, `.claude/skills/api-conventions`, `.claude/skills/design-system` | After review |

`config.py`, `browse.py`, `plex.py`, `tautulli.py`, `ai.py`, `themes.py` and `settings_page.py` don't change. No file has two owners. **ux-designer is needed**: about 50 new classes across a new page type, the lists pages, card tools and the season UI. frontend-dev can't edit app.css.

**Phases.** web.py imports render functions from pages.py at load time, so new GET routes can't land before the renderers exist. This is the same 3-phase pattern as specs/search-and-arr-library.md:
1. **Phase A: backend-dev ‖ ux-designer.**
   - Backend does everything in the Python interface and **all POST routes**: `/add` seasons, `/rate` title, and the `/lists/*` routes, which need no renderer. It also does `MAX_FORM_BYTES`, the `LIST_OPTIONS["added"]` change and the `parse_list_query` fallout.
   - It does **not** add the new GET routes or change `from pages import`.
   - The full suite stays green. A Library `show=open` request then validates but renders as today until phase B.
2. **Phase B: frontend-dev**, after backend A.
   - Test the renderers directly with patched `web.*` (`title_view` results built by hand, `lists_view`, `list_page_view`, `season_choices`, `with_user_state`).
   - Update the test_ui tests that pin the removed modal hooks (`data-open-detail`, `openDetail(card`, the hero `<details>`/"Your rating" text in the hero, lib-card "no form").
   - frontend may start the app.js items during phase A if the orchestrator wants the overlap; app.js depends only on this contract.
3. **Phase C: backend-dev** (resume).
   - Add `GET /title/...`, `/lists`, `/lists/<id>`, `/watchlist`, `/list-dialog`, `/requests`, and the `from pages import render_title, render_lists, render_list, render_list_dialog`.
   - Add the end-to-end tests. The full suite goes green.
4. Reviewer, then Hamish clicks through `SAMPLE=1 PORT=8099 python3 web.py`.

Not worth splitting further: one backend agent does both phases, because ownership puts every `.py` but pages.py with it.

## Acceptance criteria
Full suite: `python3 -m unittest discover -s tests` (747 OK today). Every new route has JSON, no-JS redirect, bad input (400), sample and CSRF (403) tests.

**Phase A (backend)**
- [ ] **`put_json`** sends method PUT with a JSON body and no retries, and an HTTPError message includes the body. Proof: `python3 -m unittest tests.test_http_util`
- [ ] **TMDB title** (`mock.patch.object(tmdb, "get_json")`). Proof: `python3 -m unittest tests.test_plex_tmdb`
  - `title()` makes **one** request with exactly the append list, timeout 8 and retries 1, and writes `details:v2`, `title:v1` and `external_ids`;
  - `cached_title` never requests;
  - `normalize_title`:
    - trailer preference order;
    - a non-YouTube video, a bad key or `javascript:` gives None;
    - cast max 12 with characters and a profile URL only for a valid path;
    - movie crew priority, and tv creators;
    - seasons sorted, specials kept here, a missing episode_count gives 0;
    - similar fills up from `similar`, deduped, max 20, malformed rows skipped;
    - a bad imdb_id gives None;
    - a hostile string type gives the default.
- [ ] **Radarr/Sonarr.** Proof: `python3 -m unittest tests.test_radarr_sonarr`
  - `arr_id`/`seasons` in normalize (missing statistics gives 0/0; `totalEpisodeCount` falls back to `episodeCount`).
  - Sonarr `add`, checked with `mock.patch.object(sonarr, "get_json"/"post_json"/"put_json")` by asserting the **exact payloads**:
    - new series with a pick: every lookup season present, only the picks monitored, specials false, no `addOptions.monitor`, `monitorNewItems` "none";
    - `"all"`: every season > 0 monitored, `monitorNewItems` "all";
    - `None`: today's payload plus the season flags;
    - `search=False`: `monitored: false` and `searchForMissingEpisodes: false`;
    - a picked season not in the lookup gives `(False, "Season 7 isn't listed ...")` and no POST.
  - Existing series:
    - `seasons=None` gives "Already in Sonarr" with no PUT;
    - a pick: PUT `/api/v3/series/{id}`, only additive changes, `series.monitored` True, one `SeasonSearch` per new season when searching and none when not;
    - `"all"` gives `SeriesSearch`;
    - nothing new gives "Already monitoring";
    - `_find` filters on `tvdbId` client-side.
  - `last_item` is set from the response and stays None on failure.
  - Radarr `last_item`.
  - The API key appears in no message.
- [ ] **arr_library.** Proof: `python3 -m unittest tests.test_arr_library`
  - `find` (tmdb, tvdb, title fallback only without a tmdb id);
  - `season_rows` covers every state, the union of TMDB and arr numbers, specials dropped, and `selectable`/`requested`;
  - `request_state` covers every row of the D4 table, including a TV pick summed over the requested seasons and the no-arr Plex case.
- [ ] **db.** Proof: `python3 -m unittest tests.test_lists tests.test_library`
  - migrations run twice on a pre-feature file (the old `added` without `seasons` keeps its rows), and the duplicate-column race is tolerated;
  - `record_added` merge rules;
  - `added_items()["seasons"]` round-trip, and corrupt gives None;
  - `rating_rows`;
  - `ensure_watchlist` is idempotent (one row after two calls and after a simulated race);
  - `lists()` order and counts;
  - create/update/delete (the watchlist refuses; delete removes its items);
  - add-to-top positions, duplicate add gives False, remove, every move direction including the ends, `memberships`.
- [ ] **profile / recommend / sources.** Proof: `python3 -m unittest tests.test_profile tests.test_recommend tests.test_sources`
  - `rated_entries` skips watched titles and doesn't mutate its input;
  - a 5★ unwatched title raises its genres in `profile.build`;
  - a rated-only title is excluded from `recommend` items;
  - `title=None` gives "Because you watched {details title}";
  - live `run()` passes the synthetic entries (with `db.rating_rows` mocked);
  - sample recommendations are byte-for-byte unchanged (keys and order);
  - `add_to_library` returns 3-tuples, including every refusal path (`(False, msg, None)`).
- [ ] **web helpers.** Proof: `python3 -m unittest tests.test_title tests.test_library tests.test_web_async`
  - `parse_seasons` handles every rule in D2;
  - `title_stub` follows its source order and makes no request with `fetch=False`;
  - `title_data` covers cached (no budget use) / limited / error (no exception text) / 404 gives not_found / sample / no token;
  - `title_view` covers:
    - rec vs fit sections;
    - similar ordering (rec by match, then cached content_score, then TMDB order; dismissed dropped);
    - status/arr/seasons merge;
    - partial with the base item from a rec when TMDB is down;
    - user state.
  - `season_choices` covers tracked and untracked series and the unknown case;
  - `note_arr_item` replaces or inserts under the lock;
  - `with_user_state` (sample gives None/[]/False);
  - `requests_items`;
  - `lists_view` (sample: a virtual Watchlist and **no db write**, asserted with a patched `db.ensure_watchlist`);
  - `list_page_view` covers every sort (None last, title tie-break), paging, `can_move`, and a missing list gives None;
  - `list_view(show="open")`.
- [ ] **POST routes.** Proof: `python3 -m unittest tests.test_web_async`
  - `/add`:
    - tv with `seasons=pick&season=1&season=3` reaches `add_to_library(..., seasons=[1, 3])`;
    - `seasons=all` gives `"all"`; absent gives None;
    - 400 for `seasons=bogus`, `pick` with no season, `season=0`, `season=x`, `season=10000` and 201 values;
    - movies ignore seasons;
    - success records seasons and calls `note_arr_item`; a failure records nothing.
  - `/lists/create|update|delete|add|remove|set|move`: every success shape in the HTTP table, every 400 message, the 50-list and 1000-item limits (200 ok:false), the watchlist rename/delete refusal, `list=watchlist` creating the Watchlist on first use, `/lists/set` diffs (add + remove + new list), and the lookup failure refusal.
  - For every route: no-JS 303 with msg to `return_to` (defaults as in the table, off-site gives the default); sample refusal with no db write in both modes.
  - All new POSTs are in `POST_ROUTES`, giving 403 cross-site and 303 same-origin.
  - A 10 KB form body is read whole.
  - The `/rate` message uses the stub title for a non-watched recommendation.

**Phase B (frontend)**: `python3 -m unittest tests.test_ui`
- [ ] **Detail page** renders every state:
  - ok (movie and tv), partial (with the note), unavailable, not_found;
  - hostile title/tagline/cast/overview/season names are escaped;
  - a `javascript:` trailer/backdrop/profile URL is dropped;
  - "Why Compass picked this" only for recs, "How it fits your taste" only for fit;
  - seasons:
    - checkboxes only for selectable seasons when Sonarr is configured and it's not sample;
    - the two submit buttons have `name="seasons"` values `pick`/`all`;
    - `data-after="reload"`;
    - `return_to=/title/tv/N`.
  - the similar row uses row cards with match only for recs;
  - in-lists links;
  - full rate form, Watchlist toggle and list link;
  - dismiss vs undismiss;
  - no Add/dismiss/season form in sample mode.
- [ ] **Poster links and tools.**
  - Every card renderer (row, hero, search, library, watched, lib-card, AI, list, requests) has `a.poster-link` to `/title/{type}/{id}`, and no `data-open-detail` remains in pages.py.
  - Cards without a tmdb_id have no link and no tools.
  - `.card-tools` (compact stars, toggle, link) appears where D3 says, with the right state for `stars`/`on_watchlist`.
  - The hero has `hero-more`, `hero-tools` and no `<details>`.
- [ ] **Add dialog seasons.**
  - Untracked: the radios plus the season list.
  - Tracked: "All remaining seasons", disabled checked requested seasons, no profile select.
  - Unknown: only all, plus the note.
  - Movies: no picker.
- [ ] **List dialog** in page and partial modes: checked state, the new-list field only when `can_create`, the sample note, escaping.
- [ ] **Lists pages**: tiles, the virtual Watchlist not linked, the create form only when `can_create`, the edit/delete details only for custom lists, sort options, move forms only when `can_move`, the remove form with `data-remove-card`, pager links keep `sort`, the empty state.
- [ ] **Requests tab**: the "Requests" label, each state badge label, the seasons line, the show filter labels.
- [ ] **Static** (`tests.test_ui.TestStaticAssets`):
  - no external URLs;
  - exactly one `innerHTML`, inside `setFragment`;
  - app.js contains `data-list-dialog`, `poster-link`, `data-after`, `list-move`, `data-remove-card`, `season-check`, `on_watchlist`;
  - app.js does **not** contain `openDetail(`.

**Phase C (backend)**: `python3 -m unittest tests.test_web_async tests.test_web tests.test_title`
- [ ] **Routes.**
  - `/title/movie/1001` reaches `render_title` with the view, and `msg`/undo are passed through;
  - not_found gives a 404 HTML page;
  - `/title/film/1`, `/title/movie/abc`, `/title/movie/1/x` and `/title/movie/` give the 404 text;
  - `/lists/<id>` passes the parsed sort/page; a bad id gives 404 text;
  - `/requests` and `/watchlist` give a 303 with the right Location;
  - `/list-dialog` partial has `Cache-Control: no-store`, no `<html`, and a bad id gives 404.
- [ ] **Sample end to end:**
  - `/title/movie/1005` (Dune) shows "In library"/"In Radarr", the cast names and no Add;
  - `/title/tv/2040` shows Season 1 "Available" and Season 2 "Wanted" (5/5 and 0/5);
  - `/title/movie/999999` gives 404;
  - every card on `/`, `/search?q=dune`, `/library`, `/library?type=added` and `/lists` links to `/title/`;
  - `/lists` shows "Watchlist" and the sample note;
  - `/library` subtabs include "Requests" and "Lists".
- [ ] **Full suite green**: `python3 -m unittest discover -s tests`

**Manual** (no browser here): Hamish clicks through `SAMPLE=1 PORT=8099 python3 web.py`.
- Poster click and the preview's More info open the page, and Back returns.
- Stars toast "Sample data - not saved" on every surface listed in D3, including at phone width (375px, no horizontal scroll).
- The Watchlist toggle and the list dialog open and toast.
- With JS off: the detail page, the season form, the list dialog page, the lists pages and the stars all work as plain forms.
- Every theme, light and dark; focus rings visible.
- Live Sonarr season adds and the "request more seasons" PUT can only be checked on arr after deploy, with Hamish's OK (Risk 1).

## Live-request budget
**Zero** for every implementer and the reviewer. Mock at the boundary: `tmdb.get_json`, `radarr.get_json`/`post_json`, `sonarr.get_json`/`post_json`/`put_json`, `TmdbClient.title`/`details`, `sources.add_to_library`, `web.lookup_item`. Any live-mode `/lists/*` or `/title` test must patch `TmdbClient.title` and `web.lookup_item`.

Runtime cost in production:
- one TMDB request per uncached title page (3-day cache), which also pre-warms `details:v2` and `external_ids`, within the shared budget with the lookup reserve kept;
- at most one budgeted lookup per list add of a title not otherwise known;
- Sonarr: one extra `GET /series?tvdbId=`, plus PUT and commands, only when requesting more seasons;
- **no new per-page-view Radarr/Sonarr/Plex calls.**

## Risks (riskiest first)
1. **Sonarr API semantics** aren't verifiable here:
   - whether omitting `addOptions.monitor` keeps our per-season flags on v3 **and** v4;
   - `monitorNewItems` on v3;
   - whether `PUT /api/v3/series/{id}` re-flags episodes for newly monitored seasons;
   - whether `GET /series?tvdbId=` filters.

   The payloads match Overseerr/Jellyseerr's, but Hamish must test one add with a pick and one "request more seasons" on arr after deploy.
2. **TMDB vs TVDB season numbering** (anime, year-numbered shows). We validate against Sonarr and show a clear refusal; the picker may still list seasons Sonarr calls something else.
3. **Retiring the modal** touches the most JS (preview, focus return, hero) and pins in test_ui. That's the biggest frontend churn.
4. **The rated-only profile change** shifts real recommendations (rated titles disappear at the next rebuild). That's Q1.
5. **Scope.** Four agent runs and about 30 new tests per side. If it has to shrink, cut Lists to the Watchlist only (no custom lists, set or move) and keep the rest.

## Open questions (none block phase A's db/clients work; Q1-Q3 shape behaviour)
**Resolved 2026-10-10 by Hamish: every default below is accepted** (Q1 yes, Q2 no weight, Q3 Library subtab, Q4-Q9 yes). Build exactly the defaults.

1. **A rating on an unwatched title means "I've seen it"** (D3): it seeds the taste profile by stars and the title drops out of recommendations at the next rebuild. Default **yes**. Alternative: ratings on unwatched titles only feed the dislike penalty (today's behaviour), and the README line gets corrected.
2. **Watchlist and taste.** Default: no taste weight, no exclusion, no boost (D6). Alternative: Watchlist titles seed the profile at 0.25 weight (`item_weight × 0.25`, no recency decay), and/or a "From your Watchlist" Home row in Phase 2.
3. **Where Lists live.** Default: a "Lists" Library subtab plus `/lists` and `/watchlist`, with no nav change. Alternative: a 6th nav item (bottom nav gets tight at 375px; ux would need to re-space it).
4. **Retire the card modal** in favour of the detail page everywhere (D1/D5), so phones lose the in-place quick view and the hero loses "Not interested". Default yes.
5. **"Added here" becomes "Requests"** (same URL, `/requests` alias). Default yes.
6. **Specials (season 0)** are never requestable in Phase 1. Default yes.
7. **"Search now" unchecked** on a new TV add keeps today's meaning: added unmonitored, chosen seasons pre-flagged. For "request more seasons" it means monitor without searching. Default yes.
8. **Limits**: 50 custom lists, 1000 titles per list, 60-character names, 300-character descriptions. Default yes.
9. **Discover rows** moved to Phase 2. Confirm.
10. **Phase 2 backlog** (not designed): multi-user, approvals, notifications, issue reporting, Plex OAuth, Trakt OAuth sync/import/export/scrobbling, Discover rows, add whole list to arr, drag-and-drop ordering, list undo, Plex per-season availability, arr queue progress, unmonitoring seasons.

---

## Task briefs

### backend-dev: phase A, then phase C
Spec: `specs/seerr-parity.md`. The Contract, HTTP table and Data/storage are fixed; report back if any of it can't work. Live-request budget: **none** (see "Live-request budget" for what to mock).

Own: `http_util.py`, `tmdb.py`, `radarr.py`, `sonarr.py`, `arr_library.py`, `db.py`, `profile.py`, `recommend.py`, `sources.py`, `sample.py`, `web.py`, and tests `test_http_util.py`, `test_plex_tmdb.py`, `test_radarr_sonarr.py`, `test_arr_library.py`, `test_library.py`, `test_profile.py`, `test_recommend.py`, `test_sources.py`, `test_web_async.py`, `test_web.py`, new `test_lists.py`, new `test_title.py`. Never touch `pages.py`, `static/*` or `tests/test_ui.py`.

**Phase A** (no new GET routes, no `from pages import` change; the suite stays green):
1. `http_util.put_json`.
2. tmdb:
   - constants, `normalize_title`, `cached_title`, `title()` (one request, three cache writes).
   - In sample: `SampleTmdb.title`/`cached_title`, `_SEASONS`, and arr fixtures with `arr_id`/`seasons`.
   - Prove the sample recommendations are unchanged.
3. Radarr/Sonarr: `arr_id`/`seasons` in normalize, `last_item`, Sonarr `_find`/`_put`/`add(seasons=)` exactly per D2.
4. `arr_library.find`, `season_rows`, `request_state`.
5. db:
   - the migrations (lists tables + index; the guarded `added.seasons` column);
   - `record_added(seasons)` merge, `added_items` seasons, `rating_rows`, and every list function.
6. `profile.rated_entries`, the recommend title fallback, and the sources history change plus the 3-tuple `add_to_library` (update every mock that returns a 2-tuple).
7. web:
   - constants, `MAX_FORM_BYTES`, `parse_seasons`, `title_stub`, `title_data`, `title_view`, `season_choices`, `note_arr_item`, `with_user_state` (wired into `search_view` and `browse_view`), `requests_items`, `library_items("added")`, `LIST_OPTIONS["added"]` + `list_view` open/available, `lists_view`, `list_page_view`, `parse_list_page_query`, `watchlist_id`;
   - `/add` seasons and record/inject;
   - the `/rate` title;
   - all seven `/lists/*` POST routes with the exact messages;
   - `POST_ROUTES` additions.
8. Tests per Phase A acceptance criteria.

Report any `test_web`/`test_library` assertion you had to change, and why.

**Phase C** (after frontend phase B):
- Add `GET /title/<type>/<id>` (strict path parsing; `render_title(title_view(...), msg, undo)`; 404 status for not_found), `/lists`, `/lists/<id>`, `/watchlist`, `/list-dialog` (partial: `Cache-Control: no-store`) and `/requests`, plus the four imports.
- Add the Phase C tests and the sample end-to-end checks.

Run `python3 -m unittest discover -s tests` after each phase. Report files changed, test counts, the real sample output you verified (e.g. `/title/tv/2040` season states), and any deviations.

### frontend-dev: phase B (after backend phase A)
Spec: `specs/seerr-parity.md`, especially D1, D3, D5, D6 and "Markup contract". Live-request budget: **none**.

Own: `pages.py`, `static/app.js`, `tests/test_ui.py`. Don't edit `static/app.css`; ask via the orchestrator if a class is missing. Use only existing classes plus the ones named in the spec.
1. Add `_title_url` and the poster link in every card renderer, and remove `data-open-detail`. Library, watched and lib-card titles link to the detail page.
2. `_rate_form(compact=)` and `_card_tools`, placed per the D3 table. lib-card gains `data-preview` and tools.
3. Hero per D5.
4. `render_title(view, msg, undo)` with every state and section, `_shell` heading param, cinematic layout and the season form.
5. The season picker in `render_add_dialog` via `web.season_choices`.
6. `render_list_dialog`, `render_lists`, `render_list` (sorts, move/remove forms, pager), and the "Lists" subtab link.
7. Requests tab: label, badges, seasons line, show labels.
8. app.js items 1-10. Exactly one `innerHTML`.
9. test_ui per Phase B acceptance criteria, built on patched `web.*` (the GET routes arrive in phase C). Update the tests that pinned the modal and hero `<details>`, and list them in your report.

Run `python3 -m unittest tests.test_ui`, then the full suite. Name any expected-red tests, and list everything in app.js that needs a hand check.

### ux-designer: phase A (parallel with backend)
Spec: `specs/seerr-parity.md` ("Markup contract"). Live-request budget: **none**. Own: `static/app.css` only.

Style these:
- **Cards:**
  - `.poster-link` (fills the poster box, no underline, focus ring visible on the poster). Siblings `.poster-top`, `.kind`, `.rank`, `.match` inside `.card-poster` get `pointer-events: none`.
  - `.card-tools` (wrapping row under the card text).
  - `.rate-compact` (smaller stars, still ≥28×32 px targets; `.star-clear` small).
  - `.list-toggle` (+ `.on`, `[aria-pressed="true"]`) with `.list-icon` and `.list-toggle-text` (the text may be visually hidden on grid cards but not on the detail page; pick a modifier via context such as `.title-actions .list-toggle-text`).
  - `.list-link`.
  - Hide `.card-tools` inside `.row-card` under `html.has-dialog`, like the row details.
- **Hero:** `.hero-more` and `.hero-tools` in `.hero-actions` (legible over the backdrop).
- **Detail page:**
  - `.title-page`, `.title-hero` (backdrop under the transparent top bar, gradient scrim, tinted `--h` fallback), `.title-backdrop`, `.title-hero-inner`, `.title-poster`, `.title-head`, `.title-name`, `.title-tagline`, `.title-meta`, `.title-genres`, `.title-status`, `.title-actions`, `.trailer-link`, `.in-lists`;
  - `.title-section` (+ h2), `.title-overview`, `.title-facts` (dl grid), `.why`, `.why-match`, `.fit`;
  - `.seasons`, `.season-form`, `.season-list`, `.season-row`, `.season-name`, `.season-eps`, `.season-state` (reuse `.badge.arr-state.state-*`), `.season-check`, `.season-actions`;
  - `.cast`, `.cast-list` (horizontal scroll like `.track`), `.cast-card`, `.cast-photo`, `.cast-name`, `.cast-role`;
  - `.title-similar` (reuses `.row`).
  - At 375px the poster stacks above the head.
- **Season picker** (add dialog): `.season-picker`, `.season-mode`.
- **Lists:**
  - `.list-dialog`, `.list-choices`, `.list-choice`, `.list-new`;
  - `.lists-page`, `.lists-grid`, `.list-tile` (link and div), `.list-tile-posters` (2×2 collage with placeholders), `.list-tile-name`, `.list-tile-meta`, `.list-tile-desc`, `.list-create`;
  - `.list-head`, `.list-edit`, `.list-delete`, `.btn-ghost.danger`, `.list-move`.
- **Requests:** `.badge.req-state` with `.req-requested`, `.req-processing`, `.req-upcoming`, `.req-partial`, `.req-available`, `.req-unmonitored` (text carries the meaning; don't rely on colour alone), and `.req-seasons`.

Rules:
- Tokens only. Prefer existing or non-theme tokens; a new theme token must go in all six theme blocks, dark and light (`TestThemeCss`).
- AA contrast in every theme, light and dark.
- `:focus-visible` kept.
- No horizontal scroll at 375px.
- `prefers-reduced-motion` respected.
- No external assets.

Run `python3 -m unittest tests.test_ui`. Report the classes styled, tokens added, and any class you needed that the spec doesn't name.
