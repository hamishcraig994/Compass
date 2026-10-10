"""Small SQLite store: a cache of TMDB answers, the titles you've said "not interested" in, titles
added to your library, and settings saved through the web UI (see config.py, which layers these
over environment variables)."""
import json
import os
import sqlite3
import time
from datetime import datetime, timezone

DB_PATH = os.path.join(os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")),
                       "compass.db")
_LEGACY_NAME = "whatsnext.db"   # the database's name before the app was renamed to Compass
_SIDECARS = ("-wal", "-shm", "-journal")


def _resolve_path():
    """The file to open. For the default name (compass.db) a pre-rename whatsnext.db in the same folder
    is moved over once, with its -wal/-shm/-journal files. Never overwrites anything; if the move
    cannot be completed it is undone and the old file keeps being used, so no data is lost."""
    path = DB_PATH
    if os.path.basename(path) != "compass.db":
        return path                      # custom path: used exactly as given
    old = os.path.join(os.path.dirname(path), _LEGACY_NAME)
    if os.path.exists(path) or not os.path.exists(old):
        return path
    pairs = [(old + sfx, path + sfx) for sfx in _SIDECARS if os.path.exists(old + sfx)] + [(old, path)]
    if any(os.path.lexists(dst) for _, dst in pairs):
        return old                       # stray leftovers at the new name: don't touch, keep the old db
    done = []
    try:
        for src, dst in pairs:           # main file last, so a half-done move never looks complete
            os.rename(src, dst)
            done.append((src, dst))
        return path
    except OSError:
        for src, dst in reversed(done):
            try:
                os.rename(dst, src)
            except OSError:
                pass
        return old


def _connect():
    path = _resolve_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, fetched_at REAL NOT NULL, body TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS dismissed (media_type TEXT NOT NULL, tmdb_id INTEGER NOT NULL, "
                 "PRIMARY KEY (media_type, tmdb_id))")
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS added (media_type TEXT NOT NULL, tmdb_id INTEGER NOT NULL, "
                 "title TEXT NOT NULL, year INTEGER, poster_url TEXT, url TEXT, added_at TEXT NOT NULL, "
                 "PRIMARY KEY (media_type, tmdb_id))")
    conn.execute("CREATE TABLE IF NOT EXISTS ratings (media_type TEXT NOT NULL, tmdb_id INTEGER NOT NULL, "
                 "stars INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5), rated_at TEXT NOT NULL, "
                 "PRIMARY KEY (media_type, tmdb_id))")
    _migrate(conn)
    return conn


def _migrate(conn):
    """Additive, idempotent schema changes (safe on the live database and on a re-run)."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(added)")}
    if "seasons" not in columns:
        try:
            with conn:
                conn.execute("ALTER TABLE added ADD COLUMN seasons TEXT")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise   # two threads can race on first connect; the loser's attempt is harmless
    conn.execute("CREATE TABLE IF NOT EXISTS lists (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
                 "description TEXT NOT NULL DEFAULT '', "
                 "kind TEXT NOT NULL DEFAULT 'custom' CHECK (kind IN ('watchlist','custom')), "
                 "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS lists_one_watchlist ON lists(kind) WHERE kind = 'watchlist'")
    conn.execute("CREATE TABLE IF NOT EXISTS list_items (list_id INTEGER NOT NULL, "
                 "media_type TEXT NOT NULL CHECK (media_type IN ('movie','tv')), tmdb_id INTEGER NOT NULL, "
                 "title TEXT NOT NULL, year INTEGER, poster_url TEXT, added_at TEXT NOT NULL, "
                 "position INTEGER NOT NULL, PRIMARY KEY (list_id, media_type, tmdb_id))")
    conn.execute("CREATE INDEX IF NOT EXISTS list_items_title ON list_items(media_type, tmdb_id)")


def cache_get(key, max_age_seconds):
    """The saved value for key if it is younger than max_age_seconds, else None."""
    conn = _connect()
    try:
        row = conn.execute("SELECT fetched_at, body FROM cache WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    if row and time.time() - row[0] < max_age_seconds:
        return json.loads(row[1])
    return None


def cache_put(key, value):
    conn = _connect()
    try:
        with conn:
            conn.execute("INSERT OR REPLACE INTO cache (key, fetched_at, body) VALUES (?, ?, ?)",
                         (key, time.time(), json.dumps(value)))
    finally:
        conn.close()


def dismissed():
    conn = _connect()
    try:
        return {(m, i) for m, i in conn.execute("SELECT media_type, tmdb_id FROM dismissed")}
    finally:
        conn.close()


def dismiss(media_type, tmdb_id):
    conn = _connect()
    try:
        with conn:
            conn.execute("INSERT OR IGNORE INTO dismissed (media_type, tmdb_id) VALUES (?, ?)", (media_type, tmdb_id))
    finally:
        conn.close()


def undismiss(media_type, tmdb_id):
    """Takes a title back off the "not interested" list (the Undo after a dismiss). Removing
    something that isn't there is a no-op, so a double-clicked Undo is harmless."""
    conn = _connect()
    try:
        with conn:
            conn.execute("DELETE FROM dismissed WHERE media_type = ? AND tmdb_id = ?", (media_type, tmdb_id))
    finally:
        conn.close()


