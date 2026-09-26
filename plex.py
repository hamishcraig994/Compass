"""Reads your library and watch state from Plex (read-only: nothing here changes anything in Plex).

Plex reports watch state for the account that owns the token, so use your own token."""
from datetime import datetime, timezone

from http_util import get_json

PAGE_SIZE = 200


def _tmdb_id(meta):
    for guid in meta.get("Guid") or []:
        gid = guid.get("id", "")
        if gid.startswith("tmdb://"):
            try:
                return int(gid[len("tmdb://"):])
            except ValueError:
                return None
    return None


def parse_item(meta, media_type):
    """One Plex library entry -> our simple dict, or None if Plex has no TMDB id for it."""
    tmdb_id = _tmdb_id(meta)
    if tmdb_id is None:
        return None
    last = meta.get("lastViewedAt")
    item = {
        "media_type": media_type,
        "tmdb_id": tmdb_id,
        "title": meta.get("title", "?"),
        "year": meta.get("year"),
        "last_viewed": datetime.fromtimestamp(last, timezone.utc).isoformat() if last else None,
        "user_rating": meta.get("userRating"),  # 0-10, only if you rated it in Plex
        "progress": None,
    }
    if media_type == "movie":
        item["view_count"] = meta.get("viewCount", 0)
    else:  # shows: Plex counts episodes, so "watched" means at least one episode seen
        seen, total = meta.get("viewedLeafCount", 0), meta.get("leafCount", 0)
        item["view_count"] = 1 if seen else 0
        item["progress"] = seen / total if total else None
    return item


class PlexClient:
    def __init__(self, base_url, token):
        self.base_url, self.token = base_url.rstrip("/"), token

    def _get(self, path, params=None):
        return get_json(self.base_url + path, headers={"X-Plex-Token": self.token}, params=params)["MediaContainer"]

    def test_connection(self):
        """Returns (ok, message) - never raises, so the Settings page can show it either way."""
        try:
            data = self._get("/")
            return True, f"Connected to {data.get('friendlyName', 'your Plex server')}"
        except Exception as e:
            return False, str(e)

    def sections(self):
        found = self._get("/library/sections").get("Directory") or []
        return [(s["key"], "movie" if s["type"] == "movie" else "tv") for s in found if s["type"] in ("movie", "show")]

    def _section_items(self, key):
        start = 0
        while True:
            page = self._get(f"/library/sections/{key}/all", {
                "includeGuids": 1, "X-Plex-Container-Start": start, "X-Plex-Container-Size": PAGE_SIZE})
            metas = page.get("Metadata") or []
            yield from metas
            start += len(metas)
            if not metas or start >= page.get("totalSize", 0):
                return

    def load(self):
        """Returns (watched, library_keys, skipped).
        watched: items you've watched; library_keys: {(media_type, tmdb_id)} of everything in Plex;
        skipped: how many titles had no TMDB id in Plex (older matching agents) and were ignored."""
        watched, library_keys, skipped = [], set(), 0
        for key, media_type in self.sections():
            for meta in self._section_items(key):
                item = parse_item(meta, media_type)
                if item is None:
                    skipped += 1
                    continue
                library_keys.add((media_type, item["tmdb_id"]))
                if item["view_count"] > 0:
                    watched.append(item)
        return watched, library_keys, skipped
