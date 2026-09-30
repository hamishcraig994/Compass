"""Web page with your recommendations.

Run:  python3 web.py          then open http://localhost:8080
Settings (environment variables): PORT (default 8080), HOST (default 0.0.0.0 = reachable from other
machines on your network), SAMPLE=1 to force sample data. There is no login, so keep it on your home network."""
import json
import os
import threading
import time
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse

import config
import db
import profile
import settings_page
import sources

SHOW = 24                  # suggestions per page
CACHE_SECONDS = 3600       # reuse the last result for an hour (finding suggestions takes a while)
REFRESH_MIN_SECONDS = 600  # "Refresh" is ignored if the data is newer than this, to be gentle on Plex/TMDB
SUBTABS = (("all", "All"), ("new", "New & trending"), ("movie", "Movies"), ("tv", "TV shows"))
# The sidebar/bottom-nav's top-level sections. "recommended" covers all four SUBTABS above.
NAV_SECTIONS = (("home", "Home", "/"), ("recommended", "Recommended", "/recommended"),
                ("library", "Library", "/library"), ("ai", "AI", "/ai"), ("settings", "Settings", "/settings"))
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
_build = {"pending": False, "running": False, "started": None, "error": None, "failed_at": 0.0, "generation": 0}
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
    return stale or (refresh and age > REFRESH_MIN_SECONDS)


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
    for attempt in range(attempts):
        _start_job()
        with _status_lock:
            _build.update(running=True, started=time.time())
            generation = _build["generation"]
        try:
            try:
                result = _hide_dismissed(sources.run(_is_sample()))
            except Exception as e:
                with _status_lock:
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


def _web_url(url):
    """Only plain http(s) links go into the page (never javascript: and friends)."""
    return url if url and url.startswith(("https://", "http://")) else None


def _hidden_fields(item, return_to):
    return (f'<input type="hidden" name="type" value="{escape(item["media_type"])}">'
            f'<input type="hidden" name="id" value="{int(item["tmdb_id"])}">'
            f'<input type="hidden" name="return_to" value="{escape(return_to)}">')


def _title_text(item):
    """Plain (unescaped) "Title (Year)" - escape it wherever it goes into the page."""
    return str(item["title"]) + (f' ({item["year"]})' if item.get("year") else "")


def _poster_html(item):
    """The real poster, or a tinted placeholder carrying the title (sample data has no posters)."""
    poster_url = _web_url(item.get("poster_url"))
    if poster_url:
        return f'<img class="poster" src="{escape(poster_url, quote=True)}" alt="" loading="lazy">'
    hue = (int(item["tmdb_id"]) * 47) % 360
    return f'<div class="poster poster-empty" style="--h:{hue}" aria-hidden="true">{escape(item["title"])}</div>'


def _card_key(item):
    return f'{escape(item["media_type"], quote=True)}-{int(item["tmdb_id"])}'


def _card(item, return_to, can_dismiss):
    """Poster-first card. The reason, chips and full overview sit in a <details> (works without
    JS); app.js opens the same content in a modal instead."""
    link = _web_url(item.get("url"))
    title = escape(_title_text(item))
    kind = "Movie" if item["media_type"] == "movie" else "TV"
    badges = "".join(f'<span class="badge{" alt" if flag == "trending" else ""}">{label}</span>'
                     for flag, label in (("new", "New"), ("trending", "Trending")) if item.get(flag))
    chips = "".join(f'<span class="chip">{escape(m)}</span>' for m in item.get("matches") or [])
    reason = escape(item.get("reason") or "")
    overview = escape(item.get("overview") or "No overview available.")
    ext = (f'<a class="ext-link" href="{escape(link, quote=True)}" target="_blank" rel="noopener noreferrer">'
           f'More on TMDB<span class="visually-hidden"> (opens in a new tab)</span> &#8599;</a>' if link else "")
    details = (f'<details class="card-details"><summary>Details<span class="visually-hidden">: {title}</span></summary>'
               f'<div class="detail-body">'
               + (f'<p class="reason">{reason}</p>' if reason else "")
               + (f'<div class="chips">{chips}</div>' if chips else "")
               + f'<p class="overview">{overview}</p>{ext}</div></details>')

    can_add = can_dismiss and ((item["media_type"] == "movie" and config.radarr_configured())
                               or (item["media_type"] == "tv" and config.sonarr_configured()))
    actions = ""
    if can_dismiss:
        add_button = ""
        if can_add:
            # A real page nav, not an inline dialog - so quality profiles are only ever fetched
            # when this is actually clicked, not on every render of this list (see render_add_dialog).
            # app.js opens the same URL (plus partial=1) in a modal instead.
            dialog_url = "/add-dialog?" + urlencode({"type": item["media_type"], "id": item["tmdb_id"],
                                                     "return_to": return_to})
            add_button = f'<a class="btn-add" href="{escape(dialog_url, quote=True)}" data-add-dialog>Add to library</a>'
        actions = (f'<div class="card-actions">{add_button}'
                   f'<form class="inline" method="post" action="/dismiss" data-enhance="dismiss">'
                   f'{_hidden_fields(item, return_to)}<button type="submit">Not interested</button></form></div>')
    return (f'<article class="card" data-card="{_card_key(item)}">'
            f'<div class="card-poster" data-open-detail>{_poster_html(item)}'
            f'<div class="poster-top"><span class="match">{int(item["match"])}% match</span>'
            f'<span class="badges">{badges}</span></div><span class="kind">{kind}</span></div>'
            f'<div class="card-info"><h3 class="title">{title}</h3>{details}{actions}</div></article>')


