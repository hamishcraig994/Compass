"""Finds titles you haven't got and ranks them by how well they fit your taste profile.

How a suggestion is found and scored:
  1. Take your most influential watched titles and ask TMDB "what do people who liked this also like?".
     Anything in your Plex library (or that you've dismissed) is thrown away.
  2. Add well-rated titles from your favourite genres, this week's trending titles, and recent releases
     in your favourite genres. Trending and new titles must fit your taste to be shown at all.
  3. Score each candidate:  50% content match (genres, themes, director, actors vs your profile)
                            28% how many of your favourites TMDB links it to
                            12% general quality (TMDB rating, shrunk towards average when few votes)
                            10% buzz (trending now, or recently released)
     Taste dominates: a trending title that doesn't fit you can't outrank a strong match.
  4. Titles that are trending or came out in the last 6 months are flagged, for the "New & trending" tab."""
import math
from datetime import date, datetime, timedelta, timezone

import profile

WEIGHTS = {"content": 0.50, "collab": 0.28, "quality": 0.12, "buzz": 0.10}
CATEGORY_WEIGHTS = {"genre": 0.30, "keyword": 0.35, "director": 0.15, "actor": 0.20}
MIN_VOTES = 100                        # too few votes = too little evidence it's any good
MIN_VOTES_NEW = 25                     # ...but brand-new and trending titles haven't had time to collect many
PRIOR_VOTES, PRIOR_RATING = 100, 6.5   # ratings with few votes are pulled towards this
DISCOVER_GENRES = 2                    # how many top genres to add "popular in" and "new in" titles from
MAX_DISCOVER, MAX_TRENDING, MAX_NEW = 20, 30, 30
NEW_DAYS = 180                         # released within this many days = "New"
FRESH_SPAN_DAYS = 730                  # the recency boost fades to nothing over two years
MIN_TASTE_FOR_BUZZ = 0.10              # trending/new titles need at least this much content match
LABELS = {"genre": "Genre", "keyword": "Theme", "director": "By", "actor": "Stars"}


def content_score(features, d):
    """0..1 - how well one title's tags line up with your profile."""
    genres = d["genres"]
    parts = {
        "genre": sum(features["genre"].get(g, 0) for g in genres) / len(genres) if genres else 0,
        "keyword": min(1, sum(features["keyword"].get(k, 0) for k in d["keywords"]) / 3),
        "director": min(1, sum(features["director"].get(p, 0) for p in d["directors"])),
        "actor": min(1, sum(features["actor"].get(p, 0) for p in d["cast"]) / 1.5),
    }
    return sum(CATEGORY_WEIGHTS[c] * parts[c] for c in parts)


def quality_score(d):
    votes = d["vote_count"]
    rating = (votes * d["vote_average"] + PRIOR_VOTES * PRIOR_RATING) / (votes + PRIOR_VOTES)
    return min(1, max(0, (rating - 5) / 4))  # 5/10 -> 0, 9/10 -> 1


def release_age_days(d, today):
    """Days since release (negative = not out yet), or None if TMDB has no date."""
    try:
        return (today - date.fromisoformat(d.get("release_date"))).days
    except (TypeError, ValueError):
        return None


def buzz_score(age_days, trending):
    """0..1 - 1 for anything trending, otherwise how recently it came out."""
    fresh = 0.0 if age_days is None else max(0.0, 1 - max(age_days, 0) / FRESH_SPAN_DAYS)
    return max(fresh, 1.0 if trending else 0.0)


def matches(features, d, limit=3):
    """The shared traits worth showing, e.g. ['By Denis Villeneuve', 'Genre Science Fiction']."""
    found = []
    for category in profile.CATEGORIES:
        for name in d[profile.FIELD[category]]:
            strength = features[category].get(name, 0)
            if strength >= 0.3:
                found.append((strength * CATEGORY_WEIGHTS[category], f"{LABELS[category]}: {name}"))
    return [label for _, label in sorted(found, reverse=True)[:limit]]


def _details_or_none(tmdb, media_type, tmdb_id):
    try:
        return tmdb.details(media_type, tmdb_id)
    except Exception:
        return None


