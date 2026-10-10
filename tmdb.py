"""TMDB (themoviedb.org) client. Every title we look up is cached in SQLite, so a title costs one request
per month and re-running is fast and gentle on TMDB's servers."""
import re

import db
from http_util import get_json

BASE = "https://api.themoviedb.org/3"
DETAILS_MAX_AGE = 30 * 24 * 3600
GENRES_MAX_AGE = 30 * 24 * 3600
SEARCH_MAX_AGE = 24 * 3600     # free-text search results (the title search page)
SEARCH_MAX_RESULTS = 20        # TMDB's first page
SEARCH_TIMEOUT = 8             # seconds: a page-view search shouldn't hang like a build can
TITLE_MAX_AGE = 3 * 24 * 3600  # the title detail page's extras (cast, seasons, similar...)
CAST_MAX = 12
CREW_MAX = 6
SIMILAR_MAX = 20
NAMES_MAX = 3                  # networks / studios shown
CERT_COUNTRIES = ("GB", "US")  # age-rating countries to try, in order

# TMDB uses different genre names for TV. Fold them into the movie names so "sci-fi" is one taste, not two.
GENRE_ALIASES = {"Sci-Fi & Fantasy": "Science Fiction", "Action & Adventure": "Action", "War & Politics": "War"}
_REVERSE_ALIASES = {v: k for k, v in GENRE_ALIASES.items()}


def canonical_genre(name):
    return GENRE_ALIASES.get(name, name)


def _positive_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _certification(raw, media_type):
    """First non-empty age rating for CERT_COUNTRIES (GB, then US), or None."""
    key, field = ("release_dates", None) if media_type == "movie" else ("content_ratings", "rating")
    results = (raw.get(key) or {}).get("results") or []
    for country in CERT_COUNTRIES:
        for entry in results:
            if not isinstance(entry, dict) or entry.get("iso_3166_1") != country:
                continue
            if field:
                values = [entry.get(field)]
            else:
                values = [r.get("certification") for r in entry.get("release_dates") or [] if isinstance(r, dict)]
            for value in values:
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


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
    poster, backdrop = raw.get("poster_path"), raw.get("backdrop_path")
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
        "backdrop_url": f"https://image.tmdb.org/t/p/w1280{backdrop}" if backdrop else None,
        "poster_large_url": f"https://image.tmdb.org/t/p/w780{poster}" if poster else None,
        "runtime": _positive_int(raw.get("runtime")) if media_type == "movie" else None,
        "seasons": _positive_int(raw.get("number_of_seasons")) if media_type != "movie" else None,
        "certification": _certification(raw, media_type),
    }


def _text(value, default=""):
    return value if isinstance(value, str) else default


def _year(date):
    head = date[:4] if isinstance(date, str) else ""
    return int(head) if len(head) == 4 and head.isascii() and head.isdigit() else None


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def normalize_search(raw, media_type):
    """One /search result (movie or tv) -> the slim "SearchItem" shape. Third-party data: anything of the
    wrong type degrades to a default here (or raises for a row without a usable id, which the caller skips),
    so only str/int/float/None ever reach the cache and the pages."""
    tmdb_id = raw["id"]
    if not isinstance(tmdb_id, int) or isinstance(tmdb_id, bool) or tmdb_id <= 0:
        raise ValueError("bad id")
    if media_type == "movie":
        title, date = raw.get("title"), raw.get("release_date")
    else:
        title, date = raw.get("name"), raw.get("first_air_date")
    date = date if isinstance(date, str) and date.isascii() and len(date) <= 10 else None
    poster = raw.get("poster_path")
    ok_poster = (isinstance(poster, str) and poster.startswith("/") and poster.isascii()
                 and all("!" <= c <= "~" for c in poster))
    return {
        "media_type": media_type, "tmdb_id": tmdb_id, "title": _text(title).strip() or "?",
        "year": _year(date), "release_date": date or None, "overview": _text(raw.get("overview")),
        "poster_url": f"https://image.tmdb.org/t/p/w342{poster}" if ok_poster else None,
        "url": f"https://www.themoviedb.org/{media_type}/{tmdb_id}",
        "vote_average": _number(raw.get("vote_average")), "vote_count": _number(raw.get("vote_count")),
    }


_MOVIE_CREW_JOBS = ("Director", "Screenplay", "Writer", "Story", "Novel")  # in priority order
_YOUTUBE_KEY = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
_IMDB_ID = re.compile(r"^tt\d{1,10}$")


