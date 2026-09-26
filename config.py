"""Settings come from environment variables. For running outside Docker they can also live in a
file called .env next to this code (KEY=VALUE per line); real environment variables win."""
import os

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

PLEX_URL = os.environ.get("PLEX_URL", "http://192.168.1.102:32400").rstrip("/")
PLEX_TOKEN = os.environ.get("PLEX_TOKEN", "")
TMDB_TOKEN = os.environ.get("TMDB_TOKEN", "")

# Where watch history comes from: "plex" (default) or "tautulli". Either way, PLEX_TOKEN (if set)
# is still used to list what's currently in your library, so unwatched-but-owned titles aren't
# suggested - Tautulli's history only knows what's been played, not everything you own.
HISTORY_SOURCE = os.environ.get("HISTORY_SOURCE", "plex").strip().lower()
TAUTULLI_URL = os.environ.get("TAUTULLI_URL", "").rstrip("/")
TAUTULLI_API_KEY = os.environ.get("TAUTULLI_API_KEY", "")
TAUTULLI_USER = os.environ.get("TAUTULLI_USER", "")  # Tautulli username to filter history to; blank = everyone


def tautulli_configured():
    return bool(TAUTULLI_URL and TAUTULLI_API_KEY)


def live_configured():
    """True when we have what we need to talk to TMDB and to whichever history source is picked."""
    history_ready = tautulli_configured() if HISTORY_SOURCE == "tautulli" else bool(PLEX_TOKEN)
    return bool(history_ready and TMDB_TOKEN)
