"""The HTML pages: every render_* function plus the card/shell helpers they share.

Frontend-owned (see .claude/ownership.json); web.py owns state, the HTTP handler and validation.
Reads state only through the web module (web.get_result_nowait(), web._ai_state, ...), never by
importing names from it, so tests that patch web.<name> affect rendering too."""
import time
import zlib
from html import escape
from urllib.parse import urlencode

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


def _library_card(item, show_added=False):
    """A Plex library (or "Added here") card: poster, title, year. No actions."""
    link = _web_url(item.get("url"))
    title = escape(_title_text(item) if show_added else str(item["title"]))
    if link:
        title = f'<a href="{escape(link, quote=True)}" target="_blank" rel="noopener noreferrer">{title}</a>'
    kind = "Movie" if item["media_type"] == "movie" else "TV"
    badge = ""
    progress = item.get("progress")
    if item.get("watched"):
        badge = '<span class="badge">Watched</span>'
    elif isinstance(progress, (int, float)) and 0 < progress < 1:
        badge = '<span class="badge">Started</span>'
    top = f'<div class="poster-top"><span class="badges">{badge}</span></div>' if badge else ""
    if show_added:
        sub = f'Added {escape((item.get("added_at") or "")[:10] or "unknown date")}'
    else:
        sub = escape(str(item["year"])) if item.get("year") else ""
    return (f'<article class="card"><div class="card-poster">{_poster_html(item)}{top}'
            f'<span class="kind">{kind}</span></div>'
            f'<div class="card-info"><h3 class="title">{title}</h3>'
            f'<p class="card-sub">{sub}</p></div></article>')


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
    link = _web_url(item.get("url"))
    title_html = (f'<a href="{escape(link, quote=True)}" target="_blank" rel="noopener noreferrer">{title}</a>'
                  if link else title)
    kind = "Movie" if item["media_type"] == "movie" else "TV"
    sub = f'Watched {escape((item.get("last_viewed") or "")[:10])}' if item.get("last_viewed") else "Watched"
    if (item.get("view_count") or 0) > 1:
        sub += f' &middot; {int(item["view_count"])} plays'
    mine, plex = item.get("stars"), item.get("plex_stars")
    stars, text, clear = _rate_parts(mine, plex)
    form = (f'<form class="rate" method="post" action="/rate" data-enhance="rate">{_hidden_fields(item, return_to)}'
            f'<div class="stars" role="group" aria-label="Your rating for {escape(title_text, quote=True)}">{stars}</div>'
            f'<p class="rating-text">{text}</p>{clear}</form>')
    return (f'<article class="card" data-card="{_card_key(item)}"><div class="card-poster">{_poster_html(item)}'
            f'<span class="kind">{kind}</span></div>'
            f'<div class="card-info"><h3 class="title">{title_html}</h3><p class="card-sub">{sub}</p>{form}</div></article>')


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


def render_add_dialog(media_type, tmdb_id, return_to, partial=False):
    """A dedicated page (or, with partial=True, just the dialog's inner HTML for app.js's modal):
    quality profiles are fetched here and only here, so viewing the Recommended or AI list never
    pays for a Radarr/Sonarr round-trip you might not need. Looks in both caches (web._find_item)
    rather than forcing a recompute - this can be reached from either the Recommended or the AI page."""
    return_to = web._safe_path(return_to)
    section = "home"
    for prefix, name in (("/ai", "ai"), ("/movies", "movies"), ("/tv", "tv"), ("/library", "library")):
        if return_to.startswith(prefix):
            section = name
            break
    item = web._find_item(media_type, tmdb_id) if not web._is_sample() else None
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


def _can_add(item, can_act):
    """Whether this item gets an "Add to library" link: not sample, and its Radarr/Sonarr is set up."""
    return bool(can_act and ((item["media_type"] == "movie" and config.radarr_configured())
                             or (item["media_type"] == "tv" and config.sonarr_configured())))


