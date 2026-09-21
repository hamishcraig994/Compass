"""Picks where data comes from (real Plex + TMDB, or the built-in sample) and runs the recommender on it."""
import config
import db
import plex
import recommend
import sample
import tmdb


def use_sample(force=None):
    """Sample data is used when asked for, or automatically when Plex/TMDB tokens aren't set up yet."""
    return force if force is not None else not config.live_configured()


def run(sample_mode, limit=200):
    """Returns the recommender's result dict, plus 'sample' (bool) telling which data was used."""
    if sample_mode:
        watched, library_keys = sample.load()
        result = recommend.recommend(watched, library_keys, sample.SampleTmdb(), limit=limit)
        notes = ["Showing made-up sample data. Add PLEX_TOKEN and TMDB_TOKEN to use your own library."]
    else:
        watched, library_keys, skipped = plex.PlexClient(config.PLEX_URL, config.PLEX_TOKEN).load()
        result = recommend.recommend(watched, library_keys, tmdb.TmdbClient(config.TMDB_TOKEN),
                                     dismissed=db.dismissed(), limit=limit)
        notes = []
        if skipped:
            notes.append(f"{skipped} Plex titles have no TMDB id and were ignored (try Plex's newer 'Plex Movie/TV' agents).")
    result["notes"] = notes + result["notes"]
    result["sample"] = sample_mode
    result["watched_count"] = len(watched)
    return result
