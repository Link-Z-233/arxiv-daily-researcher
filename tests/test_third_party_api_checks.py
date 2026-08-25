"""Unit tests for read-only third-party API connection diagnostics."""

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.config_io import (  # noqa: E402
    validate_openalex_connection,
    validate_semantic_scholar_connection,
)


class _Response:
    def __init__(self, payload: bytes, headers=None):
        self._payload = payload
        self.headers = headers or {}

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class ThirdPartyApiCheckTests(unittest.TestCase):
    def test_openalex_test_uses_bearer_auth_without_leaking_key_into_url(self):
        response = _Response(b'{"results": [{"id": "https://openalex.org/W1"}]}', {
            "X-RateLimit-Remaining": "999",
        })
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            ok, message = validate_openalex_connection("secret-key")

        request = urlopen.call_args.args[0]
        self.assertTrue(ok)
        self.assertIn("999", message)
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-key")
        self.assertNotIn("secret-key", request.full_url)

    def test_openalex_invalid_key_has_a_clear_non_secret_error(self):
        error = HTTPError("https://api.openalex.org/works", 401, "Unauthorized", {}, io.BytesIO())
        with patch("urllib.request.urlopen", side_effect=error):
            ok, message = validate_openalex_connection("secret-key")

        self.assertFalse(ok)
        self.assertIn("无效", message)
        self.assertNotIn("secret-key", message)

    def test_semantic_scholar_test_uses_api_key_header(self):
        response = _Response(b'{"title": "Attention Is All You Need"}')
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            ok, message = validate_semantic_scholar_connection("secret-key")

        request = urlopen.call_args.args[0]
        self.assertTrue(ok)
        self.assertIn("有效", message)
        self.assertEqual(request.get_header("X-api-key"), "secret-key")

    def test_semantic_scholar_rate_limit_explains_anonymous_and_key_cases(self):
        error = HTTPError(
            "https://api.semanticscholar.org/graph/v1/paper/ARXIV:1706.03762",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            ok, message = validate_semantic_scholar_connection("")

        self.assertFalse(ok)
        self.assertIn("限流", message)
        self.assertIn("匿名", message)


if __name__ == "__main__":
    unittest.main()
