"""Web page with your recommendations.

Run:  python3 web.py          then open http://localhost:8080
Settings (environment variables): PORT (default 8080), HOST (default 0.0.0.0 = reachable from other
machines on your network), SAMPLE=1 to force sample data. There is no login, so keep it on your home network."""
import json
import os
import threading
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse

import arr_library
import browse
import config
import db
import plex
import profile
import recommend
import sample
import settings_page
import sources
import themes
import tmdb

# Page rendering lives in pages.py (frontend-owned). Imported back for the handler, and for
# tests that call or patch web.render_* / web._card / web._shell.
from pages import (  # noqa: E402
    _card, _shell, render_add_dialog, render_ai_page, render_appearance, render_browse, render_home,
    render_library, render_list, render_list_dialog, render_lists, render_search, render_search_results,
    render_title,
)

CACHE_SECONDS = 3600       # reuse the last result for an hour (finding suggestions takes a while)
REFRESH_MIN_SECONDS = 600  # "Refresh" is ignored if the data is newer than this, to be gentle on Plex/TMDB
compute_lock = threading.Lock()  # one recompute at a time - held for a whole build, so requests never wait on it
_state = {"time": 0.0, "result": None}
# separate from _state - AI only ever runs when asked to. "building"/"error" are guarded by _status_lock.
_ai_state = {"time": 0.0, "result": None, "building": False, "error": None}

# Styles live in static/app.css and behaviour in static/app.js (served by Handler; linked from _shell).


ERROR_RETRY_SECONDS = 30   # after a failed build, page loads wait this long before retrying on their own
FORGET_STASH = 50          # how many removed suggestions Undo can put back without a rebuild
# Small bookkeeping, separate from compute_lock: it's only ever held for a few lines (never across a
# build or a network call), so /api/status, page renders and clicks answer instantly mid-build.
_status_lock = threading.Lock()
_build = {"pending": False, "running": False, "started": None, "error": None, "failed_at": 0.0, "generation": 0,
          "ratings_changed": False}  # True once you've rated something since the last build started
_forgotten = []  # (cache name, index, item) removed by forget(), newest last - see restore()
# Every forget()/restore() since the current build or AI generation started, oldest first:
# ("hide", key) or ("restore", cache name, index, item). sources.run() reads the dismissed list
# when it starts, a minute or two before its result is installed, so _install() replays these
# onto the new result - otherwise a click made mid-build would be undone by the build finishing.
# Cleared when a job starts; jobs all hold compute_lock, so there's only ever one in flight.
_actions = []
ACTIONS_MAX = 1000  # only grows between jobs; a cap so it can't grow forever if none ever runs


def _key(item):
    return item["media_type"], item["tmdb_id"]


def _needs_build(refresh):
    age = time.time() - _state["time"]
    stale = _state["result"] is None or age > CACHE_SECONDS
    return stale or (refresh and (age > REFRESH_MIN_SECONDS or _build["ratings_changed"]))


def _start_job():
    """Call with compute_lock held, as a build/generation starts."""
    with _status_lock:
        _actions.clear()


def _log_action(action):
    """Call with _status_lock held."""
    _actions.append(action)
    del _actions[:-ACTIONS_MAX]


def _install(name, result):
    """Filters a finished build/generation against everything acted on since it started, ready to
    be installed as the new result for cache `name` ("main" or "ai"). Call with _status_lock (and
    compute_lock) held, and install in the same _status_lock block, so a click can't land between
    the filtering and the install."""
    items = list(result["items"])
    for action in _actions:
        if action[0] == "hide":
            items = [i for i in items if _key(i) != action[1]]
        elif action[1] == name and not any(_key(i) == _key(action[3]) for i in items):
            items.insert(min(action[2], len(items)), action[3])
    result["items"] = items
    return result


def _hide_dismissed(result):
    """Drops anything on the "not interested" list. Belt and braces with _install(): catches a
    dismiss whose forget() happened before this job started but after sources.run() read the list.
    Not the "added" log - that's display-only (see db.record_added), Radarr/Sonarr's own library
    is what keeps added titles out."""
    if result.get("sample"):
        return result
    hidden = db.dismissed()
    result["items"] = [i for i in result["items"] if _key(i) not in hidden]
    return result


def _build_locked(refresh):
    """One build, if one is still needed - the caller must hold compute_lock. Re-checking here
    (not just when queueing) means a build that waited behind another one doesn't repeat it.
    Returns the new result, or None if the cache was already fresh enough. A settings change
    (invalidate_cache) mid-build throws the result - or the failure - away and builds again with
    the new settings. Other errors are recorded for build_status(), then re-raised."""
    if not _needs_build(refresh):
        return None
    attempts = 3
    carried_ratings_changed = False  # cleared by an earlier attempt whose result was discarded
    for attempt in range(attempts):
        _start_job()
        with _status_lock:
            _build.update(running=True, started=time.time())
            generation = _build["generation"]
            had_ratings_changed, _build["ratings_changed"] = _build["ratings_changed"], False
            carried_ratings_changed = carried_ratings_changed or had_ratings_changed
        try:
            try:
                result = _hide_dismissed(sources.run(_is_sample()))
            except Exception as e:
                with _status_lock:
                    # the new ratings never made it into a result - keep offering the refresh
                    _build["ratings_changed"] = carried_ratings_changed or _build["ratings_changed"]
                    if generation != _build["generation"] and attempt < attempts - 1:
                        continue  # failed with settings that have since changed - try the new ones
                    _build.update(error=str(e) or type(e).__name__, failed_at=time.time())
                raise
            with _status_lock:
                if generation == _build["generation"]:
                    _state["result"], _state["time"] = _install("main", result), time.time()
                    _build["error"] = None
                    return result
        finally:
            with _status_lock:
                _build["running"] = False
    return result  # settings kept changing mid-build: hand back the last one without caching it


def get_result(refresh=False):
    """Blocking version for the CLI and tests: builds right here if needed. Returns
    (result, seconds_old). Pages use get_result_nowait() instead, so they never block."""
    with compute_lock:
        while True:
            fresh = _build_locked(refresh)
            if fresh is not None:
                return fresh, 0.0
            with _status_lock:
                result, updated = _state["result"], _state["time"]
            if result is not None:  # else a settings change cleared it just now - build again
                return result, time.time() - updated


def _start_build(refresh=False):
    """Queues a background build unless one is already queued or running. Returns True if it
    queued one. The blocking get_result() can still run alongside, but everything goes through
    compute_lock and _build_locked()'s re-check, so the work is never done twice."""
    with _status_lock:
        if _build["pending"] or _build["running"]:
            return False
        _build["pending"] = True
    try:
        threading.Thread(target=_background_build, args=(refresh,), daemon=True, name="build").start()
    except BaseException:  # e.g. "can't start new thread" - don't leave it looking like it's building forever
        with _status_lock:
            _build["pending"] = False
        raise
    return True


def _background_build(refresh):
    try:
        with compute_lock:
            _build_locked(refresh)
    except Exception as e:  # already recorded for build_status()
        print(f"Finding recommendations failed: {e}")
    finally:
        with _status_lock:
            _build["pending"] = False


def get_result_nowait():
    """For the pages: never waits for a build. Returns (result, seconds_old), or (None, None)
    while the very first build is still running. A missing or old result queues a background
    build, and the old result (if any) is returned meanwhile, so an hour-old list still shows
    instantly. After a failed build it waits ERROR_RETRY_SECONDS before retrying by itself, so
    the error actually gets shown instead of every reload quietly starting another attempt."""
    with _status_lock:
        result, updated = _state["result"], _state["time"]
        backing_off = _build["error"] is not None and time.time() - _build["failed_at"] < ERROR_RETRY_SECONDS
    age = time.time() - updated
    if (result is None or age > CACHE_SECONDS) and not backing_off:
        _start_build()
    return (result, age) if result is not None else (None, None)


def build_status():
    """Cheap snapshot for GET /api/status and the "Finding your recommendations..." screen. Only
    takes _status_lock, never compute_lock, so it answers instantly mid-build. "error" (the
    top-level one) is the last build's failure until a build succeeds; state is only "error" when
    there's also nothing to show."""
    with _status_lock:
        has_result = _state["result"] is not None
        if _build["pending"] or _build["running"]:
            state = "building"
        else:
            state = "error" if _build["error"] is not None and not has_result else "ready"
        if _ai_state["building"]:
            ai_state = "building"
        elif _ai_state["error"] is not None:
            ai_state = "error"
        else:
            ai_state = "ready" if _ai_state["result"] is not None else "idle"
        return {"state": state, "has_result": has_result, "started": _build["started"],
                "updated": _state["time"] if has_result else None, "error": _build["error"],
                "ai": {"state": ai_state, "error": _ai_state["error"]}}


def start_ai_generation():
    """Queues a background AI generation unless one is already running. Returns True if it
    started one. It holds compute_lock while it runs, like a build, so the AI batch and the main
    list never hit Plex/TMDB at the same time - fine now that nothing a request does waits on it."""
    with _status_lock:
        if _ai_state["building"]:
            return False
        _ai_state["building"], _ai_state["error"] = True, None
    try:
        threading.Thread(target=_ai_generate, daemon=True, name="ai").start()
    except BaseException:
        with _status_lock:
            _ai_state["building"] = False
        raise
    return True