_ADDED_COLUMNS = ("media_type", "tmdb_id", "title", "year", "poster_url", "url", "added_at")


def _encode_seasons(seasons):
    if seasons is None:
        return None
    return "all" if seasons == "all" else json.dumps(sorted(set(seasons)))


def _decode_seasons(text):
    """NULL/corrupt -> None; 'all'; or a sorted list of positive-or-zero ints."""
    if text == "all":
        return "all"
    if not isinstance(text, str):
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    if isinstance(value, list) and all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in value):
        return sorted(set(value))
    return None


def record_added(item, seasons=None):
    """Logs a title as added to your library (for the Library page) - separate from what actually
    keeps it out of future recommendations, which is Radarr/Sonarr's own library, re-checked on
    every refresh (see sources.py). This is purely a display log of what you've approved.
    seasons (tv): None, "all" or [int]. A repeat request merges with the stored value (union; "all"
    wins); None never erases a stored value."""
    conn = _connect()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT seasons FROM added WHERE media_type = ? AND tmdb_id = ?",
                               (item["media_type"], item["tmdb_id"])).fetchone()
            old = _decode_seasons(row[0]) if row else None
            if seasons is None:
                merged = old
            elif old is None:
                merged = seasons
            elif old == "all" or seasons == "all":
                merged = "all"
            else:
                merged = sorted(set(old) | set(seasons))
            conn.execute("INSERT OR REPLACE INTO added (media_type, tmdb_id, title, year, poster_url, url, added_at, "
                         "seasons) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (item["media_type"], item["tmdb_id"], item["title"], item.get("year"),
                          item.get("poster_url"), item.get("url"), datetime.now(timezone.utc).isoformat(),
                          _encode_seasons(merged)))
    finally:
        conn.close()


def added_items():
    """Everything logged as added, most recent first. "seasons": None | "all" | [int]."""
    conn = _connect()
    try:
        rows = conn.execute(f"SELECT {', '.join(_ADDED_COLUMNS)}, seasons FROM added ORDER BY added_at DESC").fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        entry = dict(zip(_ADDED_COLUMNS, row))
        entry["seasons"] = _decode_seasons(row[-1])
        out.append(entry)
    return out


def added_count():
    conn = _connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM added").fetchone()[0]
    finally:
        conn.close()


def ratings():
    """Your own 1-5 star ratings: {(media_type, tmdb_id): stars}. Local only - never sent to Plex."""
    conn = _connect()
    try:
        return {(m, i): s for m, i, s in conn.execute("SELECT media_type, tmdb_id, stars FROM ratings")}
    finally:
        conn.close()


