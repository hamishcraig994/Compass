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
4. If an AI is configured, its own dedicated **AI** page (sidebar) can, on request, ask it for a
   batch of ideas grounded in your taste profile - see "AI-assisted suggestions" below. This never
   happens automatically as part of the list above; every suggestion names a title, never a trusted
   id, so it's looked up on TMDB ourselves (matching by year, not just taking the first search
   result) before being treated as real, and it's scored by the exact same formula as everything
   else below, with no bonus for coming from the AI.
5. Ranks candidates: 50% fit with your profile, 28% how many of your favourites link to it, 12% general
   quality, 10% buzz (trending, or recently released). Taste dominates: trending or new titles that don't fit
   your profile are left out entirely, and can't outrank a strong match. "Match %" is relative to the best
   suggestion in the batch, not a probability.
6. Anything trending or released in the last 6 months gets a badge and also appears on the
   "New & trending" tab (`python3 cli.py --type new`), as well as in the main list.
7. If Radarr/Sonarr are configured, movie/TV cards get an "Add to library" button - opening a
   dialog to pick a quality profile and whether to search immediately - and anything already
   tracked there is excluded from recommendations too, not just what's already in Plex.

Everything TMDB tells us is cached for 30 days in SQLite (`data/`), so it's gentle on TMDB.

## Try it now (no accounts needed)
    python3 cli.py --sample --profile
    python3 web.py                    # no tokens set -> shows sample data; http://localhost:8080
    python3 -m unittest discover -s tests

## The web page
- **First load after a restart** takes a minute or two (a cold TMDB cache means ~190 lookups).
  That now happens in the background: you get a "Finding your recommendations..." screen that
  swaps itself for the list when it's ready, instead of a page that looks hung. After that, the
  list is reused for an hour and rebuilt in the background - you keep seeing the old one, with an
  "Updating..." badge, until the new one is ready. `GET /api/status` reports where a build is at.
- **Buttons work in place.** "Not interested", "Add to library", "Refresh" and "Generate" don't
  reload the page; a small message confirms what happened, and "Not interested" has an **Undo**.
  Clicking a poster opens the full description and why it was picked.
- **JavaScript is optional.** It's one small plain-JS file (`static/app.js`, no libraries) that
  enhances ordinary forms. With it off, every button still works as a normal form post, Undo is a
  note at the top of the page, and loading screens reload themselves every 5 seconds instead.
- **Library** (sidebar) shows what's in your Plex library as a poster grid, with tabs for All,
  Movies, TV shows, **Watched** and "Added here" (what you added through Radarr/Sonarr), plus
  search, filters, sorting and paging. Posters are fetched through the app (`/poster`), so your Plex
  token never reaches the browser. No new Plex requests are made per page view; it uses the
  snapshot from the last build.
- **Rate what you've watched.** The Watched tab lists your history with 1-5 star buttons. Your
  rating overrides your Plex rating (clear it to fall back to Plex's) and feeds the taste profile:
  4-5 stars count more, 3 counts less, 1-2 count for nothing, and titles TMDB links to a 1 or 2
  star title are ranked lower (never hidden). Ratings are stored locally in SQLite and are never
  written to Plex. After rating, "Update recommendations" rebuilds the list. In sample mode the
  stars show but nothing is saved.
- Dark by default, with a light theme if your device asks for one; the sidebar becomes a bottom
  bar on phones.

## Use your own library
Easiest: `python3 web.py`, then open the page and go to **Settings** - one tab per app (Plex,
Watch history, TMDB, Radarr & Sonarr). Fill a tab in, hit **Test connection** to check it before
saving (it tests whatever's in the form, not necessarily what's already saved), then **Save**.
Nothing to restart; it applies immediately. Radarr/Sonarr's quality profile and root folder become
dropdowns, pulled live from your instance, once that tab's URL/API key are reachable.

Or, without the web UI: copy `.env.example` to `.env` and fill in `PLEX_TOKEN` and `TMDB_TOKEN`,
then `python3 cli.py --profile`. A setting saved through the Settings page always wins over its
`.env`/environment-variable equivalent, so `.env` is really just the first-boot default.

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
- No login - keep it on the home network. Cross-site requests are refused, though: any
  form post or button press your browser says came from another website (or another app on a
  different port of the same host) gets a 403, so a page you visit can't quietly change Settings
  or add titles. If you ever put it behind a reverse proxy, the proxy must pass the original
  `Host` header through, or the app's own buttons will look cross-site and be refused.

### Adding to Radarr/Sonarr
Set them up in Settings -> Radarr & Sonarr (or `RADARR_URL`/`RADARR_API_KEY` and/or
`SONARR_URL`/`SONARR_API_KEY` in `.env` - see `.env.example`). "Add to library" opens a dialog to
pick a quality profile for that title (defaulting to whichever's configured in Settings) and
whether to search for it immediately; leaving the profile on "Default" uses Settings' choice, and
whichever root folder is configured there is always used (there's no per-title override for that).
A movie is added straight from its TMDB id; a TV show's TMDB id is converted to the TVDB id Sonarr
needs via TMDB's `external_ids` (cached, same as everything else). Sample data never touches
Radarr/Sonarr - the button doesn't even appear until you're on your own library.

The dialog is its own page (`/add-dialog`; with JavaScript on it opens as a pop-up over the list,
loading that same page), not pre-rendered into the Recommended list, specifically so
quality profiles are only ever fetched when you actually open it - viewing or refreshing the
Recommended list never touches Radarr/Sonarr just to pre-populate a dropdown you might not use.
Whichever list Radarr/Sonarr gave that day is also cached for 10 minutes either way.

### AI-assisted suggestions
Set it up in Settings -> AI (or `AI_PROVIDER_URL`/`AI_TOKEN`/`AI_MODEL` in `.env`). Any
OpenAI-compatible endpoint works - OpenAI itself, a self-hosted server, OpenRouter, etc. - not just
OpenAI, so change the provider URL for anything else.

**Manual only, by design.** Once configured, a new **AI** section appears in the sidebar with its
own **Generate** button. Clicking it is the only thing that ever triggers an AI request - it never
runs as part of the automatic Recommended list, its hourly refresh, or Home's stats. That's
deliberate: every click is a real request (and, depending on your provider, a real cost), so it
should only ever happen because you asked. The AI page shows whatever your last Generate produced
until you generate again; "Add to library" and "Not interested" work the same as they do on the
main Recommended list.

Two rules keep this from being a black box or a liability:
- **Nothing the AI says is trusted outright.** It names a title and year; that gets looked up on
  TMDB ourselves (preferring a result within a year of the one given, over just the first search
  hit) before it's treated as a real candidate. If nothing matches closely, the suggestion is
  dropped rather than guessed at.
- **No special scoring bonus.** An AI-sourced candidate is scored by the exact same formula as
  everything else in this list - it competes on fit and quality, not on where it came from.

## Deploy with Docker / Portainer
1. Build the image `whatsnext:latest` on the Docker host (`docker build -t whatsnext:latest .`) or via
   Portainer -> Images -> Build a new image.
2. Portainer -> Stacks -> Add stack -> Web editor -> paste `docker-compose.yml`. Deploy.
3. Open http://<arr-vm-ip>:8091 -> Settings, and fill everything in there (no environment
   variables needed - though they still work as the first-boot default if you'd rather set them
   in the Portainer stack instead).

The container needs the LAN (Plex) and the internet (TMDB). It's deliberately not behind the gluetun VPN.
