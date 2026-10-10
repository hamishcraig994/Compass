"""Adds shows to Sonarr. Unlike everything else in this app, this writes: adding a show creates it
in Sonarr and (by default) starts a search for missing episodes right away.

Sonarr's own primary key for a show is its TVDB id, not TMDB's - see tmdb.py's external_ids()."""
import arr_library
import db
from http_util import get_json, post_json, put_json

PROFILES_MAX_AGE = 600  # quality profiles/root folders rarely change; see radarr.py's PROFILES_MAX_AGE


def _positive_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _added_at(raw):
    added = raw.get("added")
    return added if isinstance(added, str) and added and not added.startswith("0001-") else None


def _seasons(raw):
    """Per-season [{"number", "monitored", "have", "total"}] from a series resource, sorted by number."""
    out = {}
    for s in raw.get("seasons") if isinstance(raw.get("seasons"), list) else []:
        number = s.get("seasonNumber") if isinstance(s, dict) else None
        if not isinstance(number, int) or isinstance(number, bool) or number < 0:
            continue
        stats = s.get("statistics") if isinstance(s.get("statistics"), dict) else {}
        total = _count(stats.get("totalEpisodeCount")) or _count(stats.get("episodeCount"))
        out[number] = {"number": number, "monitored": bool(s.get("monitored")),
                       "have": _count(stats.get("episodeFileCount")), "total": total}
    return [out[n] for n in sorted(out)]


def normalize_series(raw):
    """One Sonarr /api/v3/series entry -> the slim "ArrItem" the Library uses (see arr_library)."""
    tmdb_id, monitored = _positive_int(raw.get("tmdbId")), bool(raw.get("monitored"))
    stats = raw.get("statistics") if isinstance(raw.get("statistics"), dict) else {}
    have, total = _count(stats.get("episodeFileCount")), _count(stats.get("episodeCount"))
    if total > 0 and have >= total:
        state = "downloaded"
    elif have > 0:
        state = "partial"
    elif not monitored:
        state = "unmonitored"
    elif raw.get("status") == "upcoming" or total == 0:
        state = "upcoming"
    else:
        state = "missing"
    return {"media_type": "tv", "service": "sonarr", "tmdb_id": tmdb_id, "tvdb_id": _positive_int(raw.get("tvdbId")),
            "title": raw.get("title") or "?", "year": _positive_int(raw.get("year")), "added_at": _added_at(raw),
            "monitored": monitored, "arr_state": state, "episodes": {"have": have, "total": total},
            "arr_id": _positive_int(raw.get("id")), "seasons": _seasons(raw),
            "poster_url": arr_library.poster_url(raw.get("images")),
            "url": f"https://www.themoviedb.org/tv/{tmdb_id}" if tmdb_id else None}


