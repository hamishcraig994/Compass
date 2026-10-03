"""The Settings page: configure Plex/TMDB/Tautulli/Radarr/Sonarr through the browser instead of
editing .env, one app at a time, each with its own "Test connection" check. Secret fields
(tokens/API keys) are never echoed back into the page - they render blank, and are only
overwritten if you type a new value. Radarr/Sonarr's quality profile and root folder are pulled
live and offered as dropdowns instead of free text, when that app is reachable."""
from html import escape

import ai
import config
import plex
import radarr
import sonarr
import tautulli
import tmdb

SECTIONS = [("plex", "Plex"), ("tautulli", "Watch history"), ("tmdb", "TMDB"), ("arr", "Radarr & Sonarr"),
           ("ai", "AI")]
DEFAULT_SECTION = "plex"

# name, label, kind ("text"/"secret"/"select"), extra (placeholder text, or option list for "select")
PLAIN_FIELDS = {
    "plex": [
        ("PLEX_URL", "Plex URL", "text", "http://192.168.1.102:32400"),
        ("PLEX_TOKEN", "Plex token", "secret", None),
    ],
    "tautulli": [
        ("HISTORY_SOURCE", "Watch history source", "select", ["plex", "tautulli"]),
        ("TAUTULLI_URL", "Tautulli URL", "text", None),
        ("TAUTULLI_API_KEY", "Tautulli API key", "secret", None),
        ("TAUTULLI_USER", "Tautulli username (blank = everyone on the server)", "text", None),
    ],
    "tmdb": [
        ("TMDB_TOKEN", "TMDB token", "secret", None),
    ],
    "ai": [
        ("AI_PROVIDER_URL", "Provider URL", "text", "https://api.openai.com/v1"),
        ("AI_TOKEN", "API key", "secret", None),
        ("AI_MODEL", "Model", "text", "gpt-4o-mini"),
    ],
}
ARR_FIELD_NAMES = ("RADARR_URL", "RADARR_API_KEY", "RADARR_QUALITY_PROFILE_ID", "RADARR_ROOT_FOLDER",
                  "SONARR_URL", "SONARR_API_KEY", "SONARR_QUALITY_PROFILE_ID", "SONARR_ROOT_FOLDER")
SECRET_NAMES = {"PLEX_TOKEN", "TAUTULLI_API_KEY", "TMDB_TOKEN", "RADARR_API_KEY", "SONARR_API_KEY", "AI_TOKEN"}
TEST_LABELS = {"plex": "Test Plex connection", "tautulli": "Test Tautulli connection", "tmdb": "Test TMDB connection",
              "radarr": "Test Radarr connection", "sonarr": "Test Sonarr connection", "ai": "Test AI connection"}


def _field_names(section):
    if section == "arr":
        return list(ARR_FIELD_NAMES)
    return [name for name, *_ in PLAIN_FIELDS.get(section, [])]


def _field_html(name, label, kind, extra, overrides=None):
    current = (overrides or {}).get(name, getattr(config, name))
    if kind == "secret":
        hint = "leave blank to keep current" if getattr(config, name) else "not set"
        return (f'<label>{escape(label)}<input type="password" name="{escape(name)}" '
               f'placeholder="{escape(hint)}" autocomplete="off"></label>')
    if kind == "select":
        options = "".join(f'<option value="{escape(v)}"{" selected" if v == current else ""}>{escape(v)}</option>'
                          for v in extra)
        return f'<label>{escape(label)}<select name="{escape(name)}">{options}</select></label>'
    value = "" if current is None else str(current)
    placeholder = f' placeholder="{escape(extra)}"' if extra else ""
    return f'<label>{escape(label)}<input type="text" name="{escape(name)}" value="{escape(value)}"{placeholder}></label>'


