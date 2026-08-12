import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sources.openalex_source import OpenAlexFetchError, OpenAlexSource  # noqa: E402
from sources.search_agent import SearchAgent  # noqa: E402


def _work(index: int) -> dict:
    """Return the smallest valid OpenAlex work used by pagination tests."""
    return {
        "id": f"https://openalex.org/W{index}",
        "doi": f"https://doi.org/10.9999/test.{index}",
        "title": f"Paper {index}",
        "authorships": [{"author": {"display_name": "Test Author"}}],
        "abstract_inverted_index": {"test": [0], "abstract": [1]},
        "publication_date": "2026-08-12",
        "primary_location": {},
        "open_access": {},
        "locations": [],
    }


class OpenAlexFetchTests(unittest.TestCase):
    def test_daily_scan_is_cursor_paginated_and_not_limited_by_max_results(self):
        first_page = [_work(index) for index in range(200)]
        second_page = [_work(index) for index in range(200, 201)]
        requests = []

        def fake_api_request(_url, params):
            requests.append(params.copy())
            if params["cursor"] == "*":
                return {"results": first_page, "meta": {"next_cursor": "next-page"}}
            if params["cursor"] == "next-page":
                return {"results": second_page, "meta": {"next_cursor": "unused"}}
            self.fail(f"unexpected cursor: {params['cursor']}")

        with tempfile.TemporaryDirectory() as temp_dir:
            source = OpenAlexSource(Path(temp_dir), journals=["prl"], max_results=1)
            source._api_request = fake_api_request
            papers = source.fetch_papers(days=1)

        self.assertEqual(len(papers), 201)
        self.assertEqual([request["cursor"] for request in requests], ["*", "next-page"])
        self.assertTrue(all(request["per-page"] == 200 for request in requests))
        self.assertTrue(all("page" not in request for request in requests))

    def test_missing_continuation_on_full_page_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = OpenAlexSource(Path(temp_dir), journals=["prl"])
            source._api_request = lambda _url, _params: {"results": [_work(i) for i in range(200)]}
            with self.assertRaisesRegex(OpenAlexFetchError, "next_cursor"):
                source.fetch_papers(days=1)

    def test_second_journal_failure_does_not_return_partial_first_journal_results(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = OpenAlexSource(Path(temp_dir), journals=["prl", "pra"])

            def fake_fetch(*, issn_list, journal_code, journal_name, from_date):
                if journal_code == "prl":
                    return [_work(1)]
                raise RuntimeError("network unavailable")

            source._fetch_journal_papers = fake_fetch
            with self.assertRaisesRegex(OpenAlexFetchError, "pra .*network unavailable"):
                source.fetch_papers(days=1)

    def test_unknown_journal_and_malformed_results_are_configuration_or_fetch_errors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = OpenAlexSource(Path(temp_dir), journals=["not-a-journal"])
            with self.assertRaisesRegex(OpenAlexFetchError, "未知期刊"):
                source.fetch_papers(days=1)

            source = OpenAlexSource(Path(temp_dir), journals=["prl"])
            source._api_request = lambda _url, _params: {"results": {"not": "a list"}}
            with self.assertRaisesRegex(OpenAlexFetchError, "results"):
                source.fetch_papers(days=1)

    def test_search_agent_rejects_unknown_source_instead_of_ignoring_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "未知数据源代码"):
                SearchAgent(
                    Path(temp_dir),
                    enabled_sources=["arxiv", "typo-source"],
                    enable_semantic_scholar=False,
                )

    def test_search_agent_rejects_unknown_journal_configuration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "未知 OpenAlex 期刊代码"):
                SearchAgent(
                    Path(temp_dir),
                    enabled_sources=["arxiv"],
                    journals=["typo-journal"],
                    enable_semantic_scholar=False,
                )


if __name__ == "__main__":
    unittest.main()
