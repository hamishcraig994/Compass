"""Settings, in priority order: saved through the web UI's Settings page (in SQLite - see db.py),
else an environment variable, else a hardcoded default. For running outside Docker, environment
variables can also come from a file called .env next to this code (KEY=VALUE per line).

Every setting is resolved through _resolve()/_resolve_int() and re-applied by _apply(), so
set_setting() (called when the Settings page saves) can update everything live - no restart
needed, and nothing else in the app needs to change how it reads config.PLEX_TOKEN etc."""
import os

import db

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_env_file(path):
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env_file(os.path.join(HERE, ".env"))


def _resolve(name, default=""):
    saved = db.get_setting(name)
    return saved if saved is not None else os.environ.get(name, default)


def _resolve_int(name, default=None):
    raw = _resolve(name, "")
    return int(raw) if raw not in ("", None) else default


def _apply():
    """(Re-)reads every setting into this module's globals. Called once at import, and again
    whenever the Settings page saves a change."""
    global PLEX_URL, PLEX_TOKEN, TMDB_TOKEN, HISTORY_SOURCE, TAUTULLI_URL, TAUTULLI_API_KEY, TAUTULLI_USER
    global RADARR_URL, RADARR_API_KEY, RADARR_QUALITY_PROFILE_ID, RADARR_ROOT_FOLDER
    global SONARR_URL, SONARR_API_KEY, SONARR_QUALITY_PROFILE_ID, SONARR_ROOT_FOLDER

    PLEX_URL = _resolve("PLEX_URL", "http://192.168.1.102:32400").rstrip("/")
    PLEX_TOKEN = _resolve("PLEX_TOKEN")
    TMDB_TOKEN = _resolve("TMDB_TOKEN")

    # Where watch history comes from: "plex" (default) or "tautulli". Either way, PLEX_TOKEN (if
    # set) is still used to list what's currently in your library, so unwatched-but-owned titles
    # aren't suggested - Tautulli's history only knows what's been played, not everything you own.
    HISTORY_SOURCE = _resolve("HISTORY_SOURCE", "plex").strip().lower()
    TAUTULLI_URL = _resolve("TAUTULLI_URL").rstrip("/")
    TAUTULLI_API_KEY = _resolve("TAUTULLI_API_KEY")
    TAUTULLI_USER = _resolve("TAUTULLI_USER")  # blank = every user on the server

    # Radarr/Sonarr: optional. When set, "Add to library" appears on movie/TV cards, and anything
    # already tracked there is excluded from recommendations too (not just what's in Plex). Quality
    # profile/root folder are optional - left blank, whichever one Radarr/Sonarr lists first is used.
    RADARR_URL = _resolve("RADARR_URL").rstrip("/")
    RADARR_API_KEY = _resolve("RADARR_API_KEY")
    RADARR_QUALITY_PROFILE_ID = _resolve_int("RADARR_QUALITY_PROFILE_ID")
    RADARR_ROOT_FOLDER = _resolve("RADARR_ROOT_FOLDER")

    SONARR_URL = _resolve("SONARR_URL").rstrip("/")
    SONARR_API_KEY = _resolve("SONARR_API_KEY")
    SONARR_QUALITY_PROFILE_ID = _resolve_int("SONARR_QUALITY_PROFILE_ID")
    SONARR_ROOT_FOLDER = _resolve("SONARR_ROOT_FOLDER")


_apply()


def set_setting(name, value):
    """Saves one setting (from the Settings page) and applies it immediately."""
    db.set_setting(name, value)
    _apply()


def tautulli_configured():
    return bool(TAUTULLI_URL and TAUTULLI_API_KEY)


def radarr_configured():
    return bool(RADARR_URL and RADARR_API_KEY)


def sonarr_configured():
    return bool(SONARR_URL and SONARR_API_KEY)


def live_configured():
    """True when we have what we need to talk to TMDB and to whichever history source is picked."""
    history_ready = tautulli_configured() if HISTORY_SOURCE == "tautulli" else bool(PLEX_TOKEN)
    return bool(history_ready and TMDB_TOKEN)