def _dropdown_or_text(name, label, options, current):
    """options: [(value, display), ...] fetched live from Radarr/Sonarr, or None if that wasn't
    possible (not configured, or the live fetch failed) - falls back to a plain text field."""
    if options is None:
        value = "" if current is None else str(current)
        return (f'<label>{escape(label)}<input type="text" name="{escape(name)}" value="{escape(value)}" '
               f'placeholder="connect it below first, then reload this page"></label>')
    chosen = "" if current is None else str(current)
    opts = '<option value="">First available</option>' + "".join(
        f'<option value="{escape(str(value))}"{" selected" if str(value) == chosen else ""}>{escape(str(display))}</option>'
        for value, display in options)
    return f'<label>{escape(label)}<select name="{escape(name)}">{opts}</select></label>'


def _test_button(app_key):
    return f'<button type="submit" name="action" value="test_{app_key}" class="btn-ghost">{escape(TEST_LABELS[app_key])}</button>'


def _radarr_options():
    """([(id, name), ...] profiles, [(path, path), ...] folders), or (None, None) if not
    configured/reachable right now."""
    if not config.radarr_configured():
        return None, None
    try:
        client = radarr.RadarrClient(config.RADARR_URL, config.RADARR_API_KEY)
        return ([(p["id"], p["name"]) for p in client.quality_profiles()],
               [(f["path"], f["path"]) for f in client.root_folders()])
    except Exception:
        return None, None


def _sonarr_options():
    if not config.sonarr_configured():
        return None, None
    try:
        client = sonarr.SonarrClient(config.SONARR_URL, config.SONARR_API_KEY)
        return ([(p["id"], p["name"]) for p in client.quality_profiles()],
               [(f["path"], f["path"]) for f in client.root_folders()])
    except Exception:
        return None, None


def _render_arr_section(overrides=None):
    radarr_profiles, radarr_folders = _radarr_options()
    sonarr_profiles, sonarr_folders = _sonarr_options()
    radarr_fields = (_field_html("RADARR_URL", "Radarr URL", "text", None, overrides)
                     + _field_html("RADARR_API_KEY", "Radarr API key", "secret", None, overrides)
                     + _dropdown_or_text("RADARR_QUALITY_PROFILE_ID", "Quality profile", radarr_profiles,
                                        config.RADARR_QUALITY_PROFILE_ID)
                     + _dropdown_or_text("RADARR_ROOT_FOLDER", "Root folder", radarr_folders, config.RADARR_ROOT_FOLDER))
    sonarr_fields = (_field_html("SONARR_URL", "Sonarr URL", "text", None, overrides)
                     + _field_html("SONARR_API_KEY", "Sonarr API key", "secret", None, overrides)
                     + _dropdown_or_text("SONARR_QUALITY_PROFILE_ID", "Quality profile", sonarr_profiles,
                                        config.SONARR_QUALITY_PROFILE_ID)
                     + _dropdown_or_text("SONARR_ROOT_FOLDER", "Root folder", sonarr_folders, config.SONARR_ROOT_FOLDER))
    return (f'<fieldset><legend>Radarr (movies)</legend>{radarr_fields}'
           f'<div class="card-actions">{_test_button("radarr")}</div></fieldset>'
           f'<fieldset><legend>Sonarr (TV)</legend>{sonarr_fields}'
           f'<div class="card-actions">{_test_button("sonarr")}</div></fieldset>'
           f'<button type="submit" name="action" value="save" class="btn-add">Save</button>')


APPEARANCE_TAB = ("appearance", "Appearance", "/appearance")


def subtabs_html(current):
    """The sub-tab row shared by the settings sections and the Appearance page."""
    nav = "".join(f'<a class="subtab{" on" if key == current else ""}" href="/settings?section={key}">{escape(label)}</a>'
                  for key, label in SECTIONS)
    key, label, href = APPEARANCE_TAB
    nav += f'<a class="subtab{" on" if key == current else ""}" href="{href}">{escape(label)}</a>'
    return f'<nav class="subtabs" aria-label="Settings sections">{nav}</nav>'


