"""Picks where data comes from (real Plex/Tautulli + TMDB, or the built-in sample) and runs the
recommender on it."""
import ai
import browse
import config
import db
import plex
import profile
import radarr
import recommend
import sample
import sonarr
import tautulli
import tmdb


def use_sample(force=None):
    """Sample data is used when asked for, or automatically when nothing's configured yet."""
    return force if force is not None else not config.live_configured()


def _arr_library(notes):
    """One library() call per configured Radarr/Sonarr. Returns (keys, arr): keys are the (media_type,
    tmdb_id) pairs already tracked there, so they're excluded from recommendations even before they've
    been downloaded and shown up in Plex; arr is what the Library page shows (result["arr"]). A service
    that can't be reached gets state "error" plus a note, never an exception."""
    items = []
    arr = {"radarr": {"state": "off", "count": 0}, "sonarr": {"state": "off", "count": 0}}
    for name, configured, make in (
            ("radarr", config.radarr_configured, lambda: radarr.RadarrClient(config.RADARR_URL, config.RADARR_API_KEY)),
            ("sonarr", config.sonarr_configured, lambda: sonarr.SonarrClient(config.SONARR_URL, config.SONARR_API_KEY))):
        if not configured():
            continue
        try:
            found = make().library()
            items.extend(found)
            arr[name] = {"state": "ok", "count": len(found)}
        except Exception as e:
            arr[name] = {"state": "error", "count": 0}
            notes.append(f"Couldn't reach {name.capitalize()}: {e}")
    arr["items"] = items
    return {(i["media_type"], i["tmdb_id"]) for i in items if i.get("tmdb_id")}, arr


def _load_live():
    """Returns (watched, library_keys, notes, library, arr). arr is _arr_library()'s result["arr"] dict. library is Plex's full item list (raw
    parse_library_item() dicts, thumbs included), or None when there's no PLEX_TOKEN to read it with."""
    notes = []
    if config.HISTORY_SOURCE == "tautulli":
        watched, skipped = tautulli.TautulliClient(config.TAUTULLI_URL, config.TAUTULLI_API_KEY,
                                                   config.TAUTULLI_USER).load()
        if skipped:
            notes.append(f"{skipped} Tautulli history entries had no TMDB id and were ignored.")
        if config.PLEX_TOKEN:
            # Only used for "everything currently in the library" (to exclude owned-but-unwatched
            # titles) - Tautulli's own history above is what actually drives the taste profile.
            _, library_keys, _, library = plex.PlexClient(config.PLEX_URL, config.PLEX_TOKEN).load()
        else:
            library_keys, library = set(), None
            notes.append("PLEX_TOKEN not set: can't exclude titles you own but haven't watched yet.")
    else:
        watched, library_keys, skipped, library = plex.PlexClient(config.PLEX_URL, config.PLEX_TOKEN).load()
        if skipped:
            notes.append(f"{skipped} Plex titles have no TMDB id and were ignored (try Plex's newer 'Plex Movie/TV' agents).")
    arr_keys, arr = _arr_library(notes)
    return watched, set(library_keys) | arr_keys, notes, library, arr


def _valid_thumb(thumb):
    """Only Plex paths under /library/ are kept, so the poster proxy can't be aimed at another host."""
    return thumb if isinstance(thumb, str) and thumb.startswith("/library/") else None


def _snapshot(watched, library, tmdb_client=None):
    """What the Library page shows, kept in the build result so pages never call Plex or TMDB.
    Returns (library_items | None, watched_items, thumbs). thumbs: {ratingKey: thumb path} - server
    side only; no item dict carries a thumb. Watched posters/links come from TMDB's local cache only
    (tmdb_client.cached_details), never a request."""
    thumbs = {}

    def poster_key(raw):
        key, thumb = raw.get("rating_key"), _valid_thumb(raw.get("thumb"))
        if config.PLEX_TOKEN and isinstance(key, int) and not isinstance(key, bool) and thumb:
            thumbs[key] = thumb
            return key
        return None

    library_items = None
    if library is not None:
        library_items = []
        for raw in library:
            tmdb_id = raw.get("tmdb_id")
            library_items.append({
                "media_type": raw["media_type"], "tmdb_id": tmdb_id, "title": raw.get("title") or "?",
                "year": raw.get("year"), "added_at": raw.get("added_at"),
                "watched": (raw.get("view_count") or 0) > 0, "progress": raw.get("progress"),
                "poster_key": poster_key(raw),
                "url": f"https://www.themoviedb.org/{raw['media_type']}/{tmdb_id}" if tmdb_id else None})
    watched_items = []
    for raw in watched:
        details = None
        if tmdb_client is not None:
            try:
                details = tmdb_client.cached_details(raw["media_type"], raw["tmdb_id"])
            except Exception:
                details = None
        watched_items.append({
            "media_type": raw["media_type"], "tmdb_id": raw["tmdb_id"], "title": raw.get("title") or "?",
            "year": raw.get("year"), "last_viewed": raw.get("last_viewed"), "user_rating": raw.get("user_rating"),
            "view_count": raw.get("view_count") or 0, "progress": raw.get("progress"),
            "poster_key": poster_key(raw),
            "poster_url": (details or {}).get("poster_url"), "url": (details or {}).get("url")})
    return library_items, watched_items, thumbs


def ai_client():
    return ai.AiClient(config.AI_TOKEN, config.AI_PROVIDER_URL, config.AI_MODEL) if config.ai_configured() else None


