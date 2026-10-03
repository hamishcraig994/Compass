---
name: design-system
description: What's Next's visual design system - the CSS tokens in static/app.css, the existing component classes, breakpoints, accessibility rules and the progressive-enhancement markup hooks. Use before changing static/app.css, pages.py markup or static/app.js UI behaviour, and when reviewing UI changes.
---

# What's Next design system

Source of truth: `static/app.css` (styles), `pages.py` (markup), `static/app.js` (behaviour). Dark-first media UI. **No web fonts, CDNs or external assets** (tests/test_ui.py checks the static files have no external URLs).

## Tokens (`:root` in static/app.css)
Use these; add a new token rather than hard-coding a colour.

| Token | Dark (default) | Light override | Use |
|---|---|---|---|
| `--bg` / `--bg-2` | `#0a0c11` / `#10131a` | `#f3f4f7` / `#e9ebf0` | page background |
| `--surface` / `--surface-2` | `#161a23` / `#1e2330` | `#ffffff` / `#f0f1f5` | cards, panels, inputs |
| `--line` | `#2a3140` | `#d9dde5` | borders, dividers |
| `--text` / `--muted` | `#f3f5f9` / `#a3acbd` | `#10131a` / `#535c6c` | body / secondary text |
| `--accent` | `#ffb224` | same | **fills only**: buttons, active nav |
| `--accent-ink` | `#1a1200` | same | text on `--accent` |
| `--accent-text` | `#ffc557` | `#8a4f00` | accent-coloured text on bg/surface (links) |
| `--accent-soft` | amber 14% | amber 22% | tinted backgrounds |
| `--danger` / `--danger-soft` | `#ff8a7a` | `#b3261e` | errors |
| `--ok` / `--ok-soft` | `#6ee7a8` | `#146c43` | success |
| `--shadow` | dark drop shadow | lighter | raised surfaces |
| `--radius` / `--radius-sm` | `14px` / `10px` | | corners |
| `--nav-h` | `68px` | | bottom-nav height (phones) |
| `--font` | system-ui stack | | all text |

Light mode = `@media (prefers-color-scheme: light)` overriding the same tokens; `color-scheme: dark light` is set. Every change must look right in both.

**TODO:** there is no spacing scale - paddings/margins/gaps are literal values in each rule. Match neighbouring rules; propose a `--space-*` scale if a feature needs one.

## Themes
Six themes (registry in `themes.py`: amber, crimson, lime, ocean, teal, mono) selected by `<html data-theme>` from the `wn_theme` cookie. Every theme block in `static/app.css` (`[data-theme="key"]`, Amber also on `:root`) declares exactly the same 15 tokens in dark and in `@media (prefers-color-scheme: light)`; `tests/test_ui.py::TestThemeCss` enforces this and the registry sync. Component rules use tokens only (new tokens: `--accent-glow`, `--accent-hover`, `--match-text`, `--focus`). No brand names in the CSS. Contrast targets: text AA, `--focus` >= 3:1. Picker classes: `.theme-picker`, `.theme-grid`, `.theme-option`, `.theme-radio`, `.theme-swatch` (+ `.swatch-*`), `.theme-text/-name/-current/-desc`, `.theme-submit` (hidden under `html.js`). Full spec: `specs/themes.md`.

