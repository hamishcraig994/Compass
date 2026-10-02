"""Small SQLite store: a cache of TMDB answers, the titles you've said "not interested" in, titles
added to your library, and settings saved through the web UI (see config.py, which layers these
over environment variables)."""
import json
import os
import sqlite3
import time
from datetime import datetime, timezone

DB_PATH = os.path.join(os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")),
                       "whatsnext.db")


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
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
    return conn


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


def record_added(item):
    """Logs a title as added to your library (for the Library page) - separate from what actually
    keeps it out of future recommendations, which is Radarr/Sonarr's own library, re-checked on
    every refresh (see sources.py). This is purely a display log of what you've approved."""
    conn = _connect()
    try:
        with conn:
            conn.execute("INSERT OR REPLACE INTO added (media_type, tmdb_id, title, year, poster_url, url, added_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (item["media_type"], item["tmdb_id"], item["title"], item.get("year"),
                          item.get("poster_url"), item.get("url"), datetime.now(timezone.utc).isoformat()))
    finally:
        conn.close()


def added_items():
    """Everything logged as added, most recent first."""
    conn = _connect()
    try:
        rows = conn.execute(f"SELECT {', '.join(_ADDED_COLUMNS)} FROM added ORDER BY added_at DESC").fetchall()
    finally:
        conn.close()
    return [dict(zip(_ADDED_COLUMNS, row)) for row in rows]


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
