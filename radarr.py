"""Adds movies to Radarr. Unlike everything else in this app, this writes: adding a movie creates
it in Radarr and (by default) starts a search for it right away."""
import arr_library
import db
from http_util import get_json, post_json

PROFILES_MAX_AGE = 600  # quality profiles/root folders rarely change; this is what stood between a
# page nav and a live Radarr round-trip before - fetched on every Recommended/Settings render for
# the "Add to library" dropdown, whether or not you were actually adding anything.


def _positive_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _added_at(raw):
    added = raw.get("added")
    return added if isinstance(added, str) and added and not added.startswith("0001-") else None


def normalize_movie(raw):
    """One Radarr /api/v3/movie entry -> the slim "ArrItem" the Library uses (see arr_library)."""
    tmdb_id, monitored = _positive_int(raw.get("tmdbId")), bool(raw.get("monitored"))
    if raw.get("hasFile"):
        state = "downloaded"
    elif not monitored:
        state = "unmonitored"
    elif raw.get("isAvailable") is False or ("isAvailable" not in raw and raw.get("status") in ("announced", "tba")):
        state = "upcoming"
    else:
        state = "missing"
    return {"media_type": "movie", "service": "radarr", "tmdb_id": tmdb_id, "tvdb_id": None,
            "title": raw.get("title") or "?", "year": _positive_int(raw.get("year")), "added_at": _added_at(raw),
            "monitored": monitored, "arr_state": state, "episodes": None,
            "poster_url": arr_library.poster_url(raw.get("images")),
            "url": f"https://www.themoviedb.org/movie/{tmdb_id}" if tmdb_id else None}


class RadarrClient:
    def __init__(self, base_url, api_key, quality_profile_id=None, root_folder=None):
        self.base_url, self.api_key = base_url.rstrip("/"), api_key
        self._quality_profile_id, self._root_folder = quality_profile_id, root_folder or None

    def _get(self, path, params=None):
        return get_json(self.base_url + path, headers={"X-Api-Key": self.api_key}, params=params)

    def _post(self, path, body):
        return post_json(self.base_url + path, headers={"X-Api-Key": self.api_key}, body=body)

    def test_connection(self):
        """Returns (ok, message) - never raises, so the Settings page can show it either way."""
        try:
            status = self._get("/api/v3/system/status")
            return True, f"Connected to Radarr v{status.get('version', '?')}"
        except Exception as e:
            return False, str(e)

    def quality_profiles(self):
        key = f"radarr_profiles:{self.base_url}"
        cached = db.cache_get(key, PROFILES_MAX_AGE)
        if cached is not None:
            return cached
        result = self._get("/api/v3/qualityprofile")
        db.cache_put(key, result)
        return result

    def root_folders(self):
        key = f"radarr_folders:{self.base_url}"
        cached = db.cache_get(key, PROFILES_MAX_AGE)
        if cached is not None:
            return cached
        result = self._get("/api/v3/rootfolder")
        db.cache_put(key, result)
        return result

    def _resolved_quality_profile_id(self):
        """Your configured profile if you set one, else whichever one Radarr lists first."""
        if self._quality_profile_id is not None:
            return self._quality_profile_id
        profiles = self.quality_profiles()
        return profiles[0]["id"] if profiles else None

    def _resolved_root_folder(self):
        if self._root_folder:
            return self._root_folder
        folders = self.root_folders()
        return folders[0]["path"] if folders else None

    def library(self):
        """Every movie in Radarr as normalized items - one GET. Raises on failure."""
        return [normalize_movie(m) for m in self._get("/api/v3/movie") if isinstance(m, dict)]

    def existing_tmdb_ids(self):
        """Every movie already in Radarr, so recommendations can exclude them even before they've
        been downloaded and shown up in Plex."""
        movies = self._get("/api/v3/movie")
        return {m["tmdbId"] for m in movies if m.get("tmdbId")}

    def _exists(self, tmdb_id):
        try:
            return bool(self._get("/api/v3/movie", {"tmdbId": tmdb_id}))
        except Exception:
            return False

    def add(self, tmdb_id, search=True):
        """Returns (success, message). Never raises for an expected outcome (already added, not
        found, nothing configured) - only a real connection problem surfaces as a message too."""
        try:
            if self._exists(tmdb_id):
                return False, "Already in Radarr"
            movie = self._get("/api/v3/movie/lookup/tmdb", {"tmdbId": tmdb_id})
            if not movie:
                return False, "Radarr couldn't find this movie"
            quality_profile_id = self._resolved_quality_profile_id()
            root_folder = self._resolved_root_folder()
            if quality_profile_id is None or root_folder is None:
                return False, "Radarr has no quality profile or root folder to add into"
            # monitored tracks search: unmonitored means Radarr's own automatic search/RSS cycle
            # would also grab it shortly after, regardless of the "search now" choice - so "don't
            # search yet" has to mean "don't monitor yet" too, or it wouldn't actually be honored.
            movie.update({"qualityProfileId": quality_profile_id, "rootFolderPath": root_folder,
                         "monitored": search, "addOptions": {"searchForMovie": search}})
            self._post("/api/v3/movie", movie)
            return True, f"Added \"{movie.get('title', 'the movie')}\" to Radarr"
        except Exception as e:
            return False, f"Radarr error: {e}"