def _add_link(item, return_to):
    # A real page nav, not an inline dialog (see _card); app.js opens it in a modal instead.
    dialog_url = "/add-dialog?" + urlencode({"type": item["media_type"], "id": item["tmdb_id"], "return_to": return_to})
    return f'<a class="btn-add" href="{escape(dialog_url, quote=True)}" data-add-dialog>Add to library</a>'


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
    """Inner spans of a .card-meta line: match, year, certification, runtime / seasons."""
    try:
        match = int(item.get("match") or 0)
    except (TypeError, ValueError):
        match = 0
    parts = [f'<span class="match">{match}% match</span>']
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
    return ('<div class="detail-body">'
            f'<p class="card-meta">{_meta_html(item)}</p>'
            + (f'<p class="reason">{reason}</p>' if reason else "")
            + (f'<div class="chips">{chips}</div>' if chips else "")
            + f'<p class="overview">{escape(item.get("overview") or "No overview available.")}</p>{ext}</div>')


def _rate_form(item, return_to):
    """1-5 stars posting to /rate (the same form the Library uses), showing your saved rating (item["stars"]).
    Rating a recommendation never hides it. Shown even in sample mode: the server answers
    "Sample data - not saved" and app.js toasts it."""
    title = escape(_title_text(item), quote=True)
    stars, text, clear = _rate_parts(_star_value(item.get("stars")), _star_value(item.get("plex_stars")))
    return (f'<form class="rate" method="post" action="/rate" data-enhance="rate">{_hidden_fields(item, return_to)}'
            f'<div class="stars" role="group" aria-label="Your rating for {title}">{stars}</div>'
            f'<p class="rating-text">{text}</p>{clear}</form>')


def _card_actions(item, return_to, can_act):
    add = _add_link(item, return_to) if _can_add(item, can_act) else ""
    dismiss = (f'<form class="inline" method="post" action="/dismiss" data-enhance="dismiss">'
               f'{_hidden_fields(item, return_to)}<button type="submit">Not interested</button></form>'
               if can_act else "")
    return f'<div class="card-actions">{add}{dismiss}{_rate_form(item, return_to)}</div>'


def _row_card(item, return_to, can_act, rank=None):
    """A poster card in a row. Reason, overview and actions sit in a <details> (no JS); with JS the
    poster becomes a button that opens the same content in the detail modal."""
    title = escape(_title_text(item))
    rank_html = f'<span class="rank" aria-hidden="true">{int(rank)}</span>' if rank is not None else ""
    lib = '<span class="lib-tag">In library</span>' if item.get("in_library") else ""
    return (f'<article class="card row-card" data-card="{_card_key(item)}">'
            f'<div class="card-poster" data-open-detail>{rank_html}{_poster_html(item)}'
            f'<div class="poster-top"><span class="match">{int(item.get("match") or 0)}% match</span>{lib}</div></div>'
            f'<div class="card-info"><h4 class="title">{title}</h4>'
            f'<details class="card-details"><summary>Details<span class="visually-hidden">: {title}</span></summary>'
            f'{_detail_body(item)}{_card_actions(item, return_to, can_act)}</details></div></article>')


def _library_row_card(item):
    """A "New in your library" card: poster, tag, title (linked to TMDB when known). No actions."""
    link = _web_url(item.get("url"))
    title = escape(_title_text(item))
    if link:
        title = f'<a href="{escape(link, quote=True)}" target="_blank" rel="noopener noreferrer">{title}</a>'
    return (f'<article class="card row-card lib-card"><div class="card-poster">{_poster_html(item)}</div>'
            f'<span class="lib-tag">In library</span>'
            f'<div class="card-info"><h4 class="title">{title}</h4></div></article>')


def _row_html(row, return_to, can_act):
    rid = escape(str(row["id"]), quote=True)
    if row.get("source") == "library":
        cards = "".join(_library_row_card(i) for i in row["items"])
    else:
        numbered = row.get("numbered")
        cards = "".join(_row_card(i, return_to, can_act, n if numbered else None)
                        for n, i in enumerate(row["items"], 1))
    sub = f'<p class="row-sub">{escape(row["subtitle"])}</p>' if row.get("subtitle") else ""
    return (f'<section class="row" data-row="{rid}" aria-labelledby="row-{rid}">'
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
    return (f'<article class="hero-slide{" on" if n == 1 else ""}" data-slide data-card="{_card_key(item)}" '
            f'aria-label="{n} of {total}: {escape(str(item["title"]), quote=True)}"{"" if n == 1 else " hidden"}>'
            f'{_hero_art(item)}'
            f'<div class="card-poster" hidden>{_poster_html(item)}</div>'
            f'<div class="hero-copy"><p class="kicker">Top pick for you</p>'
            f'<h3 class="title hero-title">{title}</h3>'
            f'<p class="hero-meta card-meta">{_meta_html(item)}'
            + (f'<span class="hero-genres">{genres}</span>' if genres else "")
            + '</p>'
            + (f'<p class="reason">{reason}</p>' if reason else "")
            + f'<div class="hero-actions">{primary}'
            f'<details class="card-details hero-details"><summary>More info<span class="visually-hidden">: {title}</span></summary>'
            f'{_detail_body(item)}{_card_actions(item, return_to, can_act)}</details></div></div></article>')


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
                ("added", "Added here"))
