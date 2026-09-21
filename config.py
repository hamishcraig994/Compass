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


def live_configured():
    """True when we have what we need to talk to the real Plex and TMDB."""
    return bool(PLEX_TOKEN and TMDB_TOKEN)
