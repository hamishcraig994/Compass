"""Web page with your recommendations.

Run:  python3 web.py          then open http://localhost:8080
Settings (environment variables): PORT (default 8080), HOST (default 0.0.0.0 = reachable from other
machines on your network), SAMPLE=1 to force sample data. There is no login, so keep it on your home network."""
import os
import threading
import time
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import config
import db
import profile
import settings_page
import sources

SHOW = 24                  # suggestions per page
CACHE_SECONDS = 3600       # reuse the last result for an hour (finding suggestions takes a while)
REFRESH_MIN_SECONDS = 600  # "Refresh" is ignored if the data is newer than this, to be gentle on Plex/TMDB
TABS = (("all", "All"), ("new", "New & trending"), ("movie", "Movies"), ("tv", "TV shows"))
compute_lock = threading.Lock()  # one recompute at a time
_state = {"time": 0.0, "result": None}

CSS = """
:root { --bg:#f6f7f9; --card:#fff; --text:#1c2430; --muted:#65707f; --line:#e2e6eb; --accent:#5b4bd6; --accent-bg:#ecebfa; --warn:#a35a00; --warn-bg:#fdf1de; }
@media (prefers-color-scheme: dark) { :root { --bg:#14181d; --card:#1d232b; --text:#e6e9ee; --muted:#9aa5b4; --line:#2c3540; --accent:#a99bff; --accent-bg:#2a2650; --warn:#f0b35c; --warn-bg:#3a2d16; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
main { max-width:1000px; margin:0 auto; padding:24px 16px 48px; }
h1 { font-size:1.5rem; margin:0 0 4px; } .sub, .muted { color:var(--muted); } .muted { font-size:.9rem; }
.top { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; flex-wrap:wrap; }
.tabs { display:flex; gap:6px; margin:18px 0; flex-wrap:wrap; }
.tabs a { padding:6px 14px; border:1px solid var(--line); border-radius:99px; color:var(--text); text-decoration:none; background:var(--card); }
.tabs a.on { background:var(--accent); border-color:var(--accent); color:#fff; font-weight:600; }
button { padding:6px 14px; font:inherit; color:var(--text); background:transparent; border:1px solid var(--line); border-radius:8px; cursor:pointer; }
.note { background:var(--warn-bg); color:var(--warn); border-radius:8px; padding:8px 12px; margin:0 0 8px; font-size:.9rem; }
.taste { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 14px; margin-bottom:18px; font-size:.92rem; }
.taste b { display:inline-block; min-width:76px; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:12px; }
.card { display:flex; gap:12px; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px; }
.poster { width:84px; height:126px; flex:none; border-radius:6px; background:var(--accent-bg); object-fit:cover; }
.body { min-width:0; display:flex; flex-direction:column; gap:4px; }
.title { font-weight:600; } .title a { color:var(--text); text-decoration:none; }
.match { color:var(--accent); font-weight:700; }
.badge { font-size:.72rem; font-weight:700; color:#fff; background:var(--accent); border-radius:99px; padding:1px 8px; margin-left:4px; vertical-align:middle; }
.kind { font-size:.72rem; font-weight:600; color:var(--accent); background:var(--accent-bg); border-radius:99px; padding:1px 8px; margin-left:4px; vertical-align:middle; }
.reason { font-size:.9rem; }
.chips { display:flex; gap:4px; flex-wrap:wrap; } .chip { font-size:.75rem; color:var(--muted); border:1px solid var(--line); border-radius:99px; padding:0 8px; }
.blurb { font-size:.85rem; color:var(--muted); display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; }
form.inline { margin:0; }
.card-actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:4px; }
.btn-add { color:#fff; background:var(--accent); border-color:var(--accent); font-weight:600; }
.note.success { background:var(--accent-bg); color:var(--accent); }
.settings-link { color:var(--text); text-decoration:none; padding:6px 14px; border:1px solid var(--line); border-radius:8px; }
fieldset { display:grid; gap:12px; border:1px solid var(--line); border-radius:10px; padding:16px; margin:0 0 16px; }
legend { padding:0 6px; font-weight:600; }
fieldset label { display:grid; gap:4px; font-size:.9rem; color:var(--text-soft, var(--muted)); }
fieldset input, fieldset select { padding:8px 10px; font:inherit; color:var(--text); background:var(--card); border:1px solid var(--line); border-radius:8px; }
"""


