import io
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from http_util import get_json, post_json

# Every other test in this suite mocks get_json/post_json themselves at the module boundary -
# this file is the one place that actually exercises urllib's error handling, which is exactly
# where a real bug slipped through: HTTPError's own str() is just "HTTP Error 400: Bad Request",
# with none of the response body an API actually puts its useful detail in.


def http_error(code, body=b"", reason="Bad Request", headers=None):
    return urllib.error.HTTPError(url="http://x", code=code, msg=reason,
                                  hdrs=headers or {}, fp=io.BytesIO(body))


class TestGetJsonErrors(unittest.TestCase):
    def test_a_4xx_error_includes_the_response_body_not_just_the_status_line(self):
        body = b'{"error": {"message": "Unsupported parameter: max_tokens. Use max_completion_tokens instead."}}'
        with mock.patch("urllib.request.urlopen", side_effect=http_error(400, body)):
            with self.assertRaises(RuntimeError) as ctx:
                get_json("http://x")
        self.assertIn("max_tokens", str(ctx.exception))
        self.assertIn("max_completion_tokens", str(ctx.exception))
        self.assertIn("400", str(ctx.exception))

    def test_falls_back_to_the_reason_phrase_when_the_body_is_empty(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(404, b"", reason="Not Found")):
            with self.assertRaises(RuntimeError) as ctx:
                get_json("http://x")
        self.assertIn("Not Found", str(ctx.exception))

    def test_long_bodies_are_truncated(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(400, b"x" * 5000)):
            with self.assertRaises(RuntimeError) as ctx:
                get_json("http://x")
        self.assertLess(len(str(ctx.exception)), 600)

    def test_429_is_retried_then_raises_with_body_once_exhausted(self):
        body = b'{"error": "rate limited"}'
        with mock.patch("urllib.request.urlopen", side_effect=http_error(429, body)), \
             mock.patch("time.sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                get_json("http://x", retries=1)
        self.assertIn("rate limited", str(ctx.exception))

    def test_a_4xx_that_is_not_retried_fails_on_the_first_attempt(self):
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(1)
            raise http_error(400, b"bad request")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(RuntimeError):
                get_json("http://x", retries=2)
        self.assertEqual(len(calls), 1)


class TestPostJsonErrors(unittest.TestCase):
    def test_error_includes_the_response_body(self):
        body = b'{"error": {"message": "Invalid API key"}}'
        with mock.patch("urllib.request.urlopen", side_effect=http_error(401, body)):
            with self.assertRaises(RuntimeError) as ctx:
                post_json("http://x", body={"a": 1})
        self.assertIn("Invalid API key", str(ctx.exception))

    def test_post_json_does_not_retry(self):
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(1)
            raise http_error(500, b"server error")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(RuntimeError):
                post_json("http://x", body={})
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
