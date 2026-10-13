"""Colour theme registry: keys, labels, dark browser-chrome (bg) and logo colours; dark-only, no light variant, and the compass_theme cookie.
Pure - no I/O, no imports from web/pages/db. The swatch/preview colours live in static/app.css."""

DEFAULT = "crimson"
COOKIE = "compass_theme"
COOKIE_MAX_AGE = 34560000  # 400 days, the browser cap
TOKENS = ("--bg", "--bg-2", "--surface", "--surface-2", "--line", "--text", "--text-2", "--muted",
          "--accent", "--accent-ink", "--accent-hover", "--focus")
THEMES = (
    {"key": "amber", "label": "Amber", "description": "Warm gold on midnight - the original",
     "bg": "#0f1115", "logo": "#ffb224"},
    {"key": "crimson", "label": "Crimson", "description": "Bold red on near-black",
     "bg": "#101114", "logo": "#e50914"},
    {"key": "lime", "label": "Lime", "description": "Neon green on charcoal",
     "bg": "#0f1011", "logo": "#1ce783"},
    {"key": "ocean", "label": "Ocean", "description": "Bright blue on deep navy",
     "bg": "#0d1220", "logo": "#2f8cff"},
    {"key": "teal", "label": "Teal Night", "description": "Cyan on blue-black",
     "bg": "#0e161c", "logo": "#00a8e1"},
    {"key": "mono", "label": "Mono", "description": "Black and white, almost no colour - great on OLED",
     "bg": "#000000", "logo": "#f5f5f7"},
)
BY_KEY = {t["key"]: t for t in THEMES}


def is_valid(key):
    return isinstance(key, str) and key in BY_KEY


def get(key):
    return BY_KEY[key] if is_valid(key) else BY_KEY[DEFAULT]


def from_cookie(header):
    """The theme key from a Cookie header. Parsed by hand: other apps on the same host can set
    malformed cookies that would stop http.cookies at the first one. Never raises."""
    try:
        for part in (header or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and is_valid(value):
                return value
    except Exception:
        pass
    return DEFAULT


def set_cookie_value(key):
    if not is_valid(key):
        raise ValueError(f"unknown theme: {key!r}")
    return f"{COOKIE}={key}; Path=/; Max-Age={COOKIE_MAX_AGE}; SameSite=Lax"
