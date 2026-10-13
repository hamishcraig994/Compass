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


class TestModelTuning(unittest.TestCase):
    """gpt-6-luna and other OpenAI reasoning models: max_completion_tokens, low effort, structured output."""

    def body_for(self, client, side_effect=None):
        sent = []

        def fake_post(url, headers=None, body=None):
            sent.append(dict(body))
            if side_effect:
                side_effect(body)
            return chat_response(json.dumps({"suggestions": [{"title": "Arrival", "year": 2016,
                                                              "media_type": "movie", "reason": "x"}]}))
        with mock.patch.object(ai, "post_json", side_effect=fake_post):
            result = client.suggest(PROFILE, [])
        return sent, result

    def test_reasoning_model_on_openai_gets_the_tuned_request(self):
        sent, result = self.body_for(AiClient("k", model="gpt-6-luna"))
        self.assertEqual(len(sent), 1)
        body = sent[0]
        self.assertNotIn("max_tokens", body)
        self.assertGreaterEqual(body["max_completion_tokens"], 4000)
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertEqual(body["response_format"]["type"], "json_schema")
        schema = body["response_format"]["json_schema"]
        self.assertTrue(schema["strict"])
        self.assertEqual(schema["schema"]["properties"]["suggestions"]["items"]["properties"]["media_type"]["enum"],
                         ["movie", "tv"])
        self.assertEqual(result, [{"title": "Arrival", "year": 2016, "media_type": "movie", "reason": "x"}])

    def test_reasoning_prefixes(self):
        for model in ("gpt-6-luna", "GPT-5", "gpt-5-mini", "o1", "o3-mini", "o4-mini"):
            self.assertTrue(ai.is_reasoning_model(model), model)
        for model in ("gpt-4o-mini", "gpt-4.1", "llama3", "mistral-large"):
            self.assertFalse(ai.is_reasoning_model(model), model)

    def test_other_providers_get_no_schema_and_plain_max_tokens(self):
        sent, _ = self.body_for(AiClient("k", base_url="https://openrouter.ai/api/v1", model="llama3"))
        self.assertIn("max_tokens", sent[0])
        self.assertNotIn("response_format", sent[0])
        self.assertNotIn("reasoning_effort", sent[0])
        self.assertIn('{"suggestions"', sent[0]["messages"][0]["content"])

    def test_rejected_parameters_are_dropped_one_at_a_time(self):
        def reject(body):
            if "reasoning_effort" in body:
                raise RuntimeError("HTTP 400: Unsupported parameter: 'reasoning_effort'")
            if "response_format" in body:
                raise RuntimeError("HTTP 400: Invalid parameter: 'response_format' of type 'json_schema'")
        sent, result = self.body_for(AiClient("k", model="o1-mini"), side_effect=reject)
        self.assertEqual(len(sent), 3)
        self.assertNotIn("reasoning_effort", sent[2])
        self.assertNotIn("response_format", sent[2])
        self.assertIn("max_completion_tokens", sent[2])
        self.assertEqual(len(result), 1)

    def test_unrelated_errors_are_not_retried(self):
        client = AiClient("k", model="gpt-6-luna")
        with mock.patch.object(ai, "post_json", side_effect=RuntimeError("HTTP 401: invalid_api_key")) as post:
            self.assertEqual(client.suggest(PROFILE, []), [])
        self.assertEqual(post.call_count, 1)
        self.assertIn("invalid_api_key", client.last_error)


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

    def test_finds_the_array_inside_surrounding_text_or_a_wrapping_object(self):
        raw = [{"title": "Arrival", "year": 2016, "media_type": "movie"}]
        for content in ("Here you go:\n" + json.dumps(raw) + "\nEnjoy!", json.dumps({"recommendations": raw})):
            with mock.patch.object(ai, "post_json", return_value=chat_response(content)):
                self.assertEqual(self.client.suggest(PROFILE, []), raw, content)
            self.assertIsNone(self.client.last_error)

    def test_last_error_explains_an_empty_result(self):
        cases = [
            (dict(side_effect=RuntimeError("401 Unauthorized")), "request to gpt-4o-mini failed: 401 Unauthorized"),
            (dict(return_value=chat_response("not json at all")), "wasn't a list of titles"),
            (dict(return_value=chat_response("[]")), "didn't suggest any movies or shows"),
            (dict(return_value={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}),
             "ran out of tokens"),
            (dict(return_value={"choices": [{"message": {"content": None}, "finish_reason": "stop"}]}),
             "empty reply"),
            (dict(return_value={"error": "nope"}), "expected format"),
        ]
        for kwargs, expected in cases:
            with mock.patch.object(ai, "post_json", **kwargs):
                self.assertEqual(self.client.suggest(PROFILE, []), [])
            self.assertIn(expected, self.client.last_error)

    def test_asks_for_enough_tokens_for_reasoning_models(self):
        with mock.patch.object(ai, "post_json", return_value=chat_response("[]")) as post:
            self.client.suggest(PROFILE, [])
        self.assertGreaterEqual(post.call_args.kwargs["body"]["max_tokens"], 4000)

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