def _ai_generate():
    """Same acted-on filtering as a build (_hide_dismissed + _install), so a title dismissed or
    added while the AI was thinking doesn't pop back up when its batch lands."""
    try:
        with compute_lock:
            _start_job()
            result = _hide_dismissed(sources.generate_ai_recommendations())
            with _status_lock:
                _ai_state["result"], _ai_state["time"] = _install("ai", result), time.time()
    except Exception as e:
        print(f"AI generation failed: {e}")
        with _status_lock:
            _ai_state["error"] = str(e) or type(e).__name__
    finally:
        with _status_lock:
            _ai_state["building"] = False


def mark_ratings_changed():
    """Called after a rating is saved or cleared: the recommendations are now out of date, and
    Refresh may skip its REFRESH_MIN_SECONDS guard."""
    with _status_lock:
        _build["ratings_changed"] = True


def ratings_changed():
    with _status_lock:
        return _build["ratings_changed"]


def plex_thumb(rating_key):
    """The Plex thumb path for a ratingKey in the current snapshot, or None. Server-side only."""
    with _status_lock:
        result = _state["result"]
        return ((result or {}).get("thumbs") or {}).get(rating_key)


# --- Library page data (pure helpers; pages.py does the rendering) ---
LIBRARY_TABS = ("all", "movie", "tv", "watched", "added")
LIST_OPTIONS = {   # tab -> (sorts, shows); the first of each is the default
    "all":     (("added", "title", "year"), ("all", "unwatched", "watched")),
    "movie":   (("added", "title", "year"), ("all", "unwatched", "watched")),
    "tv":      (("added", "title", "year"), ("all", "unwatched", "watched")),
    "watched": (("recent", "title", "rating"), ("all", "rated", "unrated")),
    "added":   (("added", "title", "year"), ("all", "open", "available")),   # Requests
}
LIST_SOURCES = {   # tab -> Source filter values; the first is the default. LIST_OPTIONS is separate.
    "all":     ("all", "plex", "arr", "wanted"),
    "movie":   ("all", "plex", "arr", "wanted"),
    "tv":      ("all", "plex", "arr", "wanted"),
    "watched": ("all",),
    "added":   ("all",),
}
MAX_FORM_BYTES = 16384   # POST bodies are read up to this (a 300-char UTF-8 description alone can reach 3600)
SEASON_NUMBER_MAX, MAX_SEASONS_PER_REQUEST = 9999, 200
MAX_LISTS, MAX_LIST_ITEMS, LIST_NAME_MAX, LIST_DESC_MAX = 50, 1000, 60, 300
LIST_SORTS = ("manual", "added", "title", "year", "rating", "match")
TITLE_PARTIAL = "Some details (cast, trailer, seasons) couldn't load right now - try again in a minute."
TITLE_UNAVAILABLE = "Couldn't load this title right now - TMDB didn't answer. Try again in a minute."
TITLE_NOT_FOUND = "TMDB doesn't know this title."
LOOKUP_FAILED = "Couldn't look that title up right now - try again in a minute."
LIST_PAGE_SIZE = 48  # divisible by the 2/3/4/6-column grids
MAX_QUERY_CHARS = 100

# --- Title search (GET /search; the route and page come in a later phase) ---
SEARCH_KINDS, SEARCH_MIN_CHARS = ("all", "movie", "tv"), 2
TMDB_BUDGET_MAX, TMDB_BUDGET_WINDOW = 60, 60   # uncached TMDB requests per window, process-wide
LOOKUP_RESERVE = 10   # the last uses of the budget are kept for add-dialog and /add lookups; search can't take them
SEARCH_ERROR = "Search isn't available right now - TMDB didn't answer. Try again in a moment."
SEARCH_LIMITED = ("Too many new searches in the last minute - wait a moment and try again. "
                  "Searches you've already made still work.")
_budget_lock = threading.Lock()   # its own lock: never compute_lock, held for a few lines only
_budget_uses = []                 # time.monotonic() of each recent use


def _tmdb_budget(reserve=0):
    """True (and records one use) if fewer than TMDB_BUDGET_MAX uncached TMDB requests were made by search,
    the add dialog and /add in the last TMDB_BUDGET_WINDOW seconds. /search is a GET with no login, so
    this stops any page that makes your browser hit it from draining the TMDB quota."""
    now = time.monotonic()
    with _budget_lock:
        _budget_uses[:] = [t for t in _budget_uses if now - t < TMDB_BUDGET_WINDOW]
        if len(_budget_uses) >= TMDB_BUDGET_MAX - reserve:
            return False
        _budget_uses.append(now)
        return True


def parse_list_query(query):
    """query: parse_qs dict -> {"tab", "q", "sort", "show", "page", "source"}. Every invalid value falls back to its default."""
    def first(name):
        return (query.get(name) or [""])[0]
    tab = first("type")
    tab = tab if tab in LIBRARY_TABS else "all"
    sorts, shows = LIST_OPTIONS[tab]
    sort, show = first("sort"), first("show")
    sources_ = LIST_SOURCES[tab]
    source = first("source")
    return {"tab": tab, "q": first("q").strip()[:MAX_QUERY_CHARS],
            "sort": sort if sort in sorts else sorts[0], "show": show if show in shows else shows[0],
            "source": source if source in sources_ else sources_[0],
            "page": max(1, _parse_id(first("page")) or 1)}


def parse_search_query(query):
    """query: parse_qs dict -> {"q", "kind"}. q is trimmed, whitespace-collapsed and cut to MAX_QUERY_CHARS;
    an invalid kind becomes "all"."""
    def first(name):
        return (query.get(name) or [""])[0]
    kind = first("type")
    return {"q": " ".join(first("q").split())[:MAX_QUERY_CHARS].strip(),
            "kind": kind if kind in SEARCH_KINDS else "all"}


def _sorted_desc_none_last(items, value):
    """Title order first, then a stable descending sort on value(item); items without a value go last."""
    items = sorted(items, key=lambda i: (i.get("title") or "").casefold())
    have = [i for i in items if value(i) is not None]
    missing = [i for i in items if value(i) is None]
    return sorted(have, key=value, reverse=True) + missing


