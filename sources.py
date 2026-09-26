"""Picks where data comes from (real Plex/Tautulli + TMDB, or the built-in sample) and runs the
recommender on it."""
import config
import db
import plex
import radarr
import recommend
import sample
import sonarr
import tautulli
import tmdb


def use_sample(force=None):
    """Sample data is used when asked for, or automatically when nothing's configured yet."""
    return force if force is not None else not config.live_configured()


def _arr_exclusions(notes):
    """Titles already tracked in Radarr/Sonarr, so they're excluded from recommendations even
    before they've been downloaded and shown up in Plex."""
    keys = set()
    if config.radarr_configured():
        try:
            client = radarr.RadarrClient(config.RADARR_URL, config.RADARR_API_KEY)
            keys |= {("movie", i) for i in client.existing_tmdb_ids()}
        except Exception as e:
            notes.append(f"Couldn't reach Radarr: {e}")
    if config.sonarr_configured():
        try:
            client = sonarr.SonarrClient(config.SONARR_URL, config.SONARR_API_KEY)
            keys |= {("tv", i) for i in client.existing_tmdb_ids()}
        except Exception as e:
            notes.append(f"Couldn't reach Sonarr: {e}")
    return keys


def _load_live():
    """Returns (watched, library_keys, notes)."""
    notes = []
    if config.HISTORY_SOURCE == "tautulli":
        watched, skipped = tautulli.TautulliClient(config.TAUTULLI_URL, config.TAUTULLI_API_KEY,
                                                   config.TAUTULLI_USER).load()
        if skipped:
            notes.append(f"{skipped} Tautulli history entries had no TMDB id and were ignored.")
        if config.PLEX_TOKEN:
            # Only used for "everything currently in the library" (to exclude owned-but-unwatched
            # titles) - Tautulli's own history above is what actually drives the taste profile.
            _, library_keys, _ = plex.PlexClient(config.PLEX_URL, config.PLEX_TOKEN).load()
        else:
            library_keys = set()
            notes.append("PLEX_TOKEN not set: can't exclude titles you own but haven't watched yet.")
    else:
        watched, library_keys, skipped = plex.PlexClient(config.PLEX_URL, config.PLEX_TOKEN).load()
        if skipped:
            notes.append(f"{skipped} Plex titles have no TMDB id and were ignored (try Plex's newer 'Plex Movie/TV' agents).")
    library_keys = set(library_keys) | _arr_exclusions(notes)
    return watched, library_keys, notes


def run(sample_mode, limit=200):
    """Returns the recommender's result dict, plus 'sample' (bool) telling which data was used."""
    if sample_mode:
        watched, library_keys = sample.load()
        result = recommend.recommend(watched, library_keys, sample.SampleTmdb(), limit=limit)
        notes = ["Showing made-up sample data. Add PLEX_TOKEN/TAUTULLI_* and TMDB_TOKEN to use your own library."]
    else:
        watched, library_keys, notes = _load_live()
        result = recommend.recommend(watched, library_keys, tmdb.TmdbClient(config.TMDB_TOKEN),
                                     dismissed=db.dismissed(), limit=limit)
    result["notes"] = notes + result["notes"]
    result["sample"] = sample_mode
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
