import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai
from ai import AiClient

PROFILE = {"genre": ["Science Fiction"], "keyword": ["time travel"], "director": ["Denis Villeneuve"], "actor": []}


def chat_response(content):
    return {"choices": [{"message": {"content": content}}]}


class TestChatFallback(unittest.TestCase):
    """Mirrors the exact max_tokens/max_completion_tokens fix already shipped for Recomendarr -
    same bug class, fixed here from the start instead of after the fact."""

    def setUp(self):
        self.client = AiClient("key123")

    def test_uses_max_tokens_and_does_not_retry_when_accepted(self):
        with mock.patch.object(ai, "post_json", return_value=chat_response("ok")) as post:
            self.client._chat([{"role": "user", "content": "hi"}], max_tokens=50)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["body"]["max_tokens"], 50)
        self.assertNotIn("max_completion_tokens", post.call_args.kwargs["body"])

    def test_falls_back_to_max_completion_tokens_on_that_specific_error(self):
        rejection = RuntimeError("Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead.")
        with mock.patch.object(ai, "post_json", side_effect=[rejection, chat_response("ok")]) as post:
            self.client._chat([{"role": "user", "content": "hi"}], max_tokens=50)
        self.assertEqual(post.call_count, 2)
        self.assertNotIn("max_tokens", post.call_args.kwargs["body"])
        self.assertEqual(post.call_args.kwargs["body"]["max_completion_tokens"], 50)

    def test_does_not_retry_on_an_unrelated_error(self):
        with mock.patch.object(ai, "post_json", side_effect=RuntimeError("401 Unauthorized")) as post:
            with self.assertRaises(RuntimeError):
                self.client._chat([{"role": "user", "content": "hi"}], max_tokens=50)
        self.assertEqual(post.call_count, 1)


class TestChatFallbackEndToEnd(unittest.TestCase):
    """The layers above (this class's siblings, and test_http_util.py) each mock one boundary -
    this one mocks only urllib.request.urlopen, the real bottom, to prove ai.py's fallback and
    http_util.py's error-body surfacing actually compose correctly. This is the exact failure this
    was written to catch: without http_util.py surfacing the response body, ai.py's fallback check
    (which greps the error text for "max_tokens") had nothing to match against - a real model
    rejecting max_tokens would have surfaced as a bare "HTTP Error 400: Bad Request" instead of
    quietly retrying with the right parameter name."""

    def test_real_400_body_triggers_the_fallback_and_recovers(self):
        rejection_body = (b'{"error": {"message": "Unsupported parameter: \'max_tokens\' is not supported '
                          b'with this model. Use \'max_completion_tokens\' instead.", "code": "unsupported_parameter"}}')

        def fake_urlopen(request, timeout=None):
            if b'"max_tokens"' in request.data:
                raise urllib.error.HTTPError("http://x", 400, "Bad Request", {}, io.BytesIO(rejection_body))
            return io.BytesIO(json.dumps(chat_response("OK")).encode())

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok, msg = AiClient("key", model="o1-mini").test_connection()
        self.assertTrue(ok)
        self.assertIn("o1-mini", msg)


class TestTestConnection(unittest.TestCase):
    def test_returns_ok_and_message_on_success(self):
        with mock.patch.object(ai, "post_json", return_value=chat_response("OK")):
            ok, msg = AiClient("key").test_connection()
        self.assertTrue(ok)
        self.assertIn("gpt-4o-mini", msg)

    def test_returns_failure_message_without_raising(self):
        with mock.patch.object(ai, "post_json", side_effect=RuntimeError("invalid_api_key")):
            ok, msg = AiClient("bad-key").test_connection()
        self.assertFalse(ok)
        self.assertIn("invalid_api_key", msg)


class TestSuggest(unittest.TestCase):
    def setUp(self):
        self.client = AiClient("key123")

    def test_parses_a_plain_json_array(self):
        raw = [{"title": "Arrival", "year": 2016, "media_type": "movie", "reason": "cerebral sci-fi"}]
        with mock.patch.object(ai, "post_json", return_value=chat_response(json.dumps(raw))):
            result = self.client.suggest(PROFILE, ["Interstellar"])
        self.assertEqual(result, raw)

    def test_strips_markdown_code_fences(self):
        raw = [{"title": "Arrival", "year": 2016, "media_type": "movie", "reason": "x"}]
        fenced = "```json\n" + json.dumps(raw) + "\n```"
        with mock.patch.object(ai, "post_json", return_value=chat_response(fenced)):
            result = self.client.suggest(PROFILE, [])
        self.assertEqual(result, raw)

    def test_filters_out_entries_missing_a_title_or_with_a_bad_media_type(self):
        raw = [{"title": "Arrival", "media_type": "movie"}, {"media_type": "movie"},  # no title
              {"title": "X", "media_type": "podcast"}]  # not movie/tv
        with mock.patch.object(ai, "post_json", return_value=chat_response(json.dumps(raw))):
            result = self.client.suggest(PROFILE, [])
        self.assertEqual(result, [{"title": "Arrival", "media_type": "movie"}])

    def test_returns_empty_list_on_invalid_json_instead_of_raising(self):
        with mock.patch.object(ai, "post_json", return_value=chat_response("not json at all")):
            result = self.client.suggest(PROFILE, [])
        self.assertEqual(result, [])

    def test_returns_empty_list_when_the_request_itself_fails(self):
        with mock.patch.object(ai, "post_json", side_effect=RuntimeError("down")):
            result = self.client.suggest(PROFILE, [])
        self.assertEqual(result, [])

    def test_prompt_includes_the_profile_and_watch_history(self):
        with mock.patch.object(ai, "post_json", return_value=chat_response("[]")) as post:
            self.client.suggest(PROFILE, ["Interstellar", "Arrival"], count=5)
        user_message = post.call_args.kwargs["body"]["messages"][1]["content"]
        self.assertIn("Science Fiction", user_message)
        self.assertIn("Denis Villeneuve", user_message)
        self.assertIn("Interstellar", user_message)
        self.assertIn("5", user_message)


if __name__ == "__main__":
    unittest.main()
