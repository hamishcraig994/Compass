"""Adds movies to Radarr. Unlike everything else in this app, this writes: adding a movie creates
it in Radarr and (by default) starts a search for it right away."""
from http_util import get_json, post_json


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
        return self._get("/api/v3/qualityprofile")

    def root_folders(self):
        return self._get("/api/v3/rootfolder")

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
            movie.update({"qualityProfileId": quality_profile_id, "rootFolderPath": root_folder,
                         "monitored": True, "addOptions": {"searchForMovie": search}})
            self._post("/api/v3/movie", movie)
            return True, f"Added \"{movie.get('title', 'the movie')}\" to Radarr"
        except Exception as e:
            return False, f"Radarr error: {e}"