def _image(path, size):
    ok = isinstance(path, str) and path.startswith("/") and path.isascii() and all("!" <= c <= "~" for c in path)
    return f"https://image.tmdb.org/t/p/{size}{path}" if ok else None


def _dicts(value):
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _name(value):
    return _text(value).strip()


def _trailer(raw):
    tiers = ([], [], [])  # official Trailer, Trailer, Teaser - TMDB order within each
    for v in _dicts((raw.get("videos") or {}).get("results") if isinstance(raw.get("videos"), dict) else None):
        key = v.get("key")
        if v.get("site") != "YouTube" or not isinstance(key, str) or not _YOUTUBE_KEY.match(key):
            continue
        kind = v.get("type")
        if kind == "Trailer":
            tiers[0 if v.get("official") is True else 1].append(v)
        elif kind == "Teaser":
            tiers[2].append(v)
    for tier in tiers:
        if tier:
            v = tier[0]
            return {"name": _name(v.get("name")) or "Trailer", "url": f"https://www.youtube.com/watch?v={v['key']}"}
    return None


def _crew(raw, media_type):
    if media_type != "movie":
        out, seen = [], set()
        for c in _dicts(raw.get("created_by")):
            name = _name(c.get("name"))
            if name and name not in seen:
                seen.add(name)
                out.append({"name": name, "job": "Creator"})
        return out[:CREW_MAX]
    crew = _dicts((raw.get("credits") or {}).get("crew") if isinstance(raw.get("credits"), dict) else None)
    out, seen = [], set()
    for job in _MOVIE_CREW_JOBS:
        for c in crew:
            name = _name(c.get("name"))
            if c.get("job") == job and name and name not in seen:
                seen.add(name)
                out.append({"name": name, "job": job})
    return out[:CREW_MAX]


def _similar(raw, media_type, own_id):
    out, seen = [], {own_id}
    for key in ("recommendations", "similar"):
        block = raw.get(key)
        for r in _dicts(block.get("results") if isinstance(block, dict) else None):
            try:
                item = normalize_search(r, media_type)
            except Exception:
                continue
            if item["tmdb_id"] in seen:
                continue
            seen.add(item["tmdb_id"])
            out.append(item)
            if len(out) >= SIMILAR_MAX:
                return out
    return out


def normalize_title(raw, media_type):
    """TMDB's raw JSON (with the title page's appended blocks) -> "TitleExtras": what the detail page
    needs beyond normalize(). Third-party strings are validated like normalize_search: wrong types
    degrade to defaults, URLs are only built from validated pieces."""
    tmdb_id = raw["id"]
    credits = raw.get("credits") if isinstance(raw.get("credits"), dict) else {}
    cast = []
    for c in _dicts(credits.get("cast")):
        name = _name(c.get("name"))
        if name:
            cast.append({"name": name, "character": _name(c.get("character")),
                         "profile_url": _image(c.get("profile_path"), "w185")})
        if len(cast) >= CAST_MAX:
            break
    seasons = []
    if media_type == "tv":
        for s in _dicts(raw.get("seasons")):
            number = s.get("season_number")
            if not isinstance(number, int) or isinstance(number, bool) or number < 0:
                continue
            air = s.get("air_date")
            episodes = s.get("episode_count")
            seasons.append({"number": number, "name": _name(s.get("name")) or f"Season {number}",
                            "episodes": episodes if isinstance(episodes, int) and not isinstance(episodes, bool)
                            and episodes > 0 else 0,
                            "air_date": air if isinstance(air, str) and air.isascii() and 0 < len(air) <= 10 else None,
                            "poster_url": _image(s.get("poster_path"), "w342")})
        seasons.sort(key=lambda s: s["number"])
    ext = raw.get("external_ids") if isinstance(raw.get("external_ids"), dict) else {}
    imdb = ext.get("imdb_id")
    return {
        "media_type": media_type, "tmdb_id": tmdb_id,
        "tagline": _name(raw.get("tagline")), "status": _name(raw.get("status")),
        "cast": cast, "crew": _crew(raw, media_type), "trailer": _trailer(raw), "seasons": seasons,
        "networks": [n for n in (_name(x.get("name")) for x in _dicts(raw.get("networks"))) if n][:NAMES_MAX]
        if media_type == "tv" else [],
        "studios": [n for n in (_name(x.get("name")) for x in _dicts(raw.get("production_companies"))) if n][:NAMES_MAX],
        "tvdb_id": _positive_int(ext.get("tvdb_id")),
        "imdb_id": imdb if isinstance(imdb, str) and _IMDB_ID.match(imdb) else None,
        "similar": _similar(raw, media_type, tmdb_id),
    }