def get_result(refresh=False):
    """The recommendations, from memory if fresh enough. Returns (result, seconds_old)."""
    with compute_lock:
        age = time.time() - _state["time"]
        stale = _state["result"] is None or age > CACHE_SECONDS
        if stale or (refresh and age > REFRESH_MIN_SECONDS):
            _state["result"] = sources.run(sources.use_sample(os.environ.get("SAMPLE") == "1" or None))
            _state["time"] = time.time()
            age = 0.0
        return _state["result"], age


def forget(media_type, tmdb_id):
    """Remove one suggestion from the cached result (after 'Not interested')."""
    with compute_lock:
        if _state["result"]:
            _state["result"]["items"] = [i for i in _state["result"]["items"]
                                         if (i["media_type"], i["tmdb_id"]) != (media_type, tmdb_id)]


def invalidate_cache():
    """Forces the next page load to recompute from scratch (after a settings change)."""
    with compute_lock:
        _state["result"], _state["time"] = None, 0.0


def _web_url(url):
    """Only plain http(s) links go into the page (never javascript: and friends)."""
    return url if url and url.startswith(("https://", "http://")) else None


def _hidden_fields(item, tab):
    return (f'<input type="hidden" name="type" value="{escape(item["media_type"])}">'
            f'<input type="hidden" name="id" value="{int(item["tmdb_id"])}">'
            f'<input type="hidden" name="tab" value="{escape(tab)}">')


def _card(item, tab, can_dismiss):
    poster_url, link = _web_url(item.get("poster_url")), _web_url(item.get("url"))
    poster = (f'<img class="poster" src="{escape(poster_url, quote=True)}" alt="" loading="lazy">'
              if poster_url else '<div class="poster"></div>')
    title = escape(item["title"]) + (f' ({item["year"]})' if item["year"] else "")
    if link:
        title = f'<a href="{escape(link, quote=True)}" target="_blank" rel="noopener noreferrer">{title}</a>'
    kind = "Movie" if item["media_type"] == "movie" else "TV"
    badges = "".join(f'<span class="badge">{label}</span>'
                     for flag, label in (("new", "New"), ("trending", "Trending")) if item.get(flag))
    chips = "".join(f'<span class="chip">{escape(m)}</span>' for m in item["matches"])

    can_add = can_dismiss and ((item["media_type"] == "movie" and config.radarr_configured())
                               or (item["media_type"] == "tv" and config.sonarr_configured()))
    actions = ""
    if can_dismiss:
        add_button = (f'<form class="inline" method="post" action="/add">{_hidden_fields(item, tab)}'
                      f'<button type="submit" class="btn-add">Add to library</button></form>') if can_add else ""
        actions = (f'<div class="card-actions">{add_button}'
                  f'<form class="inline" method="post" action="/dismiss">{_hidden_fields(item, tab)}'
                  f'<button type="submit">Not interested</button></form></div>')
    return (f'<div class="card">{poster}<div class="body">'
            f'<div class="title">{title}<span class="kind">{kind}</span>{badges}</div>'
            f'<div><span class="match">{int(item["match"])}% match</span></div>'
            f'<div class="reason">{escape(item["reason"])}</div>'
            f'<div class="chips">{chips}</div>'
            f'<div class="blurb">{escape(item["overview"])}</div>{actions}</div></div>')


def _in_tab(item, tab):
    if tab == "new":
        return bool(item.get("new") or item.get("trending"))
    return tab == "all" or item["media_type"] == tab


