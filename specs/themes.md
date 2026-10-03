# Colour themes

Status: draft (open questions are all non-blocking; defaults are stated)
Author: architect · Date: 2026-10-03

## Goal
You can pick a colour theme on each device. The choice is per device, not per account. There are six themes: **Amber** (today's look, unchanged), **Crimson** (red on black, cinema style), **Lime** (neon green on charcoal), **Ocean** (bright blue on deep navy), **Teal Night** (cyan on blue-black) and **Mono** (black and white, OLED-friendly).

- Every theme keeps the automatic light/dark behaviour.
- The cinematic Home/Movies/TV pages keep working unchanged, because themes only swap the existing CSS tokens. The hero scrims are already built from `--bg`, so they follow the theme.
- The UI never names a brand. Themes are named by character.

## Out of scope
- DB or server-side storage of the choice. There is no per-user setting and no Settings-table row.
- A "force dark / force light" switch. Light/dark still follows `prefers-color-scheme` (Q2).
- Theme-specific layouts, fonts, logos or icons. Colours only.
- Web fonts, CDNs, external URLs, background images or gradients on `--bg` (body stays a solid `var(--bg)`).
- Re-theming content colours: poster placeholder hues (`--h`), toasts, `.badge.alt`, `.kind`, modal backdrop, `--danger*`/`--ok*`, `--shadow` and the star colours (`--star-*` stay gold in every theme, Q4).
- A top-bar shortcut to the picker (Q1).

## Decisions
1. **Registry = `themes.py` (new, backend-owned). It's the single source of truth for keys, labels, descriptions and browser-chrome colours.** The swatch *preview colours* deliberately aren't in the registry. Each swatch card carries `data-theme="<key>"`, and the theme CSS blocks use the attribute selector `[data-theme="<key>"]`, which matches any element, not just `<html>`. So each card re-declares its own theme's tokens and previews itself straight from `app.css`, in light mode too. That means hex values live in exactly one place. The brief suggested swatch colours in the registry; this approach was chosen because it avoids keeping two copies in sync. Tests keep the registry and CSS in sync (acceptance criteria).
2. **The cookie is `wn_theme`, not `theme`.** Cookies are scoped by host, not port, so every app on arr (Radarr, Sonarr, ...) shares one cookie jar, and a generic `theme` name could collide.
   - The server sets it: `Path=/; Max-Age=34560000; SameSite=Lax` (400 days, the browser cap). It has no `Secure` flag because the app is served over plain http on the LAN.
   - It is **not HttpOnly**. app.js reads it only to re-sync `data-theme` when a page comes back from the back/forward cache (`pageshow` with `persisted`). Without that, Back after a change shows the old theme. JS never writes the cookie.
   - The value isn't sensitive.
3. **The server parses the cookie by hand, not with `http.cookies.SimpleCookie`.** SimpleCookie stops at the first malformed cookie, and other apps on the same host can set those. The rule: split the header on `;`, strip each part, `partition("=")`, and take the first part whose name is `wn_theme` and whose value is a registry key. Otherwise use `DEFAULT`.
4. **Per-request theme via a thread-local in web.py.** At the start of `do_GET` and `do_POST`, the Handler stores the parsed cookie value. `pages._shell` reads it via `web.current_theme()`. Pages already read state only through `web.*` inside functions.
   - No `render_*` signature changes.
   - Direct renders in tests (no request) get `"amber"`.
   - `ThreadingHTTPServer` runs one thread per connection. The value is set on every request, so nothing stale can leak.
5. **The picker lives on its own page, `GET /appearance`, rendered by `pages.render_appearance` (frontend), and is reached from a new last "Appearance" tab in the Settings sub-tabs.**
   - It isn't a `settings_page` section, for two reasons. First, `POST /settings` saves to `config` and calls `invalidate_cache()`, which a theme change must not do. Second, `settings_page.py` is backend-owned, and this is design-led markup.
   - The page uses `_shell(..., "settings")`, so the Settings nav item is active and the heading reads "Settings", like every settings section.
   - The sub-tab row is shared through `settings_page.subtabs_html()`.
6. **`POST /theme` works in sample mode as well.** It only sets a cookie and writes nothing server-side, so the `SAMPLE_MESSAGE` refusal doesn't apply. This is a deliberate exception to the sample-mode pattern, and it makes the `SAMPLE=1` demo fully work.
7. **CSS structure. Every theme block declares exactly `themes.TOKENS`, no more and no less, in both dark and light.**
   - Amber's blocks use the selector `:root, [data-theme="amber"]`. Their values are today's, so Amber renders identically.
   - Every other theme uses `[data-theme="<key>"]`, and its light block sits inside `@media (prefers-color-scheme: light)`.
   - Order in the file: amber dark, amber light, then for each theme in registry order its dark block followed by its light block.
   - All of these selectors have equal specificity (0,1,0), so order decides. That's why every light block must re-declare every token: otherwise a theme's dark value would beat amber's light value in light mode.
   - Non-theme tokens stay in plain `:root` / `@media (light) :root` blocks: `--danger*`, `--ok*`, `--shadow`, `--radius*`, `--nav-h`, `--topbar-h`, `--gutter`, `--hero-overlap`, `--scrim-*`, `--topbar-bg`, `--font` and `--star-*`.
   - `--scrim-*` and `--topbar-bg` are computed on `<html>` from the theme's `--bg`, so they follow the theme automatically. This only works because `data-theme` is on `<html>`; it must never move to `<body>`.
8. **Two new tokens replace the only hard-coded amber in component rules:**
   - `--accent-glow` replaces the `.brand-mark` box-shadow `rgba(255, 178, 36, .3)`.
   - `--match-text` replaces the `.match` chip text `#ffc557`. The chip is always dark (`rgba(8,10,14,.78)`), so this token has the same value in dark and light.
9. **Choosing Amber sets `wn_theme=amber`.** It doesn't delete the cookie. The server always renders an explicit `data-theme`, and `"amber"` when the cookie is missing or invalid.

## Contract

### Python interface
```python
# themes.py (NEW, backend-dev) - pure: no I/O, no imports from web/pages/db
DEFAULT = "amber"
COOKIE = "wn_theme"
COOKIE_MAX_AGE = 34560000            # 400 days
TOKENS = ("--bg", "--bg-2", "--surface", "--surface-2", "--line", "--text", "--muted",
          "--accent", "--accent-ink", "--accent-hover", "--focus", "--accent-text",
          "--accent-soft", "--accent-glow", "--match-text")
THEMES = (  # display order; each a dict
    {"key": "amber",   "label": "Amber",      "description": "Warm gold on midnight - the original", "bg": "#0a0c11", "bg_light": "#f3f4f7"},
    {"key": "crimson", "label": "Crimson",    "description": "Bold red on near-black",                "bg": "#141414", "bg_light": "#f4f4f4"},
    {"key": "lime",    "label": "Lime",       "description": "Neon green on charcoal",                "bg": "#0b0c0f", "bg_light": "#f2f5f3"},
    {"key": "ocean",   "label": "Ocean",      "description": "Bright blue on deep navy",              "bg": "#0c1224", "bg_light": "#eef2f9"},
    {"key": "teal",    "label": "Teal Night", "description": "Cyan on blue-black",                    "bg": "#0f171e", "bg_light": "#eef3f6"},
    {"key": "mono",    "label": "Mono",       "description": "Black and white, almost no colour - great on OLED", "bg": "#000000", "bg_light": "#f5f5f7"},
)
BY_KEY = {t["key"]: t for t in THEMES}
def is_valid(key) -> bool: ...                  # str in BY_KEY (exact, case-sensitive); non-str -> False
def get(key) -> dict: ...                       # BY_KEY[key], else BY_KEY[DEFAULT]
def from_cookie(header) -> str: ...             # Decision 3; header may be None/""; never raises
def set_cookie_value(key) -> str: ...           # "wn_theme=<key>; Path=/; Max-Age=34560000; SameSite=Lax"
                                                # (raises ValueError if not is_valid(key))
# bg / bg_light = the theme's --bg in dark / light (used for <meta name="theme-color">); tests check they match app.css.

# settings_page.py (backend-dev)
APPEARANCE_TAB = ("appearance", "Appearance", "/appearance")
def subtabs_html(current) -> str: ...
    # The existing <nav class="subtabs" aria-label="Settings sections">...</nav>, byte-identical for the
    # SECTIONS tabs (href="/settings?section=<key>", class "subtab on" for current), plus
    # <a class="subtab[ on]" href="/appearance">Appearance</a> last. render() uses it. SECTIONS is unchanged
    # (it stays the POST /settings whitelist).

# web.py (backend-dev)
def current_theme() -> str: ...      # the theme key for the request being handled; themes.DEFAULT outside a request
# Handler: do_GET/do_POST first set the thread-local from themes.from_cookie(self.headers.get("Cookie")).
# _json(payload, status=200, headers=None) and _redirect(path, msg=None, extra=None, headers=None) gain an
# optional extra-headers dict (for Set-Cookie). Existing callers are unchanged.

# pages.py (frontend-dev)
def render_appearance(msg="") -> str: ...   # the full page via _shell(body, "settings")
# _shell: unchanged signature; reads web.current_theme() and themes.get() (so an unknown value renders as amber).
```

### HTTP
| Method | Path | Request fields | JSON response (Accept: application/json) | No-JS response |
|---|---|---|---|---|
| GET | `/appearance` | query `msg` (others ignored) | n/a | 200 `render_appearance(msg=msg)` |
| POST | `/theme` | `theme`, `return_to` (default `/appearance`) | 200 `{"ok": true, "message": "Theme set to Crimson", "theme": "crimson"}` + `Set-Cookie` · 400 `{"ok": false, "message": "That isn't a valid theme"}` (no cookie) · 403 CSRF (no cookie) | 303 to `_safe_path(return_to, "/appearance")` + `msg=Theme set to Crimson` + `Set-Cookie`. Invalid theme: same 303 with `msg=That isn't a valid theme` and no cookie |

- The CSRF check runs first, as for every POST (`do_POST` already does this). A 403 never sets a cookie.
- Valid means `themes.is_valid(form["theme"][0])`. Missing, empty, wrong case (`Crimson`), unknown and `<script>` are all invalid.
- The message label comes from the registry (`Theme set to {label}`). The server never echoes user input.
- Sample mode behaves exactly like live mode (Decision 6). The route never touches `db`, `config`, `_state`, `_log_action` or `invalidate_cache`.
- Every page response (all GETs, and the 200 from `POST /settings` `test_*`) renders `<html lang="en" data-theme="{current_theme()}">`.
- Routes table to add to api-conventions (orchestrator): `GET /appearance?msg=`, `POST /theme`.

### Data / storage
None. There's no DB change and no migration. The only state is the `wn_theme` cookie.

### Markup contract (pages.py / app.js / app.css share these)
**Shell** (every full page): changes only these bits:
```html
<html lang="en" data-theme="{key}">
...<meta name="color-scheme" content="dark light">
<meta name="theme-color" media="(prefers-color-scheme: dark)" content="{theme.bg}">
<meta name="theme-color" media="(prefers-color-scheme: light)" content="{theme.bg_light}">
```
- This replaces the single `<meta name="theme-color" content="#0a0c11">`.
- `{key}` always comes from `themes.get(web.current_theme())["key"]`, and is still `escape`d.
- The `partial=1` add-dialog fragment stays shell-less.

**Appearance page body** (`render_appearance`):
```html
<div class="settings appearance">
  {settings_page.subtabs_html("appearance")}
  {_message_notes(msg, None, "/appearance")}                       <!-- <p class="note" role="status"> when msg -->
  <form class="theme-form" method="post" action="/theme" data-enhance="theme">
    <input type="hidden" name="return_to" value="/appearance">
    <fieldset class="theme-picker">
      <legend>Colour theme</legend>
      <p class="muted theme-help">Saved on this device only. Light or dark follows your device setting.</p>
      <div class="theme-grid">
        <!-- one per themes.THEMES entry, in order -->
        <label class="theme-option" data-theme="{key}" data-meta-dark="{bg}" data-meta-light="{bg_light}">
          <input class="theme-radio" type="radio" name="theme" value="{key}"[ checked]>
          <span class="theme-swatch" aria-hidden="true">
            <span class="swatch-top"></span>
            <span class="swatch-hero"><span class="swatch-line"></span><span class="swatch-line short"></span><span class="swatch-btn"></span></span>
            <span class="swatch-row"><span class="swatch-card"></span><span class="swatch-card"></span><span class="swatch-card"></span></span>
          </span>
          <span class="theme-text">
            <span class="theme-name">{label}</span>[<span class="theme-current">Current</span>]
            <span class="theme-desc">{description}</span>
          </span>
        </label>
      </div>
    </fieldset>
    <button type="submit" class="btn-add theme-submit">Use this theme</button>
  </form>
</div>
```
- `checked` and `.theme-current` both go on the current theme's option, and only that one.
- `.theme-current` marks the *saved* theme with visible text, so the marking isn't colour-only. `:checked` marks the *selected* one.
- All labels, descriptions and hex values are `escape`d, even though they are constants.
- The page has no inline `style`.
- Native radios in a `<fieldset>`/`<legend>` give the radio-group semantics and arrow-key behaviour, so no ARIA roles are needed.
- The radio may be visually hidden, but it must stay focusable, so not `display:none`.

**app.js** (frontend-dev). Use createElement/textContent only. The single `innerHTML` stays.
- **Saved theme.** On load, `saved = <html>.getAttribute("data-theme")`.
- **Applying a theme.** `applyTheme(option)` does two things:
  - sets `data-theme` on `<html>` to the option's radio value;
  - sets the `content` of `meta[name=theme-color][media="(prefers-color-scheme: dark)"]` / `[media="(prefers-color-scheme: light)"]` from `data-meta-dark` / `data-meta-light`.
- **`change` on `form[data-enhance=theme] .theme-radio`.** Apply the theme at once, then save after a 400 ms debounce. A new change cancels a pending save. Arrow-keying through the group previews each theme live but saves only the last one.
- **Save.** `post("/theme", {theme, return_to})`.
  - `ok`: `saved = data.theme`, move the `.theme-current` span to that option, then `toast(data.message)`.
  - `ok:false`: re-apply and re-check the `saved` option, then show an error toast.
  - Not JSON: `failed(form, err)`, which falls back to a native submit.
  - Network error: revert as for `ok:false`, then toast "Couldn't reach the server - try again.".
- **`handlers.theme`** (submit): cancel any pending debounce and save the checked value now.
- **`pageshow` with `event.persisted`.** Read `wn_theme` from `document.cookie`. If it matches `^[a-z]{1,20}$`, set `<html data-theme>`. If the picker is on the page, also check that radio and move `.theme-current`.
- **No transitions** on theme change.
- `html.js` hides `.theme-submit` via CSS (ux).

**app.css** (ux-designer): Decision 7 structure plus the token table below.
- Style `.appearance`, `.theme-picker`, `.theme-help`, `.theme-grid`, `.theme-option`, `.theme-radio`, `.theme-swatch`, `.swatch-top`, `.swatch-hero`, `.swatch-line(.short)`, `.swatch-btn`, `.swatch-row`, `.swatch-card`, `.theme-text`, `.theme-name`, `.theme-current`, `.theme-desc`, `.theme-submit` (`html.js .theme-submit { display: none }`).
- **`.theme-option` must set its own `background: var(--surface); color: var(--text); border-color: var(--line)`.** Inherited `color` is already computed from the page's theme, so without this the card wouldn't show its own theme's colours.
- The swatch parts may use any of `themes.TOKENS`.
- The swatch parts must **not** use `--scrim-*` or `--topbar-bg`. Those are inherited from `<html>`, so they show the page's theme, not the card's.
- **Selected state** (`.theme-option:has(.theme-radio:checked)`): a 2-3px `var(--accent)` border plus a check mark, so it isn't colour-only.
- **Focus**: `.theme-option:has(.theme-radio:focus-visible)` gets `outline: 3px solid var(--focus)`.
- **Grid layout**: 2 columns at 375px, auto-fill on wider screens, no horizontal scroll.

### Token table (dark / light). Exact values, `rgba` alphas included
Amber is today's values. The only additions are the two new tokens, set to the values currently hard-coded. Ratios are WCAG contrast, computed with the sRGB formula. ux-designer may tune any value but must keep every ratio at or above its threshold and report changes.

| Token | Amber | Crimson | Lime | Ocean | Teal Night | Mono |
|---|---|---|---|---|---|---|
| `--bg` | `#0a0c11` / `#f3f4f7` | `#141414` / `#f4f4f4` | `#0b0c0f` / `#f2f5f3` | `#0c1224` / `#eef2f9` | `#0f171e` / `#eef3f6` | `#000000` / `#f5f5f7` |
| `--bg-2` | `#10131a` / `#e9ebf0` | `#1a1a1a` / `#eaeaea` | `#111318` / `#e7ece9` | `#111a30` / `#e3e9f4` | `#141f29` / `#e2eaef` | `#0a0a0a` / `#ebebef` |
| `--surface` | `#161a23` / `#ffffff` | `#1f1f1f` / `#ffffff` | `#171a21` / `#ffffff` | `#16203a` / `#ffffff` | `#1a2632` / `#ffffff` | `#141414` / `#ffffff` |
| `--surface-2` | `#1e2330` / `#f0f1f5` | `#2a2a2a` / `#f0f0f0` | `#1f232c` / `#eef2ef` | `#1e2a48` / `#edf1f8` | `#232f3e` / `#edf2f5` | `#1f1f1f` / `#f0f0f3` |
| `--line` | `#2a3140` / `#d9dde5` | `#3a3a3a` / `#dcdcdc` | `#2b303b` / `#d5ddd8` | `#2c3a5c` / `#d3dbea` | `#33414f` / `#d0dbe3` | `#2e2e2e` / `#d2d2d7` |
| `--text` | `#f3f5f9` / `#10131a` | `#ffffff` / `#141414` | `#f3f5f9` / `#0b0c0f` | `#f2f5fb` / `#0c1224` | `#f2f4f8` / `#0f171e` | `#f5f5f7` / `#1d1d1f` |
| `--muted` | `#a3acbd` / `#535c6c` | `#b3b3b3` / `#595959` | `#a3acbd` / `#4f5a54` | `#a7b2c8` / `#4d5873` | `#9fb0c0` / `#4b5a67` | `#a1a1a6` / `#626267` |
| `--accent` | `#ffb224` / same | `#e50914` / same | `#1ce783` / same | `#0072d2` / same | `#00a8e1` / same | `#f5f5f7` / `#1d1d1f` |
| `--accent-ink` | `#1a1200` / same | `#ffffff` / same | `#0b0c0f` / same | `#ffffff` / same | `#0f171e` / same | `#000000` / `#ffffff` |
| `--accent-hover` | `#ffc24f` / same | `#b20710` / same | `#5cf0a8` / same | `#005fb8` / same | `#33bde9` / same | `#d2d2d7` / `#3a3a3c` |
| `--focus` | `#ffb224` / `#8a4f00` | `#ff5a61` / `#b20710` | `#1ce783` / `#087040` | `#4da3ff` / `#005bb0` | `#00a8e1` / `#006c94` | `#f5f5f7` / `#0066cc` |
| `--accent-text` | `#ffc557` / `#8a4f00` | `#ff5a61` / `#b20710` | `#1ce783` / `#087040` | `#4da3ff` / `#005bb0` | `#00a8e1` / `#006c94` | `#2997ff` / `#0066cc` |
| `--accent-soft` | `rgba(255,178,36,.14)` / `.22` | `rgba(229,9,20,.16)` / `.12` | `rgba(28,231,131,.13)` / `.2` | `rgba(0,114,210,.22)` / `.12` | `rgba(0,168,225,.15)` / `.12` | `rgba(255,255,255,.1)` / `rgba(0,0,0,.07)` |
| `--accent-glow` (new) | `rgba(255,178,36,.3)` / same | `rgba(229,9,20,.35)` / `.25` | `rgba(28,231,131,.3)` / `.35` | `rgba(0,137,236,.4)` / `rgba(0,114,210,.25)` | `rgba(0,168,225,.32)` / `.25` | `rgba(255,255,255,.18)` / `rgba(0,0,0,.15)` |
| `--match-text` (new) | `#ffc557` / same | `#46d369` / same | `#1ce783` / same | `#7dbbff` / same | `#4cc4ee` / same | `#f5f5f7` / same |

"same" means the light value equals the dark value. It is still declared in the light block (Decision 7). An alpha alone means the same rgb with that alpha.

**Contrast (dark / light), all at or above threshold:**

| Check (threshold) | Crimson | Lime | Ocean | Teal Night | Mono |
|---|---|---|---|---|---|
| `--accent-ink` on `--accent` (4.5) | 4.8 / 4.8 | 11.9 / 11.9 | 4.8 / 4.8 | 6.6 / 6.6 | 19 / 16.9 |
| `--accent-ink` on `--accent-hover` (4.5) | 7.2 / 7.2 | >11.9 | 6.3 / 6.3 | >6.6 | 13.8 / 11.4 |
| `--accent-text` on `--surface-2`, the worst surface (4.5) | 4.7 / 6.3 | 9.6 / 5.4 | 5.4 / 5.9 | 5.0 / 5.2 | 5.5 / 4.9 |
| `--focus` on `--bg` and `--surface` (3.0) | ≥5.4 / ≥6.3 | ≥10 / ≥5.4 | ≥6 / ≥5.9 | ≥5.6 / ≥5.2 | ≥15 / ≥4.9 |
| `--muted` on `--surface-2` (4.5) | 6.8 / 6.1 | 6.9 / 6.4 | 6.7 / 6.3 | 6.1 / 6.3 | 6.4 / 5.4 |
| `--match-text` on the chip over a white poster, worst case (4.5) | 5.5 | 6.5 | 5.3 | 5.3 | high |

Notes:
- Brand red `#e50914` is only 3.8:1 on `#141414`, so Crimson's dark `--accent-text`/`--focus` are lifted to `#ff5a61`. Red is used only as a fill and as accent text, never as a background wash. `--accent-soft` stays at 16% or less.
- Teal's `#00a8e1` fails with white text (2.7:1), so its ink is dark. Ocean uses the darker `#0072d2` so that white ink passes. `#0089ec` with white is 3.6:1.
- Mono's fill is near-white with black ink in dark mode and inverts in light mode. Links are blue for affordance. There's no orange secondary in Teal Night: one accent keeps the token set small.
- The hero copy over a backdrop is covered by the `--bg`-based scrims. ux must still eyeball Mono (pure-black scrims) and every light variant over a bright backdrop.

## File ownership
| Agent | Files | Task |
|---|---|---|
| backend-dev | `themes.py` (new), `web.py`, `settings_page.py`, `tests/test_themes.py` (new), `tests/test_web_async.py`, `tests/test_web.py`, `tests/test_settings.py` | Registry, cookie parse/set, thread-local + `current_theme`, `POST /theme`, `subtabs_html`, then `GET /appearance` |
| frontend-dev | `pages.py`, `static/app.js`, `tests/test_ui.py` | `_shell` data-theme + metas, `render_appearance`, JS picker, CSS-sync tests |
| ux-designer | `static/app.css` | Token restructure (Decision 7), five new theme blocks, two new tokens, picker styling |
| orchestrator | `README.md`, `.claude/skills/design-system` (themes section + token table), `.claude/skills/api-conventions` (routes, cookie) | After review |

`db.py`, `config.py`, `browse.py`, `sources.py` and every integration module are unchanged. No file has two owners.

**Phases.** web.py imports page names from pages.py at load time, and pages.py needs `themes`, `web.current_theme` and `settings_page.subtabs_html`. So:
1. **Phase 1: backend-dev ‖ ux-designer.**
   - Backend does everything except the `GET /appearance` route and the import change. The full suite stays green.
   - ux works from this spec alone.
2. **Phase 2: frontend-dev**, after backend phase 1. It needs ux's CSS for the CSS-sync tests to go green; if ux isn't finished, those tests are expected red, and frontend lists them.
3. **Phase 3: backend-dev** (resume, about 5 minutes). Add `GET /appearance`, add `render_appearance` to web.py's `from pages import`, and add the end-to-end tests that need the real `_shell`.

Then the reviewer. Splitting is worth it here: three owned areas, all small. A single agent can't do it because of the ownership rules.

## Acceptance criteria
- [ ] **Registry** (`python3 -m unittest tests.test_themes`):
  - keys in order `amber, crimson, lime, ocean, teal, mono`, `DEFAULT == "amber"`;
  - keys match `^[a-z]{1,20}$`;
  - labels and descriptions are non-empty and contain no brand names: regex `(?i)netflix|hulu|disney|amazon|prime video|apple ?tv|hbo`;
  - `bg`/`bg_light` match `^#[0-9a-f]{6}$`;
  - `TOKENS` has the 15 names above;
  - `is_valid`/`get` fallbacks work;
  - `set_cookie_value` gives the exact string and raises on a bad key.
- [ ] **`from_cookie`** (`python3 -m unittest tests.test_themes`):
  - `None` and `""` give amber;
  - `wn_theme=lime` gives lime;
  - `a=1; wn_theme=ocean` gives ocean;
  - a malformed cookie before it (`junk="a,b; c"; wn_theme=mono`) still gives mono;
  - `wn_theme=Crimson`, `wn_theme=evil`, `wn_theme=<script>`, `theme=crimson` and `wn_theme=` give amber;
  - for two `wn_theme` cookies, the first valid one wins.
- [ ] **POST /theme and plumbing** (`python3 -m unittest tests.test_web_async`, new class `TestTheme`, using `NoRedirect`):
  - JSON 200 shape and the exact `Set-Cookie` value;
  - no-JS 303 to `/appearance?msg=Theme+set+to+Crimson` with `Set-Cookie`;
  - `return_to=//evil.example` falls back to `/appearance`;
  - invalid theme gives JSON 400 (no `Set-Cookie`), and no-JS 303 with the error msg and no cookie;
  - sample mode sets the cookie too;
  - the DB is untouched (`db.get_setting` / dismissed / ratings unchanged) and `invalidate_cache` is not called.
- [ ] **Thread-local** (same command): `web.current_theme()` outside a request is `"amber"`. With `web.render_home` patched to return `web.current_theme()`, `GET /` with `Cookie: wn_theme=crimson` returns `crimson`, and with no cookie or a bad one returns `amber`.
- [ ] **CSRF** (`python3 -m unittest tests.test_web_async.TestCsrf`): `("/theme", "theme=crimson")` is added to `POST_ROUTES`. Blocked requests return 403 and carry no `Set-Cookie`.
- [ ] **Settings sub-tabs** (`python3 -m unittest tests.test_settings`): `settings_page.render("plex")` contains `href="/appearance">Appearance</a>`, and the existing tab markup is unchanged. `subtabs_html("appearance")` marks only Appearance as `on`.
- [ ] **Shell** (`python3 -m unittest tests.test_ui`, with `web.current_theme` patched):
  - `<html lang="en" data-theme="ocean">` and both theme-color metas with `#0c1224`/`#eef2f9`;
  - an unknown value renders `amber`;
  - an unpatched direct render is `amber`;
  - the old single theme-color meta is gone;
  - the add-dialog partial has no `<html`.
- [ ] **Appearance markup** (`python3 -m unittest tests.test_ui`):
  - 6 `.theme-option`s in registry order, each with `data-theme`/`data-meta-*`;
  - exactly one `checked` and one `.theme-current`, both on the patched current theme;
  - the form has `action="/theme"`, `data-enhance="theme"` and the hidden `return_to=/appearance`;
  - `.theme-submit` is present;
  - `msg="<b>x</b>"` is escaped inside `.note`;
  - the Settings top-bar item has `aria-current="page"`;
  - the sub-tabs show Appearance as `on`;
  - no `style=` appears.
- [ ] **CSS ↔ registry sync** (`python3 -m unittest tests.test_ui`, new class `TestThemeCss`; parse app.css with comments stripped and braces matched):
  - for every key there's exactly one dark block and one light block (inside `@media (prefers-color-scheme: light)`), using the Decision 7 selectors;
  - each block declares exactly `themes.TOKENS`;
  - no CSS `data-theme="x"` key is missing from the registry;
  - each theme's dark `--bg` equals the registry `bg` and its light `--bg` equals `bg_light`;
  - order: each non-amber block comes after both amber blocks, and each light block after its own dark block;
  - amber dark has `--accent: #ffb224` and `--bg: #0a0c11` (unchanged);
  - every line containing `#ffb224`, `#ffc557` or `255, 178, 36` is a custom-property declaration (`^\s*--[a-z0-9-]+:`);
  - `.brand-mark` uses `var(--accent-glow)` and `.match` uses `var(--match-text)`;
  - app.css contains no brand names (same regex as above).
- [ ] **JS hooks** (`python3 -m unittest tests.test_ui`): app.js contains `data-enhance=theme`, `pageshow`, `wn_theme` and `theme-color`. It still has exactly one `innerHTML` and no external URLs.
- [ ] **End to end in sample mode** (`python3 -m unittest tests.test_web`, phase 3):
  - `GET /appearance` returns 200 with 6 options and amber checked;
  - with `Cookie: wn_theme=ocean`, `/`, `/movies`, `/library`, `/settings` and `/appearance` all render `data-theme="ocean"`, and ocean is checked on `/appearance`;
  - `/appearance?x=<script>` returns 200;
  - `/settings` shows the Appearance tab.
- [ ] **Manual** (no browser here): Hamish clicks through `SAMPLE=1 PORT=8099 python3 web.py` and checks:
  - each swatch previews its own colours, in light mode too;
  - clicking or arrow-keying applies the theme instantly and a toast confirms it;
  - a reload keeps the theme;
  - Back after a change shows the new theme;
  - with JS off, the "Use this theme" button posts and comes back with a note;
  - for each theme, Home's hero, rows, hover preview, top bar (scrolled and unscrolled), bottom nav at 375px, modal, Library stars and Settings look right in dark and light;
  - focus rings are visible everywhere.
- [ ] **Full suite** green after phase 3: `python3 -m unittest discover -s tests`

## Live-request budget
**Zero** for every implementer and the reviewer. Nothing in this feature talks to Plex, TMDB, Radarr, Sonarr, Tautulli or AI providers, and tests use the existing sample mode and temp DBs.

## Open questions
None of these block. Each has a default.
1. **Top-bar shortcut.** Should there be a small palette icon next to Settings that goes to `/appearance`? Default: **no**, which keeps the cinematic top bar clean; Appearance is the last Settings tab. If yes, it's a follow-up for frontend-dev plus ux (one link, one icon).
2. **Light variants.** Streaming apps are dark-only, but per the brief every theme follows the device's light/dark setting. Default: keep that. An "Always dark" toggle could follow later (it would need a second cookie).
3. **Mono's accent.** Default: a white fill with black text, and blue links (`#2997ff` dark / `#0066cc` light). The alternative is a blue fill.
4. **Stars stay gold** (`--star-*`) in every theme, because gold is the universal rating colour. Default: yes.
5. **Cookie name.** It's `wn_theme`, not `theme` (Decision 2). This is just to confirm.

---

## Task briefs

### backend-dev: phase 1, then phase 3
Spec: `specs/themes.md`. The Contract is fixed; report back if any of it can't work. Live-request budget: **none**. Own: `themes.py` (new), `web.py`, `settings_page.py`, `tests/test_themes.py` (new), `tests/test_web_async.py`, `tests/test_web.py`, `tests/test_settings.py`.

**Phase 1** (no `from pages import` change; the suite must stay green):
1. Write `themes.py` exactly as in the Contract (pure, never raises in `from_cookie`).
2. In `web.py`:
   - add the thread-local and `current_theme()`, and set it first thing in `do_GET` and `do_POST`;
   - add the optional `headers` parameter to `_json`/`_redirect`;
   - add `POST /theme` per the HTTP table. Read `return_to` with default `/appearance` and pass it through `_safe_path(..., "/appearance")`. Sample mode is **not** refused. Don't touch db/config/state.
3. In `settings_page.py`: add `APPEARANCE_TAB` and `subtabs_html(current)`, and make `render()` use it. The existing tab markup must stay byte-identical.
4. Tests: `test_themes.py`, `TestTheme` in test_web_async (including the thread-local check via a patched `web.render_home`), `/theme` in `POST_ROUTES` with no `Set-Cookie` on 403, and the sub-tab test.

**Phase 3** (after frontend phase 2):
1. Add `GET /appearance` → `render_appearance(msg=query msg)`.
2. Add `render_appearance` to web.py's `from pages import`.
3. Add the sample-mode end-to-end tests in `test_web.py`.

Run `python3 -m unittest discover -s tests`. Report files changed, test results and any deviations.

### frontend-dev: phase 2
Spec: `specs/themes.md` ("Markup contract", Decisions 4-5). Own: `pages.py`, `static/app.js`, `tests/test_ui.py`. Live-request budget: **none**. Start after backend phase 1, because you need `themes.py`, `web.current_theme()` and `settings_page.subtabs_html()`.
1. Change `_shell` to emit `data-theme` on `<html>` and the two theme-color metas from `themes.get(web.current_theme())`. Don't change the signature.
2. Write `render_appearance(msg="")` exactly per the markup contract, using `_shell(body, "settings")`. Import `themes` and `settings_page` at the top of pages.py; neither imports web or pages.
3. In app.js: `applyTheme`, the debounced save on `change`, `handlers.theme`, revert on failure, moving `.theme-current`, the `pageshow` re-sync and the theme-color meta updates. Keep the createElement/textContent rule.
4. In test_ui: the Shell, Appearance markup, `TestThemeCss` (CSS ↔ registry sync, parsed as described in the acceptance criteria) and JS-hook criteria. Patch `web.current_theme` with `mock.patch.object`.

Don't edit app.css. If a class is missing, ask via the orchestrator. `web.py` won't route `/appearance` until phase 3. Test `render_appearance` directly. Run `python3 -m unittest tests.test_ui`, then the full suite. List any expected-red tests and everything in app.js that needs a hand check.

### ux-designer: phase 1 (parallel with backend-dev)
Spec: `specs/themes.md` (Decisions 1, 7, 8, the token table and the app.css part of the markup contract). Own: `static/app.css` only. Live-request budget: **none**.
1. **Restructure the tokens per Decision 7.**
   - Amber's TOKENS move into `:root, [data-theme="amber"]` (dark) and the same selector inside `@media (prefers-color-scheme: light)`, using today's values plus `--accent-glow` and `--match-text`.
   - Non-theme tokens stay in plain `:root` blocks.
   - Amber must render pixel-identical to today.
2. **Add the five theme blocks** (dark, then light) in registry order, each declaring exactly the 15 TOKENS. Use the selector form `[data-theme="key"]` exactly; the test parses for it.
3. **Replace the two hard-coded ambers:** `.brand-mark` box-shadow becomes `var(--accent-glow)`, and `.match` colour becomes `var(--match-text)`.
4. **Style the picker classes** listed in the markup contract:
   - mini preview cards: a top-bar strip, a hero with title lines and an accent button, and a row of three cards;
   - the selected border plus a check mark (not colour-only);
   - `:focus-visible` on the card via `:has()`;
   - 2 columns at 375px with no horizontal scroll;
   - `.theme-current` as a small pill;
   - `html.js .theme-submit { display: none }`.

Rules:
- Tokens only. Write no comments that name brands; a test forbids brand names in app.css.
- Keep every contrast ratio in the table at or above its threshold, recomputing any value you change.
- Check each theme's hero copy over the scrims, the transparent and scrolled top bar, `.rank` outlines, `.lib-tag`, `.badge`, `.note` and the toast action button in light mode.
- `prefers-reduced-motion` is unaffected. Don't add transitions on theme change.

Run `python3 -m unittest tests.test_ui`. `TestThemeCss` arrives in phase 2; until then, self-check against its rules. Report any token values you changed (with their new ratios), the classes you styled, and any class you needed that the spec doesn't name.