def list_view(items, tab="all", q="", sort="added", show="all", page=1, per_page=LIST_PAGE_SIZE, source="all"):
    """Filter, sort and paginate a list of library/watched items. Pure. source (all/plex/arr/wanted) only
    applies to the all/movie/tv tabs.
    -> {"items": this page, "total", "page" (clamped to 1..pages), "pages" (>= 1)}"""
    found = list(items)
    if tab in ("movie", "tv"):
        found = [i for i in found if i.get("media_type") == tab]
    needle = (q or "").strip().casefold()
    if needle:
        found = [i for i in found if needle in (i.get("title") or "").casefold()]
    if tab in ("all", "movie", "tv"):
        if source == "plex":
            found = [i for i in found if "plex" in (i.get("sources") or ())]
        elif source == "arr":
            found = [i for i in found if {"radarr", "sonarr"} & set(i.get("sources") or ())]
        elif source == "wanted":
            found = [i for i in found if i.get("arr_state") in arr_library.WANTED_STATES]
    if show in ("unwatched", "watched"):
        found = [i for i in found if bool(i.get("watched")) == (show == "watched")]
    elif show in ("open", "available"):
        found = [i for i in found if (i.get("request_state") == "available") == (show == "available")]
    elif show in ("rated", "unrated"):
        found = [i for i in found if (i.get("stars") is not None) == (show == "rated")]
    if sort == "title":
        found.sort(key=lambda i: (i.get("title") or "").casefold())
    elif sort == "year":
        found = _sorted_desc_none_last(found, lambda i: i.get("year"))
    elif sort == "recent":
        found = _sorted_desc_none_last(found, lambda i: i.get("last_viewed"))
    elif sort == "rating":
        found = _sorted_desc_none_last(
            found, lambda i: i["stars"] if i.get("stars") is not None else i.get("plex_stars"))
    else:  # "added"
        found = _sorted_desc_none_last(found, lambda i: i.get("added_at"))
    per_page = max(1, per_page)
    pages = max(1, -(-len(found) // per_page))
    page = min(max(1, page), pages)
    return {"items": found[(page - 1) * per_page:page * per_page], "total": len(found), "page": page, "pages": pages}


def watched_items(result):
    """Copies of the build's raw watch history with "stars" (your own rating; always None in sample
    mode, which ignores stored ratings) and "plex_stars" (Plex/Tautulli's rating as 1-5)."""
    mine = {} if _is_sample() else db.ratings()
    out = []
    for item in (result or {}).get("watched") or []:
        item = dict(item)
        item["stars"] = mine.get((item["media_type"], item["tmdb_id"]))
        item["plex_stars"] = profile.stars_from_ten(item.get("user_rating"))
        out.append(item)
    return out


def library_items(result, tab):
    """The items for one Library tab, or None if there's neither a Plex library (Tautulli without PLEX_TOKEN)
    nor any Radarr/Sonarr item. all/movie/tv merge the Plex snapshot with Radarr/Sonarr (arr_library.merge)."""
    if tab == "added":
        return requests_items(result)
    if tab == "watched":
        return watched_items(result)
    plex_items = (result or {}).get("library")
    arr_items = _arr_items(result)
    if plex_items is None and not arr_items:
        return None
    return arr_library.merge(plex_items, arr_items)  # list_view() narrows movie/tv tabs by media_type


def _arr_items(result):
    return ((result or {}).get("arr") or {}).get("items") or []


def arr_status(result):
    """{"radarr": state, "sonarr": state}, each "off" | "ok" | "error" ("off" when the result lacks it)."""
    arr = (result or {}).get("arr") or {}
    return {name: (arr.get(name) or {}).get("state") or "off" for name in ("radarr", "sonarr")}


def owned_keys(result):
    """{(media_type, tmdb_id)} of titles already in the Plex snapshot or tracked by Radarr/Sonarr, plus (live mode only) the ones
    added through this app."""
    keys = {(e["media_type"], e["tmdb_id"]) for e in (result or {}).get("library") or [] if e.get("tmdb_id")}
    keys |= {(e["media_type"], e["tmdb_id"]) for e in _arr_items(result) if e.get("tmdb_id")}
    if not _is_sample():
        keys |= {(a["media_type"], a["tmdb_id"]) for a in db.added_items()}
    return keys


def browse_view(result, kind):
    """The rows/hero for one browse page (cheap: no I/O beyond the local DB)."""
    view = browse.view(result, kind, owned_keys(result))
    maps = _user_state_maps()
    view["hero"] = with_user_state(view["hero"], maps)
    for row in view["rows"]:
        row["items"] = with_user_state(row["items"], maps)
    return view


def _find_item(media_type, tmdb_id):
    """Checks both the main Recommended cache and the AI page's - an action (add/dismiss) can come
    from either."""
    for state in (_state, _ai_state):
        result = state["result"]
        if not result:
            continue
        for item in result["items"]:
            if (item["media_type"], item["tmdb_id"]) == (media_type, tmdb_id):
                return item
    return None


def lookup_item(media_type, tmdb_id):
    """A title for the add dialog and /add: a recommendation if it is one, else TMDB details (cached first,
    then one budgeted request). None in sample mode, without a TMDB token, or on any failure. Holds no lock
    while fetching."""
    item = _find_item(media_type, tmdb_id)
    if item is not None:
        return item
    if _is_sample() or not config.TMDB_TOKEN:
        return None
    try:
        client = tmdb.TmdbClient(config.TMDB_TOKEN)
        item = client.cached_details(media_type, tmdb_id)
        if item:
            return item
        if not _tmdb_budget():
            return None
        return client.details(media_type, tmdb_id, timeout=tmdb.SEARCH_TIMEOUT, retries=1)
    except Exception as e:
        print(f"Title lookup failed: {type(e).__name__}")  # never the message: a v3 key sits in TMDB's URLs
        return None


def search_view(q, kind):
    """Everything the search page shows (see the spec's SearchView). One TMDB request at most, and only for
    an uncached query within the shared budget; upstream error text is never put in the result."""
    kind = kind if kind in SEARCH_KINDS else "all"
    q = (q or "").strip()
    sample_mode = _is_sample()
    view = {"q": q, "kind": kind, "state": "empty", "message": None, "results": [], "capped": False,
            "library_known": False, "sample": sample_mode}
    if not q:
        return view
    if len(q) < SEARCH_MIN_CHARS:
        view["state"] = "short"
        return view
    client = sample.SampleTmdb() if sample_mode else (tmdb.TmdbClient(config.TMDB_TOKEN) if config.TMDB_TOKEN else None)
    if client is None:
        view.update(state="error", message=SEARCH_ERROR)
        return view
    try:
        found = client.cached_search(q, kind)
        if found is None:
            if not _tmdb_budget(reserve=LOOKUP_RESERVE):
                view.update(state="limited", message=SEARCH_LIMITED)
                return view
            found = client.search_titles(q, kind)
    except Exception as e:
        print(f"Search failed: {type(e).__name__}")  # not the message: a v3 TMDB key travels in the URL
        view.update(state="error", message=SEARCH_ERROR)
        return view
    result, _ = get_result_nowait()
    added = set() if sample_mode else {(a["media_type"], a["tmdb_id"]) for a in db.added_items()}
    hidden = set() if sample_mode else db.dismissed()
    view["results"] = with_user_state(arr_library.annotate(
        found.get("results") or [], (result or {}).get("library"), _arr_items(result),
        (result or {}).get("watched") or [], added, hidden))
    view.update(state="ok", capped=bool(found.get("capped")), library_known=result is not None)
    return view


# --- Per-user state on cards: stars, lists ---
def _user_state_maps():
    """(ratings, memberships, watchlist id) from the local db in one go; all empty in sample mode."""
    if _is_sample():
        return {}, {}, None
    watch = next((entry["id"] for entry in db.lists() if entry["kind"] == "watchlist"), None)
    return db.ratings(), db.memberships(), watch


def with_user_state(items, maps=None):
    """Copies of items with "stars", "lists" (list ids) and "on_watchlist". Sample mode: None, [], False.
    One db.ratings() + one db.memberships() per call (pass maps from _user_state_maps() to share them)."""
    ratings, member, watch = maps if maps is not None else _user_state_maps()
    out = []
    for raw in items:
        item = dict(raw)
        key = (item.get("media_type"), item.get("tmdb_id"))
        ids = list(member.get(key, [])) if item.get("tmdb_id") else []
        item["stars"] = ratings.get(key) if item.get("tmdb_id") else None
        item["lists"] = ids
        item["on_watchlist"] = watch is not None and watch in ids
        out.append(item)
    return out


# --- Seasons (POST /add) ---
def parse_seasons(form):
    """form: parse_qs dict -> (None | "all" | [int], error message | None). Absent `seasons` is the
    legacy behaviour (None). "all" ignores any `season` values; "pick" needs 1..200 values, each
    1..SEASON_NUMBER_MAX in ASCII digits (deduped, sorted)."""
    if "seasons" not in form:
        return None, None
    mode = (form.get("seasons") or [""])[0]
    if mode == "all":
        return "all", None
    if mode != "pick":
        return None, "That isn't a valid season choice"
    values = form.get("season") or []
    if not values:
        return None, "Pick at least one season"
    if len(values) > MAX_SEASONS_PER_REQUEST:
        return None, "That isn't a valid season"
    numbers = set()
    for raw in values:
        if not (raw and len(raw) <= 4 and raw.isascii() and raw.isdigit()) or not 1 <= int(raw) <= SEASON_NUMBER_MAX:
            return None, "That isn't a valid season"
        numbers.add(int(raw))
    return sorted(numbers), None


# --- Title pages: stub, data, view ---
def _snapshot_result():
    with _status_lock:
        return _state["result"]


def _tmdb_url(media_type, tmdb_id):
    return f"https://www.themoviedb.org/{media_type}/{tmdb_id}"


def title_stub(media_type, tmdb_id, fetch=False):
    """{"media_type","tmdb_id","title","year","poster_url","url"} for a title, or None. Sources, in order:
    a recommendation, the watched snapshot, the library snapshot, Radarr/Sonarr items, cached TMDB
    details (live) / the sample catalogue (sample). Only with fetch=True (and live) does it fall back to
    lookup_item() (one budgeted TMDB request). Never holds a lock while fetching."""
    key = (media_type, tmdb_id)
    candidates = []
    rec = _find_item(media_type, tmdb_id)
    if rec is not None:
        candidates.append(rec)
    result = _snapshot_result() or {}
    for source in (result.get("watched") or [], result.get("library") or [], _arr_items(result)):
        for entry in source:
            if (entry.get("media_type"), entry.get("tmdb_id")) == key:
                candidates.append(entry)
                break
    sample_mode = _is_sample()
    try:
        if sample_mode:
            candidates.append(sample.SampleTmdb().details(media_type, tmdb_id))
        elif config.TMDB_TOKEN:
            cached = tmdb.TmdbClient(config.TMDB_TOKEN).cached_details(media_type, tmdb_id)
            if cached:
                candidates.append(cached)
    except Exception:
        pass
    if not sample_mode:   # offline fallbacks: what we stored when the title was listed or requested
        try:
            for list_id in db.memberships().get(key, [])[:1]:
                candidates.extend(i for i in db.list_items(list_id) if (i["media_type"], i["tmdb_id"]) == key)
            candidates.extend(a for a in db.added_items() if (a["media_type"], a["tmdb_id"]) == key)
        except Exception:
            pass
    if not any(c.get("title") for c in candidates) and fetch and not sample_mode:
        found = lookup_item(media_type, tmdb_id)
        if found:
            candidates.append(found)
    named = [c for c in candidates if c.get("title")]
    if not named:
        return None
    first = named[0]
    return {"media_type": media_type, "tmdb_id": tmdb_id, "title": first["title"],
            "year": next((c.get("year") for c in named if c.get("year")), None),
            "poster_url": next((c.get("poster_url") for c in named if c.get("poster_url")), None),
            "url": next((c.get("url") for c in named if c.get("url")), None) or _tmdb_url(media_type, tmdb_id)}


def title_data(media_type, tmdb_id):
    """The TMDB side of a title page: {"details", "extras", "state"}. state: "ok" (both available),
    "partial" (no TMDB token; whatever was cached), "limited" (budget used up), "error" (TMDB failed;
    only the exception type is logged) or "not_found" (TMDB said 404). Cached data never uses the budget."""
    if _is_sample():
        try:
            details, extras = sample.SampleTmdb().title(media_type, tmdb_id)
        except KeyError:
            return {"details": None, "extras": None, "state": "not_found"}
        return {"details": details, "extras": extras, "state": "ok"}
    if not config.TMDB_TOKEN:
        return {"details": None, "extras": None, "state": "error"}
    client = tmdb.TmdbClient(config.TMDB_TOKEN)
    try:
        details, extras = client.cached_details(media_type, tmdb_id), client.cached_title(media_type, tmdb_id)
    except Exception:
        details = extras = None
    if details and extras:
        return {"details": details, "extras": extras, "state": "ok"}
    if not _tmdb_budget(reserve=LOOKUP_RESERVE):
        return {"details": details, "extras": extras, "state": "limited"}
    try:
        details, extras = client.title(media_type, tmdb_id)
    except Exception as e:
        print(f"Title lookup failed: {type(e).__name__}")  # never the message: a v3 key sits in TMDB's URLs
        if isinstance(e, RuntimeError) and str(e).startswith("HTTP 404"):
            return {"details": None, "extras": None, "state": "not_found"}
        return {"details": details, "extras": extras, "state": "error"}
    return {"details": details, "extras": extras, "state": "ok"}


def _find_rec(media_type, tmdb_id):
    """(item, "main"|"ai") for a current recommendation, else (None, None)."""
    for name, state in (("main", _state), ("ai", _ai_state)):
        with _status_lock:
            result = state["result"]
            items = list(result["items"]) if result else []
        for item in items:
            if (item["media_type"], item["tmdb_id"]) == (media_type, tmdb_id):
                return item, name
    return None, None


def _rec_matches():
    """{(media_type, tmdb_id): match} over the current recommendations (main wins over AI)."""
    out = {}
    for state in (_ai_state, _state):
        with _status_lock:
            result = state["result"]
            items = list(result["items"]) if result else []
        for item in items:
            out[(item["media_type"], item["tmdb_id"])] = item.get("match")
    return out


def _status_for(items, result):
    """annotate() the items against the build snapshot (and, live, the added log and dismissed list)."""
    sample_mode = _is_sample()
    added = set() if sample_mode else {(a["media_type"], a["tmdb_id"]) for a in db.added_items()}
    hidden = set() if sample_mode else db.dismissed()
    return arr_library.annotate(items, (result or {}).get("library"), _arr_items(result),
                                (result or {}).get("watched") or [], added, hidden)


def _cached_or_sample_details(media_type, tmdb_id):
    try:
        if _is_sample():
            return sample.SampleTmdb().details(media_type, tmdb_id)
        if config.TMDB_TOKEN:
            return tmdb.TmdbClient(config.TMDB_TOKEN).cached_details(media_type, tmdb_id)
    except Exception:
        pass
    return None


def _similar_view(extras, result, profile_features, maps):
    """TMDB's similar titles, ordered by our engine: current recommendations first (by match), then titles
    with cached details by content_score, then TMDB's order. Cache reads only - no requests."""
    raw = list((extras or {}).get("similar") or [])
    hidden = set() if _is_sample() else db.dismissed()
    raw = [r for r in raw if (r["media_type"], r["tmdb_id"]) not in hidden]
    matches = _rec_matches()
    scored = []
    for rank, entry in enumerate(raw):
        match = matches.get((entry["media_type"], entry["tmdb_id"]))
        score = None
        if match is None and profile_features:
            details = _cached_or_sample_details(entry["media_type"], entry["tmdb_id"])
            if details:
                try:
                    score = recommend.content_score(profile_features, details)
                except Exception:
                    score = None
        tier = 0 if match is not None else (1 if score is not None else 2)
        scored.append((tier, -(match if match is not None else (score or 0)), rank, entry, match))
    scored.sort(key=lambda t: t[:3])
    annotated = _status_for([{**t[3]} for t in scored], result)
    out = []
    for t, item in zip(scored, annotated):
        item["match"] = t[4]
        out.append(item)
    return with_user_state(out, maps)


def title_view(media_type, tmdb_id):
    """Everything the title detail page shows (the spec's TitleView). Never blocks on a build."""
    sample_mode = _is_sample()
    result, _ = get_result_nowait()
    data = title_data(media_type, tmdb_id)
    view = {"media_type": media_type, "tmdb_id": tmdb_id, "state": "ok", "message": None, "item": None,
            "extras": data["extras"], "rec": None, "fit": None, "status": None, "arr": None, "seasons": [],
            "stars": None, "lists": [], "on_watchlist": False, "list_names": [], "similar": [],
            "can_add": False, "sample": sample_mode, "library_known": result is not None}
    if data["state"] == "not_found":
        view.update(state="not_found", message=TITLE_NOT_FOUND, extras=None)
        return view
    rec, source = _find_rec(media_type, tmdb_id)
    details = data["details"]
    base = dict(rec) if rec else (dict(details) if details else title_stub(media_type, tmdb_id))
    if base is None:
        view.update(state="unavailable", message=TITLE_UNAVAILABLE)
        return view
    if data["state"] != "ok":
        view.update(state="partial", message=TITLE_PARTIAL)
    view["item"] = base
    features = (result or {}).get("profile") or None
    if rec:
        view["rec"] = {"match": rec.get("match"), "reason": rec.get("reason") or "", "matches": list(rec.get("matches") or []),
                       "because": list(rec.get("because") or [])[:5], "new": bool(rec.get("new")),
                       "trending": bool(rec.get("trending")), "source": source}
    elif details and features:
        try:
            found = recommend.matches(features, details)
        except Exception:
            found = []
        if found:
            view["fit"] = {"matches": found}
    probe = {"media_type": media_type, "tmdb_id": tmdb_id, "title": base.get("title"), "year": base.get("year")}
    annotated = _status_for([probe], result)[0]
    view["status"] = {k: annotated[k] for k in ("status", "in_library", "sources", "arr_state", "episodes",
                                                "watched", "dismissed")}
    extras = data["extras"]
    arr = arr_library.find(_arr_items(result), media_type, tmdb_id, tvdb_id=(extras or {}).get("tvdb_id"),
                           title=base.get("title"), year=base.get("year"))
    view["arr"] = arr
    if media_type == "tv":
        view["seasons"] = arr_library.season_rows((extras or {}).get("seasons") or [], arr, date.today())
    maps = _user_state_maps()
    mine = with_user_state([probe], maps)[0]
    view.update(stars=mine["stars"], lists=mine["lists"], on_watchlist=mine["on_watchlist"])
    if not sample_mode and mine["lists"]:
        view["list_names"] = [{"id": e["id"], "name": e["name"]} for e in db.lists() if e["id"] in mine["lists"]]
    view["similar"] = _similar_view(extras, result, features, maps)
    configured = config.radarr_configured() if media_type == "movie" else config.sonarr_configured()
    view["can_add"] = view["status"]["status"] == "none" and configured and not sample_mode
    return view


def season_choices(tmdb_id):
    """{"seasons": [SeasonRow], "tracked": bool, "known": bool} for the add dialog's season picker."""
    data = title_data("tv", tmdb_id)
    extras = data["extras"]
    result = _snapshot_result()
    stub = title_stub("tv", tmdb_id) or {}
    arr = arr_library.find(_arr_items(result), "tv", tmdb_id, tvdb_id=(extras or {}).get("tvdb_id"),
                           title=stub.get("title"), year=stub.get("year"))
    rows = arr_library.season_rows((extras or {}).get("seasons") or [], arr, date.today())
    return {"seasons": rows, "tracked": arr is not None, "known": bool(rows)}


def note_arr_item(item):
    """Puts a just-added Radarr/Sonarr item (the client's last_item) into the cached build result so Requests and
    the title page show it at once, with no extra arr call. Replaces the same service + tmdb/tvdb id.
    If a build is running, its result may lack the item until the next build (accepted)."""
    if not isinstance(item, dict):
        return
    with _status_lock:
        result = _state["result"]
        arr = (result or {}).get("arr")
        if arr is None:
            return
        same = lambda e: e.get("service") == item.get("service") and (
            (item.get("tmdb_id") and e.get("tmdb_id") == item.get("tmdb_id"))
            or (item.get("tvdb_id") and e.get("tvdb_id") == item.get("tvdb_id")))
        items = [e for e in arr.get("items") or []]
        replaced = any(same(e) for e in items)
        items = [item if same(e) else e for e in items]
        if not replaced:
            items.append(item)
            service = arr.get(item.get("service"))
            if isinstance(service, dict):
                service["count"] = service.get("count", 0) + 1
        arr["items"] = items


def requests_items(result):
    """The Requests tab: everything logged as added, with its live-ish state from the build snapshot."""
    entries = []
    for a in db.added_items():
        entries.append({"media_type": a["media_type"], "tmdb_id": a["tmdb_id"], "title": a["title"], "year": a["year"],
                        "added_at": a["added_at"], "watched": False, "progress": None, "poster_key": None,
                        "poster_url": a["poster_url"], "url": a["url"], "seasons": a.get("seasons")})
    annotated = _status_for(entries, result) if entries else []
    arr_items = _arr_items(result)
    for entry, note in zip(entries, annotated):
        entry["sources"] = note["sources"]
        entry["request_state"], entry["episodes"] = None, None
        if result is not None:
            arr = arr_library.find(arr_items, entry["media_type"], entry["tmdb_id"],
                                   title=entry["title"], year=entry["year"])
            entry["request_state"], entry["episodes"] = arr_library.request_state(
                entry, arr, "plex" in note["sources"])
    return with_user_state(entries)


# --- Lists ---
def watchlist_id():
    """The Watchlist's id (created on first use) in live mode; None in sample mode."""
    return None if _is_sample() else db.ensure_watchlist()


def _summary(entry, posters=None):
    return {"id": entry["id"], "name": entry["name"], "description": entry["description"], "kind": entry["kind"],
            "count": entry["count"], "url": f"/lists/{entry['id']}" if entry["id"] is not None else None,
            "posters": posters if posters is not None else
            [i["poster_url"] for i in db.list_items(entry["id"]) if i.get("poster_url")][:4]}


def lists_view():
    """The Lists page: every list with a count and up to 4 posters. Sample mode shows a virtual, empty
    Watchlist and writes nothing."""
    if _is_sample():
        virtual = {"id": None, "name": "Watchlist", "description": "", "kind": "watchlist", "count": 0}
        return {"lists": [_summary(virtual, [])], "sample": True, "can_create": False, "max_lists": MAX_LISTS}
    db.ensure_watchlist()
    entries = db.lists()
    custom = sum(1 for e in entries if e["kind"] == "custom")
    return {"lists": [_summary(e) for e in entries], "sample": False, "can_create": custom < MAX_LISTS,
            "max_lists": MAX_LISTS}


def parse_list_page_query(query):
    """query: parse_qs dict -> {"sort": valid or "manual", "page": int >= 1}."""
    sort = (query.get("sort") or [""])[0]
    return {"sort": sort if sort in LIST_SORTS else "manual",
            "page": max(1, _parse_id((query.get("page") or [""])[0]) or 1)}


def list_page_view(list_id, sort="manual", page=1):
    """One list's page (the spec's ListPageView), or None if it doesn't exist (always None in sample mode)."""
    if _is_sample() or not isinstance(list_id, int):
        return None
    entry = db.get_list(list_id)
    if entry is None:
        return None
    sort = sort if sort in LIST_SORTS else "manual"
    raw = db.list_items(list_id)
    result = _snapshot_result()
    items = []
    for note, row in zip(_status_for(raw, result) if raw else [], raw):
        note["url"] = _tmdb_url(row["media_type"], row["tmdb_id"])
        items.append(note)
    matches = _rec_matches()
    items = with_user_state([{**i, "match": matches.get((i["media_type"], i["tmdb_id"]))} for i in items])
    by_title = sorted(items, key=lambda i: (i["title"] or "").casefold())
    if sort == "title":
        items = by_title
    elif sort == "added":
        items = sorted(by_title, key=lambda i: i["added_at"], reverse=True)
    elif sort in ("year", "rating", "match"):
        field = {"year": "year", "rating": "stars", "match": "match"}[sort]
        items = _sorted_desc_none_last(items, lambda i: i.get(field))
    per_page = LIST_PAGE_SIZE
    pages = max(1, -(-len(items) // per_page))
    page = min(max(1, page), pages)
    return {"list": _summary(entry), "sort": sort, "sorts": LIST_SORTS, "page": page, "pages": pages,
            "total": len(items), "items": items[(page - 1) * per_page:page * per_page],
            "sample": False, "can_move": sort == "manual"}


def forget(media_type, tmdb_id):
    """Remove one suggestion from whichever cache currently has it (after 'Not interested' or
    'Add to library'), stashing what it removed so Undo (restore()) can put it back in the same
    spot without a rebuild. Only takes _status_lock, so a click mid-build returns straight away."""
    key = (media_type, tmdb_id)
    with _status_lock:
        for name, state in (("main", _state), ("ai", _ai_state)):
            result = state["result"]
            if not result:
                continue
            items = result["items"]
            _forgotten.extend((name, index, item) for index, item in enumerate(items)
                              if (item["media_type"], item["tmdb_id"]) == key)
            result["items"] = [i for i in items if (i["media_type"], i["tmdb_id"]) != key]
        del _forgotten[:-FORGET_STASH]
        _log_action(("hide", key))  # so a build/generation that's running doesn't bring it back


def restore(media_type, tmdb_id):
    """Undo for forget(): puts the stashed copy of this title back where it was, in whichever
    cache it came from. Returns the item, or None if it isn't stashed any more (e.g. the app
    restarted since) - it then comes back with the next rebuild instead, since it's no longer
    dismissed."""
    key = (media_type, tmdb_id)
    with _status_lock:
        matches = [e for e in _forgotten if (e[2]["media_type"], e[2]["tmdb_id"]) == key]
        _forgotten[:] = [e for e in _forgotten if (e[2]["media_type"], e[2]["tmdb_id"]) != key]
        for name, index, item in matches:
            _log_action(("restore", name, index, item))  # survives a build that's running right now
            result = (_state if name == "main" else _ai_state)["result"]
            if result is None or any((i["media_type"], i["tmdb_id"]) == key for i in result["items"]):
                continue
            items = list(result["items"])
            items.insert(min(index, len(items)), item)
            result["items"] = items
    return matches[-1][2] if matches else None


def invalidate_cache():
    """Forces the next page load to recompute from scratch (after a settings change). Doesn't
    touch the AI cache - that's never automatic, so a settings change doesn't invalidate a batch
    you deliberately generated; Generate again picks up the new settings regardless. A build
    that's running right now is thrown away when it finishes (see _build_locked()), and a past
    failure is forgotten so the new settings get tried straight away."""
    with _status_lock:
        _state["result"], _state["time"] = None, 0.0
        _build["generation"] += 1
        _build["error"], _build["failed_at"] = None, 0.0


def _is_sample():
    return sources.use_sample(os.environ.get("SAMPLE") == "1" or None)



def _safe_path(path, default="/recommended"):
    """Only ever redirect within this app - never to another host (an attacker-supplied return_to
    shouldn't be able to bounce a browser off this page to somewhere else). Printable ASCII only:
    browsers treat "\\" like "/" (so "/\\evil.example" is another host) and skip tabs/newlines
    inside URLs ("/\\t/evil.example"), and a CR/LF would end the Location header early and let
    the rest inject headers of its own. Non-ASCII can't go in a header at all."""
    if (path and path[0] == "/" and path[1:2] not in ("/", "\\") and "://" not in path
            and all("!" <= c <= "~" and c != "\\" for c in path)):
        return path
    return default


def _parse_id(raw):
    """A TMDB id from a form/query value, or None. isdigit() alone also accepts things like "²"
    that int() then chokes on, and a huge number would overflow SQLite's integer column."""
    return int(raw) if raw and len(raw) <= 12 and raw.isascii() and raw.isdigit() else None


def _item_from(fields, type_key="type", id_key="id"):
    """(media_type, tmdb_id) from parsed form/query fields, or (None, None) if either is invalid."""
    media_type, tmdb_id = fields.get(type_key, [""])[0], _parse_id(fields.get(id_key, [""])[0])
    return (media_type, tmdb_id) if media_type in ("movie", "tv") and tmdb_id else (None, None)


_request = threading.local()  # per-request state; the Handler sets .theme first thing


def current_theme():
    """Theme key for the request being handled (themes.DEFAULT outside a request)."""
    return getattr(_request, "theme", themes.DEFAULT)


SAMPLE_MESSAGE = "Sample data - not saved"


def _cross_site(headers):
    """True if a POST was sent by a browser from some other site - CSRF protection. There's no
    login, so without this any web page you visit could make your browser post to this app (save
    a malicious RADARR_URL in Settings, add titles, ...). Not authentication: a request with
    neither header below (curl, scripts, old browsers) is allowed.
    - Sec-Fetch-Site (all current browsers): only "same-origin", or "none" (typed/bookmarked).
      "same-site" is refused too - another app on the same host at a different port counts as
      same-site, and that's exactly what a home server's other apps are.
    - Otherwise Origin: must be this app's own scheme://host:port, compared with the Host header
      ("null", from sandboxed frames and the like, is refused). Behind a reverse proxy, the proxy
      must forward the original Host header or every browser POST will be refused - arr serves
      this directly on its own port today, so that's fine."""
    fetch_site = headers.get("Sec-Fetch-Site")
    if fetch_site is not None:
        return fetch_site.strip().lower() not in ("same-origin", "none")
    origin = headers.get("Origin")
    if origin is None:
        return False
    try:
        parsed, host = urlparse(origin.strip().lower()), urlparse("//" + (headers.get("Host") or "").strip().lower())
        default = {"http": 80, "https": 443}.get(parsed.scheme)
        if default is None or not parsed.hostname or not host.hostname:
            return True  # "null", or anything else that isn't a plain http(s) origin
        return (parsed.hostname, parsed.port or default) != (host.hostname, host.port or default)
    except ValueError:  # e.g. a non-numeric port
        return True


# --- List form helpers ---
def _clean_line(value):
    """Whitespace collapsed to single spaces, non-printable characters dropped."""
    return " ".join("".join(c for c in (value or "") if c.isprintable() or c.isspace()).split())


def _parse_list_text(form, name_key="name", required=True):
    """(name, description, error) from a list form. Errors are the spec's messages."""
    name = _clean_line(form.get(name_key, [""])[0])
    description = _clean_line(form.get("description", [""])[0])
    if not name:
        return name, description, ("Give the list a name" if required else None)
    if len(name) > LIST_NAME_MAX:
        return name, description, f"List names can be up to {LIST_NAME_MAX} characters"
    if len(description) > LIST_DESC_MAX:
        return name, description, f"Descriptions can be up to {LIST_DESC_MAX} characters"
    return name, description, None


def _name_taken(name, except_id=None):
    folded = name.casefold()
    return folded == "watchlist" or any(e["name"].casefold() == folded and e["id"] != except_id for e in db.lists())


def _title_lists(media_type, tmdb_id):
    """([list ids containing the title], on_watchlist)."""
    ids = sorted(db.memberships().get((media_type, tmdb_id), []))
    watch = next((e["id"] for e in db.lists() if e["kind"] == "watchlist"), None)
    return ids, watch is not None and watch in ids


def _parse_list_ref(form):
    """('watchlist' | list id | None for bad input) from list_id (digits) or list=watchlist."""
    if "list_id" in form:
        return _parse_id(form["list_id"][0])
    return "watchlist" if form.get("list", [""])[0] == "watchlist" else None



class Handler(BaseHTTPRequestHandler):
    # Served by name from this fixed list - the request path is never joined onto a filesystem
    # path, so there's nothing to traverse.
    STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    STATIC_FILES = {"app.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8"}

    def _send(self, status, body, content_type="text/html; charset=utf-8", headers=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, path, msg=None, extra=None, headers=None):
        """extra: more query params to add (the non-JS Undo's undo_type/undo_id). Any of those
        already on the path are replaced rather than repeated - parse_qs would read the first,
        stale one."""
        path = _safe_path(path)
        params = {"msg": msg} if msg else {}
        params.update(extra or {})
        if params:
            if extra:
                url = urlparse(path)
                kept = [(k, v) for k, v in parse_qsl(url.query, keep_blank_values=True) if k not in params]
                path = url.path + ("?" + urlencode(kept) if kept else "")
            path += ("&" if "?" in path else "?") + urlencode(params)
        self._send(303, "", headers={"Location": path, **(headers or {})})

    def _wants_json(self):
        """The JS sends Accept: application/json; a plain form post (no JS) never does."""
        return "application/json" in (self.headers.get("Accept") or "")

    def _json(self, payload, status=200, headers=None):
        self._send(status, json.dumps(payload), "application/json",
                   headers={"Cache-Control": "no-store", **(headers or {})})

    def _static(self, name):
        content_type = self.STATIC_FILES.get(name)
        if content_type is None:
            return self._send(404, "Not found", "text/plain")
        try:
            with open(os.path.join(self.STATIC_DIR, name), encoding="utf-8") as f:
                body = f.read()
        except OSError:
            return self._send(404, "Not found", "text/plain")
        return self._send(200, body, content_type, headers={"Cache-Control": "no-cache"})

    def do_GET(self):
        _request.theme = themes.from_cookie(self.headers.get("Cookie"))
        url = urlparse(self.path)
        if url.path == "/health":
            return self._send(200, "ok", "text/plain")
        if url.path == "/api/status":
            # Also (re)starts a build if nothing's cached - the same as a page load would - so a
            # "Finding your recommendations..." screen polling this can never wait on an idle app.
            get_result_nowait()
            return self._json(build_status())
        if url.path.startswith("/static/"):
            return self._static(url.path[len("/static/"):])
        if url.path in ("/", "/movies", "/tv"):
            query = parse_qs(url.query)
            undo = _item_from(query, "undo_type", "undo_id")
            msg = query.get("msg", [""])[0]
            undo = undo if undo[0] else None
            if url.path == "/":
                return self._send(200, render_home(msg=msg, undo=undo))
            return self._send(200, render_browse("movie" if url.path == "/movies" else "tv", msg=msg, undo=undo))
        if url.path == "/recommended":
            # Old grid page: redirect to the browse page for that type (keeps msg and a valid undo).
            query = parse_qs(url.query)
            target = {"movie": "/movies", "tv": "/tv"}.get(query.get("type", [""])[0], "/")
            undo_type, undo_id = _item_from(query, "undo_type", "undo_id")
            extra = {"undo_type": undo_type, "undo_id": undo_id} if undo_type else None
            return self._redirect(target, query.get("msg", [""])[0] or None, extra=extra)
        if url.path == "/library":
            query = parse_qs(url.query)
            return self._send(200, render_library(**parse_list_query(query), msg=query.get("msg", [""])[0]))
        if url.path == "/search":
            query = parse_qs(url.query)
            args = parse_search_query(query)
            if query.get("partial", [""])[0] == "1":
                return self._send(200, render_search_results(**args), headers={"Cache-Control": "no-store"})
            return self._send(200, render_search(**args, msg=query.get("msg", [""])[0]))
        if url.path.startswith("/title/"):
            parts = url.path.split("/")   # ["", "title", type, id]
            media_type, tmdb_id = (parts[2], _parse_id(parts[3])) if len(parts) == 4 else (None, None)
            if media_type not in ("movie", "tv") or not tmdb_id:   # id 0 would spend a TMDB request
                return self._send(404, "Not found", "text/plain")
            query = parse_qs(url.query)
            undo = _item_from(query, "undo_type", "undo_id")
            view = title_view(media_type, tmdb_id)
            page = render_title(view, query.get("msg", [""])[0], undo if undo[0] else None)
            return self._send(404 if view["state"] == "not_found" else 200, page)
        if url.path == "/requests":
            msg = parse_qs(url.query).get("msg", [""])[0]
            return self._redirect("/library?type=added", msg or None)
        if url.path == "/lists":
            return self._send(200, render_lists(msg=parse_qs(url.query).get("msg", [""])[0]))
        if url.path.startswith("/lists/"):
            raw = url.path[len("/lists/"):]
            query = parse_qs(url.query)
            list_id = _parse_id(raw)
            view = list_page_view(list_id, **parse_list_page_query(query)) if list_id is not None else None
            if view is None:
                return self._send(404, "Not found", "text/plain")
            return self._send(200, render_list(view, msg=query.get("msg", [""])[0]))
        if url.path == "/watchlist":
            watch = watchlist_id()
            return self._redirect(f"/lists/{watch}" if watch is not None else "/lists")
        if url.path == "/list-dialog":
            query = parse_qs(url.query)
            media_type, tmdb_id = _item_from(query)
            if media_type is None:
                return self._send(404, "Not found", "text/plain")
            partial = query.get("partial", [""])[0] == "1"
            page = render_list_dialog(media_type, tmdb_id, query.get("return_to", ["/recommended"])[0], partial=partial)
            return self._send(200, page, headers={"Cache-Control": "no-store"} if partial else None)
        if url.path == "/appearance":
            return self._send(200, render_appearance(msg=parse_qs(url.query).get("msg", [""])[0]))
        if url.path == "/poster":
            return self._poster(parse_qs(url.query))
        if url.path == "/ai":
            query = parse_qs(url.query)
            undo = _item_from(query, "undo_type", "undo_id")
            return self._send(200, render_ai_page(msg=query.get("msg", [""])[0], undo=undo if undo[0] else None))
        if url.path == "/add-dialog":
            query = parse_qs(url.query)
            media_type, tmdb_id = _item_from(query)
            return_to = query.get("return_to", ["/recommended"])[0]
            if media_type is None:
                return self._send(404, "Not found", "text/plain")
            partial = query.get("partial", [""])[0] == "1"
            return self._send(200, render_add_dialog(media_type, tmdb_id, return_to, partial=partial))
        if url.path == "/settings":
            query = parse_qs(url.query)
            section = query.get("section", [settings_page.DEFAULT_SECTION])[0]
            saved = query.get("saved", ["0"])[0] == "1"
            return self._send(200, _shell(settings_page.render(section, saved), "settings"))
        return self._send(404, "Not found", "text/plain")

    def do_POST(self):
        _request.theme = themes.from_cookie(self.headers.get("Cookie"))
        if _cross_site(self.headers):  # before reading the body - see _cross_site()
            if self._wants_json():
                return self._json({"ok": False, "message": "Blocked: request came from another site"}, 403)
            return self._send(403, "Blocked: request came from another site", "text/plain; charset=utf-8")
        length = min(int(self.headers.get("Content-Length") or 0), MAX_FORM_BYTES)
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        return_to = form.get("return_to", ["/recommended"])[0]
        path = urlparse(self.path).path
        as_json = self._wants_json()
        if path == "/refresh":
            # Starts a background rebuild rather than waiting for it - the page it redirects to
            # (or the JS) shows the old list meanwhile, and GET /api/status says when it's done.
            if _needs_build(refresh=True):
                started, message = _start_build(refresh=True), "Refreshing..."
            else:
                started, message = False, "Already up to date"
            if as_json:
                return self._json({"ok": True, "started": started, "message": message})
            return self._redirect(return_to)
        if path in ("/dismiss", "/undismiss"):
            return self._post_dismiss(path == "/dismiss", form, return_to, as_json)
        if path == "/ai/generate":
            if _is_sample():
                if as_json:
                    return self._json({"ok": False, "started": False, "message": SAMPLE_MESSAGE})
                return self._redirect(return_to)
            if not config.ai_configured():  # nothing to run - the AI page already says so
                if as_json:
                    return self._json({"ok": False, "started": False,
                                       "message": "AI isn't configured - add a token in Settings -> AI first."})
                return self._redirect(return_to)
            started = start_ai_generation()
            if as_json:
                return self._json({"ok": True, "started": started,
                                   "message": "Generating..." if started else "Already generating..."})
            return self._redirect(return_to)
        if path == "/settings":
            section = parse_qs(urlparse(self.path).query).get("section", [settings_page.DEFAULT_SECTION])[0]
            if section not in dict(settings_page.SECTIONS):
                # it's echoed into the Location header below - never let an unknown value get there
                return self._redirect("/settings")
            action = form.get("action", [""])[0]
            if action == "save":
                settings_page.apply_form(section, form)
                invalidate_cache()
                return self._send(303, "", headers={"Location": f"/settings?section={section}&saved=1"})
            if action.startswith("test_"):
                test_result = settings_page.run_test(action[len("test_"):], form)
                overrides = settings_page.form_overrides(section, form)
                body = settings_page.render(section, test_result=test_result, overrides=overrides)
                return self._send(200, _shell(body, "settings"))
            return self._redirect(f"/settings?section={section}")
        if path == "/theme":
            return self._post_theme(form, as_json)
        if path == "/rate":
            return self._post_rate(form, form.get("return_to", ["/library?type=watched"])[0], as_json)
        if path.startswith("/lists/"):
            handler = self.LIST_ROUTES.get(path[len("/lists/"):])
            if handler is not None:
                return handler(self, form, return_to, as_json)
        if path == "/add":
            media_type, tmdb_id = _item_from(form)
            if media_type is None:
                if as_json:
                    return self._json({"ok": False, "message": "That isn't a valid title"}, 400)
                return self._redirect(return_to)
            seasons, season_error = parse_seasons(form) if media_type == "tv" else (None, None)
            if season_error:
                if as_json:
                    return self._json({"ok": False, "message": season_error}, 400)
                return self._redirect(return_to, season_error)
            if _is_sample():
                if as_json:
                    return self._json({"ok": False, "message": SAMPLE_MESSAGE})
                return self._redirect(return_to)
            quality_profile_id = _parse_id(form.get("quality_profile_id", [""])[0])
            search = form.get("search", [""])[0] == "1"
            try:
                extra = {"seasons": seasons} if media_type == "tv" else {}  # movies ignore seasons
                ok, msg, arr_item = sources.add_to_library(media_type, tmdb_id, search=search,
                                                           quality_profile_id=quality_profile_id, **extra)
            except Exception as e:
                ok, msg, arr_item = False, f"Couldn't add it: {e}", None
            if ok:
                item = title_stub(media_type, tmdb_id, fetch=True)  # only on success: a failed add costs no TMDB request
                if item:
                    db.record_added(item, seasons)
                note_arr_item(arr_item)
                forget(media_type, tmdb_id)
            if as_json:
                return self._json({"ok": bool(ok), "message": msg or ""})
            return self._redirect(return_to, msg)
        return self._send(404, "Not found", "text/plain")

    # --- Lists (POST /lists/*) ---
    def _reply(self, as_json, return_to, ok, message, status=200, payload=None, silent=False):
        """One answer for both transports: JSON {"ok", "message", ...} or a 303 to return_to with the message."""
        if as_json:
            return self._json({"ok": ok, "message": message, **(payload or {})}, status)
        return self._redirect(return_to, None if silent else message)

    def _sample_refusal(self, as_json, return_to):
        if as_json:
            return self._json({"ok": False, "message": SAMPLE_MESSAGE})
        return self._redirect(return_to, SAMPLE_MESSAGE)

    def _bad_title(self, as_json, return_to):
        return self._reply(as_json, return_to, False, "That isn't a valid title", 400, silent=True)

    def _post_list_create(self, form, return_to, as_json):
        back = _safe_path(form.get("return_to", [""])[0], "/lists")
        name, description, error = _parse_list_text(form)
        if error:
            return self._reply(as_json, back, False, error, 400)
        media_type, tmdb_id = _item_from(form)
        if ("type" in form or "id" in form) and media_type is None:
            return self._bad_title(as_json, back)
        if _is_sample():
            return self._sample_refusal(as_json, back)
        if _name_taken(name):
            return self._reply(as_json, back, False, f'You already have a list called "{name}"', 400)
        if sum(1 for e in db.lists() if e["kind"] == "custom") >= MAX_LISTS:
            return self._reply(as_json, back, False, f"You can have up to {MAX_LISTS} lists")
        stub = None
        if media_type is not None:
            stub = title_stub(media_type, tmdb_id, fetch=True)
            if stub is None:
                return self._reply(as_json, back, False, LOOKUP_FAILED)
        list_id = db.create_list(name, description)
        if stub is not None:
            db.add_to_list(list_id, stub)
        message = f'Created "{name}"'
        target = _safe_path(form.get("return_to", [""])[0], f"/lists/{list_id}")
        return self._reply(as_json, target, True, message, payload={"list": _summary(db.get_list(list_id))})

    def _existing_custom_list(self, form, as_json, return_to):
        """The custom list a request means, or None after answering 400 (missing list, or the Watchlist)."""
        list_id = _parse_id(form.get("list_id", [""])[0])
        entry = db.get_list(list_id) if list_id is not None else None
        if entry is None:
            self._reply(as_json, return_to, False, "That list doesn't exist", 400)
        elif entry["kind"] == "watchlist":
            self._reply(as_json, return_to, False, "The Watchlist can't be renamed or deleted", 400)
            return None
        return entry

    def _post_list_update(self, form, return_to, as_json):
        list_id = _parse_id(form.get("list_id", [""])[0])
        default = f"/lists/{list_id}" if list_id is not None else "/lists"
        target = _safe_path(form.get("return_to", [""])[0], default)
        if list_id is None:
            return self._reply(as_json, "/lists", False, "That list doesn't exist", 400)
        name, description, error = _parse_list_text(form)
        if error:
            return self._reply(as_json, target, False, error, 400)
        if _is_sample():
            return self._sample_refusal(as_json, target)
        entry = self._existing_custom_list(form, as_json, target)
        if entry is None:
            return
        if _name_taken(name, except_id=entry["id"]):
            return self._reply(as_json, target, False, f'You already have a list called "{name}"', 400)
        if not db.update_list(entry["id"], name, description):
            return self._reply(as_json, target, False, "That list doesn't exist", 400)
        return self._reply(as_json, target, True, f'Saved "{name}"', payload={"list": _summary(db.get_list(entry["id"]))})

    def _post_list_delete(self, form, return_to, as_json):
        list_id = _parse_id(form.get("list_id", [""])[0])
        target = _safe_path(form.get("return_to", [""])[0], "/lists")
        if list_id is None:
            return self._reply(as_json, target, False, "That list doesn't exist", 400)
        if _is_sample():
            return self._sample_refusal(as_json, target)
        entry = self._existing_custom_list(form, as_json, target)
        if entry is None:
            return
        if not db.delete_list(entry["id"]):
            return self._reply(as_json, target, False, "That list doesn't exist", 400)
        return self._reply(as_json, target, True, f'Deleted "{entry["name"]}"')

    def _list_target(self, form, as_json, return_to):
        """The list a /lists/add|remove request means (the Watchlist is created on first use), or None after
        answering 400. Call after the sample check."""
        ref = _parse_list_ref(form)
        entry = db.get_list(db.ensure_watchlist() if ref == "watchlist" else ref) if ref is not None else None
        if entry is None:
            self._reply(as_json, return_to, False, "That list doesn't exist", 400)
        return entry

    def _post_list_toggle(self, add, form, return_to, as_json):
        media_type, tmdb_id = _item_from(form)
        if media_type is None:
            return self._bad_title(as_json, return_to)
        if _parse_list_ref(form) is None:
            if _is_sample():
                return self._sample_refusal(as_json, return_to)
            return self._reply(as_json, return_to, False, "That list doesn't exist", 400)
        if _is_sample():
            return self._sample_refusal(as_json, return_to)
        entry = self._list_target(form, as_json, return_to)
        if entry is None:
            return
        name, in_list = entry["name"], None
        present = any((i["media_type"], i["tmdb_id"]) == (media_type, tmdb_id) for i in db.list_items(entry["id"]))
        if add:
            if present:
                message, in_list = f"Already on {name}", True
            elif entry["count"] >= MAX_LIST_ITEMS:
                return self._reply(as_json, return_to, False, f"That list is full ({MAX_LIST_ITEMS} titles)")
            else:
                stub = title_stub(media_type, tmdb_id, fetch=True)
                if stub is None:
                    return self._reply(as_json, return_to, False, LOOKUP_FAILED)
                added = db.add_to_list(entry["id"], stub)
                message = f'Added "{stub["title"]}" to {name}' if added else f"Already on {name}"
                in_list = True
        else:
            title = next((i["title"] for i in db.list_items(entry["id"])
                          if (i["media_type"], i["tmdb_id"]) == (media_type, tmdb_id)), None)
            if present and db.remove_from_list(entry["id"], media_type, tmdb_id):
                message = f'Removed "{title}" from {name}'
            else:
                message = f"Not on {name}"
            in_list = False
        ids, on_watchlist = _title_lists(media_type, tmdb_id)
        return self._reply(as_json, return_to, True, message, payload={
            "list": {"id": entry["id"], "name": name, "kind": entry["kind"]},
            "item": {"type": media_type, "id": tmdb_id}, "in_list": in_list, "lists": ids,
            "on_watchlist": on_watchlist})

    def _post_list_add(self, form, return_to, as_json):
        return self._post_list_toggle(True, form, return_to, as_json)

    def _post_list_remove(self, form, return_to, as_json):
        return self._post_list_toggle(False, form, return_to, as_json)

    def _post_list_set(self, form, return_to, as_json):
        media_type, tmdb_id = _item_from(form)
        if media_type is None:
            return self._bad_title(as_json, return_to)
        wanted = set()
        for raw in form.get("list_id", []):
            list_id = _parse_id(raw)
            if list_id is None:
                return self._reply(as_json, return_to, False, "That list doesn't exist", 400)
            wanted.add(list_id)
        new_name, new_desc, error = _parse_list_text(form, "new_list", required=False)
        if error:
            return self._reply(as_json, return_to, False, error, 400)
        if _is_sample():
            return self._sample_refusal(as_json, return_to)
        known = {e["id"]: e for e in db.lists()}
        if any(i not in known for i in wanted):
            return self._reply(as_json, return_to, False, "That list doesn't exist", 400)
        if new_name and _name_taken(new_name):
            return self._reply(as_json, return_to, False, f'You already have a list called "{new_name}"', 400)
        if new_name and sum(1 for e in known.values() if e["kind"] == "custom") >= MAX_LISTS:
            return self._reply(as_json, return_to, False, f"You can have up to {MAX_LISTS} lists")
        key = (media_type, tmdb_id)
        current = set(db.memberships().get(key, []))
        to_add, to_remove = wanted - current, current - wanted
        full = [known[i]["name"] for i in to_add if known[i]["count"] >= MAX_LIST_ITEMS]
        if full:
            return self._reply(as_json, return_to, False, f"That list is full ({MAX_LIST_ITEMS} titles)")
        stub = None
        if to_add or new_name:
            stub = title_stub(media_type, tmdb_id, fetch=True)
            if stub is None:
                return self._reply(as_json, return_to, False, LOOKUP_FAILED)
        created = None
        if new_name:
            new_id = db.create_list(new_name, "")
            created = {"id": new_id, "name": new_name}
            to_add = to_add | {new_id}
        for list_id in sorted(to_add):
            db.add_to_list(list_id, stub)
        for list_id in to_remove:
            db.remove_from_list(list_id, media_type, tmdb_id)
        ids, on_watchlist = _title_lists(media_type, tmdb_id)
        message = ("Saved - not on any list" if not ids
                   else f"Saved - on {len(ids)} list" + ("" if len(ids) == 1 else "s"))
        return self._reply(as_json, return_to, True, message, payload={
            "item": {"type": media_type, "id": tmdb_id}, "lists": ids, "on_watchlist": on_watchlist,
            "created": created})

    MOVES = ("up", "down", "top", "bottom")

    def _post_list_move(self, form, return_to, as_json):
        list_id = _parse_id(form.get("list_id", [""])[0])
        target = _safe_path(form.get("return_to", [""])[0], f"/lists/{list_id}" if list_id is not None else "/lists")
        direction = form.get("direction", [""])[0]
        if direction not in self.MOVES:
            return self._reply(as_json, target, False, "That isn't a valid move", 400, silent=True)
        if list_id is None:
            return self._reply(as_json, target, False, "That list doesn't exist", 400)
        media_type, tmdb_id = _item_from(form)
        if media_type is None:
            return self._bad_title(as_json, target)
        if _is_sample():
            return self._sample_refusal(as_json, target)
        if db.get_list(list_id) is None:
            return self._reply(as_json, target, False, "That list doesn't exist", 400)
        moved = db.move_in_list(list_id, media_type, tmdb_id, direction)
        return self._reply(as_json, target, True, "Moved" if moved else "Already at the end", payload={"moved": moved})

    LIST_ROUTES = {"create": _post_list_create, "update": _post_list_update, "delete": _post_list_delete,
                   "add": _post_list_add, "remove": _post_list_remove, "set": _post_list_set,
                   "move": _post_list_move}

    POSTER_TYPES = ("image/jpeg", "image/png", "image/webp")

    def _poster(self, query):
        """A Plex poster, fetched here so the token and thumb path never reach the browser. Only keys
        in the current snapshot are served. Fetches outside every lock; any failure is a plain 404."""
        key = _parse_id((query.get("key") or [""])[0])
        if key is None or _is_sample() or not config.PLEX_TOKEN:
            return self._send(404, "Not found", "text/plain")
        thumb = plex_thumb(key)
        if not thumb:
            return self._send(404, "Not found", "text/plain")
        try:
            content_type, data = plex.PlexClient(config.PLEX_URL, config.PLEX_TOKEN).poster(thumb)
        except Exception:
            return self._send(404, "Not found", "text/plain")
        if content_type not in self.POSTER_TYPES or not isinstance(data, bytes):
            return self._send(404, "Not found", "text/plain")
        return self._send(200, data, content_type, headers={"Cache-Control": "private, max-age=86400"})

    def _post_theme(self, form, as_json):
        """Sets the per-device colour theme cookie. Deliberately works in sample mode too: it only
        sets a cookie and never touches db, config or the cached state."""
        key = form.get("theme", [""])[0]
        if not themes.is_valid(key):
            if as_json:
                return self._json({"ok": False, "message": "That isn't a valid theme"}, 400)
            return self._redirect(_safe_path(form.get("return_to", ["/appearance"])[0], "/appearance"),
                                  "That isn't a valid theme")
        message = f"Theme set to {themes.get(key)['label']}"
        cookie = {"Set-Cookie": themes.set_cookie_value(key)}
        if as_json:
            return self._json({"ok": True, "message": message, "theme": key}, headers=cookie)
        return self._redirect(_safe_path(form.get("return_to", ["/appearance"])[0], "/appearance"),
                              message, headers=cookie)

    def _post_rate(self, form, return_to, as_json):
        """Your own 1-5 star rating of something you've watched (0 clears it). Local only - nothing
        is ever written to Plex. Feeds the taste profile at the next rebuild."""
        media_type, tmdb_id = _item_from(form)
        if media_type is None:
            if as_json:
                return self._json({"ok": False, "message": "That isn't a valid title"}, 400)
            return self._redirect(return_to)
        raw = form.get("stars", [""])[0]
        if raw not in ("0", "1", "2", "3", "4", "5"):
            if as_json:
                return self._json({"ok": False, "message": "Pick a rating from 1 to 5"}, 400)
            return self._redirect(return_to)
        if _is_sample():  # sample ids are real TMDB ids - never store ratings against them
            if as_json:
                return self._json({"ok": False, "message": SAMPLE_MESSAGE})
            return self._redirect(return_to, SAMPLE_MESSAGE)
        stars = int(raw)
        if stars:
            db.set_rating(media_type, tmdb_id, stars)
        else:
            db.clear_rating(media_type, tmdb_id)
        mark_ratings_changed()
        with _status_lock:
            snapshot = (_state["result"] or {}).get("watched") or []
        known = next((w for w in snapshot if (w["media_type"], w["tmdb_id"]) == (media_type, tmdb_id)), None)
        stub = title_stub(media_type, tmdb_id, fetch=False)
        if stub:
            title = stub["title"]
            message = f'Rated "{title}" {stars}/5' if stars else f'Cleared your rating for "{title}"'
        else:
            message = f"Rated {stars}/5" if stars else "Rating cleared"
        if as_json:
            return self._json({"ok": True, "message": message, "rating": {
                "type": media_type, "id": tmdb_id, "stars": stars or None,
                "plex_stars": profile.stars_from_ten(known.get("user_rating")) if known else None}})
        return self._redirect(return_to, message)

    def _post_dismiss(self, dismiss, form, return_to, as_json):
        """"Not interested" and its Undo. Without JS, a dismiss redirects back with undo_type/undo_id
        so the page can offer an Undo form; an undismiss just goes back."""
        media_type, tmdb_id = _item_from(form)
        if media_type is None:
            if as_json:
                return self._json({"ok": False, "message": "That isn't a valid title"}, 400)
            return self._redirect(return_to)
        if _is_sample():  # fake sample ids must never reach the real dismissed list
            if as_json:
                return self._json({"ok": False, "message": SAMPLE_MESSAGE})
            return self._redirect(return_to)
        if dismiss:
            item = _find_item(media_type, tmdb_id)
            db.dismiss(media_type, tmdb_id)
            forget(media_type, tmdb_id)
            if as_json:
                return self._json({"ok": True, "message": f'Hidden "{item["title"]}"' if item else "Hidden",
                                   "undo": {"type": media_type, "id": tmdb_id}})
            return self._redirect(return_to, extra={"undo_type": media_type, "undo_id": tmdb_id})
        db.undismiss(media_type, tmdb_id)
        item = restore(media_type, tmdb_id)
        if as_json:
            return self._json({"ok": True, "message": f'Restored "{item["title"]}"' if item
                               else "Restored - it'll be back after the next refresh"})
        return self._redirect(return_to)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}")


def make_server(host="0.0.0.0", port=8080):
    return ThreadingHTTPServer((host, port), Handler)


def serve():
    server = make_server(os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "8080")))
    print(f"Listening on port {server.server_address[1]}")
    server.serve_forever()


if __name__ == "__main__":
    # Run from the importable "web" module, not this __main__ copy: pages.py does `import web`, and
    # without this it would get a second copy of the module with its own empty _state.
    import web
    web.serve()