def _library_card(item):
    link = _web_url(item.get("url"))
    title = escape(_title_text(item))
    if link:
        title = f'<a href="{escape(link, quote=True)}" target="_blank" rel="noopener noreferrer">{title}</a>'
    kind = "Movie" if item["media_type"] == "movie" else "TV"
    added_date = (item.get("added_at") or "")[:10] or "unknown date"
    return (f'<article class="card"><div class="card-poster">{_poster_html(item)}<span class="kind">{kind}</span></div>'
            f'<div class="card-info"><h3 class="title">{title}</h3>'
            f'<p class="added">Added {escape(added_date)}</p></div></article>')


def _in_tab(item, tab):
    if tab == "new":
        return bool(item.get("new") or item.get("trending"))
    return tab == "all" or item["media_type"] == tab


def _subtabs_html(active):
    return "".join(f'<a class="subtab{" on" if key == active else ""}" href="/recommended?{urlencode({"type": key})}">'
                   f'{escape(label)}</a>' for key, label in SUBTABS)


def _configured_profiles(client_factory):
    """Fetches quality profiles once per page render (not once per card) - None if not
    configured, [] if configured but unreachable right now (the modal still works, just
    without extra choices beyond the configured default)."""
    client = client_factory()
    if client is None:
        return None
    try:
        return [(p["id"], p["name"]) for p in client.quality_profiles()]
    except Exception:
        return []


