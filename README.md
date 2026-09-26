# What's Next

Suggests movies and TV shows you *don't* have yet, based on what you've watched in Plex.

## How it works
1. Reads your watch history (read-only) - from Plex directly, or from Tautulli
   (`HISTORY_SOURCE=tautulli`), which keeps history even for titles you've since deleted from Plex.
   Either way, Plex (if `PLEX_TOKEN` is set) also supplies everything currently in your library, so
   owned-but-unwatched titles aren't suggested. Recent watches, rewatches and your own Plex
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

### Using Tautulli instead of Plex for history
Set `HISTORY_SOURCE=tautulli`, `TAUTULLI_URL` and `TAUTULLI_API_KEY` in `.env`. Worth it because
Tautulli keeps a permanent history log, so a title you watched and later deleted from Plex still
shapes your taste - reading Plex directly loses it the moment it leaves your library.

**Check it before trusting it:** Tautulli's exact field names have shifted across versions and
this integration hasn't been checked against a live server. Run
`python3 cli.py --tautulli-probe` first - it prints the raw history/metadata shapes your server
actually returns, so a field-name mismatch is obvious instead of silently losing watch history.

Limits worth knowing:
- Watch state is for one account (yours) - Plex's token, or `TAUTULLI_USER` if set; leave the
  latter blank to pull every user on the server, which mixes household members' taste together.
- With Tautulli, unwatched-but-owned titles are only excluded if `PLEX_TOKEN` is *also* set -
  Tautulli's history alone doesn't know what's currently in your library, only what's been played.
- Titles with no TMDB id (old "Legacy" Plex agents) are skipped; the page tells you how many.
- "Not interested" is remembered in the database; sample data never writes to it.
- No login - keep it on the home network.

## Deploy with Docker / Portainer (not deployed yet)
1. Build the image `whatsnext:latest` on the Docker host (`docker build -t whatsnext:latest .`) or via
   Portainer -> Images -> Build a new image.
2. Portainer -> Stacks -> Add stack -> Web editor -> paste `docker-compose.yml`.
   Under "Environment variables" add `PLEX_TOKEN` and `TMDB_TOKEN`. Deploy.
3. Open http://<arr-vm-ip>:8091

The container needs the LAN (Plex) and the internet (TMDB). It's deliberately not behind the gluetun VPN.