class SonarrClient:
    def __init__(self, base_url, api_key, quality_profile_id=None, root_folder=None):
        self.base_url, self.api_key = base_url.rstrip("/"), api_key
        self._quality_profile_id, self._root_folder = quality_profile_id, root_folder or None
        self.last_item = None  # normalize_series() of the last successful add()'s response, else None

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
        key = f"sonarr_profiles:{self.base_url}"
        cached = db.cache_get(key, PROFILES_MAX_AGE)
        if cached is not None:
            return cached
        result = self._get("/api/v3/qualityprofile")
        db.cache_put(key, result)
        return result

    def root_folders(self):
        key = f"sonarr_folders:{self.base_url}"
        cached = db.cache_get(key, PROFILES_MAX_AGE)
        if cached is not None:
            return cached
        result = self._get("/api/v3/rootfolder")
        db.cache_put(key, result)
        return result

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

    def library(self):
        """Every show in Sonarr as normalized items - one GET. Raises on failure."""
        return [normalize_series(s) for s in self._get("/api/v3/series") if isinstance(s, dict)]

    def existing_tmdb_ids(self):
        """TMDB ids of shows already in Sonarr, for exclusion. Sonarr keys shows by TVDB id, but
        recent versions also resolve and store a tmdbId per show - only those are usable here
        without an extra TMDB lookup per show, so a show Sonarr hasn't matched to TMDB yet is (rarely)
        missed rather than wrongly excluded."""
        series = self._get("/api/v3/series")
        return {s["tmdbId"] for s in series if s.get("tmdbId")}

    def _find(self, tvdb_id):
        """The series resource for a TVDB id, or None. Filtered client-side too, because some versions
        ignore the tvdbId parameter. Raises on a request failure."""
        found = self._get("/api/v3/series", {"tvdbId": tvdb_id})
        for series in found if isinstance(found, list) else []:
            if isinstance(series, dict) and series.get("tvdbId") == tvdb_id:
                return series
        return None

    def _put(self, path, body):
        return put_json(self.base_url + path, headers={"X-Api-Key": self.api_key}, body=body)

    def add(self, tvdb_id, search=True, seasons=None):
        """Returns (success, message). Never raises for an expected outcome. seasons: None (everything;
        "Already in Sonarr" if tracked), "all", or a list of season numbers (new series: only those are
        monitored; tracked series: those get monitored too, additively)."""
        self.last_item = None
        try:
            try:
                existing = self._find(tvdb_id)
            except Exception:
                existing = None  # Sonarr refuses a duplicate itself
            if existing is not None:
                if seasons is None:
                    return False, "Already in Sonarr"
                return self._request_more(existing, search, seasons)
            results = self._get("/api/v3/series/lookup", {"term": f"tvdb:{tvdb_id}"})
            show = results[0] if results else None
            if not show:
                return False, "Sonarr couldn't find this show"
            title = show.get("title", "the show")
            listed = [s for s in show.get("seasons") or [] if isinstance(s, dict)
                      and isinstance(s.get("seasonNumber"), int)]
            missing = self._unlisted(seasons, {s["seasonNumber"] for s in listed})
            if missing is not None:
                return False, f"Season {missing} isn't listed for \"{title}\" in Sonarr"
            quality_profile_id = self._resolved_quality_profile_id()
            root_folder = self._resolved_root_folder()
            if quality_profile_id is None or root_folder is None:
                return False, "Sonarr has no quality profile or root folder to add into"
            wanted = (lambda n: n > 0) if seasons in (None, "all") else (lambda n: n in seasons)
            for s in listed:
                s["monitored"] = wanted(s["seasonNumber"]) and s["seasonNumber"] > 0
            # monitored tracks search: unmonitored means Sonarr's own automatic search/RSS cycle
            # would also grab it shortly after, regardless of the "search now" choice - so "don't
            # search yet" has to mean "don't monitor yet" too, or it wouldn't actually be honored.
            # addOptions.monitor is deliberately omitted so Sonarr keeps our per-season flags.
            show.update({"qualityProfileId": quality_profile_id, "rootFolderPath": root_folder,
                        "monitored": search,
                        "monitorNewItems": "all" if seasons in (None, "all") else "none",
                        "addOptions": {"searchForMissingEpisodes": search, "searchForCutoffUnmetEpisodes": False,
                                       "ignoreEpisodesWithFiles": False}})
            created = self._post("/api/v3/series", show)
            self.last_item = normalize_series(created) if isinstance(created, dict) else None
            return True, f"Added \"{title}\" to Sonarr"
        except Exception as e:
            return False, f"Sonarr error: {e}"

    @staticmethod
    def _unlisted(seasons, numbers):
        """The first requested season number that isn't in numbers, else None."""
        if isinstance(seasons, list):
            for n in seasons:
                if n not in numbers:
                    return n
        return None

    def _request_more(self, series, search, seasons):
        """Monitor more seasons of a series Sonarr already has (additive only)."""
        title = series.get("title", "the show")
        listed = [s for s in series.get("seasons") or [] if isinstance(s, dict)
                  and isinstance(s.get("seasonNumber"), int)]
        missing = self._unlisted(seasons, {s["seasonNumber"] for s in listed})
        if missing is not None:
            return False, f"Season {missing} isn't listed for \"{title}\" in Sonarr"
        changed = []
        for s in listed:
            n = s["seasonNumber"]
            if n > 0 and (seasons == "all" or n in seasons) and not s.get("monitored"):
                s["monitored"] = True
                changed.append(n)
        if not changed:
            return False, f"Already monitoring those seasons of \"{title}\" in Sonarr"
        series["monitored"] = True
        updated = self._put(f"/api/v3/series/{series['id']}", series)
        self.last_item = normalize_series(updated) if isinstance(updated, dict) else None
        searched = False
        if search:
            try:
                if seasons == "all":
                    self._post("/api/v3/command", {"name": "SeriesSearch", "seriesId": series["id"]})
                else:
                    for n in changed:
                        self._post("/api/v3/command", {"name": "SeasonSearch", "seriesId": series["id"],
                                                       "seasonNumber": n})
                searched = True
            except Exception:
                pass  # the seasons are monitored already; Sonarr's own cycle will pick them up
        if len(changed) <= 2:
            label = "season " + " and ".join(map(str, changed))
        else:
            label = "seasons " + ", ".join(map(str, changed[:-1])) + f" and {changed[-1]}"
        return True, (f"Now monitoring {label} of \"{title}\" in Sonarr" + (" - searching now" if searched else ""))
