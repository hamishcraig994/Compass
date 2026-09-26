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
    'You are a movie and TV recommendation assistant. Given a viewer\'s taste profile, suggest '
    'titles they have not seen that fit their taste. Reply with ONLY a JSON array, no other text '
    'and no markdown formatting, in this exact shape: '
    '[{"title": "...", "year": 2020, "media_type": "movie", "reason": "one short sentence"}]. '
    'media_type must be exactly "movie" or "tv". Never suggest a title already in their watch history.'
)


class AiClient:
    def __init__(self, token, base_url="https://api.openai.com/v1", model="gpt-4o-mini"):
        self.token, self.base_url, self.model = token, base_url.rstrip("/"), model

    def _post(self, body):
        return post_json(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.token}"},
                         body=body)

    def _chat(self, messages, max_tokens):
        """Tries max_tokens first, falls back to max_completion_tokens only if the API rejects it
        specifically for that reason. OpenAI's newer reasoning models (o1, o3, gpt-5, ...) require
        the new name and reject the old one outright; most other OpenAI-compatible providers
        (self-hosted, OpenRouter, etc.) still expect max_tokens, so neither is assumed up front."""
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens}
        try:
            return self._post(body)
        except Exception as e:
            message = str(e).lower()
            if "max_tokens" in message and "max_completion_tokens" in message:
                del body["max_tokens"]
                body["max_completion_tokens"] = max_tokens
                return self._post(body)
            raise

    def test_connection(self):
        """Returns (ok, message) - never raises, so the Settings page can show it either way."""
        try:
            response = self._chat([{"role": "user", "content": "Reply with just: OK"}], max_tokens=5)
            response["choices"][0]["message"]["content"]
            return True, f"Connected - {self.model} responded"
        except Exception as e:
            return False, str(e)

    def suggest(self, profile_summary, watched_titles, count=15):
        """profile_summary: profile.summary()'s shape ({"genre": [...], "keyword": [...], ...}).
        Returns a list of {"title", "year", "media_type", "reason"} dicts - unverified leads, not
        yet matched to a real TMDB entry (see recommend.py). Never raises; an empty list means
        "couldn't get suggestions right now", not "the AI found nothing worth suggesting"."""
        watched_text = ", ".join(watched_titles[:30]) or "nothing yet"
        user_prompt = (
            f"Taste profile:\n"
            f"Genres: {', '.join(profile_summary.get('genre', [])) or 'none yet'}\n"
            f"Themes: {', '.join(profile_summary.get('keyword', [])) or 'none yet'}\n"
            f"Directors/creators: {', '.join(profile_summary.get('director', [])) or 'none yet'}\n"
            f"Actors: {', '.join(profile_summary.get('actor', [])) or 'none yet'}\n"
            f"Already watched (never suggest these): {watched_text}\n\n"
            f"Suggest {count} movies and TV shows this person has not seen."
        )
        try:
            response = self._chat(
                [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
                max_tokens=1200)
            content = response["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.strip("`")
                content = content[4:] if content.lower().startswith("json") else content
            raw = json.loads(content)
            return [r for r in raw if isinstance(r, dict) and r.get("title") and r.get("media_type") in ("movie", "tv")]
        except Exception:
            return []
