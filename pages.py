"""The HTML pages: every render_* function plus the card/shell helpers they share.

Frontend-owned (see .claude/ownership.json); web.py owns state, the HTTP handler and validation.
Reads state only through the web module (web.get_result_nowait(), web._ai_state, ...), never by
importing names from it, so tests that patch web.<name> affect rendering too."""
import re
import time
import zlib
from html import escape
from urllib.parse import quote, urlencode

import config
import profile
import settings_page
import sources
import themes

SHOW = 24                  # suggestions per page
# The top bar's / bottom nav's sections (Settings sits in the top bar only).
NAV_SECTIONS = (("home", "Home", "/"), ("movies", "Movies", "/movies"), ("tv", "TV", "/tv"),
                ("library", "Library", "/library"), ("ai", "AI picks", "/ai"), ("settings", "Settings", "/settings"))
BROWSE_PAGES = {"all": ("home", "/"), "movie": ("movies", "/movies"), "tv": ("tv", "/tv")}  # kind -> (section, return_to)


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
    """The real poster (TMDB url, else the server-side Plex proxy by ratingKey), or a tinted
    placeholder carrying the title (sample data has no posters)."""
    poster_url = _web_url(item.get("poster_url"))
    if poster_url:
        return f'<img class="poster" src="{escape(poster_url, quote=True)}" alt="" loading="lazy">'
    try:
        poster_key = int(item["poster_key"]) if item.get("poster_key") is not None else None
    except (TypeError, ValueError):
        poster_key = None
    if poster_key is not None:
        return f'<img class="poster" src="/poster?key={poster_key}" alt="" loading="lazy">'
    return f'<div class="poster poster-empty" style="--h:{_hue(item)}" aria-hidden="true">{escape(str(item.get("title") or ""))}</div>'


def _hue(item):
    seed = item.get("tmdb_id")
    try:
        seed = int(seed)
    except (TypeError, ValueError):
        seed = zlib.crc32(str(item.get("title") or "").encode("utf-8"))
    return (seed * 47) % 360


def _card_key(item):
    return f'{escape(item["media_type"], quote=True)}-{int(item["tmdb_id"])}'


def _kind_of(item):
    return item.get("media_type") if item.get("media_type") in ("movie", "tv") else None


def _has_id(item):
    """Whether this item can have a detail page, lists and a rating: a movie/tv type and a positive tmdb_id."""
    try:
        return _kind_of(item) is not None and int(item.get("tmdb_id")) > 0
    except (TypeError, ValueError):
        return False


def _title_url(item):
    """"/title/{movie|tv}/{id}" - the detail page of any card with a tmdb_id."""
    return f"/title/{_kind_of(item)}/{int(item['tmdb_id'])}"


def _poster_linked(item):
    """The poster (or placeholder) wrapped in a link to the title page; unlinked without a tmdb_id."""
    inner = _poster_html(item)
    if not _has_id(item):
        return inner
    label = escape(f"More info: {_title_text(item)}", quote=True)
    return f'<a class="poster-link" href="{escape(_title_url(item), quote=True)}" aria-label="{label}">{inner}</a>'


def _title_link(item, text):
    """Card title text (already escaped), linked to the title page when the item has a tmdb_id."""
    if not _has_id(item):
        return text
    return f'<a href="{escape(_title_url(item), quote=True)}">{text}</a>'


_ICON_ATTRS = ('class="list-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
               'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"')
_BOOKMARK_SVG = f'<svg {_ICON_ATTRS}><path d="M6 4h12a1 1 0 0 1 1 1v16l-7-4-7 4V5a1 1 0 0 1 1-1z"/></svg>'
_LIST_SVG = f'<svg {_ICON_ATTRS}><path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/></svg>'


def _list_toggle(item, return_to):
    """The Watchlist toggle: a form posting /lists/add or /lists/remove (app.js flips it in place)."""
    on = bool(item.get("on_watchlist"))
    return (f'<form class="inline list-toggle-form" method="post" action="/lists/{"remove" if on else "add"}" '
            f'data-enhance="list">{_hidden_fields(item, return_to)}<input type="hidden" name="list" value="watchlist">'
            f'<button type="submit" class="list-toggle{" on" if on else ""}" aria-pressed="{"true" if on else "false"}">'
            f'{_BOOKMARK_SVG}<span class="list-toggle-text">{"On Watchlist" if on else "Watchlist"}</span></button></form>')


def _list_dialog_url(item, return_to):
    return "/list-dialog?" + urlencode({"type": item["media_type"], "id": int(item["tmdb_id"]), "return_to": return_to})


def _list_link(item, return_to, detail=False):
    """A link to the list dialog page (a modal with JS). On the detail page the label is visible."""
    text = ('<span class="list-link-text">Add to list</span>' if detail
            else '<span class="visually-hidden">Lists</span>')
    name = escape(_title_text(item), quote=True)
    label = f"Add to list: {name}" if detail else f"Add {name} to a list"   # the visible words come first (WCAG 2.5.3)
    return (f'<a class="list-link" href="{escape(_list_dialog_url(item, return_to), quote=True)}" data-list-dialog '
            f'aria-label="{label}">{_LIST_SVG}{text}</a>')


def _card_tools(item, return_to, stars=True, lists=True, extra_class="", link=True):
    """Compact stars, the Watchlist toggle and the list link on one line ("" when the item has no tmdb_id).
    lists=False drops the toggle and the link; link=False drops only the link (the hero)."""
    if not _has_id(item) or not (stars or lists):
        return ""
    parts = ""
    if stars:
        parts += _rate_form(item, return_to, compact=True)
    if lists:
        parts += _list_toggle(item, return_to) + (_list_link(item, return_to) if link else "")
    return f'<div class="card-tools{" " + extra_class if extra_class else ""}">{parts}</div>'


def _card(item, return_to, can_dismiss):
    """Poster-first card. The reason, chips and full overview sit in a <details> (works without
    JS); the poster links to the title page."""
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
            f'<div class="card-poster">{_poster_linked(item)}'
            f'<div class="poster-top"><span class="match">{int(item["match"])}% match</span>'
            f'<span class="badges">{badges}</span></div><span class="kind">{kind}</span></div>'
            f'<div class="card-info"><h3 class="title">{title}</h3>{details}{actions}'
            f'{_card_tools(item, return_to)}</div></article>')


_SOURCE_LABELS = {"plex": "Plex", "radarr": "Radarr", "sonarr": "Sonarr"}
_ARR_STATE_LABELS = {"downloaded": "Downloaded", "partial": "Partly downloaded", "missing": "Missing",
                     "upcoming": "Upcoming", "unmonitored": "Unmonitored"}


def _arr_label(state, episodes=None):
    """Text for a Radarr/Sonarr download state (shared by the Library and search cards), or "" if unknown."""
    if state == "partial" and isinstance(episodes, dict):
        have, total = episodes.get("have"), episodes.get("total")
        if isinstance(have, int) and isinstance(total, int) and total > 0:
            return f"{have}/{total} episodes"
    return _ARR_STATE_LABELS.get(state, "")


_REQ_LABELS = {"requested": "Requested", "processing": "Searching", "upcoming": "Upcoming",
               "partial": "Partly available", "available": "Available", "unmonitored": "Not monitored"}


def _req_label(state, episodes=None):
    if state == "partial" and isinstance(episodes, dict):
        have, total = episodes.get("have"), episodes.get("total")
        if isinstance(have, int) and isinstance(total, int) and total > 0:
            return f"{have}/{total} episodes"
    return _REQ_LABELS.get(state, "")


def _seasons_text(seasons):
    """"All seasons" / "Seasons 1, 3" for a TV request; "" for movies and requests with no season record."""
    if seasons == "all":
        return "All seasons"
    if isinstance(seasons, list):
        numbers = [n for n in seasons if isinstance(n, int) and not isinstance(n, bool)]
        if numbers:
            return ("Season " if len(numbers) == 1 else "Seasons ") + ", ".join(str(n) for n in numbers)
    return ""


def _library_card(item, show_added=False, return_to="/library"):
    """A Plex library (or Requests) card: poster link, title, year, and the stars / Watchlist / list tools."""
    title = _title_link(item, escape(_title_text(item) if show_added else str(item["title"])))
    kind = "Movie" if item["media_type"] == "movie" else "TV"
    badge = ""
    progress = item.get("progress")
    if item.get("watched"):
        badge = '<span class="badge">Watched</span>'
    elif isinstance(progress, (int, float)) and 0 < progress < 1:
        badge = '<span class="badge">Started</span>'
    sources_ = [k for k in (item.get("sources") or ()) if k in _SOURCE_LABELS]
    state = item.get("arr_state")
    state_label = _arr_label(state, item.get("episodes"))
    if state_label and not (state == "downloaded" and "plex" in sources_):
        badge += (f'<span class="badge arr-state state-{escape(str(state), quote=True)}">{escape(state_label)}</span>')
    req_state = item.get("request_state") if show_added else None
    req_label = _req_label(req_state, item.get("episodes"))
    if req_label:
        badge += f'<span class="badge req-state req-{escape(str(req_state), quote=True)}">{escape(req_label)}</span>'
    top = f'<div class="poster-top"><span class="badges">{badge}</span></div>' if badge else ""
    src_line = ""
    if sources_:
        src_line = ('<p class="card-sources"><span class="visually-hidden">In </span>'
                    + " ".join(f'<span class="src src-{k}">{_SOURCE_LABELS[k]}</span>' for k in sources_) + '</p>')
    if show_added:
        sub = f'Added {escape((item.get("added_at") or "")[:10] or "unknown date")}'
    else:
        sub = escape(str(item["year"])) if item.get("year") else ""
    seasons = _seasons_text(item.get("seasons")) if show_added and item.get("media_type") == "tv" else ""
    seasons_line = f'<p class="card-sub req-seasons">{escape(seasons)}</p>' if seasons else ""
    return (f'<article class="card" data-preview><div class="card-poster">{_poster_linked(item)}{top}'
            f'<span class="kind">{kind}</span></div>'
            f'<div class="card-info"><h3 class="title">{title}</h3>'
            f'<p class="card-sub">{sub}</p>{seasons_line}{src_line}{_card_tools(item, return_to)}</div></article>')


