"""Reads watch history from Tautulli instead of talking to Plex directly (read-only: this only
calls Tautulli's "get" endpoints).

Why: Tautulli keeps its own permanent history log, so a title you watched and later deleted
from Plex still shapes your taste - asking Plex directly loses it the moment it leaves your
library (see the "known gaps" note in the README). Tautulli also covers every user on the
server if you want it to; this module defaults to filtering to one user, so history from
other people in the household doesn't get mixed into your taste profile.

This only replaces the watch-history half of what plex.py does. It has no way to list
everything currently in your library (only what's been played), so pair it with plex.py's
PlexClient if you also want unwatched-but-owned titles excluded from suggestions - see
sources.py.

Caveat: Tautulli's exact field names have shifted across versions and this hasn't been
checked against a live server. Run `python3 cli.py --tautulli-probe` once TAUTULLI_URL and
TAUTULLI_API_KEY are set, before trusting this - it prints the raw shapes your server
actually returns, so field-name mismatches are obvious instead of silently losing data."""
from datetime import datetime, timezone

from http_util import get_json

HISTORY_PAGE = 500
WATCHED_STATUS_MIN = 1  # Tautulli: 0 = not watched, 0.5 = partial, 1 = watched. Only 1 counts.


def _tmdb_id_from_metadata(meta):
    """Same idea as plex._tmdb_id: look for a tmdb:// guid among everything Tautulli knows
    about this item. Modern Tautulli mirrors Plex's 'guids' list; older data may only carry a
    single 'guid' string in an older agent's format, which is scanned as a fallback."""
    for guid in meta.get("guids") or []:
        gid = guid.get("id", guid) if isinstance(guid, dict) else guid
        if isinstance(gid, str) and "tmdb://" in gid:
            try:
                return int(gid.split("tmdb://")[1].split("?")[0])
            except ValueError:
                return None
    single = meta.get("guid") or ""
    if "themoviedb" in single or "tmdb://" in single:
        digits = "".join(ch for ch in single.rsplit("/", 1)[-1] if ch.isdigit())
        return int(digits) if digits else None
    return None


class TautulliClient:
    def __init__(self, base_url, api_key, user=None):
        self.base_url, self.api_key, self.user = base_url.rstrip("/"), api_key, user or None

    def _call(self, cmd, **params):
        result = get_json(f"{self.base_url}/api/v2", params={"apikey": self.api_key, "cmd": cmd, **params})
        response = result.get("response", {})
        if response.get("result") != "success":
            raise RuntimeError(f"Tautulli {cmd} failed: {response.get('message')}")
        return response["data"]

    def raw_probe(self):
        """One page of history plus one metadata lookup, unprocessed - for checking Tautulli's
        actual field names before trusting load(). Not used by the recommender itself."""
        params = {"length": 5}
        if self.user:
            params["user"] = self.user
        history = self._call("get_history", **params)
        rows = history.get("data", history) if isinstance(history, dict) else history
        metadata = self._call("get_metadata", rating_key=rows[0]["rating_key"]) if rows else None
        return {"history_sample": rows[:5], "metadata_sample": metadata}

    def _history_rows(self):
        rows, start = [], 0
        while True:
            params = {"start": start, "length": HISTORY_PAGE, "grouping": 1,
                      "order_column": "date", "order_dir": "desc"}
            if self.user:
                params["user"] = self.user
            page = self._call("get_history", **params)
            data = page.get("data", []) if isinstance(page, dict) else page
            rows.extend(data)
            start += len(data)
            if not data or start >= page.get("recordsFiltered", start):
                return rows

    def load(self):
        """Returns (watched, skipped). No library_keys here - see the module docstring."""
        rows = [r for r in self._history_rows()
                if r.get("media_type") in ("movie", "episode") and r.get("watched_status", 1) >= WATCHED_STATUS_MIN]

        # Movies group by themselves; episodes group under their show, so a show with several
        # watched episodes becomes one "watched" entry, same as Plex's viewedLeafCount idea.
        groups = {}
        for row in rows:
            is_episode = row["media_type"] == "episode"
            key = row["grandparent_rating_key"] if is_episode else row["rating_key"]
            g = groups.setdefault(key, {"media_type": "tv" if is_episode else "movie", "episodes": set(),
                                        "plays": 0, "last": 0})
            g["last"] = max(g["last"], row.get("date") or 0)
            g["plays"] += row.get("group_count") or 1
            if is_episode:
                g["episodes"].add(row["rating_key"])

        watched, skipped = [], 0
        for rating_key, g in groups.items():
            try:
                meta = self._call("get_metadata", rating_key=rating_key)
            except Exception:
                skipped += 1
                continue
            tmdb_id = _tmdb_id_from_metadata(meta)
            if tmdb_id is None:
                skipped += 1
                continue
            progress = None
            if g["media_type"] == "tv":
                total = meta.get("episode_count") or meta.get("children_count") or meta.get("leaf_count")
                if total:
                    progress = min(1.0, len(g["episodes"]) / total)
            year = meta.get("year")
            watched.append({
                "media_type": g["media_type"],
                "tmdb_id": tmdb_id,
                "title": meta.get("title") or meta.get("full_title") or "?",
                "year": int(year) if str(year or "").isdigit() else None,
                "last_viewed": datetime.fromtimestamp(g["last"], timezone.utc).isoformat() if g["last"] else None,
                "user_rating": meta.get("user_rating"),
                "view_count": g["plays"] or 1,
                "progress": progress,
            })
        return watched, skipped