_SORT_LABELS = {"added": "Recently added", "title": "Title A-Z", "year": "Newest year", "recent": "Recently watched",
                "rating": "Highest rated"}
_SHOW_LABELS = {"all": "Everything", "unwatched": "Unwatched", "watched": "Watched", "rated": "Rated by you",
                "unrated": "Not rated by you"}


def _library_url(tab, q="", sort=None, show=None, page=1):
    params = [("type", tab)]
    if q:
        params.append(("q", q))
    if sort:
        params.append(("sort", sort))
    if show:
        params.append(("show", show))
    if page and page > 1:
        params.append(("page", page))
    return "/library?" + urlencode(params)


def _library_subtabs(active, q):
    links = []
    for key, label in LIBRARY_TABS:
        params = {"type": key}
        if q:
            params["q"] = q
        links.append(f'<a class="subtab{" on" if key == active else ""}" '
                     f'href="/library?{escape(urlencode(params))}">{escape(label)}</a>')
    return f'<nav class="subtabs" aria-label="Library">{"".join(links)}</nav>'


def _library_toolbar(tab, q, sort, show, sorts, shows):
    def select(name, label, options, labels, current):
        opts = "".join(f'<option value="{escape(o, quote=True)}"{" selected" if o == current else ""}>'
                       f'{escape(labels.get(o, o))}</option>' for o in options)
        return f'<label>{label}<select name="{name}">{opts}</select></label>'
    show_select = select("show", "Show", shows, _SHOW_LABELS, show) if len(shows) > 1 else ""
    return (f'<form class="toolbar" method="get" action="/library" role="search">'
            f'<input type="hidden" name="type" value="{escape(tab, quote=True)}">'
            f'<label>Search<input type="search" name="q" value="{escape(q, quote=True)}" maxlength="100" '
            f'placeholder="Title"></label>'
            f'{select("sort", "Sort by", sorts, _SORT_LABELS, sort)}{show_select}'
            f'<button type="submit" class="btn-ghost">Apply</button></form>')


def _library_pager(tab, q, sort, show, page, pages):
    if pages <= 1:
        return ""
    def link(target, label, rel):
        href = escape(_library_url(tab, q, sort, show, target), quote=True)
        return f'<a href="{href}" rel="{rel}">{label}</a>'
    prev = link(page - 1, "&#8249; Previous", "prev") if page > 1 else '<span class="muted">&#8249; Previous</span>'
    nxt = link(page + 1, "Next &#8250;", "next") if page < pages else '<span class="muted">Next &#8250;</span>'
    return (f'<nav class="pager" aria-label="Pages">{prev}<span class="pager-status">Page {page} of {pages}</span>'
            f'{nxt}</nav>')


def _library_empty(tab, filtered):
    if filtered:
        return ('<div class="empty"><h3>Nothing matches</h3><p>No titles match your search or filters.</p>'
                f'<a class="btn-ghost" href="{escape(_library_url(tab), quote=True)}">Clear filters</a></div>')
    if tab == "added":
        return ('<div class="empty"><h3>Your added list is empty</h3>'
                '<p>Nothing added yet - approve a recommendation from the Recommended page and it\'ll show up here.</p>'
                '<a class="btn-add" href="/recommended">Browse recommendations</a></div>')
    if tab == "watched":
        return ('<div class="empty"><h3>Nothing watched yet</h3>'
                '<p>Titles you watch in Plex show up here, ready to rate.</p></div>')
    return ('<div class="empty"><h3>Your library is empty</h3>'
            '<p>Nothing in your Plex library yet.</p></div>')