def _rate_parts(mine, plex):
    """(stars html, rating text, clear-button html) for a rate form: mine = your 1-5 or None, plex = Plex's 1-5 or None."""
    effective = mine or plex or 0
    from_plex = not mine and bool(plex)
    stars = "".join(
        f'<button type="submit" name="stars" value="{n}" class="star{" on" if n <= effective else ""}'
        f'{" from-plex" if n <= effective and from_plex else ""}" aria-pressed="{"true" if n == mine else "false"}" '
        f'aria-label="Rate {n} out of 5">&#9733;</button>' for n in range(1, 6))
    if mine:
        text = f"Your rating: {int(mine)}/5"
    elif plex:
        text = f"From Plex: {int(plex)}/5"
    else:
        text = "Not rated"
    clear = ('<button type="submit" name="stars" value="0" class="link-btn star-clear">Clear rating</button>'
             if mine else "")
    return stars, text, clear


def _star_value(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= 5 else None


def _watched_card(item, return_to):
    """A watch-history card with 1-5 star buttons (a plain form, so it works without JS)."""
    title_text = _title_text(item)
    title = escape(title_text)
    title_html = _title_link(item, title)
    kind = "Movie" if item["media_type"] == "movie" else "TV"
    sub = f'Watched {escape((item.get("last_viewed") or "")[:10])}' if item.get("last_viewed") else "Watched"
    if (item.get("view_count") or 0) > 1:
        sub += f' &middot; {int(item["view_count"])} plays'
    mine, plex = item.get("stars"), item.get("plex_stars")
    stars, text, clear = _rate_parts(mine, plex)
    form = (f'<form class="rate" method="post" action="/rate" data-enhance="rate">{_hidden_fields(item, return_to)}'
            f'<div class="stars" role="group" aria-label="Your rating for {escape(title_text, quote=True)}">{stars}</div>'
            f'<p class="rating-text">{text}</p>{clear}</form>')
    return (f'<article class="card" data-card="{_card_key(item)}" data-preview><div class="card-poster">{_poster_linked(item)}'
            f'<span class="kind">{kind}</span></div>'
            f'<div class="card-info"><h3 class="title">{title_html}</h3><p class="card-sub">{sub}</p>{form}'
            f'{_card_tools(item, return_to, stars=False)}</div></article>')


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
    """web.build_status() can be "ready" with an error: an older list is showing, the last rebuild failed."""
    if not status.get("error") or status.get("state") == "building":
        return ""
    return (f'<p class="note muted-note">Showing your last list - the most recent refresh didn\'t finish '
            f'({escape(str(status["error"]))}).</p>')


def _status():
    """web.build_status(), or a harmless "ready" if it fails - the page should still render."""
    try:
        return web.build_status()
    except Exception:
        return {"state": "ready", "has_result": False, "error": None, "ai": {"state": "idle", "error": None}}


_SECTION_PREFIXES = (("/ai", "ai"), ("/movies", "movies"), ("/tv", "tv"), ("/library", "library"),
                     ("/lists", "library"), ("/watchlist", "library"), ("/search", "search"),
                     ("/title/movie", "movies"), ("/title/tv", "tv"))


def _section_for(return_to):
    """Which nav item a dialog page highlights: where it was opened from."""
    for prefix, name in _SECTION_PREFIXES:
        if return_to.startswith(prefix):
            return name
    return "home"


_SEASON_STATE_LABELS = {"available": "Available", "missing": "Wanted", "upcoming": "Upcoming",
                        "unmonitored": "Not requested"}


def _season_state_badge(row):
    state = row.get("state")
    if state == "partial":
        have, total = row.get("have"), row.get("total")
        label = (f"{have}/{total} episodes" if isinstance(have, int) and isinstance(total, int) and total > 0
                 else "Partly available")
    else:
        label = _SEASON_STATE_LABELS.get(state)
    if not label:
        return ""
    return f'<span class="badge arr-state season-state state-{escape(str(state), quote=True)}">{escape(label)}</span>'


def _season_eps_text(row):
    """"8 episodes - 2021" for a season row ("" when neither is known)."""
    parts = []
    episodes = row.get("episodes")
    if isinstance(episodes, int) and not isinstance(episodes, bool) and episodes >= 0:
        parts.append(f"{episodes} episode{'s' if episodes != 1 else ''}")
    year = str(row.get("air_date") or "")[:4]
    if len(year) == 4 and year.isdigit():
        parts.append(year)
    return " &middot; ".join(parts)


def _season_picker(choices, form_id):
    """The add dialog's season fieldset: All seasons (or "All remaining" for a tracked series), Choose seasons with
    a checkbox per season (requested ones checked and disabled), or only "all" plus a note when TMDB/Sonarr has no list."""
    rows = list(choices.get("seasons") or []) if choices.get("known") else []
    tracked = bool(choices.get("tracked"))
    fid = escape(str(form_id), quote=True)
    out = [f'<fieldset class="season-picker" id="{fid}-seasons"><legend>Seasons</legend>',
           f'<label class="season-mode"><input type="radio" name="seasons" value="all" checked> '
           f'{"All remaining seasons" if tracked else "All seasons"}</label>']
    if rows:
        items = []
        for row in rows:
            number = int(row["number"])
            requested = bool(row.get("requested")) or row.get("selectable") is False
            eps = _season_eps_text(row)
            items.append(
                f'<li class="season-row"><label class="checkbox-label"><input type="checkbox" class="season-check" '
                f'name="season" value="{number}"{" checked disabled" if requested else ""}> '
                f'{escape(str(row.get("name") or f"Season {number}"))}'
                + (f' <span class="season-eps">{eps}</span>' if eps else "")
                + (' <span class="muted">Requested</span>' if requested else "")
                + f'{_season_state_badge(row)}</label></li>')
        out.append('<label class="season-mode"><input type="radio" name="seasons" value="pick"> Choose seasons</label>'
                   f'<ul class="season-list">{"".join(items)}</ul>')
    else:
        out.append('<p class="muted">Season list unavailable right now - you can still add all seasons.</p>')
    out.append('</fieldset>')
    return "".join(out)


def render_add_dialog(media_type, tmdb_id, return_to, partial=False):
    """A dedicated page (or, with partial=True, just the dialog's inner HTML for app.js's modal):
    quality profiles are fetched here and only here, so viewing the Recommended or AI list never
    pays for a Radarr/Sonarr round-trip you might not need. web.lookup_item checks both recommendation
    caches, then (live mode) TMDB details, so any search result can be added too; it never forces a recompute.
    TV adds the season picker (web.season_choices); a series Sonarr already tracks says "Request more seasons"
    and has no profile select, since the profile can't be changed here."""
    return_to = web._safe_path(return_to)
    section = _section_for(return_to)
    item = web.lookup_item(media_type, tmdb_id) if not web._is_sample() else None
    if item is None:
        inner = (f'<div class="dialog-box"><div class="dialog-head"><div>'
                 f'<h3 id="add-dialog-title">Can&#39;t add this one</h3></div></div>'
                 f'<p class="note">That title isn\'t available to add right now.</p>'
                 f'<div class="card-actions"><a class="btn-ghost" href="{escape(return_to)}" data-close>Back</a></div></div>')
        return inner if partial else _shell(f'<div class="dialog-page">{inner}</div>', section)

    choices = None
    if media_type == "tv":
        try:
            choices = web.season_choices(int(tmdb_id))
        except Exception:
            choices = {"seasons": [], "tracked": False, "known": False}
    tracked = bool(choices and choices.get("tracked"))
    profile_select = ""
    if not tracked:
        client_factory = sources.radarr_client if media_type == "movie" else sources.sonarr_client
        profiles = _configured_profiles(client_factory)
        options = '<option value="">Default (from Settings)</option>' + "".join(
            f'<option value="{escape(str(p_id))}">{escape(str(p_name))}</option>' for p_id, p_name in (profiles or []))
        profile_select = f'<label>Quality profile<select name="quality_profile_id">{options}</select></label>'
    service = "Radarr" if media_type == "movie" else "Sonarr"
    poster_url = _web_url(item.get("poster_url"))
    thumb = (f'<div class="thumb"><img src="{escape(poster_url, quote=True)}" alt=""></div>' if poster_url else "")
    heading = (f'Request more seasons of &quot;{escape(item["title"])}&quot;' if tracked
               else f'Add &quot;{escape(item["title"])}&quot; to library')
    picker = _season_picker(choices, "add") if choices is not None else ""
    inner = (f'<div class="dialog-box"><div class="dialog-head">{thumb}<div>'
             f'<h3 id="add-dialog-title">{heading}</h3>'
             f'<p class="muted">Sends it to {service}</p></div></div>'
             f'<form method="post" action="/add" data-enhance="add">{_hidden_fields(item, return_to)}'
             f'{picker}'
             f'<fieldset><legend class="visually-hidden">Options</legend>'
             f'{profile_select}'
             f'<label class="checkbox-label"><input type="checkbox" name="search" value="1" checked> '
             f'Search and download immediately</label>'
             f'<p class="muted">Unchecked, it\'s added but left unmonitored - Radarr/Sonarr won\'t '
             f'grab it on their own either, until you turn monitoring on there yourself.</p></fieldset>'
             f'<div class="card-actions"><a href="{escape(return_to)}" class="btn-ghost" data-close>Cancel</a>'
             f'<button type="submit" class="btn-add">{"Request" if tracked else "Add"}</button></div></form></div>')
    if partial:
        return inner
    return _shell(f'<div class="dialog-page">{inner}</div>', section, f"Adding to {service}")


def _can_add(item, can_act):
    """Whether this item gets an "Add to library" link: not sample, and its Radarr/Sonarr is set up."""
    return bool(can_act and ((item["media_type"] == "movie" and config.radarr_configured())
                             or (item["media_type"] == "tv" and config.sonarr_configured())))


def _add_dialog_url(item, return_to):
    return "/add-dialog?" + urlencode({"type": item["media_type"], "id": item["tmdb_id"], "return_to": return_to})


def _add_link(item, return_to):
    # A real page nav, not an inline dialog (see _card); app.js opens it in a modal instead.
    return f'<a class="btn-add" href="{escape(_add_dialog_url(item, return_to), quote=True)}" data-add-dialog>Add to library</a>'


def _int_or_none(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _runtime_text(item):
    """"2h 46m" / "46m" for a movie, "3 seasons" / "1 season" for a show, else ""."""
    minutes = _int_or_none(item.get("runtime"))
    if minutes:
        hours, rest = divmod(minutes, 60)
        return " ".join(part for part in (f"{hours}h" if hours else "", f"{rest}m" if rest else "") if part)
    seasons = _int_or_none(item.get("seasons"))
    if seasons:
        return f"{seasons} season{'s' if seasons != 1 else ''}"
    return ""


def _meta_html(item):
    """Inner spans of a .card-meta line: match (only when the item has one), year, certification, runtime / seasons."""
    parts = []
    if item.get("match") is not None:
        try:
            parts.append(f'<span class="match">{int(item["match"])}% match</span>')
        except (TypeError, ValueError):
            pass
    if item.get("year"):
        parts.append(f'<span>{escape(str(item["year"]))}</span>')
    if item.get("certification"):
        parts.append(f'<span class="badge cert">{escape(str(item["certification"]))}</span>')
    runtime = _runtime_text(item)
    if runtime:
        parts.append(f'<span>{escape(runtime)}</span>')
    return "".join(parts)


def _detail_body(item):
    """The .detail-body shared by hero and row cards (app.js clones it into the modal)."""
    link = _web_url(item.get("url"))
    reason = escape(item.get("reason") or "")
    chips = "".join(f'<span class="chip">{escape(m)}</span>' for m in item.get("matches") or [])
    ext = (f'<a class="ext-link" href="{escape(link, quote=True)}" target="_blank" rel="noopener noreferrer">'
           f'More on TMDB<span class="visually-hidden"> (opens in a new tab)</span> &#8599;</a>' if link else "")
    meta = _meta_html(item)
    return ('<div class="detail-body">'
            + (f'<p class="card-meta">{meta}</p>' if meta else "")
            + (f'<p class="reason">{reason}</p>' if reason else "")
            + (f'<div class="chips">{chips}</div>' if chips else "")
            + f'<p class="overview">{escape(item.get("overview") or "No overview available.")}</p>{ext}</div>')


def _rate_form(item, return_to, compact=False):
    """1-5 stars posting to /rate (the same form the Library uses), showing your saved rating (item["stars"]).
    Rating a recommendation never hides it. Shown even in sample mode: the server answers
    "Sample data - not saved" and app.js toasts it. compact: the one-line card variant (text is visually hidden)."""
    title = escape(_title_text(item), quote=True)
    stars, text, clear = _rate_parts(_star_value(item.get("stars")), _star_value(item.get("plex_stars")))
    return (f'<form class="rate{" rate-compact" if compact else ""}" method="post" action="/rate" data-enhance="rate">'
            f'{_hidden_fields(item, return_to)}'
            f'<div class="stars" role="group" aria-label="Your rating for {title}">{stars}</div>'
            f'<p class="rating-text{" visually-hidden" if compact else ""}">{text}</p>{clear}</form>')


def _card_actions(item, return_to, can_act):
    """Add / Not interested / full stars, plus the Watchlist toggle and list link (the hover preview clones them)."""
    add = (_add_link(item, return_to) if _can_add(item, can_act) and (item.get("status") or "none") == "none" else "")
    dismiss = (f'<form class="inline" method="post" action="/dismiss" data-enhance="dismiss">'
               f'{_hidden_fields(item, return_to)}<button type="submit">Not interested</button></form>'
               if can_act else "")
    return (f'<div class="card-actions">{add}{dismiss}{_rate_form(item, return_to)}'
            f'{_card_tools(item, return_to, stars=False)}</div>')


def _row_tag(item):
    if item.get("in_library"):
        return '<span class="lib-tag">In library</span>'
    status = item.get("status")
    if status in _STATUS_LABELS:
        return f'<span class="lib-tag status-tag status-{escape(status, quote=True)}">{_STATUS_LABELS[status]}</span>'
    return ""


def _row_card(item, return_to, can_act, rank=None):
    """A poster card in a row. Reason, overview and actions sit in a <details> (no JS; with JS the hover
    preview shows them); the poster links to the title page."""
    title = escape(_title_text(item))
    rank_html = f'<span class="rank" aria-hidden="true">{int(rank)}</span>' if rank is not None else ""
    match = _meta_match(item)
    return (f'<article class="card row-card" data-card="{_card_key(item)}">'
            f'<div class="card-poster">{rank_html}{_poster_linked(item)}'
            f'<div class="poster-top">{match}{_row_tag(item)}</div></div>'
            f'<div class="card-info"><h4 class="title">{title}</h4>'
            f'<details class="card-details"><summary>Details<span class="visually-hidden">: {title}</span></summary>'
            f'{_detail_body(item)}{_card_actions(item, return_to, can_act)}</details></div></article>')


def _meta_match(item, tag="span", cls="match"):
    try:
        return f'<{tag} class="{cls}">{int(item["match"])}% match</{tag}>' if item.get("match") is not None else ""
    except (TypeError, ValueError):
        return ""


def _library_row_card(item, return_to="/"):
    """A "New in your library" card: poster link, tag, title, and the tools (hover preview; visible without JS)."""
    title = _title_link(item, escape(_title_text(item)))
    return (f'<article class="card row-card lib-card" data-preview><div class="card-poster">{_poster_linked(item)}</div>'
            f'<span class="lib-tag">In library</span>'
            f'<div class="card-info"><h4 class="title">{title}</h4>{_card_tools(item, return_to)}</div></article>')


def _row_html(row, return_to, can_act, extra_class=""):
    rid = escape(str(row["id"]), quote=True)
    if row.get("source") == "library":
        cards = "".join(_library_row_card(i, return_to) for i in row["items"])
    else:
        numbered = row.get("numbered")
        cards = "".join(_row_card(i, return_to, can_act, n if numbered else None)
                        for n, i in enumerate(row["items"], 1))
    sub = f'<p class="row-sub">{escape(row["subtitle"])}</p>' if row.get("subtitle") else ""
    return (f'<section class="row{" " + extra_class if extra_class else ""}" data-row="{rid}" aria-labelledby="row-{rid}">'
            f'<div class="row-head"><h3 id="row-{rid}">{escape(row["title"])}</h3>{sub}</div>'
            f'<div class="track-wrap"><div class="track{" track-numbered" if row.get("numbered") else ""}" data-track>'
            f'{cards}</div></div></section>')


def _hero_art(item):
    backdrop = _web_url(item.get("backdrop_url"))
    poster = _web_url(item.get("poster_large_url")) or _web_url(item.get("poster_url"))
    imgs = ""
    if backdrop:
        imgs += f'<img class="hero-backdrop" src="{escape(backdrop, quote=True)}" alt="" loading="lazy">'
    if poster:
        imgs += f'<img class="hero-poster" src="{escape(poster, quote=True)}" alt="" loading="lazy">'
    if not imgs:
        imgs = f'<span class="hero-glyph">{escape(str(item.get("title") or "?")[:1])}</span>'
    return f'<div class="hero-art" style="--h:{_hue(item)}" aria-hidden="true">{imgs}</div>'


def _hero_slide(item, n, total, return_to, can_act):
    title = escape(str(item["title"]))
    genres = " &middot; ".join(escape(str(g)) for g in (item.get("genres") or [])[:3])
    reason = escape(item.get("reason") or "")
    if item.get("in_library"):
        primary = '<span class="lib-tag">In library</span>'
    else:
        primary = _add_link(item, return_to) if _can_add(item, can_act) else ""
    more = (f'<a class="btn-ghost hero-more" href="{escape(_title_url(item), quote=True)}">More info'
            f'<span class="visually-hidden">: {title}</span></a>' if _has_id(item) else "")
    return (f'<article class="hero-slide{" on" if n == 1 else ""}" data-slide data-card="{_card_key(item)}" '
            f'aria-label="{n} of {total}: {escape(str(item["title"]), quote=True)}"{"" if n == 1 else " hidden"}>'
            f'{_hero_art(item)}'
            f'<div class="hero-copy"><p class="kicker">Top pick for you</p>'
            f'<h3 class="title hero-title">{title}</h3>'
            f'<p class="hero-meta card-meta">{_meta_html(item)}'
            + (f'<span class="hero-genres">{genres}</span>' if genres else "")
            + '</p>'
            + (f'<p class="reason">{reason}</p>' if reason else "")
            + f'<div class="hero-actions">{primary}{more}'
            f'{_card_tools(item, return_to, stars=False, extra_class="hero-tools", link=False)}</div></div></article>')


def _hero_html(items, return_to, can_act):
    if not items:
        return ""
    slides = "".join(_hero_slide(i, n, len(items), return_to, can_act) for n, i in enumerate(items, 1))
    return (f'<section class="hero" data-hero aria-label="Top picks for you">'
            f'<div class="hero-slides" data-hero-slides>{slides}</div></section>')


def _taste_html(result):
    top = profile.summary(result.get("profile") or {"genre": {}, "keyword": {}, "director": {}, "actor": {}}, 5)
    if not any(top.values()):
        return ""
    rows = [("Genres", top["genre"]), ("Themes", top["keyword"]), ("People", top["director"] + top["actor"][:3])]
    return ('<div class="taste">' + "".join(
        f'<div><b>{label}</b> {escape(", ".join(names))}</div>' for label, names in rows if names) + "</div>")


def render_browse(kind="all", msg="", undo=None):
    """Home / Movies / TV: a hero of the top picks, then headed rows (see browse.py). Reads state
    only through web.*; never starts an AI generation."""
    kind = kind if kind in BROWSE_PAGES else "all"
    section, return_to = BROWSE_PAGES[kind]
    messages = _message_notes(msg, undo, return_to)
    try:
        result, age = web.get_result_nowait()
    except Exception as e:
        return _shell(messages + _error_screen(str(e), return_to), section, return_to=return_to)
    status = _status()
    if result is None:
        if status.get("state") == "error":
            return _shell(messages + _error_screen(status.get("error"), return_to), section,
                          "The last attempt failed", return_to=return_to)
        return _shell(messages + _waiting_screen("recs"), section, "Getting things ready",
                      return_to=return_to, auto_refresh=True)

    view = web.browse_view(result, kind)
    building = status.get("state") == "building"
    if not view["total"]:
        body = (f'{messages}<div class="empty"><h3>Nothing to recommend here yet</h3>'
                f'<p>No recommendations found for this page. Refresh to look again.</p>'
                f'{_refresh_form(return_to)}</div>')
        return _shell(body, section, return_to=return_to, status_html=UPDATING_HTML if building else "",
                      auto_refresh=building)

    can_act = not result.get("sample")
    parts = [f'<div class="browse-messages">{messages}</div>' if messages else "",
             _hero_html(view["hero"], return_to, can_act)]
    if view["rows"]:
        parts.append('<div class="rows">' + "".join(_row_html(r, return_to, can_act) for r in view["rows"]) + '</div>')
    foot = "".join(f'<p class="note">{escape(n)}</p>' for n in result.get("notes") or [])
    foot += _stale_error_note(status) + _taste_html(result)
    if foot:
        parts.append(f'<div class="browse-foot">{foot}</div>')
    subtitle = f"Based on {result.get('watched_count', 0)} watched titles - updated {_age_text(age)}"
    return _shell("".join(parts), section, subtitle, show_refresh=True, return_to=return_to,
                  status_html=UPDATING_HTML if building else "", auto_refresh=building, cinematic=True)


def render_home(msg="", undo=None):
    return render_browse("all", msg, undo)


LIBRARY_TABS = (("all", "All"), ("movie", "Movies"), ("tv", "TV shows"), ("watched", "Watched"),
                ("added", "Requests"))
_SORT_LABELS = {"added": "Recently added", "title": "Title A-Z", "year": "Newest year", "recent": "Recently watched",
                "rating": "Highest rated"}
_SHOW_LABELS = {"all": "Everything", "unwatched": "Unwatched", "watched": "Watched", "rated": "Rated by you",
                "unrated": "Not rated by you", "open": "Not available yet", "available": "Available"}


def _library_url(tab, q="", sort=None, show=None, page=1, source=None):
    params = [("type", tab)]
    if q:
        params.append(("q", q))
    if sort:
        params.append(("sort", sort))
    if show:
        params.append(("show", show))
    if source and source != "all":
        params.append(("source", source))
    if page and page > 1:
        params.append(("page", page))
    return "/library?" + urlencode(params)


def _library_subtabs(active, q):
    """The Library's tabs plus "Lists" (/lists and /lists/<id> render under Library with it on)."""
    links = []
    for key, label in LIBRARY_TABS:
        params = {"type": key}
        if q:
            params["q"] = q
        links.append(f'<a class="subtab{" on" if key == active else ""}" '
                     f'href="/library?{escape(urlencode(params))}">{escape(label)}</a>')
    links.append(f'<a class="subtab{" on" if active == "lists" else ""}" href="/lists">Lists</a>')
    return f'<nav class="subtabs" aria-label="Library">{"".join(links)}</nav>'


def _source_labels(tab):
    arr = {"movie": "In Radarr", "tv": "In Sonarr"}.get(tab, "In Radarr or Sonarr")
    return {"all": "Everywhere", "plex": "In Plex", "arr": arr, "wanted": "Wanted (not downloaded)"}


def _library_toolbar(tab, q, sort, show, sorts, shows, source="all", sources_=("all",)):
    def select(name, label, options, labels, current):
        opts = "".join(f'<option value="{escape(o, quote=True)}"{" selected" if o == current else ""}>'
                       f'{escape(labels.get(o, o))}</option>' for o in options)
        return f'<label>{label}<select name="{name}">{opts}</select></label>'
    show_select = select("show", "Show", shows, _SHOW_LABELS, show) if len(shows) > 1 else ""
    source_select = select("source", "Source", sources_, _source_labels(tab), source) if len(sources_) > 1 else ""
    return (f'<form class="toolbar" method="get" action="/library" role="search">'
            f'<input type="hidden" name="type" value="{escape(tab, quote=True)}">'
            f'<label>Search<input type="search" name="q" value="{escape(q, quote=True)}" maxlength="100" '
            f'placeholder="Title"></label>'
            f'{select("sort", "Sort by", sorts, _SORT_LABELS, sort)}{show_select}{source_select}'
            f'<button type="submit" class="btn-ghost">Apply</button></form>')


def _pager(href_for, page, pages):
    """Previous / Page n of m / Next; href_for(page_number) -> an unescaped URL."""
    if pages <= 1:
        return ""
    def link(target, label, rel):
        return f'<a href="{escape(href_for(target), quote=True)}" rel="{rel}">{label}</a>'
    prev = link(page - 1, "&#8249; Previous", "prev") if page > 1 else '<span class="muted">&#8249; Previous</span>'
    nxt = link(page + 1, "Next &#8250;", "next") if page < pages else '<span class="muted">Next &#8250;</span>'
    return (f'<nav class="pager" aria-label="Pages">{prev}<span class="pager-status">Page {page} of {pages}</span>'
            f'{nxt}</nav>')


def _library_pager(tab, q, sort, show, page, pages, source="all"):
    return _pager(lambda target: _library_url(tab, q, sort, show, target, source), page, pages)


def _library_empty(tab, filtered, q=""):
    if filtered:
        search = ""
        if q and tab in ("all", "movie", "tv"):
            search = (f'<a class="btn-ghost" href="{escape("/search?" + urlencode({"q": q}), quote=True)}">'
                      f'Search all movies and TV for &quot;{escape(q)}&quot;</a>')
        return ('<div class="empty"><h3>Nothing matches</h3><p>No titles match your search or filters.</p>'
                f'<a class="btn-ghost" href="{escape(_library_url(tab), quote=True)}">Clear filters</a>{search}</div>')
    if tab == "added":
        return ('<div class="empty"><h3>No requests yet</h3>'
                '<p>Nothing added yet - add a recommendation to your library and it\'ll show up under Requests.</p>'
                '<a class="btn-add" href="/recommended">Browse recommendations</a></div>')
    if tab == "watched":
        return ('<div class="empty"><h3>Nothing watched yet</h3>'
                '<p>Titles you watch in Plex show up here, ready to rate.</p></div>')
    return ('<div class="empty"><h3>Your library is empty</h3>'
            '<p>Nothing in Plex, Radarr or Sonarr yet.</p></div>')


def render_library(tab="all", q="", sort=None, show="all", page=1, msg="", source="all"):
    """Library: All / Movies / TV shows (the Plex library), Watched (history, with ratings) and
    Requests (the log of what was sent to Radarr/Sonarr, with its status). Data comes from the last build's
    snapshot - this never calls Plex."""
    tab = tab if tab in dict(LIBRARY_TABS) else "all"
    sorts, shows = web.LIST_OPTIONS[tab]
    sort = sort if sort in sorts else sorts[0]
    show = show if show in shows else shows[0]
    source_options = web.LIST_SOURCES[tab]
    source = source if source in source_options else source_options[0]
    q = (q or "").strip()[:100]
    return_to = _library_url(tab, q, sort, show, page, source)
    subtabs = _library_subtabs(tab, q)
    messages = _message_notes(msg, None, return_to)

    result = None
    if tab != "added":
        try:
            result, _age = web.get_result_nowait()
        except Exception as e:
            return _shell(subtabs + messages + _error_screen(str(e), return_to), "library", return_to=return_to)
        if result is None:
            status = _status()
            if status.get("state") == "error":
                return _shell(subtabs + messages + _error_screen(status.get("error"), return_to), "library",
                              "The last attempt failed", return_to=return_to)
            return _shell(subtabs + messages + _waiting_screen("recs"), "library", "Getting things ready",
                          return_to=return_to, auto_refresh=True)

    items = web.library_items(result, tab)
    if items is None:
        body = (f'{subtabs}{messages}<div class="empty"><h3>Connect Plex to browse your library</h3>'
                '<p>Your watch history comes from Tautulli, which can\'t list the library itself. '
                'Add your Plex token to see everything in it.</p>'
                '<a class="btn-add" href="/settings?section=plex">Plex settings</a></div>')
        return _shell(body, "library", return_to=return_to)

    notes = ""
    arr_states = web.arr_status(result)
    if tab in ("all", "movie", "tv"):
        for name in ("radarr", "sonarr"):
            if arr_states.get(name) == "error":
                notes += (f'<p class="note">Couldn\'t reach {name.capitalize()} at the last refresh, '
                          f'so its titles aren\'t shown.</p>')
        if items and (result or {}).get("library") is None:
            notes += '<p class="note">Plex isn\'t connected, so only Radarr and Sonarr titles are shown.</p>'
    if tab == "watched":
        if web.ratings_changed():
            notes += ('<p class="note">Your ratings changed - refresh to update your recommendations.</p>'
                      + _refresh_form(return_to, "Refresh recommendations"))
        if web._is_sample():
            notes += '<p class="note">Sample data - ratings aren\'t saved.</p>'

    if not items:
        body = f'{subtabs}{messages}{notes}{_library_empty(tab, False)}'
        return _shell(body, "library", "0 titles", return_to=return_to)

    view = web.list_view(items, tab=tab, q=q, sort=sort, show=show, page=page, source=source)
    page = view["page"]
    return_to = _library_url(tab, q, sort, show, page, source)
    arr_on = any(state != "off" for state in arr_states.values())
    toolbar = _library_toolbar(tab, q, sort, show, sorts, shows, source,
                               source_options if arr_on and len(source_options) > 1 else ("all",))
    if view["items"]:
        page_items = view["items"]
        if tab == "watched":   # keep the stars (and Plex's) the watched list already carries; add the list state
            page_items = [{**own, "lists": mine["lists"], "on_watchlist": mine["on_watchlist"]}
                          for own, mine in zip(page_items, web.with_user_state(page_items))]
        elif tab != "added":
            page_items = web.with_user_state(page_items)
        if tab == "watched":
            cards = "".join(_watched_card(i, return_to) for i in page_items)
        else:
            cards = "".join(_library_card(i, tab == "added", return_to) for i in page_items)
        grid = f'<div class="grid" data-grid>{cards}</div>'
    else:
        grid = _library_empty(tab, True, q)
    pager = _library_pager(tab, q, sort, show, page, view["pages"], source)
    total = view["total"]
    subtitle = f"{total} title{'s' if total != 1 else ''}"
    return _shell(f'{subtabs}{messages}{notes}{toolbar}{grid}{pager}', "library", subtitle, return_to=return_to)


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

    result = web._ai_state["result"]
    generate_form = _generate_form("Generate again" if result else "Generate")
    if result is None:
        body = (f'{messages}{error}<div class="ai-intro"><p>Ask your AI for a batch of ideas grounded in your '
                'taste profile - these are separate from the main Recommended list, and each click is a real '
                f'request to your configured provider.</p>{generate_form}</div>')
        return _shell(body, "ai", show_refresh=False)

    items = web.with_user_state(result["items"][:SHOW])
    notes = "".join(f'<p class="note">{escape(n)}</p>' for n in result["notes"])
    cards = "".join(_card(i, "/ai", True) for i in items)
    if items:
        grid = f'<div class="grid" data-grid>{cards}</div>'
    else:
        grid = '<div class="empty"><h3>No suggestions this time</h3><p>Try generating again.</p></div>'
    body = (f'{messages}{error}<div class="ai-toolbar">{generate_form}'
            f'<span class="muted">Each click is a real request to your AI provider.</span></div>{notes}{grid}')
    subtitle = f"Generated {_age_text(time.time() - web._ai_state['time'])}"
    return _shell(body, "ai", subtitle, show_refresh=False)


# --- Title detail page (GET /title/<movie|tv>/<id>) ---
_IMDB_ID = re.compile(r"tt\d{1,10}\Z")
_KIND_LABELS = {"movie": "Movie", "tv": "TV series"}


def _ext_link(url, label):
    return (f'<a class="ext-link" href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">'
            f'{escape(label)}<span class="visually-hidden"> (opens in a new tab)</span> &#8599;</a>')


def _title_status_badges(status):
    badges = ""
    kind = status.get("status") or "none"
    if kind in _STATUS_LABELS:
        badges += f'<span class="lib-tag status-tag status-{escape(kind, quote=True)}">{_STATUS_LABELS[kind]}</span>'
    arr_text = _arr_label(status.get("arr_state"), status.get("episodes"))
    if arr_text:
        badges += (f'<span class="badge arr-state state-{escape(str(status.get("arr_state")), quote=True)}">'
                   f'{escape(arr_text)}</span>')
    if status.get("watched"):
        badges += '<span class="badge">Watched</span>'
    if status.get("dismissed"):
        badges += '<span class="badge">Not interested</span>'
    return badges


def _title_facts(view, item, extras):
    movie = view["media_type"] == "movie"
    rows = []

    def fact(label, value):
        if value:
            rows.append(f"<dt>{escape(label)}</dt><dd>{value}</dd>")

    if extras.get("status"):
        fact("Status", escape(str(extras["status"])))
    when = item.get("release_date") or item.get("year")
    fact("Release" if movie else "First aired", escape(str(when)) if when else "")
    runtime = _runtime_text(item)
    fact("Runtime" if movie else "Seasons", escape(runtime) if movie else escape(runtime.split(" ")[0] if runtime else ""))
    names = [str(n) for n in (extras.get("studios") if movie else extras.get("networks")) or []]
    fact(("Studio" if movie else "Network") + ("s" if len(names) > 1 else ""), escape(", ".join(names)))
    crew = extras.get("crew") or []
    if movie:
        people = [str(d) for d in item.get("directors") or []] or [str(c.get("name")) for c in crew if c.get("job") == "Director"]
        fact("Director" + ("s" if len(people) > 1 else ""), escape(", ".join(people)))
    else:
        people = [str(c.get("name")) for c in crew if c.get("job") == "Creator"]
        fact("Creator" + ("s" if len(people) > 1 else ""), escape(", ".join(people)))
    try:
        vote = float(item.get("vote_average") or 0)
    except (TypeError, ValueError):
        vote = 0.0
    if vote and item.get("vote_count"):
        fact("TMDB", f"{vote:.1f} / 10")
    return f'<dl class="title-facts">{"".join(rows)}</dl>' if rows else ""


def _season_section(view, ref, return_to, can_pick):
    rows = view.get("seasons") or []
    if not rows:
        return ""
    items = []
    selectable = False
    for row in rows:
        number = int(row["number"])
        pick = can_pick and bool(row.get("selectable")) and not row.get("requested")
        selectable = selectable or pick
        name = escape(str(row.get("name") or f"Season {number}"))
        eps = _season_eps_text(row)
        head = (f'<input type="checkbox" class="season-check" name="season" value="{number}" id="s-{number}">'
                f'<label for="s-{number}" class="season-name">{name}</label>' if pick
                else f'<span class="season-name">{name}</span>')
        items.append(f'<li class="season-row">{head}'
                     + (f'<span class="season-eps">{eps}</span>' if eps else "")
                     + f'{_season_state_badge(row)}</li>')
    actions = ""
    if selectable:
        dialog = "/add-dialog?" + urlencode({"type": "tv", "id": ref["tmdb_id"], "return_to": return_to})
        actions = ('<div class="season-actions"><label class="checkbox-label">'
                   '<input type="checkbox" name="search" value="1" checked> Search now</label>'
                   '<button type="submit" name="seasons" value="pick" class="btn-add">Request selected seasons</button>'
                   '<button type="submit" name="seasons" value="all" class="btn-ghost">Request all seasons</button>'
                   f'<a class="link-btn" href="{escape(dialog, quote=True)}" data-add-dialog>More options</a></div>')
    return (f'<section class="title-section seasons"><h2>Seasons</h2>'
            f'<form class="season-form" method="post" action="/add" data-enhance="add" data-after="reload">'
            f'{_hidden_fields(ref, return_to)}<ul class="season-list">{"".join(items)}</ul>{actions}</form></section>')


def _cast_section(extras, item):
    cast = [c for c in extras.get("cast") or [] if isinstance(c, dict) and c.get("name")]
    if not cast:
        cast = [{"name": n, "character": "", "profile_url": None} for n in item.get("cast") or [] if n]
    if not cast:
        return ""
    cards = []
    for person in cast:
        name = str(person["name"])
        photo = _web_url(person.get("profile_url"))
        art = (f'<img src="{escape(photo, quote=True)}" alt="" loading="lazy">' if photo
               else escape(name[:1].upper()))
        hue = (zlib.crc32(name.encode("utf-8")) * 47) % 360
        role = f'<span class="cast-role">{escape(str(person["character"]))}</span>' if person.get("character") else ""
        cards.append(f'<li class="cast-card"><span class="cast-photo" style="--h:{hue}">{art}</span>'
                     f'<span class="cast-name">{escape(name)}</span>{role}</li>')
    return f'<section class="title-section cast"><h2>Cast</h2><ul class="cast-list">{"".join(cards)}</ul></section>'


def render_title(view, msg="", undo=None):
    """The title detail page for web.title_view(...). Every poster click and "More info" lands here. Works without
    JS: the add, rate, Watchlist and season controls are plain forms and links (app.js only enhances them)."""
    media_type = "tv" if view.get("media_type") == "tv" else "movie"
    tmdb_id = int(view["tmdb_id"])
    section = "tv" if media_type == "tv" else "movies"
    return_to = f"/title/{media_type}/{tmdb_id}"
    notes = _message_notes(msg, undo, return_to)
    state = view.get("state")
    item = view.get("item")
    if state in ("unavailable", "not_found") or not item:
        word = "Title not found" if state == "not_found" else "Title unavailable"
        message = escape(view.get("message") or "Couldn't load this title.")
        empty = (f'<div class="empty"><h3>{message}</h3>'
                 f'<a class="btn-ghost" href="/search">Search instead</a></div>')
        return _shell(f'<article class="title-page">{notes}{empty}</article>', section, cinematic=True, heading=word)

    extras = view.get("extras") or {}
    status = view.get("status") or {}
    can_act = not view.get("sample")
    title_plain = str(item.get("title") or "?")
    ref = {"media_type": media_type, "tmdb_id": tmdb_id, "title": title_plain, "year": item.get("year"),
           "stars": view.get("stars"), "on_watchlist": view.get("on_watchlist")}
    rec = view.get("rec")

    backdrop = _web_url(item.get("backdrop_url"))
    backdrop_html = (f'<img class="title-backdrop" src="{escape(backdrop, quote=True)}" alt="">' if backdrop else "")
    poster = _poster_html({**ref, "poster_url": _web_url(item.get("poster_large_url")) or item.get("poster_url"),
                           "poster_key": None})
    meta = ""
    if rec and rec.get("match") is not None:
        meta += _meta_match({"match": rec["match"]})
    meta += _meta_html({k: item.get(k) for k in ("year", "certification", "runtime", "seasons")})
    genres = " &middot; ".join(escape(str(g)) for g in (item.get("genres") or [])[:5])
    if genres:
        meta += f'<span class="title-genres">{genres}</span>'
    tagline = f'<p class="title-tagline">{escape(str(extras["tagline"]))}</p>' if extras.get("tagline") else ""
    badges = _title_status_badges(status)
    if view.get("on_watchlist"):
        badges += '<span class="badge">On Watchlist</span>'

    actions = ""
    selectable = any(r.get("selectable") and not r.get("requested") for r in view.get("seasons") or [])
    if view.get("can_add") and _can_add(ref, can_act) and (status.get("status") or "none") == "none":
        actions += _add_link(ref, return_to)
    elif media_type == "tv" and can_act and view.get("arr") and selectable and config.sonarr_configured():
        actions += (f'<a class="btn-add" href="{escape(_add_dialog_url(ref, return_to), quote=True)}" data-add-dialog>'
                    f'Request more seasons</a>')
    elif (status.get("status") or "none") != "none":
        actions += '<span class="lib-tag">In library</span>'
    trailer = extras.get("trailer") if isinstance(extras.get("trailer"), dict) else None
    trailer_url = _web_url(trailer.get("url")) if trailer else None
    if trailer_url:
        actions += (f'<a class="btn-ghost trailer-link" href="{escape(trailer_url, quote=True)}" target="_blank" '
                    f'rel="noopener noreferrer">Watch trailer<span class="visually-hidden"> (opens YouTube in a new tab)</span></a>')
    if can_act:
        if status.get("dismissed"):
            actions += (f'<form class="inline" method="post" action="/undismiss">{_hidden_fields(ref, return_to)}'
                        f'<button type="submit" class="btn-ghost">Show in recommendations again</button></form>')
        else:
            actions += (f'<form class="inline" method="post" action="/dismiss">{_hidden_fields(ref, return_to)}'
                        f'<button type="submit" class="btn-ghost">Not interested</button></form>')
    actions += _rate_form(ref, return_to) + _list_toggle(ref, return_to) + _list_link(ref, return_to, detail=True)

    in_lists = ""
    names = [e for e in view.get("list_names") or [] if isinstance(e, dict) and isinstance(e.get("id"), int)]
    if names:
        links = ", ".join(f'<a href="/lists/{e["id"]}">{escape(str(e.get("name") or ""))}</a>' for e in names)
        in_lists = f'<p class="in-lists">On your lists: {links}</p>'

    hero = (f'<section class="title-hero" style="--h:{_hue(ref)}">{backdrop_html}<div class="title-hero-inner">'
            f'<div class="title-poster">{poster}</div><div class="title-head">'
            f'<p class="kicker">{_KIND_LABELS[media_type]}</p><h1 class="title title-name">{escape(title_plain)}</h1>'
            f'{tagline}<p class="card-meta title-meta">{meta}</p>'
            + (f'<p class="title-status badges">{badges}</p>' if badges else "")
            + f'<div class="title-actions">{actions}</div>{in_lists}</div></div></section>')

    part = f'<p class="note">{escape(view["message"])}</p>' if state == "partial" and view.get("message") else ""
    overview = escape(item.get("overview") or "No overview available.")
    ext = ""
    tmdb_link = _web_url(item.get("url"))
    if tmdb_link:
        ext += _ext_link(tmdb_link, "More on TMDB")
    imdb = str(extras.get("imdb_id") or "")
    if _IMDB_ID.match(imdb):
        ext += _ext_link(f"https://www.imdb.com/title/{imdb}/", "IMDb")
    body = (f'{hero}{notes}{part}<section class="title-section title-overview"><h2>Overview</h2>'
            f'<p class="overview">{overview}</p>{_title_facts(view, item, extras)}{ext}</section>')
    if rec:
        chips = "".join(f'<span class="chip">{escape(str(m))}</span>' for m in rec.get("matches") or [])
        because = [str(b) for b in rec.get("because") or []]
        body += ('<section class="title-section why"><h2>Why Compass picked this</h2>'
                 + _meta_match({"match": rec.get("match")}, "p", "why-match match")
                 + (f'<p class="reason">{escape(str(rec["reason"]))}</p>' if rec.get("reason") else "")
                 + (f'<div class="chips">{chips}</div>' if chips else "")
                 + (f'<p class="muted">Because you watched {escape(", ".join(because))}</p>' if because else "")
                 + '</section>')
    elif view.get("fit") and (view["fit"].get("matches")):
        chips = "".join(f'<span class="chip">{escape(str(m))}</span>' for m in view["fit"]["matches"])
        body += f'<section class="title-section why fit"><h2>How it fits your taste</h2><div class="chips">{chips}</div></section>'
    if media_type == "tv":
        body += _season_section(view, ref, return_to, can_act and config.sonarr_configured())
    body += _cast_section(extras, item)
    similar = [i for i in view.get("similar") or [] if isinstance(i, dict) and _has_id(i)]
    if similar:
        body += _row_html({"id": "similar", "title": "More like this", "items": similar}, return_to, can_act,
                          "title-similar")
    full = f"{title_plain} ({item['year']})" if item.get("year") else title_plain
    return _shell(f'<article class="title-page" data-card="{_card_key(ref)}" data-keep-on-add>{body}</article>',
                  section, cinematic=True, heading=full)


# --- Lists (a Library sub-area: /lists, /lists/<id>, and the add-to-list dialog) ---
_LIST_SORT_LABELS = {"manual": "Your order", "added": "Recently added", "title": "Title A-Z", "year": "Newest year",
                     "rating": "Your rating", "match": "Match %"}


def _list_dialog_shell(inner, partial, section):
    return inner if partial else _shell(f'<div class="dialog-page">{inner}</div>', section, "Lists")


def render_list_dialog(media_type, tmdb_id, return_to, partial=False):
    """Tick the lists a title should be on (POST /lists/set). A page without JS, just the inner HTML (partial=True)
    for app.js's modal - the same pattern as render_add_dialog. Sample mode shows a note and no form."""
    return_to = web._safe_path(return_to)
    section = _section_for(return_to)
    try:
        stub = web.title_stub(media_type, int(tmdb_id), fetch=True)
    except Exception:
        stub = None
    if stub is None:
        inner = ('<div class="dialog-box list-dialog"><div class="dialog-head"><div>'
                 '<h3 id="list-dialog-title">Can&#39;t add this one</h3></div></div>'
                 '<p class="note">That title isn\'t available to add to a list right now.</p>'
                 f'<div class="card-actions"><a class="btn-ghost" href="{escape(return_to, quote=True)}" data-close>Back</a></div></div>')
        return _list_dialog_shell(inner, partial, section)
    title = escape(str(stub.get("title") or "?"))
    head = (f'<div class="dialog-head"><div><h3 id="list-dialog-title">Add &quot;{title}&quot; to lists</h3></div></div>')
    if web._is_sample():
        inner = (f'<div class="dialog-box list-dialog">{head}<p class="note">Sample data - lists aren\'t saved.</p>'
                 f'<div class="card-actions"><a class="btn-ghost" href="{escape(return_to, quote=True)}" data-close>Back</a></div></div>')
        return _list_dialog_shell(inner, partial, section)
    ref = {"media_type": media_type, "tmdb_id": int(tmdb_id), "title": stub.get("title"), "year": stub.get("year")}
    view = web.lists_view()
    mine = set(web.with_user_state([ref])[0].get("lists") or [])
    choices = "".join(
        f'<label class="checkbox-label list-choice"><input type="checkbox" name="list_id" value="{int(entry["id"])}"'
        f'{" checked" if entry["id"] in mine else ""}> {escape(str(entry["name"]))} '
        f'<span class="muted">{int(entry["count"])}</span></label>'
        for entry in view["lists"] if entry.get("id") is not None)
    new = ('<label class="list-new">New list'
           f'<input type="text" name="new_list" maxlength="{int(web.LIST_NAME_MAX)}" placeholder="Name"></label>'
           if view.get("can_create") else "")
    inner = (f'<div class="dialog-box list-dialog">{head}'
             f'<form method="post" action="/lists/set" data-enhance="lists">{_hidden_fields(ref, return_to)}'
             f'<fieldset class="list-choices"><legend class="visually-hidden">Lists</legend>{choices}</fieldset>{new}'
             f'<div class="card-actions"><a class="btn-ghost" href="{escape(return_to, quote=True)}" data-close>Cancel</a>'
             f'<button type="submit" class="btn-add">Save</button></div></form></div>')
    return _list_dialog_shell(inner, partial, section)


def _list_tile(entry):
    posters = [u for u in (_web_url(p) for p in entry.get("posters") or []) if u][:4]
    cells = "".join(f'<img src="{escape(u, quote=True)}" alt="" loading="lazy">' for u in posters)
    cells += '<span aria-hidden="true"></span>' * (4 - len(posters))
    count = int(entry.get("count") or 0)
    desc = f'<span class="list-tile-desc">{escape(str(entry["description"]))}</span>' if entry.get("description") else ""
    inner = (f'<span class="list-tile-posters">{cells}</span><span class="list-tile-name">{escape(str(entry["name"]))}</span>'
             f'<span class="list-tile-meta">{count} title{"s" if count != 1 else ""}</span>{desc}')
    if entry.get("url"):
        return f'<a class="list-tile" href="{escape(str(entry["url"]), quote=True)}">{inner}</a>'
    return f'<div class="list-tile">{inner}</div>'


def render_lists(msg=""):
    """The Lists page: a tile per list (Watchlist first) and a create form. A Library sub-area."""
    view = web.lists_view()
    notes = _message_notes(msg, None, "/lists")
    if view.get("sample"):
        notes += '<p class="note">Sample data - lists aren\'t saved.</p>'
    tiles = "".join(_list_tile(e) for e in view["lists"])
    create = ""
    if view.get("can_create"):
        create = ('<form class="list-create" method="post" action="/lists/create">'
                  f'<label>Name<input name="name" maxlength="{int(web.LIST_NAME_MAX)}" required></label>'
                  f'<label>Description<input name="description" maxlength="{int(web.LIST_DESC_MAX)}"></label>'
                  '<button type="submit" class="btn-add">Create list</button></form>')
    body = (f'{_library_subtabs("lists", "")}<div class="lists-page">{notes}'
            f'<div class="lists-grid">{tiles}</div>{create}</div>')
    count = len(view["lists"])
    return _shell(body, "library", f"{count} list{'s' if count != 1 else ''}", return_to="/lists")


def _list_url(view, sort=None, page=None):
    """/lists/<id> keeping the sort (omitted for the default) and page (omitted for 1)."""
    sort = view["sort"] if sort is None else sort
    page = view["page"] if page is None else page
    params = ([("sort", sort)] if sort and sort != "manual" else []) + ([("page", page)] if page and page > 1 else [])
    return f"/lists/{int(view['list']['id'])}" + (("?" + urlencode(params)) if params else "")


def _list_card(item, view, return_to):
    title_text = _title_text(item)
    title = escape(title_text)
    kind = "Movie" if item["media_type"] == "movie" else "TV"
    status = item.get("status") or "none"
    badges = ""
    if status in _STATUS_LABELS:
        badges += f'<span class="lib-tag status-tag status-{escape(status, quote=True)}">{_STATUS_LABELS[status]}</span>'
    if item.get("watched"):
        badges += '<span class="badge">Watched</span>'
    if item.get("dismissed"):
        badges += '<span class="badge">Not interested</span>'
    arr_text = _arr_label(item.get("arr_state"), item.get("episodes"))
    arr_line = f'<p class="card-sub arr-line">{escape(arr_text)}</p>' if arr_text else ""
    ids = _hidden_fields(item, return_to) + f'<input type="hidden" name="list_id" value="{int(view["list"]["id"])}">'
    move = ""
    if view.get("can_move"):
        buttons = "".join(
            f'<form class="inline" method="post" action="/lists/move" data-enhance="list-move">{ids}'
            f'<input type="hidden" name="direction" value="{direction}">'
            f'<button type="submit" aria-label="Move {escape(title_text, quote=True)} {label.lower()}">{label}</button></form>'
            for direction, label in (("up", "Up"), ("down", "Down"), ("top", "Top")))
        move = f'<div class="list-move" role="group" aria-label="Reorder {escape(title_text, quote=True)}">{buttons}</div>'
    remove = (f'<form class="inline list-remove" method="post" action="/lists/remove" data-enhance="list" data-remove-card>'
              f'{ids}<button type="submit" aria-label="Remove {escape(title_text, quote=True)} from this list">Remove</button></form>')
    return (f'<article class="card search-card list-card" data-card="{_card_key(item)}">'
            f'<div class="card-poster">{_poster_linked(item)}<div class="poster-top">{_meta_match(item)}'
            f'<span class="badges">{badges}</span></div><span class="kind">{kind}</span></div>'
            f'<div class="card-info"><h3 class="title">{title}</h3>{arr_line}{_card_tools(item, return_to)}'
            f'{move}{remove}</div></article>')


def render_list(view, msg=""):
    """One list: header (edit / delete for custom lists), a sort toolbar, the cards, the pager."""
    entry = view["list"]
    list_id = int(entry["id"])
    url = _list_url(view)
    notes = _message_notes(msg, None, url)
    head = f'<h3>{escape(str(entry["name"]))}</h3>'
    if entry.get("description"):
        head += f'<p class="muted">{escape(str(entry["description"]))}</p>'
    if entry.get("kind") == "custom":
        name = escape(str(entry["name"]), quote=True)
        head += (f'<details class="list-edit"><summary>Edit</summary>'
                 f'<form method="post" action="/lists/update"><input type="hidden" name="list_id" value="{list_id}">'
                 f'<input type="hidden" name="return_to" value="{escape(url, quote=True)}">'
                 f'<label>Name<input name="name" maxlength="{int(web.LIST_NAME_MAX)}" required value="{name}"></label>'
                 f'<label>Description<input name="description" maxlength="{int(web.LIST_DESC_MAX)}" '
                 f'value="{escape(str(entry.get("description") or ""), quote=True)}"></label>'
                 f'<button type="submit" class="btn-add">Save</button></form></details>'
                 f'<details class="list-delete"><summary>Delete list</summary>'
                 f'<p>Delete &quot;{escape(str(entry["name"]))}&quot;? This can\'t be undone.</p>'
                 f'<form method="post" action="/lists/delete"><input type="hidden" name="list_id" value="{list_id}">'
                 f'<input type="hidden" name="return_to" value="/lists">'
                 f'<button type="submit" class="btn-ghost danger">Delete</button></form></details>')
    options = "".join(f'<option value="{escape(s, quote=True)}"{" selected" if s == view["sort"] else ""}>'
                      f'{escape(_LIST_SORT_LABELS.get(s, s))}</option>' for s in view.get("sorts") or ())
    toolbar = (f'<form class="toolbar" method="get" action="/lists/{list_id}">'
               f'<label>Sort by<select name="sort">{options}</select></label>'
               f'<button type="submit" class="btn-ghost">Apply</button></form>')
    if view.get("items"):
        cards = "".join(_list_card(i, view, url) for i in view["items"])
        grid = f'<div class="grid" data-grid>{cards}</div>'
    else:
        grid = ('<div class="empty"><h3>Nothing on this list yet</h3>'
                '<p>Use the bookmark or list button on any title to add it here.</p>'
                '<a class="btn-add" href="/">Browse recommendations</a></div>')
    pager = _pager(lambda target: _list_url(view, page=target), view["page"], view["pages"])
    total = int(view.get("total") or 0)
    body = (f'{_library_subtabs("lists", "")}{notes}<header class="list-head">{head}</header>'
            f'{toolbar}{grid}{pager}')
    return _shell(body, "library", f"{total} title{'s' if total != 1 else ''}", return_to=url)


# --- Search (GET /search; the page, and the fragment that live search swaps in) ---
_STATUS_LABELS = {"plex": "In library", "radarr": "In Radarr", "sonarr": "In Sonarr", "added": "Added"}
SEARCH_HINT = "Find any movie or show and send it to Radarr or Sonarr."


def _search_url(q, kind):
    """"/search?q=..&type=.." - never with partial. The return_to for every Add link on the page and in the fragment."""
    params = ([("q", q)] if q else []) + [("type", kind)]
    return "/search?" + urlencode(params)


def _arr_error_notes(search=False):
    """One note per Radarr/Sonarr that was unreachable at the last build (shared by the Library and search)."""
    try:
        result, _age = web.get_result_nowait()
        states = web.arr_status(result)
    except Exception:
        return ""
    tail = ("so titles tracked there may show here without an In Radarr/In Sonarr label." if search
            else "so its titles aren't shown.")
    return "".join(f'<p class="note">Couldn\'t reach {name.capitalize()} at the last refresh, ' + tail.replace(
        "In Radarr/In Sonarr", "In " + name.capitalize()) + '</p>'
                   for name in ("radarr", "sonarr") if states.get(name) == "error")


def _search_card(item, return_to, can_act):
    status = item.get("status") or "none"
    title = escape(_title_text(item))
    kind = "Movie" if item["media_type"] == "movie" else "TV"
    badges = ""
    if status in _STATUS_LABELS:
        badges += (f'<span class="lib-tag status-tag status-{escape(status, quote=True)}">'
                   f'{_STATUS_LABELS[status]}</span>')
    if item.get("watched"):
        badges += '<span class="badge">Watched</span>'
    if item.get("dismissed"):
        badges += '<span class="badge">Not interested</span>'
    arr_text = _arr_label(item.get("arr_state"), item.get("episodes"))
    arr_line = f'<p class="card-sub arr-line">{escape(arr_text)}</p>' if arr_text else ""
    meta = f"<span>{kind}</span>"
    if item.get("year"):
        meta += f'<span>{escape(str(item["year"]))}</span>'
    try:
        vote = float(item.get("vote_average") or 0)
    except (TypeError, ValueError):
        vote = 0.0
    if item.get("vote_count") and vote:
        meta += f"<span>TMDB {vote:.1f}</span>"
    link = _web_url(item.get("url"))
    ext = (f'<a class="ext-link" href="{escape(link, quote=True)}" target="_blank" rel="noopener noreferrer">'
           f'More on TMDB<span class="visually-hidden"> (opens in a new tab)</span> &#8599;</a>' if link else "")
    overview = escape(item.get("overview") or "No overview available.")
    details = (f'<details class="card-details"><summary>Details<span class="visually-hidden">: {title}</span></summary>'
               f'<div class="detail-body"><p class="card-meta">{meta}</p>'
               f'<p class="overview">{overview}</p>{ext}</div></details>')
    actions = ""
    if status == "none" and _can_add(item, can_act):
        actions += _add_link(item, return_to)
    if item.get("dismissed") and can_act:
        actions += (f'<form class="inline" method="post" action="/undismiss" data-enhance="undismiss">'
                    f'{_hidden_fields(item, return_to)}'
                    f'<button type="submit" class="link-btn">Show in recommendations again</button></form>')
    if actions:
        actions = f'<div class="card-actions">{actions}</div>'
    return (f'<article class="card search-card" data-card="{_card_key(item)}" data-keep-on-add>'
            f'<div class="card-poster">{_poster_linked(item)}'
            f'<div class="poster-top"><span class="badges">{badges}</span></div>'
            f'<span class="kind">{kind}</span></div>'
            f'<div class="card-info"><h3 class="title">{title}</h3>{arr_line}{details}{actions}'
            f'{_card_tools(item, return_to)}</div></article>')


def _search_fragment(view, return_to):
    """The ONE renderer of the search results, used by the full page and by the partial=1 response that
    live search swaps in. The root's data-announce is the plain-text summary app.js puts in the live region,
    so state blocks carry no role=status/alert of their own."""
    state = view.get("state")
    q = view.get("q") or ""
    results = view.get("results") or []
    count = len(results)
    quoted = f"&ldquo;{escape(q)}&rdquo;"
    if state == "short":
        announce, block = "Type at least 2 characters", '<p class="note">Type at least 2 characters.</p>'
    elif state in ("error", "limited"):
        announce = view.get("message") or ""
        block = f'<p class="note error">{escape(announce)}</p>'
    elif state == "ok" and not results:
        announce = f'No matches for "{q}"'
        block = (f'<div class="empty"><h3>No matches for {quoted}</h3>'
                 f'<p>Check the spelling or try the original title.</p></div>')
    elif state == "ok":
        announce = f'{count} match{"" if count == 1 else "es"} for "{q}"'
        more = " - showing the top 20. Add a year or more words to narrow it down." if view.get("capped") else ""
        loading = ('<p class="note muted-note">Your library is still loading, so some "In library" labels '
                   'may be missing.</p>' if not view.get("library_known") else "")
        can_act = not view.get("sample")
        cards = "".join(_search_card(i, return_to, can_act) for i in results)
        block = (f'<p class="search-summary muted">{count} match{"" if count == 1 else "es"} for {quoted}{more}</p>'
                 f'{loading}{_arr_error_notes(True)}<div class="grid search-results" data-grid>{cards}</div>')
    else:
        state, announce = "empty", ""
        block = f'<p class="muted search-hint">{SEARCH_HINT}</p>'
    return (f'<div class="search-fragment" data-search-fragment data-search-state="{escape(state, quote=True)}" '
            f'data-announce="{escape(announce, quote=True)}">{block}</div>')


def render_search_results(q="", kind="all"):
    """Just the results fragment (GET /search?...&partial=1): no shell. Calls web.search_view once."""
    view = web.search_view(q, kind)
    return _search_fragment(view, _search_url(q, kind))


def render_search(q="", kind="all", msg=""):
    """The search page. Works without JS (a GET form); with JS the box searches live (app.js)."""
    view = web.search_view(q, kind)
    return_to = _search_url(q, kind)
    sample = ('<p class="note">Sample data - searching the built-in sample catalogue.</p>'
              if view.get("sample") else "")
    pills = "".join(
        f'<label class="search-type"><input type="radio" name="type" value="{value}"'
        f'{" checked" if value == kind else ""}> {label}</label>'
        for value, label in (("all", "All"), ("movie", "Movies"), ("tv", "TV shows")))
    autofocus = "" if q else " autofocus"
    body = (f'<div class="search-page">{_message_notes(msg, None, return_to)}{sample}'
            f'<form class="search-form" method="get" action="/search" role="search" data-search-live>'
            f'<label class="search-label" for="search-q">Search all movies and TV</label>'
            f'<div class="search-row"><input class="search-input" id="search-q" type="search" name="q" '
            f'value="{escape(q, quote=True)}" maxlength="100" minlength="2" required autocomplete="off" '
            f'enterkeyhint="search" aria-controls="search-results"{autofocus}>'
            f'<span class="spinner sm search-spinner" data-search-spinner aria-hidden="true" hidden></span>'
            f'<button type="submit" class="btn-add">Search</button></div>'
            f'<fieldset class="search-types"><legend class="visually-hidden">Show</legend>{pills}</fieldset></form>'
            f'<p class="search-status visually-hidden" role="status" aria-live="polite" data-search-status></p>'
            f'<div class="search-results-region" id="search-results" data-search-results aria-busy="false">'
            f'{_search_fragment(view, return_to)}</div></div>')
    return _shell(body, "search")


# The Compass mark (assets/logo-compass.svg): a compass whose north needle is a play triangle.
# Coloured per theme from the registry - fixed strings, no user data.
def _logo_svg(theme, attrs=""):
    fg, bg = theme["logo"], theme["bg"]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"{attrs}>'
            f'<rect width="512" height="512" rx="112" fill="{bg}"/>'
            f'<circle cx="256" cy="256" r="158" fill="none" stroke="{fg}" stroke-width="22"/>'
            f'<g fill="{fg}"><rect x="248" y="118" width="16" height="30" rx="8"/>'
            f'<rect x="248" y="364" width="16" height="30" rx="8"/><rect x="118" y="248" width="30" height="16" rx="8"/>'
            f'<rect x="364" y="248" width="30" height="16" rx="8"/></g>'
            f'<g transform="rotate(30 256 256)" fill="{fg}" stroke="{fg}" stroke-linejoin="round" stroke-width="14">'
            f'<path d="M228 262 L256 372 L284 262 Z" opacity=".35"/><path d="M200 262 L256 140 L312 262 Z"/>'
            f'<circle cx="256" cy="256" r="13" fill="{bg}" stroke="none"/></g></svg>')


def _brand_mark(theme):
    """The 32px header mark. data-fg/data-bg tell app.js which colours to swap on a theme preview."""
    return _logo_svg(theme, f' class="brand-mark" width="32" height="32" aria-hidden="true" focusable="false"'
                            f' data-fg="{theme["logo"]}" data-bg="{theme["bg"]}"')


def _favicon_href(theme):
    return "data:image/svg+xml," + quote(_logo_svg(theme))


# Inline SVG nav icons (fixed strings, no user data).
_NAV_ICONS = {
    "home": '<path d="M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    "movies": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 9h18M8 5v4M16 5v4M3 15h18"/>',
    "tv": '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M8 21h8M12 18v3M9 2l3 4 3-4"/>',
    "library": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
    "ai": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
    "settings": '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/>'
                '<circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
}


def _nav_html(section, keys=None):
    """The .nav-item links for the given section keys (default: all but Settings)."""
    links = []
    for key, label, href in NAV_SECTIONS:
        if (keys is not None and key not in keys) or (keys is None and key == "settings"):
            continue
        active, current = (" active", ' aria-current="page"') if key == section else ("", "")
        links.append(f'<a class="nav-item{active}" href="{href}"{current}>'
                     f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
                     f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
                     f'{_NAV_ICONS.get(key, "")}</svg><span>{escape(label)}</span></a>')
    return "".join(links)


def render_appearance(msg=""):
    """The colour-theme picker (a Settings sub-tab). Each card carries its own data-theme, so app.css
    previews it in that theme's colours; the saved theme is the checked one."""
    current = themes.get(web.current_theme())["key"]
    options = []
    for t in themes.THEMES:
        is_current = t["key"] == current
        pill = '<span class="theme-current">Current</span>' if is_current else ""
        options.append(
            f'<label class="theme-option" data-theme="{escape(t["key"])}" data-meta-dark="{escape(t["bg"])}" '
            f'data-meta-light="{escape(t["bg_light"])}" data-logo="{escape(t["logo"])}" '
            f'data-favicon="{escape(_favicon_href(t))}">'
            f'<input class="theme-radio" type="radio" name="theme" value="{escape(t["key"])}"{" checked" if is_current else ""}>'
            '<span class="theme-swatch" aria-hidden="true"><span class="swatch-top"></span>'
            '<span class="swatch-hero"><span class="swatch-line"></span><span class="swatch-line short"></span>'
            '<span class="swatch-btn"></span></span>'
            '<span class="swatch-row"><span class="swatch-card"></span><span class="swatch-card"></span>'
            '<span class="swatch-card"></span></span></span>'
            f'<span class="theme-text"><span class="theme-name">{escape(t["label"])}</span>'
            f'{pill}'
            f'<span class="theme-desc">{escape(t["description"])}</span></span></label>')
    body = (f'<div class="settings appearance">{settings_page.subtabs_html("appearance")}'
            f'{_message_notes(msg, None, "/appearance")}'
            '<form class="theme-form" method="post" action="/theme" data-enhance="theme">'
            '<input type="hidden" name="return_to" value="/appearance">'
            '<fieldset class="theme-picker"><legend>Colour theme</legend>'
            '<p class="muted theme-help">Saved on this device only. Light or dark follows your device setting.</p>'
            f'<div class="theme-grid">{"".join(options)}</div></fieldset>'
            '<button type="submit" class="btn-add theme-submit">Use this theme</button></form></div>')
    return _shell(body, "settings")


def _search_svg():
    return (f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true" focusable="false">{_NAV_ICONS["search"]}</svg>')


def _topbar_search(section):
    """The top bar's search form (wide screens; left out on /search itself) and the .search-link icon (phones).
    Both are always in the markup and CSS picks one. The form is a plain GET: Enter lands on /search, where
    the live box takes over - this one never searches live."""
    on_page = section == "search"
    form = "" if on_page else (
        '<form class="topbar-search" method="get" action="/search" role="search">'
        '<label class="visually-hidden" for="topbar-q">Search all movies and TV</label>'
        '<input class="search-input" id="topbar-q" type="search" name="q" maxlength="100" '
        'placeholder="Search movies &amp; TV" autocomplete="off" enterkeyhint="search">'
        f'<button type="submit" class="search-submit" aria-label="Search">{_search_svg()}</button></form>')
    current = ' aria-current="page"' if on_page else ""
    link = (f'<a class="nav-item search-link{" active" if on_page else ""}" href="/search"{current}>'
            f'{_search_svg()}<span>Search</span></a>')
    return form + link


def _shell(body, section, subtitle="", show_refresh=False, return_to="/", status_html="", auto_refresh=False,
           cinematic=False, heading=None):
    """status_html: trusted markup (e.g. UPDATING_HTML) appended to the subtitle. auto_refresh: the
    no-JS fallback for the building screens - app.js polls /api/status instead. cinematic: a ready
    browse page - the hero sits under the transparent top bar and the title strip moves below the rows.
    heading: the page's own name (the title page passes its title); default is the section's label."""
    if heading is None:
        heading = "Search" if section == "search" else dict((key, label) for key, label, _ in NAV_SECTIONS).get(section, "Compass")
    nav_html = _nav_html(section)
    refresh = _refresh_form(return_to) if show_refresh else ""
    meta_refresh = '<noscript><meta http-equiv="refresh" content="5"></noscript>' if auto_refresh else ""
    body_class = ' class="cinematic"' if cinematic else ""
    if cinematic:
        main = (f'{body}<div class="top browse-top"><h2 class="visually-hidden">{escape(heading)}</h2>'
                f'<p class="sub">{escape(subtitle)}{status_html}</p><div class="top-actions">{refresh}</div></div>')
    else:
        main = (f'<div class="top"><div><h2>{escape(heading)}</h2><p class="sub">{escape(subtitle)}{status_html}</p></div>'
                f'<div class="top-actions">{refresh}</div></div>{body}')
    theme = themes.get(web.current_theme())
    return (f'<!doctype html><html lang="en" data-theme="{escape(theme["key"])}"><head><meta charset="utf-8">'
            f'<title>{escape(heading)} - Compass</title>'
            f'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            f'<meta name="color-scheme" content="dark light">'
            f'<meta name="theme-color" media="(prefers-color-scheme: dark)" content="{escape(theme["bg"])}">'
            f'<meta name="theme-color" media="(prefers-color-scheme: light)" content="{escape(theme["bg_light"])}">'
            f'<link rel="icon" type="image/svg+xml" href="{escape(_favicon_href(theme))}">'
            f'<link rel="stylesheet" href="/static/app.css"><script src="/static/app.js" defer></script>'
            f'{meta_refresh}</head><body{body_class}>'
            f'<a class="skip-link" href="#main">Skip to content</a>'
            f'<header class="topbar"><a class="brand" href="/">'
            f'{_brand_mark(theme)}'
            f'<h1 class="brand-name">Compass</h1></a>'
            f'<nav class="topnav" aria-label="Main">{nav_html}</nav>'
            f'<div class="topbar-actions">{_topbar_search(section)}{_nav_html(section, ("settings",))}</div></header>'
            f'<main class="app-main" id="main" tabindex="-1">{main}</main>'
            f'<nav class="bottom-nav" aria-label="Main">{nav_html}</nav>'
            f'<div class="toasts" id="toasts" role="status" aria-live="polite"></div>'
            f'</body></html>')


# Last, not first: web.py imports this module at load time, so by the time web is imported here
# every function above already exists - whichever of the two gets imported first.
import web  # noqa: E402
