import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.safe_url import safe_http_url  # noqa: E402


class SafeUrlTests(unittest.TestCase):
    def test_only_absolute_http_urls_are_renderable_links(self):
        self.assertEqual(
            safe_http_url(" https://arxiv.org/abs/2501.12345v1 "),
            "https://arxiv.org/abs/2501.12345v1",
        )
        self.assertEqual(safe_http_url("HTTP://example.test/paper"), "HTTP://example.test/paper")
        for unsafe in (
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "file:///etc/passwd",
            "/relative/path",
            "https:\\not-a-host",
            "https://example.test/\nsecond-line",
        ):
            with self.subTest(unsafe=unsafe):
                self.assertEqual(safe_http_url(unsafe), "")


if __name__ == "__main__":
    unittest.main()
