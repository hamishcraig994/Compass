# Streaming redesign ("projection room")

Status: draft
Author: architect · Date: 2026-10-13

## Goal
Hamish's ask: "rebuild the compass frontend to be less AI slop looking and more professional media streaming vibe".

This is a full visual rebuild of every page to the approved reference, `assets/redesign-mockup.html` (direction "projection room"). That file is a reference only: it loads Google Fonts, and the app never does.

The direction:
- **Hero.** A full-bleed backdrop with the title bottom-left, set in a condensed billing-block face.
- **Rows.** Poster rows with plain sentence-case headings.
- **Colour.** A graphite screen with warm projector-white text. The theme accent appears only on the primary action (and its echoes, such as checkbox ticks and the theme swatch) and on the "Why Compass picked this" rule. The posters supply the colour.
- **Shapes.** Buttons are 6px rectangles, not pills. Posters have a 4px radius.
- **Gone.** Gradient washes, all-caps labels, eyebrows ("Top pick for you", "Movie"), chips on the artwork, middle-dot meta strings, lift-on-hover on cards, and the light variants of the themes.
- **Kept motion.** The hero crossfade, and responses to user actions (press, open, add/remove, toast).

**Behaviour doesn't change.** Every route, form, progressive-enhancement hook and no-JS fallback from `specs/seerr-parity.md` keeps working. The only server change is serving three bundled fonts.

## Out of scope
- New features, routes, data or settings. There are no new POSTs, no db changes and no new nav items.
- Light mode. It is removed on purpose and there is no toggle.
- Any font other than the three bundled OFL faces. There are no CDNs, no proprietary faces and no `local()` main faces.
- Changing the heading outline (the brand stays the `h1`, page headings stay `h2`). Changing copy beyond what's listed in "Markup changes". The Settings form markup (backend-owned `settings_page.py`) also stays as it is: CSS restyles it.
- README (the Themes line), `.claude/skills/design-system` and `api-conventions`. The orchestrator updates these after review.
- `static/fonts/` files and licences. Hamish already placed these.

## Decisions

**D1. Fonts are bundled and served by web.py.** There are three variable woff2 files in `static/fonts/`, each with an OFL licence next to it:

| Role | File | Family name | Axes | Used for |
|---|---|---|---|---|
| Display | `sofia-sans-condensed.woff2` | `Sofia Sans Condensed` | wght 600-900 | titles (hero, title page, page headings, list name), the brand wordmark, numerals (match %, Top 10 ranks, runtimes) |
| UI | `plus-jakarta-sans.woff2` | `Plus Jakarta Sans` | wght 400-700 | all interface text |
| Note | `source-serif-4.woff2` | `Source Serif 4` | opsz 8-60, wght 400-600 | Compass explaining itself: the hero reason line, `.reason` everywhere, "Why Compass picked this" / "How it fits your taste", "Because you watched ...", the tagline, the taste summary. Always upright, never italic |

- `font-display: swap` on all three faces.
- **Preload** the two above-the-fold faces (UI and Display) from `_shell`. The serif swaps in when it arrives.
- **No metric-override fallback faces** (`size-adjust` / `ascent-override`). The overrides have to be measured from the font files, and there's no tool for that here: woff2 is Brotli-compressed and the stdlib can't read it. The fonts come from the same LAN origin with a 1-year cache, so the swap only shows on a device's first visit. The fallback stacks below are picked for similar width and x-height.
- The licences already ship: `COPY static/ ./static/` in the Dockerfile is recursive, and `.dockerignore` excludes only `*.md`, so the `OFL-*.txt` files go into the image. They are **not** served over HTTP; only the three woff2 files are.

**D2. Dark only.** Every theme block declares one dark set of tokens:
- the `@media (prefers-color-scheme: light)` blocks are deleted everywhere (themes, non-theme tokens, stars, toasts);
- `color-scheme: dark`;
- one `theme-color` meta;
- `bg_light` is removed from the registry.

The six themes keep their keys, labels and accents. Their neutrals are re-derived toward graphite and projector-white (token table below) and every pair is checked for AA. The `compass_theme` cookie and the picker are unchanged.

