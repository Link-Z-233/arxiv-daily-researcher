import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.safe_download import ExternalDownloadError, download_external_bytes  # noqa: E402
from utils.safe_url import safe_external_http_url  # noqa: E402
from parsers.mineru_parser import MineruParser  # noqa: E402


class _Response:
    def __init__(self, *, status_code=200, headers=None, chunks=(), error=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = list(chunks)
        self._error = error
        self.closed = False

    def raise_for_status(self):
        if self._error is not None:
            raise self._error
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1):
        del chunk_size
        yield from self._chunks

    def close(self):
        self.closed = True


class SafeExternalUrlTests(unittest.TestCase):
    def test_external_fetch_urls_reject_credentials_and_non_global_numeric_hosts(self):
        self.assertEqual(
            safe_external_http_url("https://arxiv.org/pdf/2501.12345.pdf"),
            "https://arxiv.org/pdf/2501.12345.pdf",
        )
        for url in (
            "http://127.0.0.1/admin",
            "http://127.1/admin",
            "http://0x7f000001/admin",
            "http://[::1]/admin",
            "http://169.254.169.254/latest/meta-data",
            "http://localhost/admin",
            "http://service.localhost/admin",
            "https://user:pass@example.test/paper.pdf",
            "file:///etc/passwd",
        ):
            with self.subTest(url=url):
                self.assertEqual(safe_external_http_url(url), "")


class BoundedExternalDownloadTests(unittest.TestCase):
    def test_valid_pdf_stream_is_bounded_and_closes_response(self):
        response = _Response(
            headers={"Content-Length": "14"},
            chunks=[b"%PDF-1.7\nbody"],
        )
        calls = []

        def request(url, **kwargs):
            calls.append((url, kwargs))
            return response

        content = download_external_bytes(
            "https://example.test/paper.pdf",
            request,
            max_bytes=100,
            required_magic=b"%PDF-",
        )
        self.assertEqual(content, b"%PDF-1.7\nbody")
        self.assertTrue(response.closed)
        self.assertFalse(calls[0][1]["allow_redirects"])
        self.assertTrue(calls[0][1]["stream"])

    def test_declared_or_streamed_oversize_is_rejected_before_parser(self):
        cases = [
            _Response(headers={"Content-Length": "101"}, chunks=[b"%PDF-"]),
            _Response(headers={}, chunks=[b"%PDF-", b"x" * 100]),
        ]
        for response in cases:
            with self.subTest(headers=response.headers):
                with self.assertRaisesRegex(ExternalDownloadError, "超过允许上限"):
                    download_external_bytes(
                        "https://example.test/paper.pdf",
                        lambda *_args, response=response, **_kwargs: response,
                        max_bytes=100,
                        required_magic=b"%PDF-",
                    )
                self.assertTrue(response.closed)

    def test_non_pdf_and_invalid_content_length_are_rejected(self):
        for response, error in (
            (_Response(chunks=[b"<html>not pdf</html>"]), "不是预期的文件格式"),
            (_Response(headers={"Content-Length": "not-a-number"}, chunks=[b"%PDF-"]), "Content-Length 无效"),
        ):
            with self.subTest(error=error):
                with self.assertRaisesRegex(ExternalDownloadError, error):
                    download_external_bytes(
                        "https://example.test/paper.pdf",
                        lambda *_args, response=response, **_kwargs: response,
                        max_bytes=100,
                        required_magic=b"%PDF-",
                    )
                self.assertTrue(response.closed)

    def test_pdf_magic_may_span_transport_chunks(self):
        response = _Response(chunks=[b"prefix%P", b"DF-1.7"])
        content = download_external_bytes(
            "https://example.test/paper.pdf",
            lambda *_args, **_kwargs: response,
            max_bytes=100,
            required_magic=b"%PDF-",
        )
        self.assertEqual(content, b"prefix%PDF-1.7")

    def test_redirect_target_is_checked_before_following(self):
        redirect = _Response(
            status_code=302,
            headers={"Location": "http://127.0.0.1/internal"},
        )
        calls = []

        def request(url, **kwargs):
            calls.append((url, kwargs))
            return redirect

        with self.assertRaisesRegex(ExternalDownloadError, "重定向目标"):
            download_external_bytes(
                "https://example.test/paper.pdf", request, max_bytes=100, required_magic=b"%PDF-"
            )
        self.assertEqual(len(calls), 1)
        self.assertTrue(redirect.closed)

    def test_relative_public_redirect_is_followed_with_every_hop_checked(self):
        redirect = _Response(status_code=302, headers={"Location": "/files/paper.pdf"})
        final = _Response(chunks=[b"%PDF-1.7"])
        responses = [redirect, final]
        calls = []

        def request(url, **kwargs):
            calls.append((url, kwargs))
            return responses.pop(0)

        content = download_external_bytes(
            "https://example.test/original.pdf", request, max_bytes=100, required_magic=b"%PDF-"
        )
        self.assertEqual(content, b"%PDF-1.7")
        self.assertEqual(
            [call[0] for call in calls],
            ["https://example.test/original.pdf", "https://example.test/files/paper.pdf"],
        )
        self.assertTrue(redirect.closed)
        self.assertTrue(final.closed)


class MineruDownloadBoundaryTests(unittest.TestCase):
    def test_mineru_refuses_unsafe_pdf_before_submitting_its_secret_bearing_request(self):
        fake_settings = SimpleNamespace(
            MINERU_API_KEY="configured-key",
            MINERU_MODEL_VERSION="pipeline",
            MINERU_POLL_INTERVAL=1,
            MINERU_POLL_TIMEOUT=1,
        )
        with patch("parsers.mineru_parser.settings", fake_settings), patch(
            "parsers.mineru_parser.requests.post"
        ) as post:
            parser = MineruParser()
            self.assertIsNone(parser._submit_task("http://127.0.0.1/private.pdf"))
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
