# Compass

A self-hosted, Seerr-style movie and TV app that recommends what you *don't* have yet, based on
what you've watched in Plex. Add titles to Radarr/Sonarr, rate anything, and keep Trakt-style lists.
Stdlib-only Python 3 and plain JS: no pip, no build step.

## Try it
    python3 web.py                    # no tokens set -> sample data on http://localhost:8080
    SAMPLE=1 PORT=8099 python3 web.py # force sample mode on another port
    python3 cli.py --sample --profile
    python3 -m unittest discover -s tests

## How recommendations work
1. **History:** reads your watch history (read-only) from Plex or Tautulli. Recent watches, rewatches
   and high ratings count more; abandoned shows and low ratings count less or not at all.
2. **Taste profile:** genres, TMDB keywords, directors/creators and actors you keep coming back to.
3. **Candidates from TMDB:** titles linked to your favourites, well-rated titles in your top genres,
   trending titles and recent releases. Anything in Plex, Radarr or Sonarr, or not out yet, is dropped.
4. **Ranking:** 50% profile fit, 28% links to your favourites, 12% quality, 10% buzz. Trending titles
   that don't fit your taste are left out. "Match %" is relative to the best pick, not a probability.

TMDB data is cached for 30 days in SQLite (`data/`). The first build after a restart runs in the
background (a minute or two); after that the list is rebuilt hourly while you keep the old one.

## Features
- **Home, Movies, TV:** streaming-style browsing with a rotating hero of your top picks, then rows like
  *Because you watched...*, *Top 10*, *Hidden Gems* and *New in your library*.
- **Title pages** (`/title/movie/<id>`, `/title/tv/<id>`): backdrop, cast, trailer link, where you
  already have it, why Compass picked it, similar titles ranked by your taste, and for TV each
  season's status. Clicking any poster opens one.
- **Add to Radarr/Sonarr:** pick a quality profile and whether to search now. For TV, request all
  seasons or tick specific ones; for a show Sonarr already has, request more seasons (it never
  unmonitors any).
- **Rate anything, anywhere:** 1-5 stars on every card, preview and title page. Ratings feed your
  taste profile (4-5 stars count more, 1-2 count for nothing), and rating a title you haven't
  watched counts as seen. Ratings stay local and are never written to Plex.
- **Lists:** a Watchlist plus up to 50 custom lists (Library -> Lists), Trakt-style. Add from any
  card or title page; sort or reorder. Lists don't change your recommendations.
- **Library:** your Plex library plus everything in Radarr/Sonarr, with badges for where it lives and
  its download state. Tabs: All, Movies, TV shows, Watched, **Requests** (what you added here, with
  status and requested seasons) and Lists.
- **Search:** live search across all of TMDB, with each result tagged In Plex, In Radarr, In Sonarr or
  Added.
- **AI picks (optional, manual only):** an AI page that, only when you click Generate, asks any
  OpenAI-compatible endpoint for ideas grounded in your profile. Every suggestion is looked up on
  TMDB and scored by the same formula as everything else, with no bonus.
- **Themes:** Crimson (default), Amber, Lime, Ocean, Teal Night and Mono, each with light and dark
  variants, remembered per device. The logo follows the theme.
- **Works without JavaScript:** every button is a plain form; JS only makes things happen in place.

## Setup
Run `python3 web.py`, open **Settings** and fill in each tab (Plex, Watch history, TMDB, Radarr &
Sonarr, AI). **Test connection** checks the form before you save; changes apply immediately.
Environment variables (`.env`, see `.env.example`) still work as first-boot defaults; anything saved
in Settings wins.

**Tautulli** (`HISTORY_SOURCE=tautulli`) keeps history for titles you've deleted from Plex. Its field
names vary by version and haven't been checked against a live server, so run
`python3 cli.py --tautulli-probe` first. Set `PLEX_TOKEN` too so owned titles are excluded, and
`TAUTULLI_USER` to avoid mixing in other household members.

**Good to know**
- Single user, no login: keep it on your home network. Cross-site form posts are refused (403). Behind
  a reverse proxy, pass the original `Host` header through.
- Titles with no TMDB id (old Plex "Legacy" agents) are skipped.
- Radarr/Sonarr are read during each build, never per page view. Quality profiles are fetched only when
  you open the Add dialog, and are cached for 10 minutes.
- Sample mode never saves anything or touches Radarr/Sonarr.

## Deploy (Docker / Portainer)
1. Get an image: publish a GitHub release to have Actions push `ghcr.io/hamishcraig994/compass:<version>`
   (and `:latest`), or build locally with `docker build -t compass:latest .`.
2. Portainer -> Stacks -> paste `docker-compose.yml` (set `image:` to match) -> Deploy.
3. Open `http://<host>:8091` -> Settings.

The container needs the LAN (Plex, Radarr, Sonarr) and the internet (TMDB). Data lives in the
`/app/data` volume.