def render_page(tab="all", refresh=False, msg=""):
    try:
        result, age = get_result(refresh)
    except Exception as e:
        return _shell(f'<p class="note">Couldn\'t get recommendations: {escape(str(e))}</p>', tab)
    items = [i for i in result["items"] if _in_tab(i, tab)][:SHOW]
    notes = "".join(f'<p class="note">{escape(n)}</p>' for n in result["notes"])
    if msg:
        notes = f'<p class="note">{escape(msg)}</p>' + notes
    taste = ""
    top = profile.summary(result["profile"], 5)
    if any(top.values()):
        rows = [("Genres", top["genre"]), ("Themes", top["keyword"]), ("People", top["director"] + top["actor"][:3])]
        taste = '<div class="taste">' + "<br>".join(
            f'<b>{label}</b> {escape(", ".join(names))}' for label, names in rows if names) + "</div>"
    cards = "".join(_card(i, tab, not result["sample"]) for i in items)
    body = (f'{notes}{taste}<div class="grid">{cards}</div>' if items
            else f'{notes}<p class="muted">No recommendations found.</p>')
    minutes = int(age // 60)
    return _shell(body, tab, f"Based on {result['watched_count']} watched titles · updated "
                              f"{'just now' if minutes < 1 else f'{minutes} min ago'}")


def _shell(body, tab, subtitle="", recommendation_nav=True):
    top_actions = f'<a class="settings-link" href="/settings">Settings</a>'
    nav = ""
    if recommendation_nav:
        tabs = "".join(f'<a href="/?{urlencode({"type": key})}"{" class=on" if key == tab else ""}>{escape(label)}</a>'
                       for key, label in TABS)
        top_actions = (f'<form class="inline" method="post" action="/refresh">'
                      f'<input type="hidden" name="tab" value="{escape(tab)}">'
                      f'<button type="submit">Refresh</button></form>{top_actions}')
        nav = f'<nav class="tabs">{tabs}</nav>'
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>What&#39;s Next</title>'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><style>{CSS}</style></head><body><main>'
            f'<div class="top"><div><h1>What&#39;s Next</h1><p class="sub">{escape(subtitle)}</p></div>'
            f'<div class="card-actions">{top_actions}</div></div>'
            f'{nav}{body}</main></body></html>')


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type="text/html; charset=utf-8", headers=None):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, tab, msg=None):
        tab = tab if tab in dict(TABS) else "all"
        params = {"type": tab, **({"msg": msg} if msg else {})}
        self._send(303, "", headers={"Location": "/?" + urlencode(params)})

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/health":
            return self._send(200, "ok", "text/plain")
        if url.path == "/settings":
            saved = parse_qs(url.query).get("saved", ["0"])[0] == "1"
            body = _shell(settings_page.render(saved), "settings", "Settings", recommendation_nav=False)
            return self._send(200, body)
        if url.path != "/":
            return self._send(404, "Not found", "text/plain")
        query = parse_qs(url.query)
        tab = query.get("type", ["all"])[0]
        msg = query.get("msg", [""])[0]
        self._send(200, render_page(tab if tab in dict(TABS) else "all", msg=msg))

    def do_POST(self):
        length = min(int(self.headers.get("Content-Length") or 0), 4096)
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        tab = form.get("tab", ["all"])[0]
        path = urlparse(self.path).path
        if path == "/refresh":
            get_result(refresh=True)
            return self._redirect(tab)
        if path == "/dismiss":
            media_type, raw_id = form.get("type", [""])[0], form.get("id", [""])[0]
            if media_type in ("movie", "tv") and raw_id.isdigit() and not (_state["result"] or {}).get("sample"):
                db.dismiss(media_type, int(raw_id))
                forget(media_type, int(raw_id))
            return self._redirect(tab)
        if path == "/settings":
            settings_page.apply_form(form)
            invalidate_cache()
            return self._send(303, "", headers={"Location": "/settings?saved=1"})
        if path == "/add":
            media_type, raw_id = form.get("type", [""])[0], form.get("id", [""])[0]
            msg = None
            if media_type in ("movie", "tv") and raw_id.isdigit() and not (_state["result"] or {}).get("sample"):
                ok, msg = sources.add_to_library(media_type, int(raw_id))
                if ok:
                    forget(media_type, int(raw_id))
            return self._redirect(tab, msg)
        self._send(404, "Not found", "text/plain")

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}")


def make_server(host="0.0.0.0", port=8080):
    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    server = make_server(os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "8080")))
    print(f"Listening on port {server.server_address[1]}")
    server.serve_forever()
