"""Finds titles you haven't got and ranks them by how well they fit your taste profile.

How a suggestion is found and scored:
  1. Take your most influential watched titles and ask TMDB "what do people who liked this also like?".
     Anything in your Plex library (or that you've dismissed) is thrown away.
  2. Add well-rated titles from your favourite genres, this week's trending titles, and recent releases
     in your favourite genres. Trending and new titles must fit your taste to be shown at all.
  3. If an AI is configured, ask it for more ideas grounded in your taste profile. Every suggestion
     names a title, never a trusted id - it's looked up on TMDB ourselves (matching by year) before
     being treated as real, and it's scored by the exact same formula as everything else here, with
     no bonus for having come from the AI.
  4. Score each candidate:  50% content match (genres, themes, director, actors vs your profile)
                            28% how many of your favourites TMDB links it to
                            12% general quality (TMDB rating, shrunk towards average when few votes)
                            10% buzz (trending now, or recently released)
     Taste dominates: a trending title that doesn't fit you can't outrank a strong match.
  5. Titles that are trending or came out in the last 6 months are flagged, for the "New & trending" tab."""
import math
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

import profile

WEIGHTS = {"content": 0.50, "collab": 0.28, "quality": 0.12, "buzz": 0.10}
CATEGORY_WEIGHTS = {"genre": 0.30, "keyword": 0.35, "director": 0.15, "actor": 0.20}
MIN_VOTES = 100                        # too few votes = too little evidence it's any good
MIN_VOTES_NEW = 25                     # ...but brand-new and trending titles haven't had time to collect many
PRIOR_VOTES, PRIOR_RATING = 100, 6.5   # ratings with few votes are pulled towards this
DISCOVER_GENRES = 2                    # how many top genres to add "popular in" and "new in" titles from
MAX_DISCOVER, MAX_TRENDING, MAX_NEW = 20, 30, 30
MAX_AI_REQUESTED = 25                  # how many ideas to ask the AI for; expect some not to resolve on TMDB
MAX_AI = 25                            # how many resolved AI candidates to actually consider
AI_TIER_CAPS = {"loved": 40, "liked": 40, "recent": 15, "disliked": 25, "abandoned": 15, "seen": 60,
                "not_interested": 30, "previously_suggested": 40}   # titles per prompt section (~200 at most)
AI_RECENT_DAYS = 60                    # watched within this many days = "recent" in the AI prompt
NEW_DAYS = 180                         # released within this many days = "New"
FRESH_SPAN_DAYS = 730                  # the recency boost fades to nothing over two years
MIN_TASTE_FOR_BUZZ = 0.10              # trending/new titles need at least this much content match
DISLIKE_FACTORS = {1: 0.4, 2: 0.7}     # score multiplier for titles TMDB links to something you rated 1 / 2 stars
MAX_DISLIKED = 50                      # how many disliked titles to look up (cached) for that
MAX_BECAUSE = 10                       # how many watched titles an item's "because" list names
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


def _ai_label(title, year, media_type):
    """How a title appears in the AI prompt: "Arrival (2016)", "The Leftovers (2014, TV)"."""
    bits = [str(year)] if year else []
    if media_type == "tv":
        bits.append("TV")
    return f"{title} ({', '.join(bits)})" if bits else title


def ai_label_for(item):
    """_ai_label for a recommendation item (title, year and media_type keys)."""
    return _ai_label(item.get("title") or "?", item.get("year"), item.get("media_type"))


