"""Row logic for the cinematic Home / Movies / TV pages. Pure: no I/O, no db, never mutates its inputs.
view() turns a build result into a hero (top picks) plus headed rows; pages.py only renders it."""
import statistics

KINDS = ("all", "movie", "tv")
HERO_MAX, ROW_MAX, ROW_MIN, TOP_N = 5, 20, 4, 10
GEM_MIN_MATCH = 60
TRENDING_GENRES = 3              # how many top profile genres to try for the trending row
LIBRARY_ROW_MAX = 20
ROW_ORDER = ("recommended", "because", "top10", "trending", "gems", "library_new")
HERO_FIELDS = ("backdrop_url", "poster_large_url", "runtime", "seasons", "certification")


def kind_items(items, kind):
    """Items of one kind, order kept. An unknown kind means all."""
    items = list(items or [])
    if kind in ("movie", "tv"):
        return [i for i in items if i.get("media_type") == kind]
    return items


def hero_picks(items, kind):
    return kind_items(items, kind)[:HERO_MAX]


def _row(row_id, title, subtitle, items, numbered=False, source="recs"):
    return {"id": row_id, "title": title, "subtitle": subtitle, "numbered": numbered,
            "source": source, "items": items}


def _because_seed(ki):
    """The watched title named in the most items' "because" lists. Ties go to the title found in the
    highest-ranked item. Returns (title, count) or (None, 0)."""
    counts, first = {}, {}
    for rank, item in enumerate(ki):
        for title in set(item.get("because") or []):
            counts[title] = counts.get(title, 0) + 1
            first.setdefault(title, rank)
    if not counts:
        return None, 0
    seed = min(counts, key=lambda t: (-counts[t], first[t], t))
    return seed, counts[seed]


def _trending_row(ki, profile):
    genres = (profile or {}).get("genre") or {}
    for genre in sorted(genres, key=lambda g: (-genres[g], g))[:TRENDING_GENRES]:
        found = [i for i in ki if genre in (i.get("genres") or []) and (i.get("trending") or i.get("new"))]
        if len(found) >= ROW_MIN:
            found.sort(key=lambda i: not i.get("trending"))  # stable: trending first, then score order
            return _row("trending", f"Trending in {genre}", None, found[:ROW_MAX])
    return None


def _gems(ki):
    medians = {}
    for t in {i.get("media_type") for i in ki}:
        medians[t] = statistics.median_low([i.get("vote_count") or 0 for i in ki if i.get("media_type") == t])
    return [i for i in ki[TOP_N:]
            if (i.get("match") or 0) >= GEM_MIN_MATCH and (i.get("vote_count") or 0) <= medians[i.get("media_type")]]


def _library_row(library, kind):
    entries = [e for e in library if kind == "all" or e.get("media_type") == kind]
    dated = sorted((e for e in entries if e.get("added_at")), key=lambda e: e["added_at"], reverse=True)
    ordered = (dated + [e for e in entries if not e.get("added_at")])[:LIBRARY_ROW_MAX]
    if not ordered:
        return None
    return _row("library_new", "New in your library", None,
                [{**e, "in_library": True} for e in ordered], source="library")


def view(result, kind, owned=frozenset()):
    """result: a build result (only "items", "profile" and "library" are read, all optional).
    owned: {(media_type, tmdb_id)} to flag as in_library. Returns the BrowseView dict."""
    kind = kind if kind in KINDS else "all"
    result = result or {}
    ki = kind_items(result.get("items"), kind)

    def mark(items):
        return [{**i, "in_library": (i.get("media_type"), i.get("tmdb_id")) in owned} for i in items]

    rows = {}
    if ki:
        rows["recommended"] = _row("recommended", "Recommended for You", "Based on your watch history",
                                   mark((ki[HERO_MAX:] or ki)[:ROW_MAX]))
    seed, count = _because_seed(ki)
    if seed is not None and count >= ROW_MIN:
        rows["because"] = _row("because", f"Because you watched {seed}", None,
                               mark([i for i in ki if seed in (i.get("because") or [])][:ROW_MAX]))
    if len(ki) >= ROW_MIN:
        rows["top10"] = _row("top10", "Top 10 picks for you", None, mark(ki[:TOP_N]), numbered=True)
    trending = _trending_row(ki, result.get("profile"))
    if trending:
        trending["items"] = mark(trending["items"])
        rows["trending"] = trending
    gems = _gems(ki)
    if len(gems) >= ROW_MIN:
        rows["gems"] = _row("gems", "Hidden Gems", "High match, less well known", mark(gems[:ROW_MAX]))
    library = result.get("library")
    if library is not None:
        lib_row = _library_row(library, kind)
        if lib_row:
            rows["library_new"] = lib_row
    return {"kind": kind, "total": len(ki), "hero": mark(ki[:HERO_MAX]),
            "rows": [rows[r] for r in ROW_ORDER if r in rows]}
