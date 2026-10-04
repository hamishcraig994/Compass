"""Pure helpers for the Radarr/Sonarr side of the Library and for search-result statuses.

No I/O and no imports from db/web/sources: everything here takes plain dicts/lists and returns new
ones (inputs are never mutated). An "arr item" is what radarr.normalize_movie / sonarr.normalize_series
return; a "plex item" is a Library snapshot entry (see sources._snapshot)."""
from urllib.parse import urlparse

POSTER_HOSTS = ("image.tmdb.org", "artworks.thetvdb.com", "thetvdb.com", "www.thetvdb.com", "assets.fanart.tv")
WANTED_STATES = ("missing", "partial", "upcoming")
_SERVICE_ORDER = ("plex", "radarr", "sonarr")


def poster_url(images):
    """The first poster's remoteUrl from an arr "images" list, if it is https on an allowed host
    (TMDB's /t/p/original/ is rewritten to the grid size, w342). Anything else -> None. The arr's own
    relative "url" (/MediaCover/..., which needs the API key) is never used."""
    if not isinstance(images, list):
        return None
    for image in images:
        if not isinstance(image, dict) or image.get("coverType") != "poster":
            continue
        url = image.get("remoteUrl")
        if not isinstance(url, str) or not url:
            continue
        try:
            parsed = urlparse(url)
            host = parsed.hostname
        except ValueError:
            return None
        if parsed.scheme != "https" or host not in POSTER_HOSTS or parsed.username or parsed.password:
            return None
        if not all("!" <= c <= "~" for c in url):
            return None
        if host == "image.tmdb.org":
            url = url.replace("/t/p/original/", "/t/p/w342/", 1)
        return url
    return None


def title_key(title):
    """Casefolded title with only letters/digits kept, so punctuation and spacing don't matter."""
    return "".join(c for c in str(title or "").casefold() if c.isalnum())


def _same_year(a, b):
    return a is None or b is None or a == b


def same_title(a, b):
    """Same media_type, same title_key, and years equal (or either unknown)."""
    return (a.get("media_type") == b.get("media_type") and title_key(a.get("title")) == title_key(b.get("title"))
            and _same_year(a.get("year"), b.get("year")))


class _Index:
    """Finds the first entry matching an item by the match rule: equal (media_type, tmdb_id) when both
    have one, else same_title() when either side lacks an id. First (lowest position) wins."""

    def __init__(self):
        self.entries = []
        self._by_id, self._by_title = {}, {}

    def add(self, entry):
        pos = len(self.entries)
        self.entries.append(entry)
        if entry.get("tmdb_id"):
            self._by_id.setdefault((entry["media_type"], entry["tmdb_id"]), pos)
        self._by_title.setdefault((entry.get("media_type"), title_key(entry.get("title"))), []).append(pos)

    def find(self, item):
        """Position of the first match, or None."""
        best = None
        tmdb_id = item.get("tmdb_id")
        if tmdb_id:
            best = self._by_id.get((item.get("media_type"), tmdb_id))
        for pos in self._by_title.get((item.get("media_type"), title_key(item.get("title"))), ()):
            if best is not None and pos >= best:
                break
            entry = self.entries[pos]
            if (not tmdb_id or not entry.get("tmdb_id")) and _same_year(item.get("year"), entry.get("year")):
                best = pos
                break
        return best


def _ordered(sources):
    return [s for s in _SERVICE_ORDER if s in sources]


def merge(plex_items, arr_items):
    """Plex entries (copied) with Radarr/Sonarr items merged in; arr-only titles are appended. A title in
    both appears once with sources like ["plex", "radarr"]. Plex's poster_key and added_at win; an arr
    poster is only offered when the entry has no Plex poster."""
    index = _Index()
    for raw in plex_items or []:
        entry = dict(raw)
        entry["sources"] = ["plex"]
        entry.setdefault("arr_state", None)
        entry.setdefault("episodes", None)
        entry.setdefault("poster_url", None)
        index.add(entry)
    for arr in arr_items or []:
        pos = index.find(arr)
        if pos is None:
            index.add({
                "media_type": arr["media_type"], "tmdb_id": arr.get("tmdb_id"), "title": arr.get("title") or "?",
                "year": arr.get("year"), "added_at": arr.get("added_at"), "watched": False, "progress": None,
                "poster_key": None, "poster_url": arr.get("poster_url"), "url": arr.get("url"),
                "sources": [arr["service"]], "arr_state": arr.get("arr_state"), "episodes": arr.get("episodes")})
            continue
        entry = index.entries[pos]
        entry["sources"] = _ordered(set(entry["sources"]) | {arr["service"]})
        if entry.get("arr_state") is None:
            entry["arr_state"], entry["episodes"] = arr.get("arr_state"), arr.get("episodes")
        if not entry.get("poster_key") and not entry.get("poster_url"):
            entry["poster_url"] = arr.get("poster_url")
        if not entry.get("added_at"):
            entry["added_at"] = arr.get("added_at")
        if not entry.get("url"):
            entry["url"] = arr.get("url")
    return index.entries


def annotate(items, plex_items=None, arr_items=(), watched_items=(), added_keys=frozenset(), dismissed=frozenset()):
    """Copies of search results with status fields. status precedence: plex > radarr/sonarr > added > none."""
    plex, arr, watched = _Index(), _Index(), _Index()
    for source, index in ((plex_items, plex), (arr_items, arr), (watched_items, watched)):
        for entry in source or ():
            index.add(entry)
    out = []
    for raw in items:
        item = dict(raw)
        key = (item.get("media_type"), item.get("tmdb_id"))
        in_plex = plex.find(item) is not None
        arr_pos = arr.find(item)
        arr_item = arr.entries[arr_pos] if arr_pos is not None else None
        sources = (["plex"] if in_plex else []) + ([arr_item["service"]] if arr_item else [])
        if in_plex:
            status = "plex"
        elif arr_item:
            status = arr_item["service"]
        elif key in added_keys:
            status = "added"
        else:
            status = "none"
        item.update({"status": status, "in_library": status != "none", "sources": _ordered(sources),
                     "arr_state": arr_item.get("arr_state") if arr_item else None,
                     "episodes": arr_item.get("episodes") if arr_item else None,
                     "watched": watched.find(item) is not None, "dismissed": key in dismissed})
        out.append(item)
    return out