def render(section=DEFAULT_SECTION, saved=False, test_result=None, overrides=None):
    if section not in dict(SECTIONS):
        section = DEFAULT_SECTION
    banner = '<p class="note success" role="status">Saved - changes apply immediately, no restart needed.</p>' if saved else ""
    if test_result:
        cls = "success" if test_result["ok"] else "error"
        banner += f'<p class="note {cls}" role="status">{escape(test_result["message"])}</p>'

    if section == "arr":
        content = _render_arr_section(overrides)
    else:
        fields_html = "".join(_field_html(*f, overrides) for f in PLAIN_FIELDS[section])
        content = (f'<fieldset>{fields_html}<div class="card-actions">'
                  f'<button type="submit" name="action" value="save" class="btn-add">Save</button>'
                  f'{_test_button(section)}</div></fieldset>')

    return (f'<div class="settings">{subtabs_html(section)}{banner}'
           f'<form method="post" action="/settings?section={section}">{content}</form>'
           f'<p class="muted settings-foot">Secret fields never show their current value here - leave one blank '
           f'to keep what\'s already saved.</p></div>')


def apply_form(section, form):
    """form: the parsed POST body (name -> [values], as urllib.parse.parse_qs gives it). A field
    missing from the submission entirely is left untouched (defensive - a real render of this page
    always includes every field in its own section, so a missing one means a malformed/partial
    POST, not "clear this"). A blank secret field is also left untouched, since it means "didn't
    type a new one" - everything else is saved as submitted, including blank, which clears it."""
    for name in _field_names(section):
        if name not in form:
            continue
        values = form[name]
        value = values[0] if values else ""
        if name in SECRET_NAMES and not value:
            continue
        config.set_setting(name, value)


def _submitted_or_saved(form, name):
    """What to actually test a connection with: what was just typed, or - for a secret field left
    blank, meaning "unchanged" - whatever's already saved. Never nothing just because secrets
    render blank by design."""
    values = form.get(name)
    value = values[0] if values else ""
    if not value and name in SECRET_NAMES:
        return getattr(config, name)
    return value


def run_test(app_key, form):
    """Tests one app's connection using whatever's in the form right now, not necessarily saved
    yet. Returns {"app": ..., "ok": ..., "message": ...}."""
    if app_key == "plex":
        client = plex.PlexClient(_submitted_or_saved(form, "PLEX_URL"), _submitted_or_saved(form, "PLEX_TOKEN"))
    elif app_key == "tautulli":
        client = tautulli.TautulliClient(_submitted_or_saved(form, "TAUTULLI_URL"),
                                         _submitted_or_saved(form, "TAUTULLI_API_KEY"),
                                         _submitted_or_saved(form, "TAUTULLI_USER"))
    elif app_key == "tmdb":
        client = tmdb.TmdbClient(_submitted_or_saved(form, "TMDB_TOKEN"))
    elif app_key == "radarr":
        client = radarr.RadarrClient(_submitted_or_saved(form, "RADARR_URL"), _submitted_or_saved(form, "RADARR_API_KEY"))
    elif app_key == "sonarr":
        client = sonarr.SonarrClient(_submitted_or_saved(form, "SONARR_URL"), _submitted_or_saved(form, "SONARR_API_KEY"))
    elif app_key == "ai":
        client = ai.AiClient(_submitted_or_saved(form, "AI_TOKEN"), _submitted_or_saved(form, "AI_PROVIDER_URL"),
                             _submitted_or_saved(form, "AI_MODEL"))
    else:
        return {"app": app_key, "ok": False, "message": f"Unknown app {app_key!r}"}
    ok, message = client.test_connection()
    return {"app": app_key, "ok": ok, "message": message}


def form_overrides(section, form):
    """Non-secret field values just submitted (for redisplaying them after a Test, since that
    response isn't a redirect - unlike after Save, where config already reflects what was saved)."""
    return {name: form[name][0] for name in _field_names(section)
           if name not in SECRET_NAMES and name in form and form[name]}
