"""Turns what you've watched into a taste profile: how much you like each genre, theme (TMDB keyword),
director/creator and actor, as numbers between 0 and 1."""
import math
from datetime import datetime, timezone

HALF_LIFE_DAYS = 365   # a watch from a year ago counts half as much as one from today...
MIN_RECENCY = 0.15     # ...but old favourites never vanish completely
CATEGORIES = ("genre", "keyword", "director", "actor")
FIELD = {"genre": "genres", "keyword": "keywords", "director": "directors", "actor": "cast"}
MIN_APPEARANCES = {"director": 2, "actor": 2}  # one appearance is a coincidence, two is a pattern
DISLIKE_MAX_STARS = 2  # 1-2 stars: no taste input, and linked titles are pushed down (see recommend.py)
GENRE_DAMPING = {"Drama": 0.4}  # so common it says little about taste; don't let it dominate the profile


def _parse(ts):
    return datetime.fromisoformat(ts)


def item_weight(item, now=None):
    """How strongly one watched title should shape your taste (roughly 0 to 2)."""
    now = now or datetime.now(timezone.utc)
    weight = 1.0
    if item.get("last_viewed"):
        age_days = max(0, (now - _parse(item["last_viewed"])).days)
        weight *= max(MIN_RECENCY, 0.5 ** (age_days / HALF_LIFE_DAYS))
    rating = item.get("user_rating")
    if rating is not None:  # 7/10 is neutral, 10/10 counts 1.6x, 4/10 or less counts nothing
        weight *= min(1.6, max(0.0, (rating - 4) / 3))
    weight *= 1 + 0.25 * min(max(item.get("view_count", 1) - 1, 0), 3)  # rewatches
    progress = item.get("progress")
    if progress is not None and progress < 0.25:  # started a show and drifted away
        weight *= 0.4
    return weight


def stars_from_ten(rating):
    """Plex's 0-10 rating -> 1-5 whole stars (rounding half up), or None if there isn't a usable one."""
    try:
        r = float(rating)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(r) or r <= 0:
        return None
    return max(1, min(5, int(r / 2 + 0.5)))


def apply_ratings(watched, ratings):
    """Copies of watched with your own star ratings laid over the Plex/Tautulli ones (stars * 2, the
    same 0-10 scale). Unrated titles are untouched. ratings: {(media_type, tmdb_id): stars}."""
    out = []
    for item in watched:
        stars = ratings.get((item["media_type"], item["tmdb_id"]))
        item = dict(item)
        if stars is not None:
            item["user_rating"] = stars * 2
            item["personal_stars"] = stars
        out.append(item)
    return out


def build(pairs, now=None):
    """pairs: [(watched_item, tmdb_details)]. Returns {category: {name: 0..1}}."""
    now = now or datetime.now(timezone.utc)
    totals = {c: {} for c in CATEGORIES}
    appearances = {c: {} for c in CATEGORIES}
    for item, details in pairs:
        weight = item_weight(item, now)
        if weight <= 0:
            continue
        for category in CATEGORIES:
            names = details.get(FIELD[category]) or []
            for name in names:
                # Split the title's weight across its tags so a title with 30 keywords doesn't drown out one with 5.
                totals[category][name] = totals[category].get(name, 0) + weight / math.sqrt(len(names))
                appearances[category][name] = appearances[category].get(name, 0) + 1
    features = {}
    for category, scores in totals.items():
        need = MIN_APPEARANCES.get(category, 1)
        scores = {n: s for n, s in scores.items() if appearances[category][n] >= need}
        if category == "genre":
            scores = {n: s * GENRE_DAMPING.get(n, 1) for n, s in scores.items()}
        top = max(scores.values(), default=0)
        features[category] = {n: s / top for n, s in scores.items()} if top else {}
    return features


def summary(features, per_category=6):
    """The strongest tastes in each category, for showing on the page."""
    return {c: sorted(f, key=f.get, reverse=True)[:per_category] for c, f in features.items()}