def _search_key(query, kind):
    return f"tmdbsearch:v1:{kind}:{' '.join(str(query).split()).casefold()}"


class TmdbClient:
    def __init__(self, token):
        # A v4 "Read Access Token" is a long JWT sent as a Bearer header; a v3 API key is 32 characters.
        if len(token) > 60:
            self.headers, self.params = {"Authorization": f"Bearer {token}"}, {}
        else:
            self.headers, self.params = {}, {"api_key": token}

    def _get(self, path, params=None, timeout=20, retries=2):
        return get_json(BASE + path, headers=self.headers, params={**self.params, **(params or {})},
                        timeout=timeout, retries=retries)

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

    def cached_details(self, media_type, tmdb_id):
        """Details from the local cache only - never makes a request. None if not cached."""
        return db.cache_get(f"details:v2:{media_type}:{tmdb_id}", DETAILS_MAX_AGE)

    def details(self, media_type, tmdb_id, refresh=False, timeout=20, retries=2):
        """refresh=True skips the cache read (but still writes the fresh result back). timeout/retries
        default to the build's patient values; page requests pass shorter ones."""
        key = f"details:v2:{media_type}:{tmdb_id}"  # v2: added release_date
        cached = None if refresh else db.cache_get(key, DETAILS_MAX_AGE)
        if cached:
            return cached
        extra = "release_dates" if media_type == "movie" else "content_ratings"
        raw = self._get(f"/{media_type}/{tmdb_id}",
                        {"append_to_response": f"keywords,credits,recommendations,{extra}"},
                        timeout=timeout, retries=retries)
        result = normalize(raw, media_type)
        db.cache_put(key, result)
        return result

    def cached_title(self, media_type, tmdb_id):
        """The title page's extras from the local cache only - never makes a request. None if not cached."""
        return db.cache_get(f"title:v1:{media_type}:{tmdb_id}", TITLE_MAX_AGE)

    def title(self, media_type, tmdb_id):
        """(details, extras) for the detail page from ONE request (page-view timeout, one retry). Also
        refreshes the details:v2 and external_ids caches. Raises on a network/HTTP error."""
        extra = "release_dates" if media_type == "movie" else "content_ratings"
        raw = self._get(f"/{media_type}/{tmdb_id}",
                        {"append_to_response": f"keywords,credits,recommendations,similar,videos,external_ids,{extra}"},
                        timeout=SEARCH_TIMEOUT, retries=1)
        details, extras = normalize(raw, media_type), normalize_title(raw, media_type)
        db.cache_put(f"details:v2:{media_type}:{tmdb_id}", details)
        db.cache_put(f"title:v1:{media_type}:{tmdb_id}", extras)
        if isinstance(raw.get("external_ids"), dict):
            db.cache_put(f"external_ids:{media_type}:{tmdb_id}", {"tvdb_id": raw["external_ids"].get("tvdb_id")})
        return details, extras

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

    def cached_search(self, query, kind="all"):
        """A cached search_titles() result, or None. Never makes a request."""
        return db.cache_get(_search_key(query, kind), SEARCH_MAX_AGE)

    def search_titles(self, query, kind="all"):
        """Free-text title search (page 1 only): {"results": [SearchItem] (max 20), "capped": bool}.
        kind "all" uses /search/multi (people dropped). Raises on a network/HTTP error. Cached 24h."""
        path = "/search/multi" if kind == "all" else f"/search/{kind}"
        raw = self._get(path, {"query": query, "include_adult": "false", "page": 1},
                        timeout=SEARCH_TIMEOUT, retries=1)
        results = []
        for r in raw.get("results") or []:
            if not isinstance(r, dict):
                continue
            media_type = r.get("media_type") if kind == "all" else kind
            if media_type not in ("movie", "tv"):
                continue
            try:
                results.append(normalize_search(r, media_type))
            except Exception:
                continue  # one malformed row never fails the whole search
        total_pages = raw.get("total_pages")
        found = {"results": results[:SEARCH_MAX_RESULTS],
                 "capped": isinstance(total_pages, int) and total_pages > 1}
        db.cache_put(_search_key(query, kind), found)
        return found

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