**D3. The theme token set shrinks from 15 to 12.**
- **Removed:** `--accent-text`, `--accent-soft`, `--accent-glow` and `--match-text`. Accent-coloured text and tints go against "accent only on the primary action". Links become `--text` with an underline, and match % becomes `--text` in the display face.
- **Added:** `--text-2`, a softer projector-white for overview and explanation copy (the mockup's `#cfccc5`).
- **Focus:** `--focus` now equals each theme's `--text`, a light ring as in the mockup.

New `themes.TOKENS`:
`("--bg", "--bg-2", "--surface", "--surface-2", "--line", "--text", "--text-2", "--muted", "--accent", "--accent-ink", "--accent-hover", "--focus")`.

Every value is a `#rrggbb` hex, which lets a test compute the contrast.

**D4. Crimson is the default (`themes.DEFAULT`), so its block takes `:root`.**
- The selector is `:root, [data-theme="crimson"]`, and it's the first theme block.
- The other five use `[data-theme="<key>"]`, in registry order.
- Order no longer matters for correctness, because nothing overrides them any more.

**D5. Stars follow the mockup: projector-white, not gold.**
- `--star-on: var(--text)` (yours), `--star-plex: var(--muted)` (inherited from Plex), `--star-off: #787878`. That's at least 3:1 on every theme's `--surface-2`.
- The rating text still says "From Plex" / "Your rating", so the state isn't conveyed by colour alone.
- This reverses specs/themes.md Q4 (gold stars). See open question 1.

**D6. Nothing sits on the artwork.**
- The match %, status tags, New/Trending/Watched badges and the "Movie/TV" kind label all move off the poster into text lines under the title: `.card-facts`, `.card-badges` and `.card-sub`.
- Posters show only the image, or the flat placeholder with the title.
- Existing tag elements are **moved, not renamed** (`lib-tag status-tag status-*`, `badge arr-state state-*`, `badge req-state req-*`, `badge`). That keeps JS and tests stable.

**D7. Meta lines have no glyph separators.** A meta line is sibling `<span>`s separated by **one literal space**, laid out by CSS with a flex `column-gap`. The textContent then reads naturally ("Movie 2016"), and nothing like `·` / `&middot;` / `|` appears. Genre lists use ", ".

**D8. Motion.**
- **Kept:** the hero crossfade (700 ms), press feedback (`scale(.98)` on `:active`), dialog/toast/preview entrance (a short fade, 6-8px rise at most), card leave/enter on dismiss/undo, and the auto-advance progress bar on desktop.
- **Removed:** the card lift (`translateY`) and row-card zoom (`scale(1.06)`), and the preview's zoom-from-card and glide. The preview just fades: 140 ms in, 100 ms out.
- `prefers-reduced-motion: reduce` turns all of it off, as today. The hero already stops auto-rotating under reduced motion (app.js).

**D9. No rows-over-hero overlap.** The mockup's rows start below the hero, so `--hero-overlap` and `.hero + .rows { margin-top: -... }` go.

**D10. Target sizes.**
- Every interactive control is at least **44×44 CSS px**.
- **Agreed exception:** stars at 28×32 (`.rate-compact .star`, full `.star` 30×32).
- **Proposed exception (open question 3):** the hover preview's controls are 36×36, with stars 26-28×36. The preview is mouse-only and not in the tab order, and every one of its controls also exists at 44px on the title page. That is the WCAG 2.5.8 "equivalent" exception.
- Inline links inside sentences (the note "Undo" link, links in `.in-lists`) are exempt under WCAG's inline exception.

**D11. Copy stays the same, with three exceptions.**
- **Sentence-case row headings:** "Recommended for You" becomes "Recommended for you", and "Hidden Gems" becomes "Hidden gems" (browse.py, backend).
- **Eyebrows removed:** the hero kicker "Top pick for you" and the title-page kicker "Movie"/"TV series".
- **Appearance help text:** "Light or dark follows your device setting" is removed.

"Add to library" stays (test_web pins it, and the dialog names the service). See open question 2.

## Contract

### HTTP (backend-dev)
| Method | Path | Response |
|---|---|---|
| GET | `/static/fonts/sofia-sans-condensed.woff2`, `/static/fonts/plus-jakarta-sans.woff2`, `/static/fonts/source-serif-4.woff2` | 200, the file's exact bytes, `Content-Type: font/woff2` (no charset), `Cache-Control: public, max-age=31536000, immutable`, a correct `Content-Length` |
| GET | `/static/app.css`, `/static/app.js` | unchanged: 200, same content types, `Cache-Control: no-cache` |
| GET | anything else under `/static/` (the OFL `.txt` files, `/static/fonts/`, `/static/fonts`, case variants, `..`, `%2e%2e`, `//`, a trailing `/`) | 404 `Not found` text/plain, as today |

- **Exact whitelist.** The keys are compared to the raw path after `/static/`. `urlparse` doesn't percent-decode, so `%2e%2e` never matches. The query string is ignored.
- **The cache policy.** Fonts get a 1-year immutable cache because the URL is the version: if a font file ever changes, it gets a **new file name** (and a new whitelist entry). CSS and JS stay `no-cache`, so a renamed font is picked up at once.

```python
# web.py Handler (backend-dev)
STATIC_FILES = {
    "app.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "fonts/sofia-sans-condensed.woff2": "font/woff2",
    "fonts/plus-jakarta-sans.woff2": "font/woff2",
    "fonts/source-serif-4.woff2": "font/woff2",
}
FONT_CACHE = "public, max-age=31536000, immutable"

def _static(self, name):
    # name must be a STATIC_FILES key, else 404. Open os.path.join(STATIC_DIR, *name.split("/")) in "rb"
    # (a whitelist constant, never request text). OSError -> 404. Send the bytes with Cache-Control
    # FONT_CACHE for font/woff2, else "no-cache".
```
`_send` already accepts bytes. Reading app.css/app.js in binary gives the same bytes. Also update the comment above `STATIC_FILES` and the Dockerfile comment on `COPY static/` to mention fonts. No other Dockerfile change is needed.

### Python interface
```python
# themes.py (backend-dev)
TOKENS = ("--bg", "--bg-2", "--surface", "--surface-2", "--line", "--text", "--text-2", "--muted",
          "--accent", "--accent-ink", "--accent-hover", "--focus")
# THEMES: each dict keeps key, label, description, logo; "bg" changes to the new --bg below;
# "bg_light" is REMOVED (phase 2, after frontend stops reading it).
#   amber   bg "#0f1115"   crimson bg "#101114"   lime bg "#0f1011"
#   ocean   bg "#0d1220"   teal    bg "#0e161c"   mono bg "#000000"
# DEFAULT, COOKIE, is_valid, get, from_cookie, set_cookie_value: unchanged.

# browse.py (backend-dev): row titles only
#   "Recommended for You" -> "Recommended for you";  "Hidden Gems" -> "Hidden gems"

# pages.py (frontend-dev): signatures unchanged. New private helper:
def _reason_html(item) -> str: ...
    # escaped reason; when reason == "Because you watched " + " and ".join(item["because"][:n]) for n in (2, 1),
    # each name is wrapped in <strong>{escape(name)}</strong>. Otherwise the plain escaped reason. "" if no reason.
```

### Colour tokens (ux-designer writes them in app.css; backend mirrors each `--bg` in themes.py)
Each value is final. ux may tune a value only if every ratio below still passes. It must then report the new value and ratio, and the `--bg` change must go to backend.

| Token | Crimson (default) | Amber | Lime | Ocean | Teal Night | Mono |
|---|---|---|---|---|---|---|
| `--bg` (screen) | `#101114` | `#0f1115` | `#0f1011` | `#0d1220` | `#0e161c` | `#000000` |
| `--bg-2` (inputs, bottom nav) | `#141518` | `#13151a` | `#131415` | `#111727` | `#121b22` | `#0a0a0a` |
| `--surface` (dialogs, preview, panels) | `#18191d` | `#181a20` | `#18191b` | `#151c2e` | `#162129` | `#141414` |
| `--surface-2` (raised, hover, tags) | `#212227` | `#212329` | `#212224` | `#1d2539` | `#1e2a33` | `#1f1f1f` |
| `--line` (hairlines only) | `#2a2b31` | `#2b2d34` | `#2a2b2e` | `#29324a` | `#2a3842` | `#2e2e2e` |
| `--text` (projector white) | `#ece9e3` | `#ece9e3` | `#e9eae6` | `#e8ecf3` | `#e6ecef` | `#f5f5f7` |
| `--text-2` (overview, notes) | `#cfccc5` | `#cfccc5` | `#cdcec9` | `#c9d0dc` | `#c7d1d6` | `#cfcfcf` |
| `--muted` (secondary text) | `#a19e97` | `#a19e97` | `#9fa09b` | `#9aa5b8` | `#98a8b2` | `#a1a1a6` |
| `--accent` (primary fill, why rule) | `#e50914` | `#ffb224` | `#1ce783` | `#0072d2` | `#00a8e1` | `#f5f5f7` |
| `--accent-ink` | `#ffffff` | `#1a1200` | `#0b0c0f` | `#ffffff` | `#0e161c` | `#000000` |
| `--accent-hover` | `#b20710` | `#ffc24f` | `#5cf0a8` | `#005fb8` | `#33bde9` | `#d2d2d7` |
| `--focus` (= `--text`) | `#ece9e3` | `#ece9e3` | `#e9eae6` | `#e8ecf3` | `#e6ecef` | `#f5f5f7` |

Contrast (WCAG sRGB formula, rounded down). Every pair passes:

| Pair (threshold) | Crimson | Amber | Lime | Ocean | Teal | Mono |
|---|---|---|---|---|---|---|
| `--text` / `--bg` (4.5) | 15.5 | 15.5 | 15.7 | 15.7 | 15.3 | 19.2 |
| `--text-2` / `--surface-2` (4.5) | 9.9 | 9.7 | 10.0 | 9.8 | 9.4 | 10.5 |
| `--muted` / `--bg` (4.5) | 7.0 | 7.0 | 7.2 | 7.5 | 7.4 | 8.1 |
| `--muted` / `--surface-2`, worst surface (4.5) | 5.9 | 5.8 | 6.0 | 6.1 | 5.9 | 6.4 |
| `--accent-ink` / `--accent` (4.5) | 4.7 | 10.3 | 11.9 | 4.8 | 6.6 | 19.2 |
| `--accent-ink` / `--accent-hover` (4.5) | 7.1 | 11.5 | 13.4 | 6.3 | 8.3 | 13.9 |
| `--focus` / `--bg` and `--surface` (3.0) | ≥13 | ≥13 | ≥13 | ≥13 | ≥12 | ≥15 |
| `--star-off` `#787878` / `--surface-2` (3.0) | 3.5 | 3.5 | 3.6 | 3.4 | 3.3 | 3.7 |
| `--danger` `#ff8a7a` / `--surface-2` (4.5) | 6.9 | 6.8 | 6.9 | 6.6 | 6.3 | 7.1 |
| `--ok` `#6ee7a8` / `--surface-2` (4.5) | ≥9.5 in every theme |

**Non-theme tokens** go in one plain `:root` block, after the theme blocks, with dark values only:
- `color-scheme: dark`;
- `--danger: #ff8a7a`, `--danger-soft: rgba(255,110,90,.14)`, `--ok: #6ee7a8`, `--ok-soft: rgba(80,220,150,.13)`;
- the `--chip-*` / `--tone-*` tokens, unchanged;
- `--star-*` (D5);
- the scrims and `--topbar-bg`, built from `var(--bg)` as today;
- the fonts, spacing, radii and motion below.

The toast colours are fixed. Mono's accent is a white fill with black ink, the same as its text colour. That's deliberate: Mono stays colourless.

### Type system
Font stacks (non-theme tokens):
```css
--font: "Plus Jakarta Sans", system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
--font-display: "Sofia Sans Condensed", "Avenir Next Condensed", "Roboto Condensed", "Arial Narrow", sans-serif-condensed, sans-serif;
--font-note: "Source Serif 4", "Source Serif Pro", Charter, "Bitstream Charter", Cambria, Georgia, serif;
```
`@font-face`, at the top of app.css (exact):
```css
@font-face { font-family: "Sofia Sans Condensed"; src: url("/static/fonts/sofia-sans-condensed.woff2") format("woff2");
  font-weight: 600 900; font-style: normal; font-display: swap; }
@font-face { font-family: "Plus Jakarta Sans"; src: url("/static/fonts/plus-jakarta-sans.woff2") format("woff2");
  font-weight: 400 700; font-style: normal; font-display: swap; }
@font-face { font-family: "Source Serif 4"; src: url("/static/fonts/source-serif-4.woff2") format("woff2");
  font-weight: 400 600; font-style: normal; font-display: swap; }
```
- `html { font-synthesis: none; }` stops faux bold or italic.
- No rule anywhere sets `font-style: italic`. `.title-tagline` becomes upright note text.
- Root size is 16px. Body text is never below 1rem on phones, inputs are 1rem (so iOS doesn't zoom), and no text is under .75rem.

| Role | Face / weight | Size | Line-height | Other |
|---|---|---|---|---|
| Hero title `.hero-title` | display 900 | `clamp(3rem, 7vw, 5.6rem)` | .88 | `letter-spacing: -.015em`, `text-wrap: balance`, `overflow-wrap: anywhere` |
| Title page `.title-name` | display 900 | `clamp(2.6rem, 6vw, 4.6rem)` | .9 | same |
| Page heading `.top h2`, list name `.list-head h3` | display 800 | `clamp(2rem, 3.6vw, 2.75rem)` | 1 | `letter-spacing: -.01em` |
| Brand `.brand-name` | display 700 | 1.25rem | 1 | `letter-spacing: .01em`, colour `--text` |
| Match % `.match` | display 800 | 1.1rem in hero/title meta, .95rem in `.card-facts`, 1.5rem in `.why-match` | 1 | `--text`, `font-variant-numeric: tabular-nums` |
| Runtime `.runtime` | display 600 | 1.08em | 1 | `letter-spacing: .01em` |
| Top 10 rank `.rank` | display 900 | 6.5rem (phones 4.5rem) | .8 | `color: transparent`, `-webkit-text-stroke: 1.5px var(--muted)` |
| Row heading `.row-head h3`, title-page `h2`, `legend` | UI 600 | 1.15rem (title-page h2 1.05rem) | 1.3 | `--text` |
| Body / overview | UI 400 | 1rem | 1.55 | overview in `--text-2`, `max-width: 52ch` (hero) / `66ch` (title page) |
| Card title `.card .title` | UI 500 | .9rem | 1.3 | 2-line clamp in rows |
| Meta, facts, labels | UI 400 / 500 for labels | .875rem (card facts .8125rem) | 1.4 | `--muted`, `tabular-nums` |
| Nav | UI 500 | .95rem | 1 | |
| Buttons | UI 600 | .95rem | 1 | |
| Tags / badges | UI 600 | .75rem | 1.2 | sentence case, `letter-spacing: 0` |
| Note text `.reason`, `.hero-reason`, `.why-because`, `.title-tagline`, `.taste` values | note 400 (`strong` 600, `--text`) | 1.05rem (hero / why), .9rem in cards / preview | 1.5 (why 1.6) | `--text-2`, `font-style: normal` |
| Toast | UI 500 | .9rem | 1.35 | |

No `text-transform: uppercase`, no `small-caps` and no tracked-out labels anywhere.

### Spacing, radii, elevation, layers, motion (non-theme tokens)
```css
--space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px; --space-5: 24px; --space-6: 32px; --space-7: 48px; --space-8: 64px;
--gutter: clamp(16px, 3.2vw, 40px);      /* page side padding (mockup: 40px desktop, ~18px phone) */
--topbar-h: 64px;  /* 56px <=820px */     --nav-h: 64px;
--radius: 8px;      /* panels: dialog, preview, fieldset, list-create */
--radius-sm: 6px;   /* controls: buttons, inputs, selects, segmented search types, notes, toasts, pager */
--radius-xs: 4px;   /* posters, cast-free art, tags/badges, cert, swatch parts */
/* 50% only for cast faces. Nothing uses 999px. */
--shadow: 0 18px 48px rgba(0, 0, 0, .5);   /* floating layers only: dialog, preview, toast, title-page poster */
--dur-fast: 120ms; --dur-med: 200ms; --dur-hero: 700ms; --ease-out: cubic-bezier(.2, .8, .2, 1);
```
- Use the spacing scale for new rules.
- Z-index stays as today: bottom nav 40, top bar 50, preview 55, toasts 60, skip link 100.
- Cards get no shadow.

### Focus
- Global `:focus-visible { outline: 2px solid var(--focus); outline-offset: 3px; }`.
- Poster links sit inside a clipped poster, so the ring goes on the poster box: `.card-poster:has(> .poster-link:focus-visible)` gets the 2px/3px outline. The `.poster-link:focus-visible` fallback stays inset (`outline-offset: -2px`) for browsers without `:has`.
- Tracks keep enough block padding (≥6px) that the ring isn't clipped.
- Hover is a different, lighter cue: an inset 2px ring at `color-mix(in srgb, var(--text) 70%, transparent)` on a `.card-poster::after` overlay. So hover and focus can't be confused.
- `outline: none` is allowed only where the focus is drawn elsewhere (`.theme-radio`, `.search-type input`).
- `html { scroll-padding-top: calc(var(--topbar-h) + 8px) }`, and the same at the bottom for the nav on phones, so the sticky bars never hide the focused control.

### Markup changes (frontend-dev: pages.py + app.js)
Classes marked **NEW** are the ones ux-designer must style from this spec. Everything else keeps its current name.

**Shell (`_shell`)**, `<head>` only:
```html
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="{theme.bg}">                    <!-- one meta, no media attribute -->
<link rel="icon" ...>                                              <!-- unchanged -->
<link rel="preload" href="/static/fonts/plus-jakarta-sans.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/static/fonts/sofia-sans-condensed.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="/static/app.css"><script src="/static/app.js" defer></script>   <!-- unchanged strings -->
```
The body, top bar, nav and toasts markup are unchanged.

**Hero slide (`_hero_slide`, `_hero_art`)**:
```html
<article class="hero-slide[ on]" data-slide data-card="{key}" aria-label="{n} of {total}: {title}"[ hidden]>
  {_hero_art}                         <!-- .hero-glyph REMOVED: an image-less slide is just the tinted field -->
  <div class="hero-copy">
    [<p class="reason hero-reason">{_reason_html(item)}</p>]          <!-- NEW .hero-reason; moved ABOVE the title -->
    <h3 class="title hero-title">{title}</h3>
    <p class="hero-meta card-meta">{_meta_html}[<span class="hero-genres">Drama, Sci-Fi</span>]</p>   <!-- ", " not middot -->
    [<p class="hero-overview">{overview}</p>]                       <!-- NEW; omitted when there's no overview -->
    <div class="hero-actions">{primary}{More info}{hero-tools}</div>  <!-- unchanged -->
  </div></article>
```
- The `<p class="kicker">Top pick for you</p>` is removed.
- `_meta_html`'s runtime span becomes `<span class="runtime">2h 46m</span>` (**NEW** `.runtime`) everywhere it's used.

**Row card (`_row_card`)**:
```html
<article class="card row-card" data-card="{key}">
  [<span class="rank" aria-hidden="true">{n}</span>]       <!-- MOVED: first child of the article, no longer inside .card-poster -->
  <div class="card-poster">{_poster_linked}</div>          <!-- .poster-top REMOVED -->
  <div class="card-info">
    <h4 class="title">{escaped title WITHOUT the year}</h4>
    [<p class="card-facts">[<span class="match">{n}%<span class="visually-hidden"> match</span></span>] [<span>{year}</span>] [{_row_tag}]</p>]   <!-- NEW .card-facts -->
    <details class="card-details">...unchanged (summary keeps "Details: {Title (Year)}", _detail_body, _card_actions)...</details>
  </div></article>
```
- The items are separated by single spaces. `.card-facts` is omitted when it would be empty.
- `_row_tag` output (`lib-tag` / `lib-tag status-tag status-*`) moves here unchanged.

**"New in your library" card (`_library_row_card`)**: `<article class="card row-card lib-card" data-preview><div class="card-poster">{_poster_linked}</div><div class="card-info"><h4 class="title">{_title_link(name only)}</h4>[<p class="card-facts"><span>{year}</span></p>]{_card_tools}</div></article>`. The per-card "In library" tag is dropped, because the row heading already says it.

**Grid cards.** In every one, `.poster-top`, `.badges` (on the poster) and `<span class="kind">` are removed from `.card-poster`, and the title keeps "Title (Year)". The `<h3 class="title">` markup stays byte-for-byte, because test_web.py's `titles_on` regex depends on it.

| Renderer | `.card-info` children, in order |
|---|---|
| `_card` (AI page) | `h3.title`, `<p class="card-facts">[match] <span>{Movie\|TV}</span></p>`, `[<p class="card-badges">{New}{Trending as plain .badge, no .alt}</p>]`, details, actions, tools |
| `_search_card` | `h3.title`, `<p class="card-facts"><span>{Movie\|TV}</span></p>`, `[<p class="card-badges">{status tag}{Watched}{Not interested}</p>]`, arr-line, details, actions, tools |
| `_list_card` | `h3.title`, `<p class="card-facts">[match] <span>{Movie\|TV}</span></p>`, `[<p class="card-badges">{status tag}{Watched}{Not interested}</p>]`, arr-line, tools, move, remove |
| `_library_card` | `h3.title`, `<p class="card-sub"><span>{Movie\|TV}</span> <span>{year \| Added date}</span></p>`, `[<p class="card-badges">{Watched\|Started}{arr-state}{req-state}</p>]`, req-seasons, card-sources, tools |
| `_watched_card` | `h3.title`, `<p class="card-sub"><span>{Movie\|TV}</span> <span>Watched {date}</span>[ <span>{n} plays</span>]</p>`, full rate form, tools |

**NEW** `.card-badges` is the off-poster tag line.

**Title page (`render_title`)**:
```html
<article class="title-page" data-card="{key}" data-keep-on-add>
  <section class="title-hero" style="--h:N">[<img class="title-backdrop" src alt="">]
    <div class="title-hero-inner">
      <div class="title-poster">{poster}</div>
      <div class="title-head">                              <!-- the kicker is REMOVED -->
        <h1 class="title title-name">{title}</h1>
        [<p class="title-tagline">{tagline}</p>]            <!-- note face, upright -->
        <p class="card-meta title-meta">[match][year][cert][runtime/seasons][<span class="title-genres">Crime, Drama</span>]</p>
        [<p class="title-status badges">...unchanged...</p>]
        <div class="title-actions">{primary}{Watchlist toggle}{list link}{trailer}{dismiss|undismiss}{full rate form}</div>   <!-- REORDERED -->
        [<p class="in-lists">...unchanged...</p>]
      </div></div></section>
  {notes}{partial note}
  <div class="title-body">                                   <!-- NEW: two columns above 900px, one below -->
    <div class="title-main">                                 <!-- NEW -->
      <section class="title-section title-overview"><h2>Overview</h2><p class="overview">...</p></section>   <!-- facts and links MOVED out -->
      [<section class="title-section why"><h2>Why Compass picked this</h2>{why-match}[<p class="reason">..</p>][chips]
         [<p class="why-because">Because you watched A, B</p>]</section>]          <!-- NEW .why-because replaces <p class="muted"> -->
      [<section class="title-section why fit">...unchanged...</section>]
      [seasons section]
    </div>
    <aside class="title-side" aria-label="Details and cast">   <!-- NEW -->
      <section class="title-section title-details"><h2>Details</h2>{dl.title-facts}[<p class="title-links">{TMDB link}{IMDb link}</p>]</section>   <!-- NEW .title-details, .title-links -->
      [<section class="title-section cast"><h2>Cast</h2><ul class="cast-list">...unchanged li.cast-card...</ul></section>]   <!-- MOVED into the aside -->
    </aside>
  </div>
  [<section class="row title-similar">...unchanged, full width after .title-body...</section>]
</article>
```
- The unavailable/not_found state is unchanged.
- Each season row's eps text becomes two siblings: `<span class="season-eps">{n} episodes</span>` then ` <span class="season-year">{yyyy}</span>` (**NEW** `.season-year`; omitted when the year is unknown).
- `_season_eps_text` no longer joins with a middot. The same change applies in `_season_picker`.
- test_web.py's regex `Season 1 5 episodes[^S]*Available` must still match: keep the order name, episodes, year, state.

**External links** (`_ext_link` and the two inline "More on TMDB" links in `_card` / `_search_card` / `_detail_body`): the arrow becomes `<span class="ext-icon" aria-hidden="true">&#8599;</span>` (**NEW**), after the visually-hidden "(opens in a new tab)".

**Appearance (`render_appearance`)**:
- Each option is `<label class="theme-option" data-theme="{key}" data-theme-color="{bg}" data-logo="{logo}" data-favicon="{...}">`. `data-meta-dark` / `data-meta-light` are **renamed/removed**.
- The help text becomes `<p class="muted theme-help">Saved on this device only.</p>`.
- The swatch markup is unchanged.

**app.js**:
1. **Theme preview.** `setMeta` updates the single `meta[name=theme-color]` from `data-theme-color`. `setLogo` reads its bg from `data-theme-color`. Remove every `data-meta-*` and `prefers-color-scheme` reference. The `pageshow` re-sync is unchanged.
2. **`markAdded`.** If a `.status-tag` exists, flip it as today. Otherwise insert the "Added" tag into `.card-badges`, creating `<p class="card-badges">` after `.card-facts`, or after `.title` when there's no `.card-facts`.
3. **Preview, rec variant.** Prepend `<div class="preview-title">` with the card `.title` textContent, before the cloned `.card-meta`, `.chips` and `.reason`.
4. **Preview, library variant.** Clone `.card-badges` instead of `.badges`. Build `.preview-sub` by cloning `.card-sub`, not from its textContent. Keep the spans and add class `preview-sub`.
5. Nothing else changes. `setFragment` stays the only `innerHTML`, there are still no external URLs, and the createElement/textContent rule holds.

### Classes: the full contract for ux-designer
- **NEW:** `.card-facts`, `.card-badges`, `.runtime`, `.hero-reason`, `.hero-overview`, `.title-body`, `.title-main`, `.title-side`, `.title-details`, `.title-links`, `.season-year`, `.why-because`, `.ext-icon`, and `.preview-title` (it exists already, but now also appears in the rec preview).
- **MOVED:** `.rank` is now a direct child of `.row-card` (Top 10), not of `.card-poster`.
- **REMOVED from the markup** (delete their CSS): `.kicker`, `.kind`, `.hero-glyph`, `.poster-top`, `.badge.alt`, `.lib-card > .lib-tag`. Also delete every other selector that nothing renders any more, including `.hero-details`, `.detail-layout`, `.detail-main`, `[data-open-detail]`, `.card-poster[role="button"]` and the `.card-poster::after` gradient.
- Everything else in `pages.py`, `app.js` and `settings_page.py` keeps its class name and needs restyling (list below).

### Per-component restyle (ux-designer, app.css)
Follow the mockup's proportions. Only tokens: no hex outside custom-property declarations, except the existing fixed toast and poster-placeholder ink.

1. **Base.**
   - `body`: `--bg`, `--text`, `font: 400 1rem/1.55 var(--font)`.
   - `a` is `--text`, underlined with `text-decoration-color: color-mix(in srgb, var(--text) 45%, transparent)` and `text-underline-offset: 3px`. Nav, card and button links aren't underlined.
   - `.muted` is `--muted`.
   - `.skip-link` is `--text` on `--bg`, 6px radius.
   - `[hidden]` rule unchanged.
2. **Buttons.** `button, .btn, .btn-add, .btn-ghost` have `min-height: 44px`, `padding: 0 20px`, 6px radius, UI 600 .95rem, no border.
   - The default and `.btn-ghost` are the **quiet** style: `background: color-mix(in srgb, var(--text) 12%, transparent)`, `--text`, hover 18%.
   - `.btn-add` is the **primary**: `--accent` / `--accent-ink`, hover `--accent-hover`.
   - Inside `.card` (grid, row and preview-source cards), `.btn-add` renders quiet, so a grid doesn't fill up with accent. The accent fill stays on page-level primaries: the hero Add, title-page Add / Request, dialog submits, Search, Save, Generate, empty-state CTAs and the season form submit.
   - `.btn-ghost.danger` is `--danger` text with a 1px `--danger` border on a transparent background.
   - `.link-btn` is underlined `--text` text.
   - `:active` gives `scale(.98)`. `:disabled` / `[aria-busy]` give opacity .5 and `cursor: progress`.
   - No pills anywhere.
3. **Top bar.**
   - Cinematic: transparent, with the `--bg` scrim `::before` as today, and solid `--topbar-bg` plus a bottom hairline when `.is-scrolled`.
   - Non-cinematic: sticky and solid `--bg` with `box-shadow: 0 1px 0 var(--line)`.
   - `.brand`: the logo at 28px plus the wordmark in the display face, in `--text` (the logo keeps its theme colours).
   - `.topnav .nav-item`: `--muted` text, 44px tall. The active one is `--text`, weight 600, with a 2px `--text` underline (border-bottom), and no tint or background.
   - `.topbar-search .search-input`: 240px, 44px tall, 6px radius, `background: color-mix(in srgb, var(--text) 7%, transparent)`, the input border rule from item 19, and a search icon.
   - Settings and `.search-link` are 44px icon buttons. Keep the 1100px and 820px switches.
4. **Bottom nav** (≤820px): 5 items, each icon plus label. `--bg-2` at 94% with a top hairline.
   - Inactive items are `--muted`. Active is `--text`, weight 600, with a 2px `--text` bar at the top. No accent.
   - Items are at least 44px tall, with safe-area padding.
5. **Sub-tabs** (`.subtabs` / `.subtab`): text tabs like the top nav, 44px tall, `--muted`. `.on` is `--text` with a 2px underline. The container has a 1px `--line` bottom border and still scrolls horizontally.
6. **Notes and feedback.**
   - `.note` is a `--surface` panel with a 1px `--line` border and 6px radius, in `--text`.
   - `.note.error` uses the `--danger` border and `--danger-soft` fill. `.note.success` uses `--ok` / `--ok-soft`. `.note.muted-note` is `--muted` text with no fill.
   - `.updating` is a neutral `--surface-2` chip.
   - `.spinner` is a `--line` ring with a `--text` top.
   - `.skeleton-row span` has a 4px radius.
   - `.building-error` is `--danger`.
7. **Toasts.** A `--text` background with `--bg` text, 6px radius and `--shadow`.
   - `.toast.error` keeps its fixed `#ffd9d3` / `#5c0f07`.
   - `.toast-action` is `--bg` fill with `--text` text. Other toast buttons are transparent with a `color-mix(in srgb, var(--bg) 30%, transparent)` border.
   - All toast buttons are 36px tall. The toast is pointer-only chrome, and the action is also reachable as a link on the page.
8. **Cards (grid).**
   - `.grid`: `minmax(160px, 1fr)` columns, gaps 28px / 16px; 2 columns at ≤520px.
   - `.card-poster`: 4px radius, `--surface-2` placeholder background, **no shadow, no transform**. `::after` gives a hairline inner edge, and the 2px ring on `.card:hover` (item 9 of Focus).
   - `.poster-empty` is a **flat** `hsl(var(--h, 30) 24% 22%)` field (no gradients), with the title in display 700 1.35rem, `rgba(255,255,255,.92)`, bottom-left.
   - `.card-info` gap is 6px. `.card .title` is UI 500 .9rem.
   - `.card-facts` and `.card-sub` are flex rows with `column-gap: 10px`, `--muted` .8125rem, tabular. The `.match` in them is display 800 `--text`.
   - `.card-badges` is a wrapping row with a 6px gap.
   - `.card-details > summary` is `--text` 600 .84rem with the chevron (no accent). `.detail-body` has no panel: just spacing, `.reason` in note text, `.overview` in `--text-2`.
   - `.chips` / `.chip`: quiet tags (4px radius, `--surface-2`, `--text`, .75rem 600), never pills.
   - `.card .card-actions` is unchanged in layout.
9. **Tags and badges** (all off the artwork now): 4px radius, UI 600 .75rem, sentence case, at least 22px tall.
   - `.badge` is neutral `--surface-2` / `--text`.
   - `.lib-tag` is neutral `--surface-2` / `--text`, with no accent fill. Inside `.card-facts` it's plain `--text-2` text with no fill.
   - `.status-tag.status-plex` is the neutral tag. `.status-radarr` / `.status-sonarr` are a transparent tag with a 1px `--muted` border. `.status-added` is tone-ok.
   - `.badge.arr-state.*` and `.badge.req-state.*` keep their tone tokens (already 7:1+, and the text carries the meaning).
   - `.badge.cert` is 1px `--muted` outlined, 4px radius.
   - `.src` is a 4px tag, `--text-2`, with a 1px `--line` border. `.src-plex` gets a `--muted` border. No uppercase.
   - In the hero, `.hero-actions .lib-tag` is a 44px-tall, non-interactive quiet tag (1px `--line` border, `--text-2`).
10. **Stars and card tools.**
    - `.star` colours come from `--star-*` (D5). Sizes are unchanged: full 30×32, compact 28×32, title page 36×40.
    - `.star-clear` is at least 32px tall.
    - `.list-toggle` and `.list-link` are 44×44 with an 18px icon, transparent and `--muted`, hover `--text`. `.on` / `[aria-pressed=true]` is `--text` with the filled bookmark (no accent tint).
    - `.card-tools` wraps (5 stars on one line, the icons on the next at 375px).
    - `.hero-tools .list-toggle` is a 44×44 quiet square. Its text is visually hidden.
    - `.title-actions .list-toggle` and `.list-link` are quiet buttons with visible text.
11. **Hero.**
    - Desktop: full-bleed, `min-height: clamp(460px, 46vw, 680px)`, running under the transparent top bar. The copy sits bottom-left, `max-width: 560px`, with `padding: 0 var(--gutter) 56px` and a gap of 14px.
    - `.hero-art`'s no-image fallback is a flat `hsl(var(--h, 30) 22% 14%)`, with no radial washes.
    - The `::after` scrim keeps the mockup's left and bottom `--bg` gradients. The copy must be AA over any image.
    - `.hero-reason` is note text 1.05rem in `--text-2`, with `strong` in `--text` 600.
    - `.hero-meta` is a flex row with `column-gap: 18px`, `--muted` .92rem; the `.match` in it is display 800 1.1rem `--text`.
    - `.hero-overview` is `--text-2`, 52ch, clamped to 3 lines, and hidden at ≤820px.
    - `.hero-actions` buttons are 44px. "More info" (`.hero-more`) is quiet.
    - `.hero-dots` sit at `right: var(--gutter)`, `bottom: 60px`. Each `.hero-dot` is a 44×44 button whose `::before` is a 22×3 bar, `color-mix(--text 30%)`, with `.on` in `--text`. The desktop progress fill is `--text`, not accent.
    - `.hero-pause` is a 44px quiet button.
    - Phones (≤820px): keep the native scroll-snap swipe and the poster image, but full-bleed (no margin, border or radius). The slide is `aspect-ratio: 4 / 5` (about 470px at 375px), runs under the transparent bar (`margin-top: calc(-1 * var(--topbar-h))` on the first block, or equivalent) and has a bottom scrim. Primary and More info share a row, with the Watchlist square after them. The dots sit centred below the hero.
12. **Rows.**
    - `.rows`: `gap: 34px`, no overlap (D9).
    - `.row-head h3` is UI 600 1.15rem. `.row-sub` is `--muted` .875rem on the same baseline.
    - `.track`: `--card-w: clamp(118px, 13vw, 200px)`, gap 14px (10px on phones), `padding-block: 8px 16px`, about 3.3 cards visible on phones.
    - `.row-card .card-poster` has no border.
    - Remove the row-card `scale(1.06)` and the poster `translateY`.
13. **Top 10** (`.track-numbered`):
    - Each `.row-card` is a grid, `grid-template-columns: var(--rank-w) minmax(0, 1fr)` with `--rank-w: 64px` (44px on phones). `.rank` is in column 1, aligned to the bottom of the poster row. The poster and `.card-info` are in column 2. The card's flex-basis is `calc(var(--card-w) + var(--rank-w))`.
    - "10" must be fully visible and not clipped by the track. It may tuck under the poster edge by up to 12px, with the poster stacked above it.
14. **Row arrows.** Unchanged behaviour. Keep the scrim gradient (functional). The chevron is `--text`.
15. **Hover preview.** 8px radius, `--surface`, 1px `--line`, `--shadow`, about 340px wide.
    - Opening is a fade only (D8). Ignore `--from-scale` / `--origin-*` and drop the `.is-moving` glide.
    - `.preview-poster` is 16:9.
    - `.preview-title` is UI 600 1rem.
    - `.preview-btn` is a 36×36 square with 6px radius (D10 exception): quiet, with `.primary` as the accent fill.
    - The cloned `.list-toggle` / `.list-link` and the stars match `.preview-btn`.
    - `.reason` is note .9rem in `--text-2`. `.card-meta` is .8125rem.
16. **Title page.**
    - `.title-hero`:
      - padding `calc(var(--topbar-h) + 86px) var(--gutter) 40px`;
      - grid `220px minmax(0, 1fr)`, gap 36px, items aligned to the end;
      - the backdrop with the mockup's left and bottom scrims;
      - a flat `hsl(var(--h) 22% 14%)` fallback.
    - `.title-poster` is 220px wide, 4px radius, with `--shadow`.
    - `.title-head` gap is 14px. `.title-meta` is like `.hero-meta`. `.title-status` is a wrapping row (the mockup's "availability" tags). `.title-actions` wraps with a 10px gap.
    - `.title-body`: `grid-template-columns: minmax(0, 1fr) 320px`, gap 48px, `padding: 32px var(--gutter) 48px`. One column at ≤900px, with the aside after the main column.
    - `.title-section` has no panels. Its `h2` is UI 600 1.05rem, and sections are 28px apart.
    - `.title-overview .overview` is `--text-2`, 66ch.
    - `.why` (and `.why.fit`) has **no panel**: `border-left: 2px solid var(--accent); padding-left: 16px`. `.why-match` is display 800 1.5rem `--text`. `.reason` is note 1.05rem/1.6. `.why-because` is note .95rem `--muted`.
    - `.title-facts` is an `auto 1fr` grid, gap 10px / 18px, .92rem. `dt` is `--muted` in sentence case (no uppercase). `dd` is `--text`.
    - `.title-links` is a wrapping row of `.ext-link`s. `.ext-icon` sits 4px after the text.
    - **Cast** sits in the aside as a vertical list:
      - `.cast-list` is a grid with a 12px gap and **no horizontal scroll**;
      - `.cast-card` is a `44px 1fr` grid, with `.cast-photo` spanning 2 rows as a 44px circle (`object-fit: cover`; the initial in the display face on `hsl(var(--h) 20% 24%)`);
      - `.cast-name` is UI 500 .9rem; `.cast-role` is `--muted` .8125rem.
    - `.title-tagline` is note 1.05rem `--text-2`, upright.
    - At ≤600px the poster is 46% wide (max 180px) above the head.
17. **Seasons.**
    - `.season-list` has no panels: rows divided by 1px `--line` top borders, plus a bottom border on the last row.
    - `.season-row` is a grid `24px minmax(0, 1fr) auto auto auto` (check, name, eps, year, state), at least 48px tall. `.season-name` always sits in column 2, even when there's no checkbox.
    - `.season-check` uses `accent-color: var(--accent)`. `.season-eps` and `.season-year` are `--muted`.
    - `.season-actions` buttons are 44px.
    - Season picker (dialog): `.season-mode` rows 44px, and `.season-picker .season-list` scrolls vertically as today.
18. **Dialogs.**
    - `dialog.modal`: `--surface`, 8px radius, 1px `--line`, `--shadow`, backdrop `rgba(0,0,0,.72)`. Entrance is a 200ms fade plus a 6px rise.
    - `.modal-close` is a 44×44 square with 6px radius.
    - `.dialog-head .thumb` has a 4px radius. `.dialog-head h3` is UI 600 1.2rem.
    - `.list-choice` rows are 44px. `.list-new` is a labelled input.
19. **Forms and Settings.**
    - `fieldset`: a `--surface` panel, 1px `--line`, 8px radius, 20px padding. `legend` is UI 600 1.05rem `--text`.
    - Labels are UI 500 .875rem `--muted`.
    - Text inputs and selects: 44px tall, 1rem, `--bg-2`, 6px radius, `border: 1px solid color-mix(in srgb, var(--muted) 70%, transparent)` (≥4:1 on `--bg`), and `--text` border on focus.
    - Checkboxes are 20px with `accent-color: var(--accent)`.
    - `.settings` is max 720px.
20. **Search page.**
    - `.search-page .search-input` is 52px with 6px radius.
    - `.search-types` is a segmented control: each `.search-type` is 44px with 6px radius, quiet. Checked is a `--text` fill with `--bg` text and the existing check mark (not colour-only). Keep the `:has()` fallback.
    - Status tags follow item 9.
21. **Library.**
    - `.toolbar` is unchanged in layout, with 44px controls.
    - `.pager a` and the disabled spans are 44px quiet rectangles with 6px radius.
    - Watched cards keep full stars.
    - `.req-seasons` is `--text-2`.
22. **Lists.**
    - `.list-tile` has no panel. `.list-tile-posters` is a 2×2 collage with 4px radius and an inner hairline, and gets the hover ring on `a.list-tile:hover`. `.list-tile-name` is UI 600 1rem. `.list-tile-meta` and `.list-tile-desc` are `--muted`.
    - `.list-create` is a `--surface` panel with 8px radius.
    - `.list-head h3` uses the display face (type table).
    - The `.list-edit` / `.list-delete` summaries are 44px quiet buttons; delete is the danger variant.
    - `.list-move` / `.list-remove` buttons are 44×44.
23. **AI page.** `.ai-intro` is **no panel, no gradient**: a 60ch `--text-2` paragraph followed by the Generate button. `.ai-toolbar` is unchanged.
24. **Appearance.**
    - `.theme-option`: `--surface`, a 2px `--line` border, 8px radius.
    - The selected option gets a 2px `--text` border plus the check mark (a `--text` circle with a `--bg` tick).
    - Focus is the `:has(.theme-radio:focus-visible)` outline, 2px `--focus`, offset 3px.
    - Swatch parts are 2-4px radius. `.swatch-btn` is `--accent`. `.swatch-top::before` is `--text`, since the active nav isn't accent any more.
    - `.theme-current` is a neutral tag.
    - `html.js .theme-submit { display: none }` is kept.
25. **Empty, building and error states.** `.empty` and `.building` are centred columns with **no border and no panel**, `padding: clamp(40px, 10vh, 96px) 20px`. `h3` is UI 600 1.25rem, `p` is `--muted` 46ch, and the spinner is 32px.
26. **Footer strip.** `.browse-foot` and `.top.browse-top` are unchanged in layout. `.taste b` is UI 600 `--muted` in sentence case (no uppercase or letter-spacing), and its values are note text.
27. **Motion and reduced motion.**
    - Keep the global reduced-motion block, and extend it to the new rules.
    - `.hero-slide` crossfade is `var(--dur-hero)`.
    - No `:hover` rule may set `transform`.

## File ownership
| Agent | Files | Task |
|---|---|---|
| backend-dev | `web.py`, `themes.py`, `browse.py`, `Dockerfile` (comment only), `tests/test_web_async.py`, `tests/test_themes.py`, `tests/test_browse.py`, `tests/test_web.py` | Phase 1: the font whitelist and binary static read, TOKENS and the new `bg` values, sentence-case row titles, tests. Phase 2: remove `bg_light` |
| frontend-dev | `pages.py`, `static/app.js`, `tests/test_ui.py` | Every markup change above, the app.js items, and test_ui rewrites and new design-rule tests |
| ux-designer | `static/app.css` | Full rewrite to the token system and the per-component list |
| orchestrator | `README.md` (Themes line, by Hamish), `.claude/skills/design-system`, `.claude/skills/api-conventions` (fonts in the static whitelist) | After review |

No file has two owners. `settings_page.py`, `db.py`, `sources.py` and the integrations don't change. ux-designer is needed: this is a design-led rewrite of about 1,500 lines of CSS. Frontend's markup work is small by comparison but touches every renderer.

**Run order:**
1. **Phase 1: all three in parallel.**
   - ux-designer works from this spec's class contract: the existing class names plus the NEW list, with the HTML skeletons above as the source of truth. It doesn't wait for the markup.
   - frontend-dev changes the markup and writes the tests.
   - backend-dev does part A.
   - Why this is safe: each agent owns different files, and the contract fixes every class name, token and value. Running sequentially (frontend first, then ux) would cost a whole extra run and buy little, because ux can't render pages here either way.
   - **Expected red until all three finish** (each agent should judge its own work with its targeted command):
     - `TestThemeCss`, the new contrast, font, accent and dead-CSS tests in test_ui: they need ux's app.css, frontend's tests and backend's TOKENS/bg;
     - the test_ui row-title assertion: it needs browse.py.
2. **Phase 2: backend-dev part B** (about 5 minutes), only after frontend-dev reports that pages.py no longer reads `bg_light`. It removes `bg_light` from themes.py and asserts it's gone. Removing it earlier would break every page render.
3. **Reviewer.** Full suite, the design-rule greps, and a class-sync check (every selector in app.css is rendered somewhere; every NEW class has a rule).
4. **One fix-up round.** ux and/or frontend, in parallel when it's different files. Then a re-review.
5. **Hamish** does the manual browser pass.

## Acceptance criteria
Full suite: `python3 -m unittest discover -s tests`. 1132 OK today, plus the new tests.

**Backend (phase 1, 2)**: `python3 -m unittest tests.test_web_async.TestStatic tests.test_themes tests.test_browse tests.test_web`
- [ ] Each of the 3 fonts gives 200, `Content-Type` exactly `font/woff2`, `Cache-Control` exactly `public, max-age=31536000, immutable`, `Content-Length` equal to the file size, and a body byte-equal to the file that starts with `b"wOF2"`. Read the raw bytes, not the str helper.
- [ ] app.css and app.js are unchanged: same types, `no-cache`, byte-equal.
- [ ] 404 for:
  - `/static/fonts/`, `/static/fonts`;
  - `/static/fonts/OFL-plusjakartasans.txt`, `/static/fonts/OFL-sourceserif4.txt`;
  - `/static/fonts/x.woff2`, `/static/FONTS/plus-jakarta-sans.woff2`;
  - `/static/fonts//plus-jakarta-sans.woff2`, `/static/fonts/plus-jakarta-sans.woff2/`;
  - `/static/fonts/../app.css`, `/static/fonts/%2e%2e/web.py`, `/static/fonts/plus-jakarta-sans.WOFF2`, `/static/plus-jakarta-sans.woff2`.
  None of them leaks file content.
- [ ] With `STATIC_DIR` patched to an empty temp dir, a font gives 404, not 500.
- [ ] `themes.TOKENS` equals the 12-tuple. Each `bg` is the new value. `DEFAULT == "crimson"`. Phase 2: no theme has a `bg_light` key.
- [ ] Row titles "Recommended for you" and "Hidden gems" appear in test_browse and in test_web's home headings.
- [ ] The Dockerfile still has `COPY --chown=app:app static/ ./static/` (recursive). Check by reading it.

**Frontend**: `python3 -m unittest tests.test_ui`
- [ ] **Shell:**
  - exactly one `<meta name="theme-color" content="{bg}">` with no `media`;
  - `<meta name="color-scheme" content="dark">`;
  - exactly 2 font preloads with `as="font" type="font/woff2" crossorigin`, both hrefs in `web.Handler.STATIC_FILES`;
  - no `http(s)://` in any `<link>` / `<script>`.
- [ ] **Appearance:** 6 options carrying `data-theme-color` equal to the registry `bg`; no `data-meta-light` / `data-meta-dark`; no word "light" in the help text.
- [ ] **TestThemeCss (rewritten):**
  - for each key, exactly one block (`:root, [data-theme="crimson"]` for the default, `[data-theme="key"]` for the others);
  - each block declares exactly `themes.TOKENS`, every value `#rrggbb`;
  - `--bg` equals the registry `bg`;
  - no `prefers-color-scheme` anywhere in app.css;
  - `color-scheme: dark` and no `dark light`;
  - theme accent hexes appear only in custom-property lines;
  - no brand names.
- [ ] **Contrast (NEW):** per theme, every pair in the contrast table meets its threshold. The test parses app.css and computes the ratios with the WCAG formula. `--star-off`, `--danger` and `--ok` are read from the `:root` block.
- [ ] **Fonts (NEW):**
  - 3 `@font-face` rules, each with `font-display: swap` and `src: url("/static/fonts/<name>.woff2") format("woff2")`;
  - each name in `STATIC_FILES` as `font/woff2`, and each file existing and starting with `wOF2`;
  - `--font`, `--font-display` and `--font-note` declared;
  - no `font-style: italic`;
  - `font-synthesis: none`.
- [ ] **Accent discipline (NEW):** every rule using `var(--accent)` or `var(--accent-hover)` has a selector containing one of: `btn-add`, `primary`, `why`, `swatch`, `checkbox`, `radio` or `season-check`.
- [ ] **Removed tokens (NEW):** no `var(--accent-text|accent-soft|accent-glow|match-text)`. Every `var(--x)` in app.css is declared in app.css or set by app.js (`--h`, `--from-scale`, `--origin-x`, `--origin-y`, `--hero-ms`).
- [ ] **No slop (NEW):**
  - app.css has no `uppercase`, `small-caps`, `radial-gradient` or `999px`;
  - `linear-gradient` appears only in rules whose selector contains `hero-art`, `title-hero`, `topbar` or `track-arrow`;
  - no `:hover` rule sets `transform`;
  - `kicker`, `hero-glyph` and `class="kind"` are absent from pages.py and from the rendered pages;
  - `&middot;`, `·` and `&#183;` are absent from pages.py, app.js and the rendered sample Home, Movies, title, Library (all tabs), Search, Lists, AI and Appearance pages;
  - no rendered text node of 4 or more letters is all uppercase, except "TMDB" and certification badges.
- [ ] **Focus (NEW):**
  - the global `:focus-visible` rule uses `outline: 2px solid var(--focus)` (2px or more) with a positive offset;
  - `outline: none|0` appears only in rules for `.theme-radio` or `.search-type input`;
  - a `.card-poster:has(> .poster-link:focus-visible)` rule exists.
- [ ] **Targets (NEW):**
  - rules that include `button`, `.btn-add`, `.btn-ghost`, `.nav-item`, `.subtab`, `.pager a`, `.list-toggle`, `.list-link`, `.hero-dot`, `.modal-close` and the text input/select selector declare `min-height` or `height` ≥ 44px;
  - `.star` is at least 28px wide and 32px tall;
  - `.preview-btn` is 36px (the documented exception).
- [ ] **Reduced motion:** the `prefers-reduced-motion: reduce` block exists and covers `.hero-slide` and `.preview`. `max-width: 820px` is still present.
- [ ] **Dead CSS (NEW):** every class in an app.css selector appears as a whole word in pages.py, app.js or settings_page.py.
- [ ] **Markup:**
  - hero: reason above title, `<strong>` names for a "Because you watched A and B" item, plain text otherwise, escaped, `.hero-overview` present, genres joined with ", ", no kicker;
  - row card: `.rank` a direct article child, no `.poster-top`, `.card-facts` with match/year/tag, title without year;
  - every grid renderer: no `.poster-top` or `.kind`, `.card-badges` / `.card-facts` / `.card-sub` per the table, `<h3 class="title">` unchanged;
  - title page: `.title-body` > `.title-main` + `aside.title-side` (with `aria-label`), `dl.title-facts` and cast in the aside, `.why-because`, no kicker, `.season-year`;
  - `.ext-icon` is `aria-hidden`.
- [ ] **app.js:** contains `data-theme-color`, `card-badges` and `preview-title`; doesn't contain `data-meta-light` or `prefers-color-scheme: light`; exactly one `innerHTML`; no external URLs.
- [ ] Existing escaping, poster-link, card-tools, season, list and no-JS tests still pass. Update only the assertions this spec changes, and list each one in the report.

**Everyone:** `python3 -m unittest discover -s tests` is green after phase 2.

**Sample-mode smoke** (orchestrator or reviewer; **port 8098, never 8099**):
```bash
SAMPLE=1 PORT=8098 python3 web.py &           # note the PID
curl -s http://127.0.0.1:8098/api/status        # repeat until "state": "ready"
curl -sI http://127.0.0.1:8098/static/fonts/plus-jakarta-sans.woff2      # 200, font/woff2, immutable cache
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8098/static/fonts/OFL-plusjakartasans.txt   # 404
for p in / /movies /tv /title/movie/1005 /title/tv/2040 /library "/library?type=watched" "/library?type=added" \
         /lists /search?q=dune /ai /settings /appearance "/list-dialog?type=movie&id=1005&return_to=/"; do
  curl -s -o /dev/null -w "%{http_code} $p\n" "http://127.0.0.1:8098$p"; done                     # all 200
curl -s http://127.0.0.1:8098/ | grep -c 'rel="preload"'                                           # 2
curl -s http://127.0.0.1:8098/ | grep -cE '&middot;|·|class="kicker"|class="kind"|prefers-color-scheme'   # 0
curl -s http://127.0.0.1:8098/static/app.css | grep -ciE 'https?://|uppercase|radial-gradient'      # 0
ss -ltnp | grep :8098                            # then kill <pid>  (never pkill -f)
```

**Manual browser checklist for Hamish** (`SAMPLE=1 PORT=8098 python3 web.py`, then open `http://<host>:8098`):
1. **Home, desktop.**
   - The hero looks like the mockup: title bottom-left in the condensed face, the serif reason line above it, the facts line with "94% match"-style numerals, the overview, and a red "Add to library" (in live mode; in sample mode there's no Add).
   - The crossfade works, the dots and Pause work, and the progress bar is white.
2. **Rows.**
   - Headings are sentence case. Posters are bare (no chips or labels on the art), with title, match and year underneath.
   - Hover shows a light ring, with no lift or zoom. The preview fades in and its buttons work.
   - The Top 10 numerals are outlined and "10" isn't clipped.
3. **Title pages** (`/title/movie/1005`, `/title/tv/2040`):
   - poster plus head over the backdrop;
   - Overview, Why and Seasons in the left column, Details and Cast on the right, with Cast as round faces;
   - the red rule beside "Why Compass picked this";
   - one column at phone width.
4. **Every theme** (Settings, Appearance): only the primary button, the why rule and the checkbox ticks carry the accent. Text stays readable, focus rings are visible, and a reload keeps the theme. The browser chrome colour follows the theme. With the OS in light mode, the app stays dark.
5. **375px** (devtools): no horizontal scroll on any page. The bottom nav has 5 items. The hero is full-bleed and swipes. Card stars fit, with the icons wrapping to a second line. Dialogs fit.
6. **Keyboard only.** Tab through Home, a title page, the Library and a dialog. Every focused control shows a light ring that isn't hidden under the top bar or bottom nav. Esc closes the dialogs.
7. **Reduced motion on.** No crossfade or auto-advance, and dialogs appear without animation.
8. **JS off.** Forms still post and come back with a note: rate, Watchlist, the season form, the list dialog page and the theme "Use this theme" button. The hero shows slide 1.
9. **Fonts.** In devtools Network, the fonts load from `/static/fonts/` and nothing goes to an outside host.
10. Feel check: does it read as a streaming app rather than a template?

## Live-request budget
**None** for every implementer and the reviewer. Nothing here talks to Plex, TMDB, Radarr, Sonarr, Tautulli or AI providers. Use sample mode and fixtures.

## Risks
1. **ux styles blind.** There's no browser or node here, and it works in parallel against a markup contract rather than rendered pages. Mitigations: exact skeletons and class lists, the dead-CSS and class-sync checks, a budgeted fix-up round, and Hamish's manual pass. Visual polish depends on that pass.
2. **Cross-agent red tests mid-phase 1.** TestThemeCss, the contrast/font/accent tests and the row titles each depend on two or three agents. They're listed as expected red, and the suite is judged after phase 2.
3. **Contrast over real backdrops** can't be tested automatically, because it depends on the scrim. Bright posters in every theme go on the manual list, Mono especially (pure-black scrims).
4. **Condensed fallback width.** The fallback faces differ in width from Sofia Sans Condensed, so a hero title can reflow when the font swaps in, on a device's first visit only. This is accepted (D1).
5. **CSS rewrite regressions** in rarely seen states: the building, error and partial title pages, the list dialog, no-JS, sample mode. The smoke list and manual items 4-8 cover them.
6. **Removing 4 tokens.** Any leftover `var(--accent-text)` etc. silently falls back. The "every var() declared" test catches it.

## Open questions
None block phase 1. Each has a default, and the agents build the default.
1. **Stars.** Projector-white (yours) and grey (Plex/off), as in the mockup, instead of gold. Default **white per the mockup**. If you want gold back, it's a 3-token change by ux.
2. **"Add to library" vs the mockup's "Add to Radarr".** Default: keep "Add to library". It's one label for both services, test_web pins it, and the dialog already says "Sends it to Radarr". Changing it is a small frontend + backend-test follow-up.
3. **Hover-preview controls at 36px** (mouse-only, with 44px equivalents on the title page). Default **yes**. The alternative is a wider preview (about 420px) with 44px controls.
4. **Cast as a vertical list in the side column** (as the mockup shows), replacing the photo scroller. Default **yes**.
5. **No rows-over-hero overlap** (as the mockup shows). Default **yes**.
6. **Theme descriptions** ("Warm gold on midnight - the original", ...). These stay; the neutrals moved toward graphite but each theme keeps its hue. Default: unchanged.

---

## Task briefs

### backend-dev: phase 1 (part A), then phase 2 (part B)
Spec: `specs/streaming-redesign.md`. The Contract is fixed; report back if any of it can't work. Live-request budget: **none**.

Own, for this task: `web.py`, `themes.py`, `browse.py`, `Dockerfile` (comment only), `tests/test_web_async.py`, `tests/test_themes.py`, `tests/test_browse.py`, `tests/test_web.py`. Never touch `pages.py`, `static/*` or `tests/test_ui.py`.

**Part A** (in parallel with frontend-dev and ux-designer):
1. `web.py`: the `STATIC_FILES` whitelist and `FONT_CACHE` exactly as in "Contract / HTTP". `_static` reads in binary (`"rb"`) and joins `name.split("/")` onto `STATIC_DIR`. Update the comment above it.
2. `themes.py`: the new 12-name `TOKENS`, and the new `bg` per theme (amber `#0f1115`, crimson `#101114`, lime `#0f1011`, ocean `#0d1220`, teal `#0e161c`, mono `#000000`). **Keep `bg_light` for now**, because pages.py still reads it until frontend finishes.
3. `browse.py`: "Recommended for You" becomes "Recommended for you", and "Hidden Gems" becomes "Hidden gems". Nothing else changes.
4. `Dockerfile`: only the comment on `COPY static/` changes, to mention fonts. Confirm the copy is recursive.
5. Tests:
   - `TestStatic` additions per the backend acceptance criteria. Fonts are binary, so read the raw response bytes rather than the decoded str helper.
   - `test_themes`: `TOKENS` is the exact tuple and each `bg` value is checked.
   - `test_browse` and `test_web` row titles.

**Expected red while the others work:** test_ui's `TestThemeCss` and the new design-rule tests (they need app.css), and test_ui's row-title assertion until frontend updates it. Judge your work with `python3 -m unittest tests.test_web_async.TestStatic tests.test_themes tests.test_browse tests.test_web`.

**Part B** (only when the orchestrator confirms frontend no longer reads `bg_light`; `grep -n bg_light pages.py static/app.js` must be empty): remove `bg_light` from every theme, and assert in `test_themes` that no theme has the key. Then run `python3 -m unittest discover -s tests`.

Report: files changed, test counts, and any deviation.

### frontend-dev: phase 1 (in parallel with backend and ux)
Spec: `specs/streaming-redesign.md`, especially D2, D6, D7, D11, "Markup changes (frontend-dev)" and the frontend acceptance criteria. Live-request budget: **none**.

Own: `pages.py`, `static/app.js`, `tests/test_ui.py`. Don't edit `static/app.css`. ux-designer is rewriting it at the same time against the class names in this spec, so **use exactly the class names given**. If one won't work, report it; don't invent a new one.

1. `_shell` head: the dark-only metas and the two font preloads. Keep the existing stylesheet and script strings byte-identical.
2. **Hero:**
   - `_reason_html`;
   - remove the kicker;
   - the reason goes above the title (`reason hero-reason`);
   - genres joined with ", ";
   - `.hero-overview`;
   - drop `.hero-glyph`;
   - `<span class="runtime">` in `_meta_html`.
3. **Row card, lib-card and the grid renderers** (`_card`, `_search_card`, `_list_card`, `_library_card`, `_watched_card`), exactly per the skeletons and table. Keep `<h3 class="title">` byte-identical on grid cards: test_web.py (backend-owned) regex-matches it. Move tags; don't rename them.
4. **Title page:**
   - remove the kicker;
   - the `.title-body` / `.title-main` / `aside.title-side` structure;
   - facts and links into `.title-details`;
   - cast into the aside;
   - `.why-because`;
   - actions reordered;
   - `.season-year` (also in `_season_picker`). Keep the seasons text order, because test_web.py has `Season 1 5 episodes[^S]*Available`.
5. `.ext-icon` on external-link arrows.
6. `render_appearance`: `data-theme-color`, no `data-meta-*`, and the new help text. **Stop reading `bg_light` anywhere.** Backend removes it in phase 2 once you report that this is done.
7. app.js items 1-4 under "Markup changes".
8. test_ui:
   - rewrite `TestThemeCss` for dark-only, the 12 tokens and the crimson `:root` block;
   - update the shell/appearance/hero/row/title/season assertions this spec changes (the middot strings, kicker, `poster-top`, rank position, lib-card tag, light meta, `bg_light`, "Recommended for you");
   - add the NEW tests in the frontend acceptance criteria: contrast, fonts, accent discipline, removed tokens / declared vars, no slop, focus, targets, dead CSS, and the markup and app.js checks.

**Expected red while the others work:** the CSS-dependent tests (until ux finishes app.css) and the row titles (until backend changes browse.py). Name them in your report rather than weakening them.

Run `python3 -m unittest tests.test_ui`, then the full suite. Report:
- files changed;
- every pre-existing assertion you changed, and why;
- the tests that are red only because of app.css or browse.py;
- everything in app.js that needs a hand check (no browser here).

### ux-designer: phase 1 (in parallel with frontend and backend)
Spec: `specs/streaming-redesign.md`: Decisions, "Colour tokens", "Type system", "Spacing, radii...", "Focus", "Classes: the full contract", "Per-component restyle", and the frontend acceptance criteria (they test your file). The visual reference is `assets/redesign-mockup.html`. Copy its proportions, but **never** its Google Fonts link. Live-request budget: **none**.

Own: `static/app.css` only. pages.py and app.js are being changed at the same time by frontend-dev. **The skeletons and class lists in this spec are your source of truth.** Read the current pages.py only for components the spec doesn't change.

1. **Rewrite app.css top to bottom.** Start with:
   - the three `@font-face` rules (exact);
   - the six theme blocks with exactly the 12 tokens and hex values from the table. Crimson's selector is `:root, [data-theme="crimson"]` and it comes first;
   - one non-theme `:root` block (`color-scheme: dark`, status/tone/chip/star tokens, scrims, fonts, spacing, radii, shadow, motion).
2. **Delete:**
   - every `prefers-color-scheme: light` block, and the four removed tokens and every use of them;
   - all pills (`999px`), all `radial-gradient`s, and all `text-transform: uppercase` / tracked labels;
   - card lift and zoom, and the dead selectors listed under "REMOVED".
3. **Style every component** in "Per-component restyle" (items 1-27), including the NEW classes.
   - Accent only in rules whose selector contains `btn-add`, `primary`, `why`, `swatch`, `checkbox`, `radio` or `season-check`.
   - `linear-gradient` only in the `hero-art`, `title-hero`, `topbar` and `track-arrow` rules.
   - No `:hover` transforms.
   - Targets of 44px or more (stars 28×32; preview 36px).
   - A visible 2px `--focus` ring with offset 3px everywhere.
   - No horizontal scroll at 375px (watch long hero/title words, the Top 10 track, card tools, dialogs and the toolbar).
4. Keep:
   - the breakpoints (1100 / 900 / 820 / 600 / 560 / 520px). `max-width: 820px` must stay literally, because a test and app.js depend on it;
   - the reduced-motion block (extend it);
   - `[hidden]`, `.visually-hidden` and `html.js` / `html.has-dialog` behaviour;
   - the `:has()` fallbacks.
5. No brand names in comments: a test forbids them.

Self-check with `python3 -m unittest tests.test_ui`. Frontend's new design-rule tests may land while you work; until then, check against the frontend acceptance criteria by hand. Report:
- any token value you changed, with its new ratio (a `--bg` change must go to backend for themes.py);
- the classes you styled;
- any class you needed that the spec doesn't name;
- spots you couldn't verify without a browser.
