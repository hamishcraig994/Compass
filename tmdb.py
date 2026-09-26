"""TMDB (themoviedb.org) client. Every title we look up is cached in SQLite, so a title costs one request
per month and re-running is fast and gentle on TMDB's servers."""
import db
from http_util import get_json

BASE = "https://api.themoviedb.org/3"
DETAILS_MAX_AGE = 30 * 24 * 3600
GENRES_MAX_AGE = 30 * 24 * 3600

# TMDB uses different genre names for TV. Fold them into the movie names so "sci-fi" is one taste, not two.
GENRE_ALIASES = {"Sci-Fi & Fantasy": "Science Fiction", "Action & Adventure": "Action", "War & Politics": "War"}
_REVERSE_ALIASES = {v: k for k, v in GENRE_ALIASES.items()}


def canonical_genre(name):
    return GENRE_ALIASES.get(name, name)


def normalize(raw, media_type):
    """TMDB's raw JSON (movie or tv) -> the one flat shape the rest of the app uses."""
    if media_type == "movie":
        title, date = raw.get("title"), raw.get("release_date")
        keywords = (raw.get("keywords") or {}).get("keywords", [])
        directors = [c["name"] for c in (raw.get("credits") or {}).get("crew", []) if c.get("job") == "Director"]
    else:
        title, date = raw.get("name"), raw.get("first_air_date")
        keywords = (raw.get("keywords") or {}).get("results", [])
        directors = [c["name"] for c in raw.get("created_by") or []]  # a show's "director" is its creator
    poster = raw.get("poster_path")
    return {
        "media_type": media_type,
        "tmdb_id": raw["id"],
        "title": title or "?",
        "year": int(date[:4]) if date and date[:4].isdigit() else None,
        "release_date": date or None,  # full YYYY-MM-DD (first air date for shows)
        "overview": raw.get("overview") or "",
        "poster_url": f"https://image.tmdb.org/t/p/w342{poster}" if poster else None,
        "url": f"https://www.themoviedb.org/{media_type}/{raw['id']}",
        "genres": [canonical_genre(g["name"]) for g in raw.get("genres") or []],
        "keywords": [k["name"] for k in keywords],
        "directors": directors,
        "cast": [c["name"] for c in (raw.get("credits") or {}).get("cast", [])[:5]],
        "vote_average": raw.get("vote_average") or 0,
        "vote_count": raw.get("vote_count") or 0,
        "recommendations": [r["id"] for r in (raw.get("recommendations") or {}).get("results", [])],
    }


class TmdbClient:
    def __init__(self, token):
        # A v4 "Read Access Token" is a long JWT sent as a Bearer header; a v3 API key is 32 characters.
        if len(token) > 60:
            self.headers, self.params = {"Authorization": f"Bearer {token}"}, {}
        else:
            self.headers, self.params = {}, {"api_key": token}

    def _get(self, path, params=None):
        return get_json(BASE + path, headers=self.headers, params={**self.params, **(params or {})})

    def test_connection(self):
        """Returns (ok, message) - never raises, so the Settings page can show it either way.
        /authentication is TMDB's own purpose-built credential check."""
        try:
            result = self._get("/authentication")
            if result.get("success"):
                return True, "TMDB token is valid"
            return False, result.get("status_message", "TMDB rejected this token")
        except Exception as e:
            return False, str(e)

    def details(self, media_type, tmdb_id):
        key = f"details:v2:{media_type}:{tmdb_id}"  # v2: added release_date
        cached = db.cache_get(key, DETAILS_MAX_AGE)
        if cached:
            return cached
        raw = self._get(f"/{media_type}/{tmdb_id}", {"append_to_response": "keywords,credits,recommendations"})
        result = normalize(raw, media_type)
        db.cache_put(key, result)
        return result

    def _genre_id(self, media_type, name):
        key = f"genres:{media_type}"
        genres = db.cache_get(key, GENRES_MAX_AGE)
        if genres is None:
            genres = self._get(f"/genre/{media_type}/list")["genres"]
            db.cache_put(key, genres)
        by_name = {g["name"]: g["id"] for g in genres}
        return by_name.get(name) or by_name.get(_REVERSE_ALIASES.get(name, ""))

    def discover(self, media_type, genre_name):
        """IDs of well-liked, well-voted titles in a genre (used for 'popular in <genre>' suggestions)."""
        genre_id = self._genre_id(media_type, genre_name)
        if genre_id is None:
            return []
        found = self._get(f"/discover/{media_type}", {
            "with_genres": genre_id, "sort_by": "vote_average.desc", "vote_count.gte": 1000})
        return [r["id"] for r in found.get("results", [])]

    def external_ids(self, media_type, tmdb_id):
        """Other databases' ids for one title - currently just TVDB, which Sonarr needs to add a
        show (Sonarr's own primary key is TVDB, not TMDB)."""
        key = f"external_ids:{media_type}:{tmdb_id}"
        cached = db.cache_get(key, DETAILS_MAX_AGE)
        if cached is not None:
            return cached
        raw = self._get(f"/{media_type}/{tmdb_id}/external_ids")
        result = {"tvdb_id": raw.get("tvdb_id")}
        db.cache_put(key, result)
        return result

    def search(self, media_type, title, year=None):
        """Finds a real TMDB id for a title by name - the only way an AI-suggested title (which
        names a title, never a trustworthy id) becomes an actual candidate in recommend.py. Prefers
        a result whose year is within 1 of the one given (release years sometimes differ by a year
        across regions/sources); returns None rather than guessing if nothing matches closely -
        letting the wrong movie through silently is worse than skipping one."""
        key = f"search:{media_type}:{title.strip().lower()}:{year or ''}"
        cached = db.cache_get(key, DETAILS_MAX_AGE)
        if cached is not None:
            return cached.get("tmdb_id")
        results = self._get(f"/search/{media_type}", {"query": title}).get("results", [])
        result = None
        if year:
            field = "release_date" if media_type == "movie" else "first_air_date"
            close = []
            for r in results:
                date = r.get(field) or ""
                if date[:4].isdigit() and abs(int(date[:4]) - year) <= 1:
                    close.append(r)
            if close:
                result = max(close, key=lambda r: r.get("vote_count", 0))
        elif results:
            result = results[0]
        db.cache_put(key, {"tmdb_id": result["id"] if result else None})
        return result["id"] if result else None

    def trending(self, media_type):
        """IDs of what's trending on TMDB this week (all genres; we filter by your taste later)."""
        found = self._get(f"/trending/{media_type}/week")
        return [r["id"] for r in found.get("results", [])]

    def new_releases(self, media_type, genre_name, since, until):
        """IDs of the most popular titles in a genre released between two YYYY-MM-DD dates. Fresh titles
        have few votes, so this sorts by popularity and only asks for a handful of votes."""
        genre_id = self._genre_id(media_type, genre_name)
        if genre_id is None:
            return []
        field = "primary_release_date" if media_type == "movie" else "first_air_date"
        found = self._get(f"/discover/{media_type}", {
            "with_genres": genre_id, "sort_by": "popularity.desc", "vote_count.gte": 20,
            f"{field}.gte": since, f"{field}.lte": until})
        return [r["id"] for r in found.get("results", [])]