def recommend(watched, library_keys, tmdb, dismissed=(), limit=200, now=None,
              max_profile_items=100, max_sources=40, max_candidates=150):
    """watched: items from plex.py; library_keys: {(media_type, id)} of everything you already have;
    tmdb: object with details(), discover(), trending() and new_releases() (see tmdb.TmdbClient).
    Returns {"items": [...best first...], "profile": {...}, "notes": [...]}."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    notes = []
    have = set(library_keys) | {(w["media_type"], w["tmdb_id"]) for w in watched}
    exclude = have | set(dismissed)

    # 1. Look up what you've watched, most influential first.
    ranked = sorted(watched, key=lambda w: profile.item_weight(w, now), reverse=True)[:max_profile_items]
    pairs = []
    for item in ranked:
        details = _details_or_none(tmdb, item["media_type"], item["tmdb_id"])
        if details:
            pairs.append((item, details))
    if len(pairs) < len(ranked):
        notes.append(f"Couldn't look up {len(ranked) - len(pairs)} watched titles on TMDB; they were skipped.")
    if not pairs:
        return {"items": [], "profile": {c: {} for c in profile.CATEGORIES}, "notes": notes + ["Nothing watched yet."]}
    features = profile.build(pairs, now)

    # 2a. Candidates: what TMDB links to your favourites.
    weighted = sorted(((profile.item_weight(i, now), i, d) for i, d in pairs), key=lambda p: -p[0])
    sources = [s for s in weighted if s[0] > 0][:max_sources]
    top_weight = sum(w for w, _, _ in sources[:3]) or 1.0
    candidates = {}

    def candidate(key, origin):
        c = candidates.setdefault(key, {"sources": [], "collab": 0.0, "genre": None, "origin": set()})
        c["origin"].add(origin)
        return c

    for w, item, details in sources:
        for rec_id in details["recommendations"]:
            key = (item["media_type"], rec_id)
            if key not in exclude:
                c = candidate(key, "linked")
                c["sources"].append((w, item["title"]))
                c["collab"] += w
    linked = sorted(candidates, key=lambda k: -candidates[k]["collab"])[:max_candidates]

    # 2b. Candidates: well-rated titles in your favourite genres, what's trending, and what's new.
    top_genres = sorted(features["genre"], key=features["genre"].get, reverse=True)[:DISCOVER_GENRES]
    since = (today - timedelta(days=NEW_DAYS)).isoformat()
    by_origin = {"genre": [], "trending": [], "new": []}

    def gather(origin, media_type, description, fetch, genre=None):
        try:
            ids = fetch()
        except Exception:
            notes.append(f"Couldn't fetch {description}.")
            return
        for tmdb_id in ids:
            key = (media_type, tmdb_id)
            if key not in exclude:
                c = candidate(key, origin)
                c["genre"] = c["genre"] or genre
                if key not in by_origin[origin]:
                    by_origin[origin].append(key)

    for media_type in ("movie", "tv"):
        gather("trending", media_type, f"trending {media_type} titles", lambda: tmdb.trending(media_type))
        for genre in top_genres:
            gather("genre", media_type, f"popular {genre} {media_type} titles",
                   lambda: tmdb.discover(media_type, genre), genre)
            gather("new", media_type, f"new {genre} {media_type} titles",
                   lambda: tmdb.new_releases(media_type, genre, since, today.isoformat()), genre)

    pool, seen = [], set()
    for keys in (linked, by_origin["genre"][:MAX_DISCOVER], by_origin["trending"][:MAX_TRENDING],
                 by_origin["new"][:MAX_NEW]):
        for key in keys:
            if key not in seen:
                seen.add(key)
                pool.append(key)

    # 3. Score.
    scored = []
    for key in pool:
        c = candidates[key]
        details = _details_or_none(tmdb, *key)
        if not details:
            continue
        age = release_age_days(details, today)
        if age is not None and age < 0:
            continue  # not out yet
        is_new = age is not None and age <= NEW_DAYS
        trending = "trending" in c["origin"]
        if details["vote_count"] < (MIN_VOTES_NEW if is_new or trending else MIN_VOTES):
            continue
        content = content_score(features, details)
        if c["origin"] <= {"trending", "new"} and content < MIN_TASTE_FOR_BUZZ:
            continue  # buzzy, but not for you
        final = (WEIGHTS["content"] * content
                 + WEIGHTS["collab"] * min(1, c["collab"] / top_weight)
                 + WEIGHTS["quality"] * quality_score(details)
                 + WEIGHTS["buzz"] * buzz_score(age, trending))
        if c["sources"]:
            names = [t for _, t in sorted(c["sources"], key=lambda s: -s[0])[:2]]
            reason = "Because you watched " + " and ".join(names)
        elif trending:
            reason = "Trending this week, and fits your taste"
        elif is_new:
            reason = "New release that fits your taste"
        else:
            reason = f"Highly rated in {c['genre']}, one of your favourite genres"
        scored.append((final, {**details, "reason": reason, "matches": matches(features, details),
                               "new": is_new, "trending": trending}))
    scored.sort(key=lambda s: -s[0])

    # "Match" is relative to the best suggestion in this batch (like a rank, not a probability).
    best = scored[0][0] if scored else 1
    items = [{**d, "match": max(1, math.floor(99 * final / best))} for final, d in scored[:limit]]
    return {"items": items, "profile": features, "notes": notes}