def _title_key(text):
    """Loose match for a title the AI echoes back: no year suffix, accents, punctuation or case."""
    text = re.sub(r"\s*\([^)]*\)\s*$", "", str(text or ""))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def ai_taste(watched, details_by_key, features, now, dismissed_labels=(), previously_suggested=()):
    """The AI prompt's view of the viewer: their history grouped by how they felt about it.
    watched: history entries (ratings applied); details_by_key: {(media_type, id): tmdb details} for titles
    and years where known. Returns {section: [labels]} (see ai.TASTE_SECTIONS) plus "patterns"."""
    tiers = {k: [] for k in ("loved", "liked", "disliked", "abandoned", "seen")}
    recent_pool = []
    for item in watched:
        key = (item["media_type"], item["tmdb_id"])
        details = details_by_key.get(key) or {}
        title = details.get("title") if details.get("title") not in (None, "?") else item.get("title")
        if not title or title == "?":
            continue
        label = _ai_label(title, details.get("year") or item.get("year"), item["media_type"])
        stars = item.get("personal_stars") or profile.stars_from_ten(item.get("user_rating"))
        progress, views = item.get("progress"), item.get("view_count") or 1
        if stars is not None and stars <= profile.DISLIKE_MAX_STARS:
            tier = "disliked"
        elif progress is not None and progress < 0.25 and (stars is None or stars < 4):
            tier = "abandoned"
        elif stars == 5 or (views >= 2 and (stars is None or stars >= 4)):
            tier = "loved"
        elif stars == 4:
            tier = "liked"
        else:
            tier = "seen"
        weight = profile.item_weight(item, now)
        last = item.get("last_viewed")
        tiers[tier].append((weight, last or "", label))
        if tier in ("loved", "liked", "seen") and last:
            try:
                age = (now - profile._parse(last)).days
            except (TypeError, ValueError):
                age = None
            if age is not None and age <= AI_RECENT_DAYS:
                recent_pool.append((last, label, tier))
    recent_pool.sort(key=lambda r: r[0], reverse=True)
    recent = recent_pool[:AI_TIER_CAPS["recent"]]
    in_recent = {label for _, label, tier in recent if tier == "seen"}   # loved/liked stay in their tier too
    taste = {"recent": [label for _, label, _ in recent]}
    for tier, rows in tiers.items():
        order = (lambda r: r[1]) if tier == "disliked" else (lambda r: r[0])   # dislikes newest first
        rows = sorted(rows, key=order, reverse=True)
        taste[tier] = [label for _, _, label in rows if label not in in_recent][:AI_TIER_CAPS[tier]]
    taste["not_interested"] = list(dismissed_labels)[:AI_TIER_CAPS["not_interested"]]
    taste["previously_suggested"] = list(previously_suggested)[:AI_TIER_CAPS["previously_suggested"]]
    summary = profile.summary(features)
    taste["patterns"] = {"genre": summary.get("genre", []), "keyword": summary.get("keyword", [])}
    return taste


