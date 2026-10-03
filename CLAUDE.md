# What's Next

Self-hosted movie/TV recommender: Plex (or Tautulli) watch history -> taste profile -> TMDB candidates, with optional Radarr/Sonarr "Add to library" and a manual-only AI page. Stdlib-only Python 3 (no pip), plain JS, no build step. See README.md for features and setup.

- Run: `python3 web.py` (sample data if no tokens; `SAMPLE=1 PORT=8099 python3 web.py` for a local demo)
- Test: `python3 -m unittest discover -s tests` (~71s) - details in `.claude/skills/testing-recipe`
- Layout: `web.py` = state, background builds, HTTP handler, validation (backend) · `browse.py` = pure hero/row logic for Home/Movies/TV · `themes.py` = colour-theme registry + cookie · `pages.py` = all HTML rendering (frontend) · `static/app.css` / `static/app.js` · integrations in `plex.py`, `tautulli.py`, `tmdb.py`, `radarr.py`, `sonarr.py`, `ai.py` · data in `db.py` (SQLite in `data/`)
- Live on arr (container `whatsnext`, port 8091). **Never deploy, restart or rebuild it without Hamish's OK** - see `.claude/skills/deploy-checklist`.
- Don't make live requests to Plex/TMDB/Radarr/Sonarr/AI providers in tests or reviews; use mocks/fixtures.

## Orchestration
The general rules are in `~/.claude/CLAUDE.md`. This project adds:
- Workflow: **architect** writes `specs/<feature>.md` (from `specs/TEMPLATE.md`) -> owning agents implement in parallel -> **reviewer** verifies -> Hamish approves.
- Delegate with explicit context: subagents start with no memory, so every brief gives the spec path, the agent's files for this task, acceptance criteria with test commands, and a live-request budget (default none).
- Each agent edits only its own paths in `.claude/ownership.json` (hook-enforced); if it needs a change elsewhere it reports back. README.md, CLAUDE.md and `.claude/` belong to the orchestrator.
- Subagents return a short summary - files changed, decisions, open questions, test results - not full dumps.
- Don't over-split: most UI features need **frontend-dev only**; add **ux-designer** just for design-led work (and brief it with the class names it may restyle). For small changes skip the architect and write the brief directly.
- Hooks: after every edit, `.claude/hooks/after-edit.py` runs py_compile + the fast tests (~5s); destructive shell commands are blocked globally.