## Components (existing classes - reuse before inventing)
- Layout: `.topbar` (+ `.brand`, `.brand-mark`, `.brand-name`, `.topnav`, `.topbar-actions`, `.nav-item`; fixed + transparent on `body.cinematic`, solid when `.is-scrolled`), `.app-main`, `.top`, `.top-actions`, `.top.browse-top`, `.bottom-nav` (phones, 5 items), `.grid`.
- Cinematic browse (Home/Movies/TV): `.hero`, `.hero-slides`, `.hero-slide(.on)`, `.hero-art`, `.hero-backdrop`, `.hero-poster`, `.hero-glyph`, `.hero-copy`, `.kicker`, `.hero-title`, `.hero-meta`, `.hero-genres`, `.hero-actions`, `.hero-details`, `.hero-dots`, `.hero-dot`, `.hero-pause`; `.rows`, `.row`, `.row-head`, `.row-sub`, `.track-wrap`, `.track(-numbered)`, `.rank`, `.track-arrow.prev/.next`, `.row-card`, `.lib-card`, `.lib-tag`, `.card-meta`, `.badge.cert`; `.preview` (+ `.preview-poster/-body/-actions/-btn/-rate`); `.browse-messages`, `.browse-foot`. `html.has-dialog` hides the no-JS `<details>` actions. Arrows at row ends are hidden with the `hidden` attribute; a global `[hidden]{display:none!important}` exists.
- Cards: `.card`, `.card-poster`, `.poster`, `.poster-empty`, `.poster-top`, `.card-info`, `.card-sub`, `.card-details`, `.card-actions`, `.title`, `.kind`, `.match`, `.badge`/`.badges`, `.chip`/`.chips`, `.reason`, `.overview`, `.ext-link`.
- Buttons: `.btn-add` (primary, accent fill), `.btn-ghost` (secondary), `.link-btn`, `.modal-close`.
- Feedback: `.note` (inline message), `.undo-note`, `.toast`/`.toasts` (aria-live region), `.updating` badge, `.spinner`, `.skeleton-row`, `.empty`, `.building-error`.
- Navigation within a page: `.subtabs` / `.subtab`.
- Home footer: `.taste` (taste summary in `.browse-foot`). The old stat tiles and `.rail`/`.section-head` are gone.
- Dialogs: native `<dialog>` with `.modal-inner`, `.modal-loading`, `.dialog-box`, `.dialog-head`, `.dialog-page`; detail view `.detail-layout`, `.detail-main`, `.detail-body`.
- Settings: `.settings`, `.settings-foot`, `.checkbox-label`. AI page: `.ai-intro`, `.ai-toolbar`.
- Utility: `.muted`, `.sub`, `.visually-hidden`, `.skip-link`; `html.js` is set by app.js when JS runs.

## Breakpoints
- `max-width: 820px` - top nav links are replaced by the fixed bottom nav (`--nav-h`); the hero becomes a swipe card and stops auto-rotating.
- `max-width: 560px` and `520px` - tighter card grid and spacing.
- Must work at **375px with no horizontal scroll** (`body` has `overflow-x: hidden` only as a backstop - don't rely on it).

## Accessibility rules (already in place - keep them)
- Visible focus everywhere: `:focus-visible { outline: 3px solid var(--accent) }`. Never remove outlines.
- Skip link (`.skip-link`) to main content; `aria-current="page"` on the active nav item.
- Toasts and status messages in an `aria-live="polite"` / `role="status"` region.
- Modals: focus moves in, Tab is trapped, Esc closes, focus returns to the trigger.
- `@media (prefers-reduced-motion: reduce)` disables animation; app.js also checks it.
- Text contrast WCAG AA in both themes (`--accent` is a fill colour - use `--accent-text` for text).

## Markup and behaviour rules
- Every untrusted string (TMDB/Plex/Radarr data, titles, overviews) goes through `html.escape`; links and images only via `_web_url()` (http/https only). `static/app.js` builds DOM with `createElement`/`textContent`; the single `innerHTML` is the server-escaped `/add-dialog?...&partial=1` fragment.
- **Progressive enhancement:** every form/link works without JS (POST + 303 redirect). app.js only enhances via data attributes:
  `data-enhance="dismiss|undismiss|add|refresh|generate|rate"` (forms), `data-add-dialog` (Add link -> modal), `data-open-detail` / `data-card` / `data-grid` (detail modal, card removal), `data-close`, `data-poll` / `data-poll-error` (screens that poll `/api/status`), `data-busy`, `data-js-empty`.
- Building / generating / "Updating..." screens must also include `<noscript><meta http-equiv="refresh" content="5"></noscript>` (via `_shell(auto_refresh=True)`).
