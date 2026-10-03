# Cinematic Home, Movies and TV

Status: draft (open questions 1-3 need Hamish; none blocks phase 1 apart from Q1's one function)
Author: architect · Date: 2026-10-03

## Goal
Home, Movies and TV move to a Netflix-style "cinematic" layout and the card grid goes away. Each page has a full-width hero that rotates through your top 5 picks, then headed horizontal rows: Recommended for You, Because you watched X, Top 10, Trending in your favourite genre, Hidden Gems and New in your library. Movies shows only movies, TV only shows, and Home mixes both. Every page, including Library, AI and Settings, gets a new top bar (brand, nav, Settings) in place of the left sidebar. The phone bottom nav stays. There is no Classic view and no toggle.

Visual source of truth: the approved mockup `/tmp/claude-1000/-home-ops-whatsnext/20f6d317-3feb-470c-84a8-eede811b9ec3/scratchpad/mockup.html`. It's a scratch file, so the **orchestrator should copy it to `specs/cinematic-home-mockup.html` before briefing**. Ignore its "View: Cinematic" pill, because there is no toggle.

## Out of scope
- A Classic/grid view or a layout toggle. The `/recommended` grid page and its subtabs, including the "New & trending" tab, are removed (see Decision 2).
- An AI picks row. AI stays manual-only. Browse pages never read `_ai_state` and never call `start_ai_generation`.
- New POST routes, DB tables or settings, and changes to `/api/status`.
- Live per-page-view requests. Pages render only from the build result plus `db.added_items()`.
- Changes to the Library, AI and Settings page bodies. Only the shell (top bar) changes there.
- Home's stat tiles (Recommended / Added / Not interested / Watched analysed). They're dropped (Decision 9).
- A no-JS title detail page. Without JS, details come from the inline `<details>` as today.
- Rating unwatched titles from the hover preview (Q2, default: no Rate button).

## Decisions
1. **Routes.** Home is `/`, Movies is `/movies` and TV is `/tv`. All three are rendered by `pages.render_browse(kind)`, with kind `all|movie|tv`. They are lowercase `/noun` paths, per api-conventions.
2. **`/recommended` becomes a 303 redirect.** `type=movie` goes to `/movies`, `type=tv` to `/tv`, and anything else (`all`, `new`, invalid, missing) to `/`.
   - `msg` and a *valid* `undo_type`/`undo_id` carry over.
   - Every existing `return_to="/recommended"` default and `_safe_path(..., default="/recommended")` keeps working through this redirect, so those defaults and their tests stay unchanged.
3. **Row logic lives in a new pure module, `browse.py` (backend-owned).** `sources.py` can't import `web` (web imports sources), but both need `hero_picks`. `web.browse_view()` wraps it with the "owned" set.
4. **New TMDB fields, no cache-key bump.**
   - `tmdb.normalize()` adds `backdrop_url`, `poster_large_url`, `runtime`, `seasons` and `certification`. The cache key stays `details:v2`.
   - Old cache entries simply lack these keys and refill as they expire (30 days).
   - To make the hero look right straight away, a **live build** re-fetches details for the hero picks that lack `backdrop_url`: at most 5 per kind, so at most 15 per build. Usually it's 0 after the first build (Q1).
   - Nothing is fetched at page-view time.
5. **Hero = the first 5 items of the kind, in score order** (honest "top picks"). It doesn't reorder to prefer titles with backdrops.
   - Desktop shows the backdrop.
   - Phone shows the poster (`poster_large_url`, falling back to `poster_url`).
   - With no image at all, it shows a tinted gradient using the same hue formula as `_poster_html`.
6. **Rows may overlap each other; a title appears at most once per row.**
   - "Recommended for You" is picks 6-25. Picks 1-5 are already in the hero right above it.
   - "Top 10" is picks 1-10, numbered.
7. **"In library" tag.** A title gets `in_library: true` when its key is in the Plex library snapshot or in `db.added_items()` (the latter not in sample mode). Recommendations already exclude owned titles at build time, so in practice the tag shows on the "New in your library" row and only rarely elsewhere. When the tag shows, the hero shows it in place of the Add button.
8. **Top bar on every page.**
   - Desktop: brand, nav (Home, Movies, TV, Library, AI picks), with Settings on the right.
   - Phones (≤820px): the top bar keeps the brand and Settings. The same 5 nav items move to the bottom nav, replacing Recommended with Movies + TV. That's 5 items, as today.
   - The mockup's 4-item phone bottom nav predates Movies/TV being nav items (Q3).
9. **Dropped from Home:** the stat tiles and the Recommended page's subtabs. **Kept:** the taste summary (`.taste`), result notes, the stale-error note, "Updated N min ago", the Updating badge and the Refresh form. All of these move to a footer strip under the rows. Message and Undo notes from no-JS actions go **above** the hero, so they're visible after the 303.
10. **One tab stop per card.**
    - Without JS (or without `<dialog>`), each row card shows its `<details>`, which holds the reason, overview and the actions, as today.
    - With JS and `<dialog>`, `html.has-dialog` hides the row cards' `<details>`, and the poster becomes a focusable button that opens the existing detail modal.
    - The hover preview is a mouse-only shortcut to the same actions.

## Contract

### Data: new fields on recommendation items
`recommend.recommend()` items gain one key:
```python
item["because"]: list[str]   # titles of ALL watched sources that TMDB links to this item, highest weight first,
                             # de-duplicated, max recommend.MAX_BECAUSE = 10. [] when the item has no linked source.
                             # (The "reason" text still names the top 2, unchanged.)
```
`tmdb.normalize(raw, media_type)` gains five keys. All of them are `None` when absent:
```python
"backdrop_url":     "https://image.tmdb.org/t/p/w1280{backdrop_path}" | None
"poster_large_url": "https://image.tmdb.org/t/p/w780{poster_path}"    | None   # poster_url (w342) unchanged
"runtime":          int minutes (movie: raw["runtime"]; tv: None) | None       # 0 -> None
"seasons":          int (tv: raw["number_of_seasons"]; movie: None) | None     # 0 -> None
"certification":    str | None   # first non-empty for country in tmdb.CERT_COUNTRIES = ("GB", "US") (Q3 default):
                                 # movie: raw["release_dates"]["results"][iso_3166_1==C]["release_dates"][*]["certification"]
                                 # tv:    raw["content_ratings"]["results"][iso_3166_1==C]["rating"]
```
- `TmdbClient.details()` appends `release_dates` (movie) or `content_ratings` (tv) to `append_to_response`, alongside `keywords,credits,recommendations`.
- `sample._details()` returns all five keys as `None`, so sample mode exercises every fallback.
- Pages and `browse.py` must use `.get()` for these five keys and for `because`, because old cache entries and test fakes (`fake_result()`, test_ui `result()`) don't have them.

### Python interface
```python
# tmdb.py (backend-dev)
CERT_COUNTRIES = ("GB", "US")
class TmdbClient:
    def details(self, media_type, tmdb_id, refresh=False): ...  # refresh=True skips the cache READ, still writes it

# recommend.py (backend-dev)
MAX_BECAUSE = 10                                              # items gain "because" (above); nothing else changes

# browse.py (NEW, backend-dev) - pure: no I/O, no db, no web/sources import; never mutates its inputs
KINDS = ("all", "movie", "tv")
HERO_MAX, ROW_MAX, ROW_MIN, TOP_N = 5, 20, 4, 10
GEM_MIN_MATCH = 60
TRENDING_GENRES = 3              # how many top profile genres to try for the trending row
LIBRARY_ROW_MAX = 20
ROW_ORDER = ("recommended", "because", "top10", "trending", "gems", "library_new")
HERO_FIELDS = ("backdrop_url", "poster_large_url", "runtime", "seasons", "certification")

def kind_items(items, kind) -> list: ...   # kind "all" -> all; "movie"/"tv" -> that media_type; order kept; bad kind -> "all"
def hero_picks(items, kind) -> list: ...   # kind_items(items, kind)[:HERO_MAX]
def view(result, kind, owned=frozenset()) -> dict: ...     # -> BrowseView (below)

# sources.py (backend-dev)
def _fill_hero_details(items, client) -> int: ...
    # Live run only, after recommend(). For each kind in browse.KINDS and each item in browse.hero_picks(items, kind)
    # (deduplicated, so at most 15) whose dict has NO "backdrop_url" key: fresh = client.details(type, id, refresh=True),
    # then item.update({k: fresh.get(k) for k in browse.HERO_FIELDS}). Exceptions are swallowed per item.
    # Returns how many it refreshed. Never called in sample mode.

# web.py (backend-dev)
def owned_keys(result) -> set: ...
    # {(media_type, tmdb_id)} from (result or {}).get("library") or [] (entries with a tmdb_id),
    # | db.added_items() keys unless _is_sample()
def browse_view(result, kind) -> dict: ...  # browse.view(result, kind, owned_keys(result)). Cheap; called per render

# pages.py (frontend-dev)
NAV_SECTIONS = (("home", "Home", "/"), ("movies", "Movies", "/movies"), ("tv", "TV", "/tv"),
                ("library", "Library", "/library"), ("ai", "AI picks", "/ai"), ("settings", "Settings", "/settings"))
BROWSE_PAGES = {"all": ("home", "/"), "movie": ("movies", "/movies"), "tv": ("tv", "/tv")}  # kind -> (section, return_to)
def render_browse(kind="all", msg="", undo=None) -> str: ...  # bad kind -> "all"
def render_home(msg="", undo=None) -> str: ...                # = render_browse("all", msg, undo); replaces render_home(refresh=False)
def _shell(body, section, subtitle="", show_refresh=False, return_to="/", status_html="", auto_refresh=False,
           cinematic=False) -> str: ...
```
In phase 4, `render_recommended`, `SUBTABS`, `_subtabs_html` and `_in_tab` are deleted from pages.py. `_card` and `SHOW` stay, because the AI page still uses the grid. pages.py keeps reading `web.*` names only inside functions.

### BrowseView shape (`browse.view` / `web.browse_view`)
```python
{"kind": "all"|"movie"|"tv",
 "total": int,                   # len(kind_items(result["items"], kind))
 "hero": [Item],                 # hero_picks; 0..5
 "rows": [Row]}                  # in ROW_ORDER, rows that don't qualify are omitted
Row  = {"id": str, "title": str, "subtitle": str | None, "numbered": bool,
        "source": "recs" | "library", "items": [Item | LibraryItem]}
Item = a shallow COPY of the recommendation item + "in_library": bool (key in owned)
LibraryItem = a copy of the result["library"] entry (shape in specs/library-and-ratings.md) + "in_library": True
```
Row rules (`ki = kind_items(result["items"], kind)`, already best-first):

| id | title (escape on render) | subtitle | items | shown when |
|---|---|---|---|---|
| `recommended` | `Recommended for You` | `Based on your watch history` | `(ki[HERO_MAX:] or ki)[:ROW_MAX]` | ≥1 item |
| `because` | `Because you watched {seed}` | None | ki items whose `because` contains `seed`, max ROW_MAX. **seed** = the title found in the most `because` lists across ki. Ties go to the title that appears in the highest-ranked item | seed count ≥ ROW_MIN |
| `top10` | `Top 10 picks for you` | None | `ki[:TOP_N]`, `numbered=True` | ≥ ROW_MIN |
| `trending` | `Trending in {genre}` | None | Try the top TRENDING_GENRES genres of `result["profile"]["genre"]` (weight desc, then name asc). Use the first genre with ≥ ROW_MIN ki items that have the genre in `genres` and `trending` or `new`. Items are ordered trending first, then score; max ROW_MAX | a genre qualifies |
| `gems` | `Hidden Gems` | `High match, less well known` | ki items not in `ki[:TOP_N]`, with `match >= GEM_MIN_MATCH` and `vote_count <= statistics.median_low(vote_count of ki items of the same media_type)`; score order; max ROW_MAX | ≥ ROW_MIN |
| `library_new` | `New in your library` | None | `result["library"]` filtered to the kind's media_type, by `added_at` desc (None last), max LIBRARY_ROW_MAX, `source="library"` | library is not None and ≥1 item |

- `result` may lack `library`, `profile`, `because` or `genres`. Treat a missing value as empty.
- `result=None` isn't passed; pages handle that state first.

### HTTP
| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/` | query `msg`, `undo_type`, `undo_id` | 200 `render_home(msg=msg, undo=undo or None)`. undo comes from `_item_from(query, "undo_type", "undo_id")`; invalid means None |
| GET | `/movies` | same | 200 `render_browse("movie", msg=..., undo=...)` |
| GET | `/tv` | same | 200 `render_browse("tv", msg=..., undo=...)` |
| GET | `/recommended` | query `type`, `msg`, `undo_type`, `undo_id` | **303** via `self._redirect(target, msg or None, extra={"undo_type", "undo_id"} only if the undo is valid)`. target per Decision 2. No HTML |

- `/movies?x` and `/tv?x` with unknown params are ignored.
- All POST routes and their JSON/no-JS contracts are **unchanged**. Browse pages post with `return_to` set to `/`, `/movies` or `/tv`.
- `render_add_dialog` picks its nav section from `return_to`: a prefix of `/ai` gives `ai`, `/movies` gives `movies`, `/tv` gives `tv`, `/library` gives `library`, and anything else gives `home`.

### Markup contract (pages.py / app.js / app.css share these)
Every untrusted string goes through `html.escape`, and every URL through `_web_url`. That covers titles, the seed title, genre names, certification, reason, overview and image URLs. Inline `style` is only ever `--h:{int}`.

**Shell** (every page):
```html
<body[ class="cinematic"]>                       <!-- cinematic=True only on browse pages with content -->
<a class="skip-link" href="#main">Skip to content</a>
<header class="topbar">
  <a class="brand" href="/"><span class="brand-mark" aria-hidden="true">W</span><h1 class="brand-name">What&#39;s Next</h1></a>
  <nav class="topnav" aria-label="Main">{5 .nav-item: home, movies, tv, library, ai}</nav>
  <div class="topbar-actions">{settings .nav-item}</div>
</header>
<main class="app-main" id="main" tabindex="-1">
  non-cinematic: <div class="top">…unchanged h2 / .sub / .top-actions…</div>{body}
  cinematic:     {body}<div class="top browse-top"><h2 class="visually-hidden">{heading}</h2>
                 <p class="sub">{subtitle}{status_html}</p><div class="top-actions">{refresh}</div></div>
</main>
<nav class="bottom-nav" aria-label="Main">{same 5 .nav-item}</nav>
<div class="toasts" id="toasts" role="status" aria-live="polite"></div>
```
- `.nav-item` keeps its icon + label markup, `active` and `aria-current="page"`. `_NAV_ICONS` gains `movies` and `tv` (fixed inline SVG) and drops `recommended`.
- The active section's link is marked in both navs, except settings, which appears once (top bar).
- The `.app-shell`/`.app-sidebar` markup is removed.
- Headings: `home` is "Home", `movies` "Movies", `tv` "TV", `ai` "AI picks".

**Browse body** (`render_browse`, result ready, `total > 0`), in this order:
1. `<div class="browse-messages">{_message_notes(msg, undo, return_to)}</div>`, omitted when empty.
2. **Hero:**
   ```html
   <section class="hero" data-hero aria-label="Top picks for you">
     <div class="hero-slides" data-hero-slides>
       <article class="hero-slide[ on]" data-slide data-card="{type}-{id}" aria-label="{n} of {N}: {title}"[ hidden for n>1]>
         <div class="hero-art" style="--h:{hue}" aria-hidden="true">
           [<img class="hero-backdrop" src="{backdrop_url}" alt="" loading="lazy">]
           [<img class="hero-poster" src="{poster_large_url or poster_url}" alt="" loading="lazy">]
           [<span class="hero-glyph">{first letter of title}</span>  only when neither image exists]
         </div>
         <div class="card-poster" hidden>{_poster_html(item)}</div>      <!-- for the detail modal only -->
         <div class="hero-copy">
           <p class="kicker">Top pick for you</p>
           <h3 class="title hero-title">{title}</h3>
           <p class="hero-meta card-meta">{_meta_html: .match "96% match", year, .badge.cert, runtime "2h 46m"/"46m" or "3 seasons"/"1 season"} <span class="hero-genres">{genres[:3] joined " · "}</span></p>
           [<p class="reason">{reason}</p>]
           <div class="hero-actions">
             {can add: <a class="btn-add" href="/add-dialog?…" data-add-dialog>Add to library</a> | in_library: <span class="lib-tag">In library</span>}
             <details class="card-details hero-details"><summary>More info<span class="visually-hidden">: {title}</span></summary>
               <div class="detail-body">{meta, reason, chips, overview, ext-link - as _card}</div>{card-actions as _card}</details>
           </div>
         </div>
       </article>…
     </div>
   </section>
   ```
   - The hero is omitted when `hero` is empty.
   - "can add" uses exactly `_card`'s rule: not sample, and Radarr (movie) or Sonarr (tv) configured. Factor it into `_can_add(item, can_act)`.
3. **Rows:**
   ```html
   <div class="rows">
     <section class="row" data-row="{id}" aria-labelledby="row-{id}">
       <div class="row-head"><h3 id="row-{id}">{title}</h3>[<p class="row-sub">{subtitle}</p>]</div>
       <div class="track-wrap"><div class="track[ track-numbered]" data-track>{cards}</div></div>
     </section>…
   </div>
   ```
   - **Recommendation card** (`_row_card(item, return_to, can_act, rank=None)`):
     ```html
     <article class="card row-card" data-card="{type}-{id}">
       <div class="card-poster" data-open-detail>[<span class="rank" aria-hidden="true">{rank}</span>]{_poster_html(item)}
         <div class="poster-top"><span class="match">{match}% match</span>[<span class="lib-tag">In library</span>]</div></div>
       <div class="card-info"><h4 class="title">{Title (Year)}</h4>
         <details class="card-details"><summary>Details<span class="visually-hidden">: {title}</span></summary>
           <div class="detail-body"><p class="card-meta">…</p>[reason][chips]<p class="overview">…</p>[ext-link]</div>
           [<div class="card-actions">{Add link if can add}{Not interested form data-enhance="dismiss"}</div>]
         </details></div>
     </article>
     ```
     The actions sit inside `<details>`, so without JS there's one tab stop per card. app.js's existing `openDetail` still finds `.detail-body` and `.card-actions`.
   - **Library card** (`_library_row_card(item)`): `<article class="card row-card lib-card">` with poster, `<span class="lib-tag">In library</span>`, and `<h4 class="title">`, which links to the TMDB `url` if there is one (the card's single tab stop). It has no `data-card` and no actions.
4. Footer: `<div class="browse-foot">{result notes}{_stale_error_note}{taste summary as on the old Recommended page}</div>`, then `_shell(cinematic=True)` appends `.top.browse-top`.
   - The subtitle reads `Based on {watched_count} watched titles - updated {age}`.
   - `show_refresh=True`. `status_html=UPDATING_HTML` and `auto_refresh` apply while building, as today.

**Browse states** (all `cinematic=False`, same as the old Recommended/Home):
- `get_result_nowait()` raises: `_error_screen`.
- No result + error: `_error_screen`.
- No result: `_waiting_screen("recs")` with `auto_refresh`.
- `total == 0`: `<div class="empty"><h3>Nothing to recommend here yet</h3><p>…</p>{_refresh_form}</div>`.

**app.js** (frontend-dev). Keep the createElement/textContent rule; the only `innerHTML` stays the add-dialog fragment.
- **Classes.** After the feature check, add `has-dialog` to `<html>` when `hasDialog`. CSS hides `.row-card .card-details` only under `html.has-dialog`.
- **Poster button.** For each `.row-card[data-card] [data-open-detail]`, set `tabindex="0"`, `role="button"` and `aria-label="More info: {title}"`. Enter/Space calls `openDetail(card, poster)`. `openDetail` un-hides its cloned `.card-poster` (the hero's is `hidden`).
- **Hero** (`[data-hero]`):
  - Remove `hidden` from the slides.
  - Build `<div class="hero-dots" role="group" aria-label="Choose a featured title">` containing one `<button type="button" class="hero-dot" aria-label="Show {title}">` per slide (`aria-current="true"` on the active one). Add a `<button type="button" class="hero-pause" aria-pressed>` labelled "Pause slideshow"/"Play slideshow".
  - **Desktop** (`matchMedia("(min-width: 821px)")`):
    - Slides are stacked and the active one gets `.on`. Inactive slides get `aria-hidden="true"` and `inert`.
    - Auto-advance every 6000 ms only when `(hover: hover) and (pointer: fine)` matches and reduced motion is off. The pause button is only shown in that case.
    - Pause while hovering (`mouseenter`), while focus is inside (`focusin`), while `document.hidden`, or after the user presses Pause (that one is sticky until Play).
    - `.hero.is-paused` freezes the dot's progress fill; CSS animates `.hero-dot.on::after` over `--hero-ms: 6000ms`.
    - A dot click shows that slide and restarts the timer.
  - **Phone** (≤820px):
    - CSS makes `.hero-slides` a scroll-snap scroller. There's no auto-advance, no inert, and swiping is native.
    - Dots follow the scroll position (index = round(scrollLeft / clientWidth)). A dot click scrolls to its slide.
  - Re-run the mode setup on a `matchMedia` change.
  - `syncHero()` rebuilds the dots from the remaining slides after a removal or reinsert, and hides the hero when none are left.
- **Rows.** For each `[data-track]`, when `(hover: hover) and (pointer: fine)`, insert `<button type="button" class="track-arrow prev|next" tabindex="-1" aria-hidden="true">`. A click scrolls by 85% of `clientWidth` (smoothly unless reduced motion). Hide each arrow at its end; update on scroll.
- **Hover preview** (only under `(hover: hover) and (pointer: fine)`):
  - There's one shared `<div class="preview" aria-hidden="true">` in `<body>`. After 400 ms over a `.row-card[data-card]`, fill it with:
    - a clone of the card's poster image (`.preview-poster`);
    - `.preview-actions` with `.preview-btn`s: the cloned `a[data-add-dialog]` (aria-label "Add to library", `.primary`), the cloned dismiss form (button aria-label "Not interested"), and a `button` "More info" that calls `openDetail`. Every control is `tabindex="-1"`, because keyboard users get the same actions in the modal;
    - clones of `.card-meta`, `.chips` and `.reason`.
  - Position it over the card, clamped to the viewport, and add `.is-open`.
  - It stays open while the pointer is over the card or the preview, closing after a 150 ms grace period. It also closes on Esc, on scroll, on any action click, on `closeModal()`, and when the card is removed.
  - The existing delegated click/submit handlers work on the clones unchanged.
- **Removing cards.**
  - Replace `findCard` with `findCards(type, id)`, which returns all matches (hero slide plus every row).
  - `dismiss` and `add` remove all of them. Undo reinserts each at its own place.
  - In `syncEmpty`, if the parent has `data-track`, toggle `hidden` on `closest(".row")` instead of inserting the empty message, and renumber the `.rank` text in `.track-numbered`. If the parent has `data-hero-slides`, call `syncHero()`.
  - The grid behaviour on Library/AI is unchanged.
- **Image errors.** On `img.hero-backdrop` / `img.hero-poster` error, remove the img (the gradient stays). The existing `img.poster` fallback stays as it is.
- **Top bar.** On `body.cinematic`, toggle `.topbar.is-scrolled` when `scrollY > 10` (passive listener).

**app.css** (ux-designer): see the brief. The hero copy must keep WCAG AA over any backdrop in both themes, so the scrims are built from `--bg`, not from hard-coded black. Hover zoom on `.row-card` happens only under `(hover: hover) and (pointer: fine)` and goes away with reduced motion.

## File ownership
| Agent | Files | Task |
|---|---|---|
| backend-dev | `tmdb.py`, `recommend.py`, `sample.py`, `sources.py`, `browse.py` (new), `web.py`, `tests/test_browse.py` (new), `tests/test_plex_tmdb.py`, `tests/test_recommend.py`, `tests/test_sources.py`, `tests/test_web_async.py`, `tests/test_web.py` | Data fields, row logic, hero detail refresh, `browse_view`, then routes |
| frontend-dev | `pages.py`, `static/app.js`, `tests/test_ui.py` | Shell/top bar, `render_browse`, hero, rows, cards, JS behaviour |
| ux-designer | `static/app.css` | Style the classes listed in the brief |
| orchestrator | `specs/cinematic-home-mockup.html` (copy), `README.md`, `.claude/skills/api-conventions` (routes table), `.claude/skills/design-system` (components, nav) | After review |

`cli.py`, `db.py`, `plex.py`, `tautulli.py`, `settings_page.py`, `radarr.py`, `sonarr.py` and `ai.py` don't change.

**Phases.** `web.py` imports names from `pages.py` at load time, so a name can't be removed from one side before the other side stops using it.
1. **Phase 1: backend-dev ‖ ux-designer.**
   - Backend does the data layer only: `tmdb`, `sample`, `recommend` `because`, `browse.py`, `sources._fill_hero_details`, `web.owned_keys`/`browse_view`, plus tests.
   - No handler or import changes, so the whole suite stays green.
   - ux-designer works from this spec and the mockup.
2. **Phase 2: frontend-dev.**
   - Builds `_shell` (top bar), `NAV_SECTIONS`, `render_browse`, `render_home(msg, undo)`, cards and app.js against the real `web.browse_view`.
   - **Keeps `render_recommended` and `SUBTABS` defined**, because web.py still imports them.
   - Nav links to `/movies` and `/tv` 404 until phase 3. Tests in `test_web`/`test_web_async` that assert old nav or Home markup are **expected red**; frontend-dev lists them.
3. **Phase 3: backend-dev.**
   - Adds the GET `/`, `/movies`, `/tv` and `/recommended` handling and drops `SUBTABS`/`render_recommended` from web.py's import (adding `render_browse`).
   - Updates `tests/test_web.py` and `tests/test_web_async.py`. The full suite goes green.
4. **Phase 4: frontend-dev** (resume the phase-2 agent, about 5 minutes). Deletes `render_recommended`, `SUBTABS`, `_subtabs_html`, `_in_tab` and their test_ui tests. The full suite stays green.

Then the reviewer.

## Acceptance criteria
- [ ] `normalize` fills the five fields from raw fixtures: GB certification wins over US, it falls back to US, missing is None, and runtime/seasons of 0 become None. `details(refresh=True)` makes a request even when cached, and still writes the cache. `append_to_response` has `release_dates` for movies and `content_ratings` for tv. Proof: `python3 -m unittest tests.test_plex_tmdb`
- [ ] Items carry `because`, ordered by source weight and capped at 10; it's `[]` for genre/trending-only items. Output is otherwise unchanged. Proof: `python3 -m unittest tests.test_recommend`
- [ ] `browse.view`:
  - row order;
  - the recommended slice, including the `ki` ≤ 5 case;
  - top10 numbered;
  - because-seed choice and its tie-break, omitted below ROW_MIN;
  - trending genre fallback across the top 3 genres, and omission;
  - gems per-type median, the match threshold and the top-10 exclusion;
  - library_new sorted and filtered by kind, omitted when `library` is None;
  - the `in_library` flag;
  - input never mutated;
  - a minimal fake result (no `library`/`because`/genres) works;
  - kind filtering (no tv in `movie`).

  Proof: `python3 -m unittest tests.test_browse`
- [ ] Live `run()` refreshes only hero picks that lack the `backdrop_url` key: at most 15, deduped across kinds, with an exception on one item not aborting the run. Sample `run()` never calls it, and sample items have the five keys set to None. No test reaches the network (`mock.patch.object(tmdb.TmdbClient, "details")`). Proof: `python3 -m unittest tests.test_sources`
- [ ] `web.owned_keys` = library keys ∪ added keys in live mode, and library keys only in sample mode. Proof: `python3 -m unittest tests.test_browse`, using a temp DB as in the testing recipe.
- [ ] Phase 3 routes, in `tests.test_web_async.TestGetPassesPageParams` (mocked renderers) plus `NoRedirect`:
  - `/`, `/movies` and `/tv` pass `msg` + undo through;
  - an invalid undo becomes None;
  - `/recommended?type=movie&msg=Hi&undo_type=movie&undo_id=5` gives a 303 to `/movies?msg=Hi&undo_type=movie&undo_id=5`;
  - `type=tv` goes to `/tv`;
  - `type=new`, `all`, `<script>` or missing go to `/`;
  - `undo_id=x` is dropped;
  - a CR/LF in `msg` stays URL-encoded in `Location`.

  Proof: `python3 -m unittest tests.test_web_async`
- [ ] Sample-mode end to end (verify these against the real sample output, and report it if the fixture differs):
  - `/` shows a `.hero` with 5 slides, "Recommended for You", "Top 10 picks for you", "Because you watched Arrival" and "New in your library";
  - `/movies` has no recommended TV title (e.g. Mindhunter);
  - `/tv` has no "Because you watched Arrival";
  - nav has Movies/TV/AI picks and no "Recommended";
  - `/recommended` lands on Home;
  - no Add buttons in sample mode;
  - Library/AI/Settings still render with the top bar.

  Proof: `python3 -m unittest tests.test_web`
- [ ] Markup checks:
  - hostile title, reason, genre, certification and seed are escaped in the hero, rows and preview source markup;
  - a `javascript:` backdrop or poster is dropped;
  - only slide 1 lacks `hidden`;
  - fallbacks work: backdrop missing gives a poster img, both missing give `.hero-glyph`;
  - meta text reads "2h 46m", "46m", "3 seasons" and "1 season";
  - "In library" replaces Add in the hero;
  - the Add link appears only when configured and not sample;
  - numbered ranks are 1..n;
  - library cards have no `data-card`;
  - row-card actions sit inside `<details>`;
  - `aria-current` is counted twice for library and once for settings;
  - `body class="cinematic"` appears only on a ready browse page;
  - building, error and empty states for each kind;
  - the undo note's `return_to` is `/movies`;
  - `render_add_dialog` maps the section from `return_to`;
  - `web.start_ai_generation` is never called by `render_browse`.

  Proof: `python3 -m unittest tests.test_ui`
- [ ] Static: no external URLs; still one `innerHTML`; CSS contains `prefers-reduced-motion`, `max-width: 820px`, `.hero` and `.track`. Proof: `python3 -m unittest tests.test_ui.TestStaticAssets`
- [ ] app.js (no browser on this machine): a read-through by the reviewer, plus Hamish clicking through `SAMPLE=1 PORT=8099 python3 web.py`. He checks:
  - the hero rotates and pauses on hover, focus and the Pause button, and doesn't rotate with reduced motion;
  - dots work;
  - row arrows appear on hover;
  - the preview opens with More info;
  - a poster opens the modal with Enter;
  - on a phone, the hero swipes with no auto-rotate and rows show about 3 posters with a peek;
  - dismiss shows the sample toast;
  - light and dark themes both work.
- [ ] Works without JS: the hero shows slide 1 and "More info" expands; rows scroll; Add/Not interested POST + 303 back to `/movies` with the note above the hero.
- [ ] 375px has no horizontal page scroll; AA contrast in both themes (hero copy over the brightest backdrop as well); `:focus-visible` everywhere, including poster buttons and dots.
- [ ] Full suite green after phases 3 and 4: `python3 -m unittest discover -s tests`

## Live-request budget
**Zero** for every implementer and the reviewer. Use fixtures and mocks only (`mock.patch.object(tmdb.TmdbClient, "details")`, raw TMDB JSON dicts for `normalize`, `sample.SampleTmdb`).

Runtime behaviour this adds in production: up to 15 TMDB detail requests per *live build*, only for hero picks missing the new fields, usually 0 (Q1). Nothing is fetched per page view.

## Open questions
1. **Hero backdrop refresh.** Is it OK for a live build to re-fetch details for up to 15 hero titles that lack backdrops? Without it, most heroes show the poster/gradient fallback until their cache entries expire (up to 30 days). Default: yes. If no, backend-dev skips `_fill_hero_details`; nothing else changes. **Blocks only that function.**
2. **Rate in the hover preview.** The mockup shows ★, but the library-and-ratings spec put rating unwatched titles out of scope. Rating a recommendation today would store a rating that only feeds `disliked` (≤2★ down-ranks linked titles), and the title would stay recommended. Default: **no Rate button**; the preview has Add / Not interested / More info. If Hamish wants it, that's a follow-up spec: what a rating on an unwatched title means, and whether it hides the title. Not blocking.
3. **Smaller defaults to confirm.** None of these block.
   - (a) The certification country is GB, falling back to US (the mockup shows BBFC 12A/15).
   - (b) The phone bottom nav has 5 items (Home, Movies, TV, Library, AI picks) with Settings in the top bar, not the mockup's 4.
   - (c) "Trending in X" also counts titles flagged `new`, because trending-only rows would usually be too short to show.
   - (d) The Home stat tiles are dropped.

---

## Task briefs

### backend-dev: phase 1 (parallel with ux-designer), then phase 3
Spec: `specs/cinematic-home.md`. The Contract is fixed; report back if any of it can't work. Live-request budget: **none**.

Own: `tmdb.py`, `recommend.py`, `sample.py`, `sources.py`, `browse.py` (new), `web.py`, and tests `test_browse.py` (new), `test_plex_tmdb.py`, `test_recommend.py`, `test_sources.py`, `test_web_async.py`, `test_web.py`.

**Phase 1** (no handler or import changes in web.py; the suite must stay fully green):
1. Five new `normalize` fields, `CERT_COUNTRIES`, `details(refresh=False)` and the extra `append_to_response`. Sample `_details` gets the five keys as None.
2. `because` + `MAX_BECAUSE` in `recommend()`.
3. `browse.py` exactly as in "BrowseView shape": constants, `kind_items`, `hero_picks`, `view`. Pure, and it never mutates its input.
4. `sources._fill_hero_details`, called from the live branch of `run()` only (subject to Q1).
5. `web.owned_keys` and `web.browse_view`.
6. Tests per the acceptance criteria. Confirm against the real sample output that `browse.view` on the sample result gives Home/Movies a "Because you watched Arrival" row, and report the actual row titles for each kind.

**Phase 3** (after frontend-dev's phase 2):
1. GET `/`, `/movies` and `/tv` with `msg` and undo.
2. `/recommended` becomes a 303 per Decision 2.
3. web.py's `from pages import` becomes `_card, _shell, render_add_dialog, render_ai_page, render_browse, render_home, render_library`.
4. Update `test_web.py` and `test_web_async.py` (fixing the expected-red tests frontend-dev lists).

Run `python3 -m unittest discover -s tests`. Report files changed, test results and any contract deviations.

### frontend-dev: phase 2, then phase 4
Spec: `specs/cinematic-home.md`, especially "Markup contract". Mockup: `specs/cinematic-home-mockup.html`. Own: `pages.py`, `static/app.js`, `tests/test_ui.py`. Live-request budget: **none**.

**Phase 2:**
1. New `_shell` (top bar, `cinematic` flag, `.top.browse-top`), `NAV_SECTIONS` with `movies`/`tv` icons, and section mapping in `render_add_dialog`.
2. `render_browse` / `render_home(msg, undo)` using `web.get_result_nowait`, `web.build_status` (via `_status`) and `web.browse_view`. Add `_hero_html`, `_row_html`, `_row_card`, `_library_row_card`, `_meta_html`, `_can_add`, plus the footer and all states.
3. **Keep `render_recommended` and `SUBTABS` working**, because web.py still imports them.
4. app.js: `has-dialog`, poster buttons, hero controller with Pause, row arrows, hover preview, `findCards`/multi-removal/`syncHero`/row hiding, hero image errors, `is-scrolled`.
5. test_ui tests per the acceptance criteria.

Use only the classes named in the spec. Don't edit app.css; ask via the orchestrator if a class is missing. Run `python3 -m unittest tests.test_ui`, then the full suite. **List every red test in `test_web`/`test_web_async`** (expected: old nav/Home markup), with the reason. Also list anything in app.js that needs checking by hand.

**Phase 4** (after backend phase 3): delete `render_recommended`, `SUBTABS`, `_subtabs_html`, `_in_tab` and their test_ui tests. The full suite must be green.

### ux-designer: phase 1 (parallel with backend-dev)
Spec: `specs/cinematic-home.md` ("Markup contract"). Mockup: `specs/cinematic-home-mockup.html`, which is the visual source of truth and uses the app's tokens. Own: `static/app.css` only.

Style these classes:
- **Shell:**
  - `.topbar`: solid and sticky on normal pages. On `body.cinematic` it's fixed and transparent with a `--bg` gradient, and `.is-scrolled` makes it solid.
  - `.brand`, `.brand-name`, `.topnav`, `.topbar-actions`, and `.nav-item` inside `.topnav` (horizontal text links; active uses `--accent-text`/`--accent` fill, not colour-only).
  - At ≤820px, `.topnav` is hidden, `.bottom-nav` shows 5 items, and Settings stays in the top bar.
  - Remove the now-unused `.app-shell`/`.app-sidebar`/`.brand-lockup` rules.
  - `.top.browse-top` is a slim footer strip.
- **Hero:**
  - `.hero`, `.hero-slides`, `.hero-slide`, `.hero-slide.on` (desktop: stacked with an opacity fade; ≤820px: horizontal scroll-snap tall card, about 3:4, with all slides visible side by side). `.hero-slide[hidden]` must stay hidden.
  - `.hero-art`: backdrop `object-fit: cover`; `--h` gradient fallback as in the mockup; `.hero-poster` shown instead of `.hero-backdrop` at ≤820px, and at desktop when there's no backdrop (`.hero-art:not(:has(.hero-backdrop)) .hero-poster` placed right, or an equivalent).
  - `.hero-backdrop`, `.hero-poster`, `.hero-glyph`, `.hero-copy`, `.kicker`, `.hero-title`, `.hero-meta`, `.hero-genres`, `.hero .reason`, `.hero-actions`.
  - `.hero-details > summary`, styled as a `.btn-ghost`-like button; its open content must still read well without JS.
  - `.hero-dots`, `.hero-dot`, `.hero-dot.on::after` (progress fill over `var(--hero-ms, 6000ms)`), `.hero.is-paused` (pause the fill), `.hero-pause`.
- **Rows:**
  - `.browse-messages`, `.rows` (may overlap the hero bottom on desktop as in the mockup), `.row`, `.row-head`, `.row-sub`, `.track-wrap`, `.track`: scroll-snap with the scrollbar hidden, and enough block padding that a 1.06 hover zoom isn't clipped.
  - `.track-numbered` + `.rank`.
  - `.track-arrow.prev/.next`: hover-reveal, at the track edges, `(hover: hover)` only.
  - `.row-card`: desktop width as in the mockup; on phones, about 3 visible with a peek. Hover zoom applies only under `(hover: hover) and (pointer: fine)`.
  - `.lib-card`, `.lib-tag`, `.card-meta`, `.badge.cert`.
  - `html.has-dialog .row-card .card-details { display: none }`.
- **Preview:** `.preview`, `.preview.is-open`, `.preview-poster`, `.preview-body`, `.preview-actions`, `.preview-btn`, `.preview-btn.primary`.
- **Footer:** `.browse-foot`.

Rules:
- Tokens only; add a token rather than hard-coding a colour.
- Scrims are built from `--bg`, so light mode gets light scrims with dark text at AA.
- Both themes; `:focus-visible` kept on dots, the Pause button, the poster buttons (`.row-card .card-poster[role=button]`) and summaries.
- `prefers-reduced-motion` disables the fade, the progress fill animation and the zoom.
- No horizontal page scroll at 375px; no external assets.

Run `python3 -m unittest tests.test_ui`. Report the classes styled, the tokens added, and any class you needed that the spec doesn't name.