def _fill_hero_details(items, client):
    """Live builds only. Hero picks cached before the cinematic fields existed lack "backdrop_url";
    re-fetch their details (skipping the cache read) so the hero has real art straight away. At most
    browse.HERO_MAX per kind (15 in total, deduplicated). One failing title never aborts the rest.
    Mutates the item dicts in place; returns how many were refreshed."""
    done, refreshed = set(), 0
    for kind in browse.KINDS:
        for item in browse.hero_picks(items, kind):
            key = (item["media_type"], item["tmdb_id"])
            if key in done or "backdrop_url" in item:
                continue
            done.add(key)
            try:
                fresh = client.details(item["media_type"], item["tmdb_id"], refresh=True)
                item.update({k: fresh.get(k) for k in browse.HERO_FIELDS})
            except Exception:
                continue
            refreshed += 1
    return refreshed


def run(sample_mode, limit=200):
    """Returns the recommender's result dict, plus 'sample' (bool) telling which data was used.
    Never uses AI - that's a separate, deliberate action (see generate_ai_recommendations()),
    since every AI request has a real cost and this runs automatically on every cache refresh."""
    if sample_mode:
        watched, library_keys = sample.load()
        arr = sample.arr_library()
        library_keys = set(library_keys) | {(i["media_type"], i["tmdb_id"]) for i in arr["items"] if i.get("tmdb_id")}
        result = recommend.recommend(watched, library_keys, sample.SampleTmdb(), limit=limit)
        snapshot = _snapshot(watched, sample.library_items())  # sample mode ignores stored ratings and the TMDB cache
        notes = ["Showing made-up sample data. Add PLEX_TOKEN/TAUTULLI_* and TMDB_TOKEN to use your own library."]
    else:
        watched, library_keys, notes, library, arr = _load_live()
        client = tmdb.TmdbClient(config.TMDB_TOKEN)
        ratings = db.ratings()
        result = recommend.recommend(profile.apply_ratings(watched, ratings), library_keys, client,
                                     dismissed=db.dismissed(), limit=limit,
                                     disliked={k: s for k, s in ratings.items() if s <= profile.DISLIKE_MAX_STARS})
        _fill_hero_details(result["items"], client)
        snapshot = _snapshot(watched, library, client)  # raw history: your ratings are overlaid when shown
    result["library"], result["watched"], result["thumbs"] = snapshot
    result["arr"] = arr
    result["notes"] = notes + result["notes"]
    result["sample"] = sample_mode
    result["watched_count"] = len(watched)
    return result


def generate_ai_recommendations(limit=50):
    """Runs the recommender in AI-only mode: just the AI's own ideas, verified against real TMDB
    data and scored the normal way - none of the TMDB-linked/genre/trending/new-release candidates
    the main Recommended list also has. Meant to be triggered deliberately (the AI page's Generate
    button) - unlike run(), every call here costs a real AI request. Never runs in sample mode."""
    empty_profile = {c: {} for c in ("genre", "keyword", "director", "actor")}
    if not config.ai_configured():
        return {"items": [], "profile": empty_profile, "notes": ["AI isn't configured - add a token in "
                "Settings -> AI first."], "sample": False, "watched_count": 0}
    watched, library_keys, notes, _, _ = _load_live()
    ratings = db.ratings()
    result = recommend.recommend(profile.apply_ratings(watched, ratings), library_keys, tmdb.TmdbClient(config.TMDB_TOKEN),
                                 dismissed=db.dismissed(), limit=limit, ai=ai_client(), ai_only=True,
                                 disliked={k: s for k, s in ratings.items() if s <= profile.DISLIKE_MAX_STARS})
    result["notes"] = notes + result["notes"]
    result["sample"] = False
    result["watched_count"] = len(watched)
    return result


def radarr_client():
    return radarr.RadarrClient(config.RADARR_URL, config.RADARR_API_KEY, config.RADARR_QUALITY_PROFILE_ID,
                               config.RADARR_ROOT_FOLDER) if config.radarr_configured() else None


def sonarr_client():
    return sonarr.SonarrClient(config.SONARR_URL, config.SONARR_API_KEY, config.SONARR_QUALITY_PROFILE_ID,
                               config.SONARR_ROOT_FOLDER) if config.sonarr_configured() else None


def add_to_library(media_type, tmdb_id, search=True, quality_profile_id=None):
    """Adds one recommended title to Radarr or Sonarr. Returns (success, message).
    quality_profile_id, if given (e.g. chosen in the "Add to library" dialog), overrides the
    configured default for just this one add."""
    if media_type == "movie":
        if not config.radarr_configured():
            return False, "Radarr isn't configured"
        client = radarr.RadarrClient(config.RADARR_URL, config.RADARR_API_KEY,
                                     quality_profile_id if quality_profile_id is not None else config.RADARR_QUALITY_PROFILE_ID,
                                     config.RADARR_ROOT_FOLDER)
        return client.add(tmdb_id, search=search)

    if not config.sonarr_configured():
        return False, "Sonarr isn't configured"
    try:
        tvdb_id = tmdb.TmdbClient(config.TMDB_TOKEN).external_ids("tv", tmdb_id).get("tvdb_id")
    except Exception as e:
        return False, f"Couldn't resolve this show for Sonarr: {e}"
    if not tvdb_id:
        return False, "TMDB has no TVDB id for this show, so Sonarr can't look it up"
    client = sonarr.SonarrClient(config.SONARR_URL, config.SONARR_API_KEY,
                                 quality_profile_id if quality_profile_id is not None else config.SONARR_QUALITY_PROFILE_ID,
                                 config.SONARR_ROOT_FOLDER)
    return client.add(tvdb_id, search=search)
