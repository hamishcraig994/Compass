---
name: design-system
description: Compass's visual design system - the CSS tokens in static/app.css, the existing component classes, breakpoints, accessibility rules and the progressive-enhancement markup hooks. Use before changing static/app.css, pages.py markup or static/app.js UI behaviour, and when reviewing UI changes.
---

# Compass design system

Source of truth: `static/app.css` (styles), `pages.py` (markup), `static/app.js` (behaviour). Direction "projection room" (spec: `specs/streaming-redesign.md`, reference `assets/redesign-mockup.html`): **dark only**, artwork supplies the colour, the interface stays quiet. **No CDNs or external assets** (tests check); fonts are bundled.

## Type (bundled in `static/fonts/`, OFL; served by web.py's static whitelist)
- `--font-display` Sofia Sans Condensed (600-900): titles, brand, numerals (match %, Top 10 ranks, runtimes).
- `--font` Plus Jakarta Sans (400-700): all interface text.
- `--font-note` Source Serif 4, **upright, never italic**: Compass explaining itself ("Because you watched...", "Why Compass picked this", fit/why).
- Sentence case everywhere. No all-caps labels, small-caps, eyebrows/kickers or middle-dot meta strings; meta lines are spans joined by a literal space with a CSS gap (genres use ", ").

## Tokens
Each theme block (`:root, [data-theme="crimson"]` first, then amber, lime, ocean, teal, mono) declares exactly the 12 tokens in `themes.TOKENS`: `--bg --bg-2 --surface --surface-2 --line --text --text-2 --muted --accent --accent-ink --accent-hover --focus` (`--focus` = the theme's `--text`). `themes.py` mirrors each `--bg` and the logo colour; `TestThemeCss` enforces the sync. Non-theme `:root`: fonts, `--space-5..8`, `--gutter`, `--topbar-h` (64/56px), `--nav-h`, radii, durations, scrims, danger/ok, `--tone-*`, star tokens, `--quiet`/`--quiet-hover`/`--field-line`.
- **Accent discipline:** the accent is for the primary action only - `.btn-add` (quiet inside `.card`), `.preview-btn.primary`, checkbox/radio/season ticks, `.swatch-btn` and the `.why` rule. A test enforces the selector list.
- **Radii by role:** posters/tags `--radius-xs` 4px, buttons/inputs `--radius-sm` 6px, dialogs `--radius` 8px, cast faces round. No 999px pills.
- **No decoration:** no radial gradients (linear only for hero/title scrims, top bar and track arrows), no hover transforms/lifts (hover = an inset ring), no glow.
- Contrast: AA for text and muted text on every surface in every theme, focus and stars-off >= 3:1; `TestRedesignCss` computes it.

## Themes
Six themes (registry `themes.py`) selected by `<html data-theme>` from the `compass_theme` cookie; one `theme-color` meta. Appearance options carry `data-theme-color`, which app.js uses to recolour the meta, logo and favicon on preview. Picker classes: `.theme-picker`, `.theme-grid`, `.theme-option`, `.theme-radio`, `.theme-swatch` (+ `.swatch-*`), `.theme-text/-name/-current/-desc`, `.theme-submit`.

## Components (existing classes - reuse before inventing)
- Layout: `.topbar` (+ `.brand`, `.brand-mark` (inline Compass logo SVG, colours per theme from `themes.py` "logo"/"bg"; app.js `setLogo` recolours it on theme preview), `.brand-name`, `.topnav`, `.topbar-actions`, `.nav-item`; fixed + transparent on `body.cinematic`, solid when `.is-scrolled`), `.app-main`, `.top`, `.top-actions`, `.top.browse-top`, `.bottom-nav` (phones, 5 items), `.grid`.
- Cinematic browse (Home/Movies/TV): `.hero`, `.hero-slides`, `.hero-slide(.on)`, `.hero-art`, `.hero-backdrop`, `.hero-poster`, `.hero-copy`, `.reason` (note font, above the title), `.hero-title`, `.hero-meta`, `.hero-genres`, `.hero-actions`, `.hero-dots`, `.hero-dot`, `.hero-pause`; `.rows`, `.row`, `.row-head`, `.row-sub`, `.track-wrap`, `.track(-numbered)`, `.rank`, `.track-arrow.prev/.next`, `.row-card`, `.lib-card`, `.lib-tag`, `.card-meta`, `.badge.cert`; `.preview` (+ `.preview-poster/-body/-actions/-btn/-rate`); `.browse-messages`, `.browse-foot`. `html.has-dialog` hides the no-JS `<details>` actions. Arrows at row ends are hidden with the `hidden` attribute; a global `[hidden]{display:none!important}` exists.
- Cards: `.card`, `.card-poster` (nothing on the artwork), `.poster`, `.poster-link`, `.poster-empty`, `.card-info`, `.card-facts` (match/year), `.card-sub`, `.card-badges` (status tags), `.card-details`, `.card-actions`, `.card-tools`, `.title`, `.match`, `.badge`/`.badges`, `.chip`/`.chips`, `.reason`, `.overview`, `.ext-link`.
- Buttons: `.btn-add` (primary, accent fill), `.btn-ghost` (secondary), `.link-btn`, `.modal-close`.
- Feedback: `.note` (inline message), `.undo-note`, `.toast`/`.toasts` (aria-live region), `.updating` badge, `.spinner`, `.skeleton-row`, `.empty`, `.building-error`.
- Navigation within a page: `.subtabs` / `.subtab`.
- Home footer: `.taste` (taste summary in `.browse-foot`). The old stat tiles and `.rail`/`.section-head` are gone.
- Dialogs: native `<dialog>` with `.modal-inner`, `.modal-loading`, `.dialog-box`, `.dialog-head`, `.dialog-page`; title page `.title-page`, `.title-hero`, `.title-head`, `.title-body` with `.title-main` (overview, why, seasons) and `aside.title-side` (details, cast list).
- Settings: `.settings`, `.settings-foot`, `.checkbox-label`. AI page: `.ai-intro`, `.ai-toolbar`.
- Utility: `.muted`, `.sub`, `.visually-hidden`, `.skip-link`; `html.js` is set by app.js when JS runs.

## Search and Library (see specs/search-and-arr-library.md)
- Top bar: `.topbar-search` (+ `.search-input`, `.search-submit`) on wide screens, `.search-link` icon at <=1100px; Enter goes to `/search` (the top-bar box is not live).
- Search page: `.search-page`, `.search-form`, `.search-row`, `.search-types`/`.search-type`, `.search-summary`, `.search-status` (the one aria-live region), `.search-results` (+ `.is-loading`, `[data-search-spinner]`), `.search-fragment[data-search-state][data-announce]`, `.search-card`, `.status-tag` (`.status-plex/-radarr/-sonarr/-added`). Live search: form `[data-search-live]`, 400 ms debounce, abort + sequence counter, `replaceState`.
- Library: `.card-sources` / `.src` (`.src-plex/-radarr/-sonarr`), `.badge.arr-state` (`.state-downloaded/-missing/-partial/-upcoming/-unmonitored`), `select[name="source"]` in the toolbar. Chip tones use the non-theme `--chip-*` / `--tone-*` tokens.

## Breakpoints
- `max-width: 820px` - top nav links are replaced by the fixed bottom nav (`--nav-h`); the hero becomes full-bleed 4:5 swipe slides under the bar and stops auto-rotating.
- `max-width: 900px` - the title page drops to one column.
- `max-width: 560px` and `520px` - tighter card grid and spacing.
- Must work at **375px with no horizontal scroll** (`body` has `overflow-x: hidden` only as a backstop - don't rely on it).

## Accessibility rules (already in place - keep them)
- Visible focus everywhere: `:focus-visible` uses `--focus` (the theme text colour); on light surfaces (toasts) the ring switches to `--bg`. Never remove outlines.
- Targets 44px; agreed exceptions: compact stars 28x32, hover-preview and toast controls 36px.
- Skip link (`.skip-link`) to main content; `aria-current="page"` on the active nav item.
- Toasts and status messages in an `aria-live="polite"` / `role="status"` region.
- Modals: focus moves in, Tab is trapped, Esc closes, focus returns to the trigger.
- `@media (prefers-reduced-motion: reduce)` disables animation; app.js also checks it.
- Text contrast WCAG AA in every theme (`--accent` is a fill colour; never use it for body text).

## Markup and behaviour rules
- Every untrusted string (TMDB/Plex/Radarr data, titles, overviews) goes through `html.escape`; links and images only via `_web_url()` (http/https only). `static/app.js` builds DOM with `createElement`/`textContent`; the single `innerHTML` lives in the shared `setFragment(node, html)` helper, which only ever receives server-escaped fragments (`/add-dialog?...&partial=1` and `/search?...&partial=1`) and refuses anything containing `<html`.
- **Progressive enhancement:** every form/link works without JS (POST + 303 redirect). app.js only enhances via data attributes:
  `data-enhance="dismiss|undismiss|add|refresh|generate|rate"` (forms), `data-add-dialog` (Add link -> modal), `data-card` / `data-grid` / `data-preview` (card removal, hover preview), `data-enhance="list|lists|list-move"`, `data-close`, `data-poll` / `data-poll-error` (screens that poll `/api/status`), `data-busy`, `data-js-empty`.
- Building / generating / "Updating..." screens must also include `<noscript><meta http-equiv="refresh" content="5"></noscript>` (via `_shell(auto_refresh=True)`).