def _age_text(age):
    minutes = int((age or 0) // 60)
    if minutes < 1:
        return "just now"
    return f"{minutes} min ago" if minutes < 120 else f"{minutes // 60} h ago"


def _refresh_form(return_to, label="Refresh"):
    return (f'<form class="inline" method="post" action="/refresh" data-enhance="refresh">'
            f'<input type="hidden" name="return_to" value="{escape(return_to)}">'
            f'<button type="submit">{escape(label)}</button></form>')


# Shown in the page subtitle while a stale result is on screen and a rebuild is running.
UPDATING_HTML = ('<span class="updating" data-poll="stale">'
                 '<span class="spinner sm" aria-hidden="true"></span>Updating...</span>')


def _waiting_screen(kind):
    """kind "recs": the first build after a restart; "ai": an AI batch being generated. app.js
    polls /api/status and reloads when it's done; without JS, _shell's <noscript> refresh does."""
    if kind == "ai":
        heading = "Asking your AI for ideas..."
        text = "This usually takes under a minute. This page updates itself when they're ready."
    else:
        heading = "Finding your recommendations..."
        text = ("This takes a minute or two after a restart - reading your watch history and asking "
                "TMDB for ideas. This page updates itself when they're ready.")
    return (f'<section class="building" data-poll="{kind}" aria-busy="true">'
            f'<div class="spinner" aria-hidden="true"></div><h3>{heading}</h3><p>{text}</p>'
            f'<p class="building-error" data-poll-error role="alert" hidden></p>'
            f'<div class="skeleton-row" aria-hidden="true"><span></span><span></span><span></span><span></span></div>'
            f'</section>')


def _error_screen(error, return_to, heading="Couldn't get recommendations"):
    return (f'<section class="empty"><h3>{escape(heading)}</h3>'
            f'<p class="building-error">{escape(error or "Something went wrong.")}</p>'
            f'{_refresh_form(return_to, "Try again")}</section>')


def _undo_note(undo, return_to, msg=""):
    """The no-JS Undo after "Not interested": a note with a form POSTing back to /undismiss."""
    try:
        media_type, tmdb_id = undo[0], int(undo[1])
    except (TypeError, ValueError, IndexError):
        return ""
    if media_type not in ("movie", "tv"):
        return ""
    return (f'<div class="note undo-note" role="status"><span>{escape(msg or "Removed.")}</span>'
            f'<form class="inline" method="post" action="/undismiss" data-enhance="undismiss">'
            f'<input type="hidden" name="type" value="{escape(media_type)}">'
            f'<input type="hidden" name="id" value="{tmdb_id}">'
            f'<input type="hidden" name="return_to" value="{escape(return_to)}">'
            f'<button type="submit" class="link-btn">Undo</button></form></div>')


def _message_notes(msg, undo, return_to):
    if undo:
        note = _undo_note(undo, return_to, msg)
        if note:
            return note
    return f'<p class="note" role="status">{escape(msg)}</p>' if msg else ""


def _stale_error_note(status):
    """build_status() can be "ready" with an error: an older list is showing, the last rebuild failed."""
    if not status.get("error") or status.get("state") == "building":
        return ""
    return (f'<p class="note muted-note">Showing your last list - the most recent refresh didn\'t finish '
            f'({escape(str(status["error"]))}).</p>')


def _status():
    """build_status(), or a harmless "ready" if it fails - the page should still render."""
    try:
        return build_status()
    except Exception:
        return {"state": "ready", "has_result": False, "error": None, "ai": {"state": "idle", "error": None}}


def render_recommended(tab="all", refresh=False, msg="", undo=None):
    """refresh is accepted for compatibility but unused: POST /refresh starts rebuilds now."""
    return_to = f"/recommended?{urlencode({'type': tab})}"
    subtabs = f'<nav class="subtabs" aria-label="Filter">{_subtabs_html(tab)}</nav>'
    messages = _message_notes(msg, undo, return_to)
    try:
        result, age = get_result_nowait()
    except Exception as e:
        body = subtabs + messages + _error_screen(str(e), return_to)
        return _shell(body, "recommended", return_to=return_to)
    status = _status()
    if result is None:
        if status.get("state") == "error":
            return _shell(subtabs + messages + _error_screen(status.get("error"), return_to), "recommended",
                          "The last attempt failed", return_to=return_to)
        return _shell(subtabs + messages + _waiting_screen("recs"), "recommended", "Getting things ready",
                      return_to=return_to, auto_refresh=True)

    items = [i for i in result["items"] if _in_tab(i, tab)][:SHOW]
    notes = messages + "".join(f'<p class="note">{escape(n)}</p>' for n in result["notes"])
    notes += _stale_error_note(status)
    taste = ""
    top = profile.summary(result["profile"], 5)
    if any(top.values()):
        rows = [("Genres", top["genre"]), ("Themes", top["keyword"]), ("People", top["director"] + top["actor"][:3])]
        taste = ('<div class="taste">' + "".join(
            f'<div><b>{label}</b> {escape(", ".join(names))}</div>' for label, names in rows if names) + "</div>")
    cards = "".join(_card(i, return_to, not result["sample"]) for i in items)
    if items:
        grid = f'<div class="grid" data-grid>{cards}</div>'
    else:
        grid = ('<div class="empty"><h3>Nothing here right now</h3><p>No recommendations found for this filter. '
                'Try another tab, or Refresh to look again.</p></div>')
    body = f'{subtabs}{notes}{taste}{grid}'
    subtitle = f"Based on {result['watched_count']} watched titles - updated {_age_text(age)}"
    building = status.get("state") == "building"
    return _shell(body, "recommended", subtitle, show_refresh=True, return_to=return_to,
                  status_html=UPDATING_HTML if building else "", auto_refresh=building)


def _is_sample():
    return sources.use_sample(os.environ.get("SAMPLE") == "1" or None)


def render_add_dialog(media_type, tmdb_id, return_to, partial=False):
    """A dedicated page (or, with partial=True, just the dialog's inner HTML for app.js's modal):
    quality profiles are fetched here and only here, so viewing the Recommended or AI list never
    pays for a Radarr/Sonarr round-trip you might not need. Looks in both caches (_find_item)
    rather than forcing a recompute - this can be reached from either the Recommended or the AI page."""
    return_to = _safe_path(return_to)
    section = "ai" if return_to.startswith("/ai") else "recommended"
    item = _find_item(media_type, tmdb_id) if not _is_sample() else None
    if item is None:
        inner = (f'<div class="dialog-box"><div class="dialog-head"><div>'
                 f'<h3 id="add-dialog-title">Can&#39;t add this one</h3></div></div>'
                 f'<p class="note">That title isn\'t available to add right now.</p>'
                 f'<div class="card-actions"><a class="btn-ghost" href="{escape(return_to)}" data-close>Back</a></div></div>')
        return inner if partial else _shell(f'<div class="dialog-page">{inner}</div>', section)

    client_factory = sources.radarr_client if media_type == "movie" else sources.sonarr_client
    profiles = _configured_profiles(client_factory)
    options = '<option value="">Default (from Settings)</option>' + "".join(
        f'<option value="{escape(str(p_id))}">{escape(str(p_name))}</option>' for p_id, p_name in (profiles or []))
    service = "Radarr" if media_type == "movie" else "Sonarr"
    poster_url = _web_url(item.get("poster_url"))
    thumb = (f'<div class="thumb"><img src="{escape(poster_url, quote=True)}" alt=""></div>' if poster_url else "")
    inner = (f'<div class="dialog-box"><div class="dialog-head">{thumb}<div>'
             f'<h3 id="add-dialog-title">Add &quot;{escape(item["title"])}&quot; to library</h3>'
             f'<p class="muted">Sends it to {service}</p></div></div>'
             f'<form method="post" action="/add" data-enhance="add">{_hidden_fields(item, return_to)}'
             f'<fieldset><legend class="visually-hidden">Options</legend>'
             f'<label>Quality profile<select name="quality_profile_id">{options}</select></label>'
             f'<label class="checkbox-label"><input type="checkbox" name="search" value="1" checked> '
             f'Search and download immediately</label>'
             f'<p class="muted">Unchecked, it\'s added but left unmonitored - Radarr/Sonarr won\'t '
             f'grab it on their own either, until you turn monitoring on there yourself.</p></fieldset>'
             f'<div class="card-actions"><a href="{escape(return_to)}" class="btn-ghost" data-close>Cancel</a>'
             f'<button type="submit" class="btn-add">Add</button></div></form></div>')
    if partial:
        return inner
    return _shell(f'<div class="dialog-page">{inner}</div>', section, f"Adding to {service}")


def render_home(refresh=False):
    """refresh is accepted for compatibility but unused: POST /refresh starts rebuilds now."""
    try:
        result, age = get_result_nowait()
    except Exception as e:
        return _shell(_error_screen(str(e), "/"), "home", return_to="/")
    status = _status()
    if result is None:
        if status.get("state") == "error":
            return _shell(_error_screen(status.get("error"), "/"), "home", "The last attempt failed", return_to="/")
        return _shell(_waiting_screen("recs"), "home", "Getting things ready", return_to="/", auto_refresh=True)
    tiles = [
        ("Recommended", len(result["items"]), "/recommended"),
        ("Added to library", db.added_count(), "/library"),
        ("Not interested", len(db.dismissed()), None),
        ("Watched titles analyzed", result["watched_count"], None),
    ]
    tiles_html = "".join(
        (f'<a class="tile" href="{href}">' if href else '<div class="tile">')
        + f'<div class="tile-value">{value}</div><div class="tile-label">{escape(label)}</div>'
        + ('</a>' if href else '</div>')
        for label, value, href in tiles)
    notes = "".join(f'<p class="note">{escape(n)}</p>' for n in result["notes"])
    picks = result["items"][:12]
    rail = ""
    if picks:
        # Read-only here (no actions): Home's redirects can't carry a message or an Undo note.
        rail = ('<div class="section-head"><h3>Top picks for you</h3><a href="/recommended">See all &#8250;</a></div>'
                f'<div class="rail">{"".join(_card(i, "/", False) for i in picks)}</div>')
    subtitle = f"Updated {_age_text(age)}"
    notes += _stale_error_note(status)
    building = status.get("state") == "building"
    return _shell(f'{notes}<div class="tiles">{tiles_html}</div>{rail}', "home", subtitle, show_refresh=True,
                  return_to="/", status_html=UPDATING_HTML if building else "", auto_refresh=building)


def render_library():
    items = db.added_items()
    if items:
        body = f'<div class="grid">{"".join(_library_card(i) for i in items)}</div>'
    else:
        body = ('<div class="empty"><h3>Your added list is empty</h3>'
                '<p>Nothing added yet - approve a recommendation from the Recommended page and it\'ll show up here.</p>'
                '<a class="btn-add" href="/recommended">Browse recommendations</a></div>')
    count = len(items)
    subtitle = f"{count} title{'s' if count != 1 else ''} added"
    return _shell(body, "library", subtitle, show_refresh=False)


def _generate_form(label):
    return (f'<form class="inline" method="post" action="/ai/generate" data-enhance="generate">'
            f'<input type="hidden" name="return_to" value="/ai">'
            f'<button type="submit" class="btn-add">{escape(label)}</button></form>')


def render_ai_page(msg="", undo=None):
    """Unlike Recommended/Home, this never computes anything on its own - it only ever shows what
    the last Generate click produced (or nothing, if there hasn't been one yet)."""
    if not config.ai_configured():
        body = ('<div class="empty"><h3>AI isn\'t set up</h3><p class="note">AI isn\'t configured yet - add a '
                'provider and API key in <a href="/settings?section=ai">Settings -&gt; AI</a> first.</p></div>')
        return _shell(body, "ai", show_refresh=False)

    messages = _message_notes(msg, undo, "/ai")
    ai = _status().get("ai") or {}
    if ai.get("state") == "building":
        return _shell(messages + _waiting_screen("ai"), "ai", "Generating", auto_refresh=True)
    error = (f'<p class="note error">The last AI request failed: {escape(ai.get("error") or "unknown error")}</p>'
             if ai.get("state") == "error" else "")

    result = _ai_state["result"]
    generate_form = _generate_form("Generate again" if result else "Generate")
    if result is None:
        body = (f'{messages}{error}<div class="ai-intro"><p>Ask your AI for a batch of ideas grounded in your '
                'taste profile - these are separate from the main Recommended list, and each click is a real '
                f'request to your configured provider.</p>{generate_form}</div>')
        return _shell(body, "ai", show_refresh=False)

    items = result["items"][:SHOW]
    notes = "".join(f'<p class="note">{escape(n)}</p>' for n in result["notes"])
    cards = "".join(_card(i, "/ai", True) for i in items)
    if items:
        grid = f'<div class="grid" data-grid>{cards}</div>'
    else:
        grid = '<div class="empty"><h3>No suggestions this time</h3><p>Try generating again.</p></div>'
    body = (f'{messages}{error}<div class="ai-toolbar">{generate_form}'
            f'<span class="muted">Each click is a real request to your AI provider.</span></div>{notes}{grid}')
    subtitle = f"Generated {_age_text(time.time() - _ai_state['time'])}"
    return _shell(body, "ai", subtitle, show_refresh=False)


# Inline SVG nav icons (fixed strings, no user data).
_NAV_ICONS = {
    "home": '<path d="M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    "recommended": '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
    "library": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
    "ai": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    "settings": '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/>'
                '<circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
}


def _nav_html(section):
    links = []
    for key, label, href in NAV_SECTIONS:
        active, current = (" active", ' aria-current="page"') if key == section else ("", "")
        links.append(f'<a class="nav-item{active}" href="{href}"{current}>'
                     f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
                     f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
                     f'{_NAV_ICONS.get(key, "")}</svg><span>{escape(label)}</span></a>')
    return "".join(links)


def _shell(body, section, subtitle="", show_refresh=False, return_to="/", status_html="", auto_refresh=False):
    """status_html: trusted markup (e.g. UPDATING_HTML) appended to the subtitle. auto_refresh: the
    no-JS fallback for the building screens - app.js polls /api/status instead."""
    heading = dict((key, label) for key, label, _ in NAV_SECTIONS).get(section, "What's Next")
    nav_html = _nav_html(section)
    refresh = _refresh_form(return_to) if show_refresh else ""
    meta_refresh = '<noscript><meta http-equiv="refresh" content="5"></noscript>' if auto_refresh else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>{escape(heading)} - What&#39;s Next</title>'
            f'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            f'<meta name="color-scheme" content="dark light"><meta name="theme-color" content="#0a0c11">'
            f'<link rel="stylesheet" href="/static/app.css"><script src="/static/app.js" defer></script>'
            f'{meta_refresh}</head><body>'
            f'<a class="skip-link" href="#main">Skip to content</a>'
            f'<div class="app-shell">'
            f'<aside class="app-sidebar"><div class="brand-lockup"><span class="brand-mark" aria-hidden="true">W</span>'
            f'<div><h1>What&#39;s Next</h1><p>Movies &amp; TV, just for you</p></div></div>'
            f'<nav class="nav-list" aria-label="Main">{nav_html}</nav></aside>'
            f'<main class="app-main" id="main" tabindex="-1">'
            f'<div class="top"><div><h2>{escape(heading)}</h2><p class="sub">{escape(subtitle)}{status_html}</p></div>'
            f'<div class="top-actions">{refresh}</div></div>'
            f'{body}</main></div>'
            f'<nav class="bottom-nav" aria-label="Main">{nav_html}</nav>'
            f'<div class="toasts" id="toasts" role="status" aria-live="polite"></div>'
            f'</body></html>')


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
    return (media_type, tmdb_id) if media_type in ("movie", "tv") and tmdb_id is not None else (None, None)


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


