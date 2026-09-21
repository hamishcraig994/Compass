# What's Next

Suggests movies and TV shows you *don't* have yet, based on what you've watched in Plex.

## How it works
1. Reads your Plex library and watch history (read-only). Recent watches, rewatches and your own Plex
   ratings count more; abandoned shows and titles you rated 4/10 or lower count less or not at all.
2. Builds a taste profile: genres, themes (TMDB keywords), directors/creators and actors you keep coming back to.
3. Finds candidates from TMDB: what's linked to your favourites ("people who liked this also liked..."),
   well-rated titles in your top genres, this week's trending titles, and releases from the last 6 months in
   your top genres. Anything already in your Plex library is dropped, and so is anything not out yet.
4. Ranks candidates: 50% fit with your profile, 28% how many of your favourites link to it, 12% general
   quality, 10% buzz (trending, or recently released). Taste dominates: trending or new titles that don't fit
   your profile are left out entirely, and can't outrank a strong match. "Match %" is relative to the best
   suggestion in the batch, not a probability.
5. Anything trending or released in the last 6 months gets a badge and also appears on the
   "New & trending" tab (`python3 cli.py --type new`), as well as in the main list.

Everything TMDB tells us is cached for 30 days in SQLite (`data/`), so it's gentle on TMDB.

## Try it now (no accounts needed)
    python3 cli.py --sample --profile
    python3 web.py                    # no tokens set -> shows sample data; http://localhost:8080
    python3 -m unittest discover -s tests

## Use your own library
1. Copy `.env.example` to `.env` and fill in `PLEX_TOKEN` and `TMDB_TOKEN` (instructions inside).
2. `python3 cli.py --profile`  or  `python3 web.py`.

Limits worth knowing:
- Watch state is for the Plex account that owns the token (yours). Other household members' history isn't included.
- Only what's *currently in your Plex library* is read. If you delete shows/movies after watching them, they
  no longer influence your taste profile. (Fix if it matters: read Plex's history endpoint or Tautulli.)
- Titles Plex has no TMDB id for (old "Legacy" agents) are skipped; the page tells you how many.
- "Not interested" is remembered in the database; sample data never writes to it.
- No login - keep it on the home network.

## Deploy with Docker / Portainer (not deployed yet)
1. Build the image `whatsnext:latest` on the Docker host (`docker build -t whatsnext:latest .`) or via
   Portainer -> Images -> Build a new image.
2. Portainer -> Stacks -> Add stack -> Web editor -> paste `docker-compose.yml`.
   Under "Environment variables" add `PLEX_TOKEN` and `TMDB_TOKEN`. Deploy.
3. Open http://<arr-vm-ip>:8091

The container needs the LAN (Plex) and the internet (TMDB). It's deliberately not behind the gluetun VPN.
