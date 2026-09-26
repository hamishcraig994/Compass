"""Adds shows to Sonarr. Unlike everything else in this app, this writes: adding a show creates it
in Sonarr and (by default) starts a search for missing episodes right away.

Sonarr's own primary key for a show is its TVDB id, not TMDB's - see tmdb.py's external_ids()."""
from http_util import get_json, post_json


class SonarrClient:
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
            return True, f"Connected to Sonarr v{status.get('version', '?')}"
        except Exception as e:
            return False, str(e)

    def quality_profiles(self):
        return self._get("/api/v3/qualityprofile")

    def root_folders(self):
        return self._get("/api/v3/rootfolder")

    def _resolved_quality_profile_id(self):
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
        """TMDB ids of shows already in Sonarr, for exclusion. Sonarr keys shows by TVDB id, but
        recent versions also resolve and store a tmdbId per show - only those are usable here
        without an extra TMDB lookup per show, so a show Sonarr hasn't matched to TMDB yet is (rarely)
        missed rather than wrongly excluded."""
        series = self._get("/api/v3/series")
        return {s["tmdbId"] for s in series if s.get("tmdbId")}

    def _exists(self, tvdb_id):
        try:
            series = self._get("/api/v3/series")
            return any(s.get("tvdbId") == tvdb_id for s in series)
        except Exception:
            return False

    def add(self, tvdb_id, search=True):
        """Returns (success, message). Never raises for an expected outcome."""
        try:
            if self._exists(tvdb_id):
                return False, "Already in Sonarr"
            results = self._get("/api/v3/series/lookup", {"term": f"tvdb:{tvdb_id}"})
            show = results[0] if results else None
            if not show:
                return False, "Sonarr couldn't find this show"
            quality_profile_id = self._resolved_quality_profile_id()
            root_folder = self._resolved_root_folder()
            if quality_profile_id is None or root_folder is None:
                return False, "Sonarr has no quality profile or root folder to add into"
            # monitored tracks search: unmonitored means Sonarr's own automatic search/RSS cycle
            # would also grab it shortly after, regardless of the "search now" choice - so "don't
            # search yet" has to mean "don't monitor yet" too, or it wouldn't actually be honored.
            show.update({"qualityProfileId": quality_profile_id, "rootFolderPath": root_folder,
                        "monitored": search, "addOptions": {"searchForMissingEpisodes": search}})
            self._post("/api/v3/series", show)
            return True, f"Added \"{show.get('title', 'the show')}\" to Sonarr"
        except Exception as e:
            return False, f"Sonarr error: {e}"
