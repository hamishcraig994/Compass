"""Small SQLite store: a cache of TMDB answers, the titles you've said "not interested" in, and
settings saved through the web UI (see config.py, which layers these over environment variables)."""
import json
import os
import sqlite3
import time

DB_PATH = os.path.join(os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")),
                       "whatsnext.db")


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, fetched_at REAL NOT NULL, body TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS dismissed (media_type TEXT NOT NULL, tmdb_id INTEGER NOT NULL, "
                 "PRIMARY KEY (media_type, tmdb_id))")
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
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