class Handler(BaseHTTPRequestHandler):
    # Served by name from this fixed list - the request path is never joined onto a filesystem
    # path, so there's nothing to traverse.
    STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    STATIC_FILES = {"app.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8"}

    def _send(self, status, body, content_type="text/html; charset=utf-8", headers=None):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, path, msg=None, extra=None):
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
        self._send(303, "", headers={"Location": path})

    def _wants_json(self):
        """The JS sends Accept: application/json; a plain form post (no JS) never does."""
        return "application/json" in (self.headers.get("Accept") or "")

    def _json(self, payload, status=200):
        self._send(status, json.dumps(payload), "application/json", headers={"Cache-Control": "no-store"})

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
        if url.path == "/":
            return self._send(200, render_home())
        if url.path == "/recommended":
            query = parse_qs(url.query)
            tab = query.get("type", ["all"])[0]
            msg = query.get("msg", [""])[0]
            undo = _item_from(query, "undo_type", "undo_id")
            return self._send(200, render_recommended(tab if tab in dict(SUBTABS) else "all", msg=msg,
                                                      undo=undo if undo[0] else None))
        if url.path == "/library":
            return self._send(200, render_library())
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
        if _cross_site(self.headers):  # before reading the body - see _cross_site()
            if self._wants_json():
                return self._json({"ok": False, "message": "Blocked: request came from another site"}, 403)
            return self._send(403, "Blocked: request came from another site", "text/plain; charset=utf-8")
        length = min(int(self.headers.get("Content-Length") or 0), 4096)
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
        if path == "/add":
            media_type, tmdb_id = _item_from(form)
            if media_type is None:
                if as_json:
                    return self._json({"ok": False, "message": "That isn't a valid title"}, 400)
                return self._redirect(return_to)
            if _is_sample():
                if as_json:
                    return self._json({"ok": False, "message": SAMPLE_MESSAGE})
                return self._redirect(return_to)
            item = _find_item(media_type, tmdb_id)
            quality_profile_id = _parse_id(form.get("quality_profile_id", [""])[0])
            search = form.get("search", [""])[0] == "1"
            try:
                ok, msg = sources.add_to_library(media_type, tmdb_id, search=search,
                                                 quality_profile_id=quality_profile_id)
            except Exception as e:
                ok, msg = False, f"Couldn't add it: {e}"
            if ok:
                if item:
                    db.record_added(item)
                forget(media_type, tmdb_id)
            if as_json:
                return self._json({"ok": bool(ok), "message": msg or ""})
            return self._redirect(return_to, msg)
        return self._send(404, "Not found", "text/plain")

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


if __name__ == "__main__":
    server = make_server(os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "8080")))
    print(f"Listening on port {server.server_address[1]}")
    server.serve_forever()
