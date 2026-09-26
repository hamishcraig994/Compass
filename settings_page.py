"""The Settings page: configure Plex/TMDB/Tautulli/Radarr/Sonarr through the browser instead of
editing .env. Secret fields (tokens, API keys) are never echoed back into the page - they render
blank, and are only overwritten if you type a new value."""
from html import escape

import config

FIELDS = [
    ("Plex", [
        ("PLEX_URL", "Plex URL", "text", "http://192.168.1.102:32400"),
        ("PLEX_TOKEN", "Plex token", "secret", None),
    ]),
    ("Watch history", [
        ("HISTORY_SOURCE", "Source", "select", ["plex", "tautulli"]),
        ("TAUTULLI_URL", "Tautulli URL", "text", None),
        ("TAUTULLI_API_KEY", "Tautulli API key", "secret", None),
        ("TAUTULLI_USER", "Tautulli username (blank = everyone on the server)", "text", None),
    ]),
    ("TMDB", [
        ("TMDB_TOKEN", "TMDB token", "secret", None),
    ]),
    ("Radarr (optional - adds \"Add to library\" for movies)", [
        ("RADARR_URL", "Radarr URL", "text", None),
        ("RADARR_API_KEY", "Radarr API key", "secret", None),
        ("RADARR_QUALITY_PROFILE_ID", "Quality profile ID (blank = first available)", "text", None),
        ("RADARR_ROOT_FOLDER", "Root folder path (blank = first available)", "text", None),
    ]),
    ("Sonarr (optional - adds \"Add to library\" for TV)", [
        ("SONARR_URL", "Sonarr URL", "text", None),
        ("SONARR_API_KEY", "Sonarr API key", "secret", None),
        ("SONARR_QUALITY_PROFILE_ID", "Quality profile ID (blank = first available)", "text", None),
        ("SONARR_ROOT_FOLDER", "Root folder path (blank = first available)", "text", None),
    ]),
]

SECRET_NAMES = {name for _, fields in FIELDS for name, _, kind, _ in fields if kind == "secret"}
ALL_NAMES = {name for _, fields in FIELDS for name, _, _, _ in fields}


def _field_html(name, label, kind, extra):
    current = getattr(config, name)
    if kind == "secret":
        hint = "leave blank to keep current" if current else "not set"
        return (f'<label>{escape(label)}<input type="password" name="{escape(name)}" '
               f'placeholder="{escape(hint)}" autocomplete="off"></label>')
    if kind == "select":
        options = "".join(f'<option value="{escape(v)}"{" selected" if v == current else ""}>{escape(v)}</option>'
                          for v in extra)
        return f'<label>{escape(label)}<select name="{escape(name)}">{options}</select></label>'
    value = "" if current is None else str(current)
    placeholder = f' placeholder="{escape(extra)}"' if extra else ""
    return f'<label>{escape(label)}<input type="text" name="{escape(name)}" value="{escape(value)}"{placeholder}></label>'


def render(saved=False):
    sections = "".join(
        f'<fieldset><legend>{escape(title)}</legend>' + "".join(_field_html(*f) for f in fields) + '</fieldset>'
        for title, fields in FIELDS)
    banner = '<p class="note success">Saved - changes apply immediately, no restart needed.</p>' if saved else ""
    return (f'{banner}<form method="post" action="/settings">{sections}'
           f'<button type="submit" class="btn-add">Save</button></form>'
           f'<p class="muted">Secret fields never show their current value here - leave one blank '
           f'to keep what\'s already saved.</p>')


def apply_form(form):
    """form: the parsed POST body (name -> [values], as urllib.parse.parse_qs gives it). A field
    missing from the submission entirely is left untouched (defensive - a real render of this page
    always includes every field, so a missing one means a malformed or partial POST, not "clear
    this"). A blank secret field is also left untouched, since it means "didn't type a new one" -
    everything else is saved as submitted, including blank, which clears it."""
    for name in ALL_NAMES:
        if name not in form:
            continue
        values = form[name]
        value = values[0] if values else ""
        if name in SECRET_NAMES and not value:
            continue
        config.set_setting(name, value)