def render_library(tab="all", q="", sort=None, show="all", page=1, msg=""):
    """Library: All / Movies / TV shows (the Plex library), Watched (history, with ratings) and
    Added here (the log of what was sent to Radarr/Sonarr). Data comes from the last build's
    snapshot - this never calls Plex."""
    tab = tab if tab in dict(LIBRARY_TABS) else "all"
    sorts, shows = web.LIST_OPTIONS[tab]
    sort = sort if sort in sorts else sorts[0]
    show = show if show in shows else shows[0]
    q = (q or "").strip()[:100]
    return_to = _library_url(tab, q, sort, show, page)
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
    if tab == "watched":
        if web.ratings_changed():
            notes += ('<p class="note">Your ratings changed - refresh to update your recommendations.</p>'
                      + _refresh_form(return_to, "Refresh recommendations"))
        if web._is_sample():
            notes += '<p class="note">Sample data - ratings aren\'t saved.</p>'

    if not items:
        body = f'{subtabs}{messages}{notes}{_library_empty(tab, False)}'
        return _shell(body, "library", "0 titles", return_to=return_to)

    view = web.list_view(items, tab=tab, q=q, sort=sort, show=show, page=page)
    page = view["page"]
    return_to = _library_url(tab, q, sort, show, page)
    toolbar = _library_toolbar(tab, q, sort, show, sorts, shows)
    if view["items"]:
        if tab == "watched":
            cards = "".join(_watched_card(i, return_to) for i in view["items"])
        else:
            cards = "".join(_library_card(i, tab == "added") for i in view["items"])
        grid = f'<div class="grid" data-grid>{cards}</div>'
    else:
        grid = _library_empty(tab, True)
    pager = _library_pager(tab, q, sort, show, page, view["pages"])
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

    items = result["items"][:SHOW]
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


# Inline SVG nav icons (fixed strings, no user data).
_NAV_ICONS = {
    "home": '<path d="M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    "movies": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 9h18M8 5v4M16 5v4M3 15h18"/>',
    "tv": '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M8 21h8M12 18v3M9 2l3 4 3-4"/>',
    "library": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
    "ai": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
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
            f'data-meta-light="{escape(t["bg_light"])}">'
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


def _shell(body, section, subtitle="", show_refresh=False, return_to="/", status_html="", auto_refresh=False,
           cinematic=False):
    """status_html: trusted markup (e.g. UPDATING_HTML) appended to the subtitle. auto_refresh: the
    no-JS fallback for the building screens - app.js polls /api/status instead. cinematic: a ready
    browse page - the hero sits under the transparent top bar and the title strip moves below the rows."""
    heading = dict((key, label) for key, label, _ in NAV_SECTIONS).get(section, "What's Next")
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
            f'<title>{escape(heading)} - What&#39;s Next</title>'
            f'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            f'<meta name="color-scheme" content="dark light">'
            f'<meta name="theme-color" media="(prefers-color-scheme: dark)" content="{escape(theme["bg"])}">'
            f'<meta name="theme-color" media="(prefers-color-scheme: light)" content="{escape(theme["bg_light"])}">'
            f'<link rel="stylesheet" href="/static/app.css"><script src="/static/app.js" defer></script>'
            f'{meta_refresh}</head><body{body_class}>'
            f'<a class="skip-link" href="#main">Skip to content</a>'
            f'<header class="topbar"><a class="brand" href="/"><span class="brand-mark" aria-hidden="true">W</span>'
            f'<h1 class="brand-name">What&#39;s Next</h1></a>'
            f'<nav class="topnav" aria-label="Main">{nav_html}</nav>'
            f'<div class="topbar-actions">{_nav_html(section, ("settings",))}</div></header>'
            f'<main class="app-main" id="main" tabindex="-1">{main}</main>'
            f'<nav class="bottom-nav" aria-label="Main">{nav_html}</nav>'
            f'<div class="toasts" id="toasts" role="status" aria-live="polite"></div>'
            f'</body></html>')


# Last, not first: web.py imports this module at load time, so by the time web is imported here
# every function above already exists - whichever of the two gets imported first.
import web  # noqa: E402
