"""Picks where data comes from (real Plex/Tautulli + TMDB, or the built-in sample) and runs the
recommender on it."""
import config
import db
import plex
import recommend
import sample
import tautulli
import tmdb


def use_sample(force=None):
    """Sample data is used when asked for, or automatically when nothing's configured yet."""
    return force if force is not None else not config.live_configured()


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