def rating_rows():
    """Your ratings with their timestamps: [{"media_type", "tmdb_id", "stars", "rated_at"}]."""
    conn = _connect()
    try:
        return [{"media_type": m, "tmdb_id": i, "stars": s, "rated_at": t}
                for m, i, s, t in conn.execute("SELECT media_type, tmdb_id, stars, rated_at FROM ratings "
                                               "ORDER BY rated_at, media_type, tmdb_id")]
    finally:
        conn.close()


def set_rating(media_type, tmdb_id, stars):
    if isinstance(stars, bool) or not isinstance(stars, int) or not 1 <= stars <= 5:
        raise ValueError("stars must be a whole number from 1 to 5")
    conn = _connect()
    try:
        with conn:
            conn.execute("INSERT OR REPLACE INTO ratings (media_type, tmdb_id, stars, rated_at) VALUES (?, ?, ?, ?)",
                         (media_type, tmdb_id, stars, datetime.now(timezone.utc).isoformat()))
    finally:
        conn.close()


def clear_rating(media_type, tmdb_id):
    """No-op if there's no rating."""
    conn = _connect()
    try:
        with conn:
            conn.execute("DELETE FROM ratings WHERE media_type = ? AND tmdb_id = ?", (media_type, tmdb_id))
    finally:
        conn.close()


def get_setting(key):
    """A setting saved through the web UI, or None if it's never been set there (config.py then
    falls back to an environment variable, then a hardcoded default)."""
    conn = _connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def set_setting(key, value):
    conn = _connect()
    try:
        with conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    finally:
        conn.close()


# ---- Lists (a built-in Watchlist plus custom lists) ----
_LIST_COLUMNS = ("id", "name", "description", "kind", "created_at", "updated_at")
_LIST_SELECT = ("SELECT l.id, l.name, l.description, l.kind, l.created_at, l.updated_at, "
                "(SELECT COUNT(*) FROM list_items i WHERE i.list_id = l.id) FROM lists l")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _list_dict(row):
    return dict(zip(_LIST_COLUMNS + ("count",), row))


def ensure_watchlist():
    """The Watchlist's id, creating it if missing. Safe to call concurrently (one row, ever)."""
    conn = _connect()
    try:
        with conn:
            now = _now()
            conn.execute("INSERT OR IGNORE INTO lists (name, description, kind, created_at, updated_at) "
                         "VALUES ('Watchlist', '', 'watchlist', ?, ?)", (now, now))
        return conn.execute("SELECT id FROM lists WHERE kind = 'watchlist'").fetchone()[0]
    finally:
        conn.close()


def lists():
    """Every list, Watchlist first, then custom lists by casefolded name. Never creates anything."""
    conn = _connect()
    try:
        rows = [_list_dict(r) for r in conn.execute(_LIST_SELECT)]
    finally:
        conn.close()
    rows.sort(key=lambda r: (r["kind"] != "watchlist", r["name"].casefold(), r["id"]))
    return rows


def get_list(list_id):
    conn = _connect()
    try:
        row = conn.execute(_LIST_SELECT + " WHERE l.id = ?", (list_id,)).fetchone()
    finally:
        conn.close()
    return _list_dict(row) if row else None


def create_list(name, description=""):
    """A new custom list; returns its id. The caller validates the name/description and limits."""
    conn = _connect()
    try:
        with conn:
            now = _now()
            return conn.execute("INSERT INTO lists (name, description, kind, created_at, updated_at) "
                                "VALUES (?, ?, 'custom', ?, ?)", (name, description, now, now)).lastrowid
    finally:
        conn.close()


def update_list(list_id, name, description):
    """False if the list is missing or is the Watchlist."""
    conn = _connect()
    try:
        with conn:
            cur = conn.execute("UPDATE lists SET name = ?, description = ?, updated_at = ? "
                               "WHERE id = ? AND kind = 'custom'", (name, description, _now(), list_id))
            return cur.rowcount > 0
    finally:
        conn.close()


def delete_list(list_id):
    """Deletes a custom list and its items in one transaction. False if missing or the Watchlist."""
    conn = _connect()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT kind FROM lists WHERE id = ?", (list_id,)).fetchone()
            if not row or row[0] != "custom":
                return False
            conn.execute("DELETE FROM list_items WHERE list_id = ?", (list_id,))
            conn.execute("DELETE FROM lists WHERE id = ?", (list_id,))
            return True
    finally:
        conn.close()


