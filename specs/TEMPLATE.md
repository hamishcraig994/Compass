# <Feature name>

Status: draft | approved | built | reviewed
Author: architect · Date: YYYY-MM-DD

## Goal
What the user gets, in 2-3 sentences. Why now.

## Out of scope
What this deliberately doesn't do, so nobody builds it.

## Contract
The fixed interface every agent builds against. Follow `.claude/skills/api-conventions`.

### Python interface
```python
# web.py (backend-dev)
def example_status() -> dict: ...   # {"state": "...", ...}

# pages.py (frontend-dev)
def render_example(msg="", undo=None) -> str: ...
```

### HTTP
| Method | Path | Request fields | JSON response (Accept: application/json) | No-JS response |
|---|---|---|---|---|
| POST | `/example` | `type`, `id`, `return_to` | 200 `{"ok": true, "message": "..."}` · 400 `{"ok": false, "message": "..."}` · 403 CSRF | 303 to `_safe_path(return_to)` + `msg` |

### Data / storage
New tables, columns or settings, and how the migration stays safe to re-run on the live DB.

## File ownership
One owner per file (see `.claude/ownership.json`). Agents that aren't needed are marked "not needed".

| Agent | Files | Task |
|---|---|---|
| backend-dev | `web.py`, `db.py`, `tests/test_web_async.py` | ... |
| frontend-dev | `pages.py`, `static/app.js`, `tests/test_ui.py` | ... |
| ux-designer | `static/app.css` | ... or "not needed" |

Can run in parallel: yes/no (and why). Order, if not.

## Acceptance criteria
Each one is testable, with the command that proves it (see `.claude/skills/testing-recipe`).
- [ ] ... — `python3 -m unittest tests.test_web_async.<Class>`
- [ ] Works without JS (form post + 303) and with JS (JSON + toast).
- [ ] Phone width (375px), light and dark mode, loading/empty/error states.
- [ ] Full suite green: `python3 -m unittest discover -s tests`

## Live-request budget
Default: none - fixtures and mocks only. Otherwise state which service, how many requests, and why.

## Open questions
Decisions that need Hamish, numbered. Implementation doesn't start until the blocking ones are answered.
