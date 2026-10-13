"""Asks an OpenAI-compatible LLM for a few more recommendation ideas, grounded in your taste
profile. Optional - without a token, this is never called and nothing else changes.

Every suggestion the AI makes is a lead, never a fact: it names a title and year, and
recommend.py looks that up on TMDB itself (matching by year, not just taking the first search
result) before trusting it - the AI's own opinion of a TMDB id, if it ever guessed one, would
never be used. This is a direct lesson from a real bug found in another app's AI integration:
it trusted an LLM-supplied TMDB id outright, so a confident wrong guess could point at a
completely different title with total silence about the mismatch. AI suggestions are also scored
by the exact same formula as every other candidate in recommend.py - no bonus for coming from
the AI, the same way "popular in your genre" candidates don't get one either."""
import json

from http_util import post_json

SYSTEM_PROMPT = (
    "You are a film and TV curator. Work out this viewer's taste from their ratings and history, then "
    "recommend titles they haven't seen. Before choosing, privately identify what connects the titles they "
    "loved (tone, pacing, themes, era, complexity) and what their disliked and abandoned titles have in "
    "common, and steer away from the latter. Ground every pick in specific titles from their loved, liked "
    "or recent lists. Prefer considered picks over the obvious blockbusters everyone is shown. Never suggest "
    "anything from their history, their not-interested list or the previously suggested list, and only "
    "suggest titles that have already been released."
)
JSON_FORMAT = (
    ' Reply with ONLY JSON, no other text and no markdown formatting, in this exact shape: '
    '{"suggestions": [{"title": "...", "year": 2020, "media_type": "movie", "because": ["a title from their '
    'lists"], "reason": "one sentence, specific to this viewer", "novelty": "safe"}]}. media_type is exactly '
    '"movie" or "tv"; novelty is exactly "safe" (a likely hit) or "discovery" (less known, more of a stretch).'
)

# Structured outputs (OpenAI's own API): the reply is guaranteed to match this, so it can't come back malformed.
SUGGESTIONS_SCHEMA = {
    "name": "suggestions",
    "strict": True,
    "schema": {
        "type": "object", "additionalProperties": False, "required": ["suggestions"],
        "properties": {"suggestions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "year", "media_type", "because", "reason", "novelty"],
            "properties": {"title": {"type": "string"}, "year": {"type": ["integer", "null"]},
                           "media_type": {"type": "string", "enum": ["movie", "tv"]},
                           "because": {"type": "array", "items": {"type": "string"}},
                           "reason": {"type": "string"},
                           "novelty": {"type": "string", "enum": ["safe", "discovery"]}}}}},
    },
}

# OpenAI reasoning models (o-series, gpt-5 and later, e.g. gpt-6-luna) think before answering, out of the same
# token budget, and default to medium effort. Inferring taste from a rated history is exactly what that thinking
# is for, so keep medium; the cap leaves room for the thinking plus 25 suggestions.
REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5", "gpt-6", "gpt-7")
REASONING_EFFORT = "medium"

# What the prompt asks for. recommend.py verifies every pick on TMDB and filters it, so ask for more than it keeps.
SUGGEST_COUNT = 25
TV_SHARE = 0.4          # roughly 60% movies, 40% TV
DISCOVERY_SHARE = 1 / 3  # at least this many lesser-known picks
TASTE_SECTIONS = (      # (key in the taste dict, heading in the prompt)
    ("loved", "Loved (5 stars, or rewatched)"),
    ("liked", "Liked (4 stars)"),
    ("recent", "Watched recently, newest first (their current mood)"),
    ("disliked", "Disliked (1-2 stars): steer away from titles like these"),
    ("abandoned", "Started but abandoned: also a sign of what doesn't work for them"),
    ("seen", "Also seen, no strong opinion"),
    ("not_interested", "Not interested: never suggest"),
    ("previously_suggested", "Previously suggested by you: don't repeat"),
)


def is_reasoning_model(model):
    return model.lower().startswith(REASONING_PREFIXES)


SUGGEST_MAX_TOKENS = 8000   # a cap, not a cost: reasoning models spend part of it thinking before they answer


def _parse_suggestions(content):
    """The JSON array from a reply, tolerating code fences, text around the array, and an object
    that wraps it ({"recommendations": [...]}). Raises ValueError if there's no array to find."""
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        content = content[4:] if content.lower().startswith("json") else content
    try:
        raw = json.loads(content)
    except ValueError:
        start, end = content.find("["), content.rfind("]")
        if start < 0 or end <= start:
            raise ValueError("the reply wasn't a JSON list")
        raw = json.loads(content[start:end + 1])
    if isinstance(raw, dict):
        raw = next((v for v in raw.values() if isinstance(v, list)), None)
    if not isinstance(raw, list):
        raise ValueError("the reply wasn't a JSON list")
    return raw