def recommend(watched, library_keys, tmdb, dismissed=(), limit=200, now=None,
              max_profile_items=100, max_sources=40, max_candidates=150, ai=None, ai_only=False, disliked=None,
              ai_avoid=()):
    """watched: items from plex.py; library_keys: {(media_type, id)} of everything you already have;
    tmdb: object with details(), discover(), trending(), new_releases() and search() (see tmdb.TmdbClient).
    ai: optional object with suggest() (see ai.AiClient) - skipped entirely if not given.
    ai_only: skip the TMDB-linked/genre/trending/new-release candidates entirely and consider only
    the AI's own ideas - for a deliberate, separate "generate AI recommendations" action (each call
    costs a real AI request, unlike everything else here, which is why it isn't automatic).
    disliked: {(media_type, tmdb_id): stars} of titles you rated low. A candidate TMDB links to one
    of them has its score multiplied by the smallest matching DISLIKE_FACTORS entry (never excluded).
    ai_avoid: labels of titles the AI suggested last time, so it doesn't repeat itself.
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

    linked = []
    by_origin = {"genre": [], "trending": [], "new": [], "ai": []}
    if not ai_only:
        for w, item, details in sources:
            for rec_id in details["recommendations"]:
                key = (item["media_type"], rec_id)
                if key not in exclude:
                    c = candidate(key, "linked")
                    c["sources"].append((w, item.get("title") or details.get("title") or "?"))
                    c["collab"] += w
        linked = sorted(candidates, key=lambda k: -candidates[k]["collab"])[:max_candidates]

        # 2b. Candidates: well-rated titles in your favourite genres, what's trending, and what's new.
        top_genres = sorted(features["genre"], key=features["genre"].get, reverse=True)[:DISCOVER_GENRES]
        since = (today - timedelta(days=NEW_DAYS)).isoformat()

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

    # 2c. Candidates: an AI's own ideas, if one is configured. Never trust a title/id it names -
    # look it up on TMDB ourselves (matching by year) before treating it as real.
    if ai is not None:
        try:
            details_by_key = {(i["media_type"], i["tmdb_id"]): d for i, d in pairs}
            dismissed_labels = []
            for d_type, d_id in list(dismissed)[:AI_TIER_CAPS["not_interested"]]:
                d = _details_or_none(tmdb, d_type, d_id)
                if d and d.get("title") not in (None, "?"):
                    dismissed_labels.append(_ai_label(d["title"], d.get("year"), d_type))
            taste = ai_taste(watched, details_by_key, features, now, dismissed_labels, ai_avoid)
            anchors = {_title_key(label): label for sec in ("loved", "liked", "recent") for label in taste[sec]}
            suggestions = ai.suggest(taste, count=MAX_AI_REQUESTED)
            unmatched = already = 0
            for raw in suggestions:
                media_type = raw.get("media_type")
                if media_type not in ("movie", "tv"):
                    continue
                try:
                    tmdb_id = tmdb.search(media_type, raw["title"], raw.get("year"))
                except Exception:
                    tmdb_id = None
                if not tmdb_id:
                    unmatched += 1
                    continue
                key = (media_type, tmdb_id)
                if key in exclude:
                    already += 1
                    continue
                c = candidate(key, "ai")
                if raw.get("reason") and not c.get("ai_reason"):
                    c["ai_reason"] = raw["reason"]
                if not c.get("ai_because"):   # only titles really in their lists; a name the AI made up is dropped
                    named = [anchors.get(_title_key(b)) for b in (raw.get("because") or []) if isinstance(b, str)]
                    c["ai_because"] = [re.sub(r"\s*\([^)]*\)$", "", n) for n in dict.fromkeys(n for n in named if n)][:2]
                if key not in by_origin["ai"]:
                    by_origin["ai"].append(key)
            if not suggestions:
                why = getattr(ai, "last_error", None)
                notes.append(f"The AI didn't return any suggestions: {why}." if isinstance(why, str) and why
                             else "The AI didn't return any suggestions.")
            elif not by_origin["ai"]:
                parts = []
                if already:
                    parts.append(f"{already} you already have or have watched")
                if unmatched:
                    parts.append(f"{unmatched} not found on TMDB")
                notes.append(f"The AI suggested {len(suggestions)} titles, but none could be used ("
                             f"{', '.join(parts)}).")
        except Exception:
            notes.append("Couldn't get AI suggestions.")

    pool, seen = [], set()
    for keys in (linked, by_origin["genre"][:MAX_DISCOVER], by_origin["trending"][:MAX_TRENDING],
                 by_origin["new"][:MAX_NEW], by_origin["ai"][:MAX_AI]):
        for key in keys:
            if key not in seen:
                seen.add(key)
                pool.append(key)

    # 2d. Candidates TMDB links to titles you disliked are pushed down.
    penalty = {}
    for (d_type, d_id), stars in list((disliked or {}).items())[:MAX_DISLIKED]:
        factor = DISLIKE_FACTORS.get(stars)
        d_details = _details_or_none(tmdb, d_type, d_id) if factor else None
        for rec_id in (d_details or {}).get("recommendations", []):
            k = (d_type, rec_id)
            penalty[k] = min(penalty.get(k, 1.0), factor)

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
        final *= penalty.get(key, 1.0)
        if c["sources"]:
            names = [t for _, t in sorted(c["sources"], key=lambda s: -s[0])[:2]]
            reason = "Because you watched " + " and ".join(names)
        elif trending:
            reason = "Trending this week, and fits your taste"
        elif is_new:
            reason = "New release that fits your taste"
        elif "ai" in c["origin"]:
            if c.get("ai_because"):
                lead = "Because you watched " + " and ".join(c["ai_because"])
                reason = f"{lead}. {c['ai_reason']}" if c.get("ai_reason") else lead
            else:
                reason = f"AI pick: {c['ai_reason']}" if c.get("ai_reason") else "AI pick based on your taste profile"
        else:
            reason = f"Highly rated in {c['genre']}, one of your favourite genres"
        because = list(c.get("ai_because") or [])
        for _, t in sorted(c["sources"], key=lambda s: -s[0]):
            if t not in because:
                because.append(t)
        scored.append((final, {**details, "reason": reason, "because": because[:MAX_BECAUSE], "matches": matches(features, details),
                               "new": is_new, "trending": trending}))
    scored.sort(key=lambda s: -s[0])

    # "Match" is relative to the best suggestion in this batch (like a rank, not a probability).
    best = scored[0][0] if scored else 1
    items = [{**d, "match": max(1, math.floor(99 * final / best))} for final, d in scored[:limit]]
    if ai_only and by_origin["ai"] and not items:
        notes.append(f"The AI's {len(by_origin['ai'])} suggestions were all left out: not released yet, "
                     "or too little rating data on TMDB to judge them.")
    return {"items": items, "profile": features, "notes": notes}