_ITEM_COLUMNS = ("media_type", "tmdb_id", "title", "year", "poster_url", "added_at", "position")


def list_items(list_id):
    """The list's titles by position ascending (manual order)."""
    conn = _connect()
    try:
        rows = conn.execute(f"SELECT {', '.join(_ITEM_COLUMNS)} FROM list_items WHERE list_id = ? "
                            "ORDER BY position, added_at, media_type, tmdb_id", (list_id,)).fetchall()
    finally:
        conn.close()
    return [dict(zip(_ITEM_COLUMNS, r)) for r in rows]


def add_to_list(list_id, item):
    """Puts a title at the top of a list (MIN(position) - 1, or 0 if empty). False if it's already there
    or the list doesn't exist."""
    conn = _connect()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM lists WHERE id = ?", (list_id,)).fetchone() is None:
                return False
            low = conn.execute("SELECT MIN(position) FROM list_items WHERE list_id = ?", (list_id,)).fetchone()[0]
            cur = conn.execute("INSERT OR IGNORE INTO list_items (list_id, media_type, tmdb_id, title, year, "
                               "poster_url, added_at, position) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (list_id, item["media_type"], item["tmdb_id"], item["title"], item.get("year"),
                                item.get("poster_url"), _now(), 0 if low is None else low - 1))
            if cur.rowcount == 0:
                return False
            conn.execute("UPDATE lists SET updated_at = ? WHERE id = ?", (_now(), list_id))
            return True
    finally:
        conn.close()


def remove_from_list(list_id, media_type, tmdb_id):
    conn = _connect()
    try:
        with conn:
            cur = conn.execute("DELETE FROM list_items WHERE list_id = ? AND media_type = ? AND tmdb_id = ?",
                               (list_id, media_type, tmdb_id))
            if cur.rowcount:
                conn.execute("UPDATE lists SET updated_at = ? WHERE id = ?", (_now(), list_id))
            return cur.rowcount > 0
    finally:
        conn.close()


def move_in_list(list_id, media_type, tmdb_id, direction):
    """direction: "up"/"down" swap positions with the neighbour; "top"/"bottom" go to MIN-1 / MAX+1.
    False if the title is absent, the direction is unknown, or it is already at that end."""
    if direction not in ("up", "down", "top", "bottom"):
        return False
    conn = _connect()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT media_type, tmdb_id, position FROM list_items WHERE list_id = ? "
                                "ORDER BY position, added_at, media_type, tmdb_id", (list_id,)).fetchall()
            keys = [(r[0], r[1]) for r in rows]
            if (media_type, tmdb_id) not in keys:
                return False
            index = keys.index((media_type, tmdb_id))
            if direction in ("up", "top") and index == 0 or direction in ("down", "bottom") and index == len(rows) - 1:
                return False
            if direction == "top":
                new = rows[0][2] - 1
            elif direction == "bottom":
                new = rows[-1][2] + 1
            else:
                other = rows[index + (-1 if direction == "up" else 1)]
                conn.execute("UPDATE list_items SET position = ? WHERE list_id = ? AND media_type = ? AND tmdb_id = ?",
                             (rows[index][2], list_id, other[0], other[1]))
                new = other[2]
            conn.execute("UPDATE list_items SET position = ? WHERE list_id = ? AND media_type = ? AND tmdb_id = ?",
                         (new, list_id, media_type, tmdb_id))
            conn.execute("UPDATE lists SET updated_at = ? WHERE id = ?", (_now(), list_id))
            return True
    finally:
        conn.close()


def memberships():
    """{(media_type, tmdb_id): [list_id, ...]} for every listed title - one query."""
    conn = _connect()
    try:
        out = {}
        for m, i, list_id in conn.execute("SELECT media_type, tmdb_id, list_id FROM list_items "
                                          "ORDER BY list_id"):
            out.setdefault((m, i), []).append(list_id)
        return out
    finally:
        conn.close()