def build_prompt(taste, count=SUGGEST_COUNT):
    """The user message: the viewer's history grouped by how they felt about it, then the ask."""
    lines = []
    for key, heading in TASTE_SECTIONS:
        titles = taste.get(key) or []
        if titles:
            lines.append(f"{heading}:\n" + "; ".join(titles))
    patterns = taste.get("patterns") or {}
    hints = [f"{label}: {', '.join(patterns[k])}" for k, label in (("genre", "genres"), ("keyword", "themes"))
             if patterns.get(k)]
    if hints:
        lines.append("Patterns in their history (for context; the titles above matter more): " + "; ".join(hints))
    if not lines:
        lines.append("They haven't watched or rated anything yet.")
    tv = max(1, round(count * TV_SHARE))
    discovery = max(1, round(count * DISCOVERY_SHARE))
    lines.append(
        f"Suggest {count} titles they haven't seen: about {count - tv} movies and {tv} TV shows, at least "
        f"{discovery} of them lesser-known (novelty \"discovery\"), at most one per franchise or creator, all "
        "already released. For each, \"because\" names one or two titles from their loved, liked or recent "
        "lists above, exactly as written there.")
    return "\n\n".join(lines)


class AiClient:
    def __init__(self, token, base_url="https://api.openai.com/v1", model="gpt-4o-mini"):
        self.token, self.base_url, self.model = token, base_url.rstrip("/"), model
        self.last_error = None   # why the last suggest() came back empty, in words for the AI page

    def _post(self, body):
        return post_json(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.token}"},
                         body=body)

    @property
    def _is_openai(self):
        return self.base_url.startswith("https://api.openai.com/")

    def _chat(self, messages, max_tokens, schema=None):
        """Sends what suits the model, then backs off if the API rejects a parameter by name:
        - reasoning models get max_completion_tokens (they reject max_tokens) and low reasoning_effort;
          everything else gets max_tokens, which most OpenAI-compatible providers still expect;
        - schema (OpenAI's API only) asks for structured output.
        A rejection that names max_tokens/max_completion_tokens swaps them; one naming reasoning_effort or
        response_format drops that parameter. Anything else is a real error and is raised."""
        reasoning = is_reasoning_model(self.model)
        body = {"model": self.model, "messages": messages,
                ("max_completion_tokens" if reasoning else "max_tokens"): max_tokens}
        if reasoning:
            body["reasoning_effort"] = REASONING_EFFORT
        if schema and self._is_openai:
            body["response_format"] = {"type": "json_schema", "json_schema": schema}
        for _ in range(4):
            try:
                return self._post(body)
            except Exception as e:
                message = str(e).lower()
                if "max_tokens" in message and "max_completion_tokens" in message:
                    old, new = (("max_tokens", "max_completion_tokens") if "max_tokens" in body
                                else ("max_completion_tokens", "max_tokens"))
                    body[new] = body.pop(old)
                elif "reasoning_effort" in message and "reasoning_effort" in body:
                    del body["reasoning_effort"]
                elif ("response_format" in message or "json_schema" in message) and "response_format" in body:
                    del body["response_format"]
                else:
                    raise
        return self._post(body)

    def test_connection(self):
        """Returns (ok, message) - never raises, so the Settings page can show it either way."""
        try:
            response = self._chat([{"role": "user", "content": "Reply with just: OK"}], max_tokens=5)
            response["choices"][0]["message"]["content"]
            return True, f"Connected - {self.model} responded"
        except Exception as e:
            return False, str(e)

    def suggest(self, taste, count=SUGGEST_COUNT):
        """taste: {section: ["Title (year)", ...]} for the keys in TASTE_SECTIONS (recommend.ai_taste builds
        it), plus optional "patterns": {"genre": [...], "keyword": [...]} from the taste profile.
        Returns a list of {"title", "year", "media_type", "because", "reason", "novelty"} dicts - unverified
        leads, not yet matched to a real TMDB entry (see recommend.py). Never raises; an empty list means
        "couldn't get suggestions right now", and last_error says why."""
        user_prompt = build_prompt(taste, count)
        self.last_error = None
        try:
            response = self._chat(
                [{"role": "system", "content": SYSTEM_PROMPT + JSON_FORMAT}, {"role": "user", "content": user_prompt}],
                max_tokens=SUGGEST_MAX_TOKENS, schema=SUGGESTIONS_SCHEMA)
        except Exception as e:
            self.last_error = f"the request to {self.model} failed: {e}"
            return []
        try:
            choice = response["choices"][0]
            content = (choice.get("message") or {}).get("content") or ""
        except (KeyError, IndexError, TypeError, AttributeError):
            self.last_error = "the provider's reply wasn't in the expected format"
            return []
        if not content.strip():
            if choice.get("finish_reason") == "length":
                self.last_error = f"{self.model} ran out of tokens before answering"
            else:
                self.last_error = f"{self.model} sent back an empty reply"
            return []
        try:
            raw = _parse_suggestions(content)
        except ValueError:
            self.last_error = f"{self.model}'s reply wasn't a list of titles"
            return []
        found = [r for r in raw if isinstance(r, dict) and r.get("title") and r.get("media_type") in ("movie", "tv")]
        if not found:
            self.last_error = f"{self.model} didn't suggest any movies or shows"
        return found
