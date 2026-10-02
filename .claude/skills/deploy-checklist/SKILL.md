---
name: deploy-checklist
description: Pre-deploy verification, deploy steps and rollback notes for What's Next on the arr VM (Portainer, image built from GitHub, container whatsnext on port 8091). Use when Hamish asks to deploy or to prepare a deploy. Never deploy without his explicit OK for that specific deploy.
---

# What's Next deploy checklist

**Hard rule:** never build, restart or replace anything on arr (or probox) without Hamish's explicit OK for this deploy. Show him exactly what will run first.

## Where it runs
- arr VM (192.168.1.100), Docker managed through **Portainer**. Container `whatsnext`, image `whatsnext:latest`, host port **8091** -> 8080 (8090 is grocery-compare, 8080 is sabnzbd).
- Data in the named volume `whatsnext_data` mounted at `/app/data` (TMDB cache, settings, "not interested", added log) - survives redeploys.
- Healthcheck: `GET /health` (Dockerfile `HEALTHCHECK`). The container runs as uid 1000, `no-new-privileges`, all caps dropped (docker-compose.yml).
- Not behind the gluetun VPN - it needs the LAN (Plex at 192.168.1.102:32400) and the internet (TMDB).

## Before deploying
1. Working tree clean and committed; full suite green: `python3 -m unittest discover -s tests` (testing-recipe).
2. Sample-mode smoke test passes (testing-recipe), and Hamish has clicked through the UI if JS/markup changed (no browser here).
3. **Pushed to GitHub** (`git push`; the repo's `core.sshCommand` uses the deploy key `~/.ssh/whatsnextgithub`). Portainer builds from GitHub, not from this machine.
4. Check the Dockerfile still copies every runtime file: `*.py` and `static/` (a new top-level directory needs its own `COPY`).
5. Note any DB schema change - migrations run on first use against the live volume and must be safe to re-run.

## Deploy (Hamish, in Portainer)
1. Images -> Build a new image -> URL `https://github.com/hamishcraig994/WhatsNext.git` (**the `.git` suffix is required**; without it Docker fetches the HTML page and fails with an unsupported Content-Type) -> name `whatsnext:latest`.
2. Stacks -> whatsnext -> Update the stack with **"Re-pull image" OFF** (the image is local), or recreate the container.
3. Verify: container healthy; `http://192.168.1.100:8091/health` returns `ok`; open the page. **The first load after a restart takes 1-2 minutes** (cold TMDB cache) - the "Finding your recommendations..." screen is expected.

## Rollback
**TODO - no rollback path exists yet.** Every build overwrites `whatsnext:latest`, so the previous image is gone. Proposed (needs Hamish's decision): before building, tag the current image, e.g. `whatsnext:prev-<date>` (Portainer -> Images -> the image -> add tag), so rollback = point the stack at that tag and recreate. Until then, rollback = rebuild from the previous git commit (Portainer can build from a branch/ref) - slower, and only safe if no DB migration ran.
