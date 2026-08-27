import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import requests

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sources.huggingface_papers_source import (  # noqa: E402
    HUGGINGFACE_PAPERS_SOURCE_NAME,
    HuggingFacePapersFetchError,
    HuggingFacePapersSource,
)
from sources.search_agent import SearchAgent  # noqa: E402


def _entry(index: int, *, arxiv_id: str | None = None) -> dict:
    arxiv_id = arxiv_id or f"2608.{index:05d}"
    return {
        "paper": {
            "id": arxiv_id,
            "title": f"Paper {index}",
            "summary": f"Abstract {index}",
            "authors": [{"name": "Test Author"}],
            "publishedAt": "2026-08-10T00:00:00.000Z",
            "submittedOnDailyAt": "2026-08-12T00:00:00.000Z",
            "ai_summary": f"AI summary {index}",
        },
        "title": f"Paper {index}",
        "summary": f"Abstract {index}",
    }


def _next_link(day: str, page: int) -> str:
    return (
        f'<https://huggingface.co/api/daily_papers?date={day}&p={page}>; '
        'rel="next"'
    )


class _FakeResponse:
    def __init__(self, payload, link=""):
        self._payload = payload
        self.headers = {"Link": link}

    def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


class HuggingFacePapersFetchTests(unittest.TestCase):
    def _source(self, temp_dir: str, **kwargs) -> HuggingFacePapersSource:
        return HuggingFacePapersSource(
            Path(temp_dir),
            availability_lag_days=0,
            lookback_grace_days=0,
            request_interval_seconds=0,
            **kwargs,
        )

    def test_full_pagination_is_unbounded_and_empty_page_is_normal_termination(self):
        day = "2026-08-12"
        first = [_entry(index) for index in range(100)]
        second = [_entry(100)]
        requests_seen = []

        def fake_request(url, params=None):
            requests_seen.append((url, params))
            page = 0 if params is not None else int(url.rsplit("p=", 1)[1])
            if page == 0:
                return _FakeResponse(first, _next_link(day, 1))
            if page == 1:
                return _FakeResponse(second, _next_link(day, 2))
            if page == 2:
                return _FakeResponse([], "")
            self.fail(f"unexpected page {page}")

        with tempfile.TemporaryDirectory() as temp_dir:
            source = self._source(temp_dir)
            source._api_request = fake_request
            papers = source.fetch_papers(days=1, now=datetime(2026, 8, 12, tzinfo=timezone.utc))

        self.assertEqual(len(papers), 101)
        self.assertEqual(papers[0].paper_id, "hf:2608.00000")
        self.assertEqual(papers[-1].paper_id, "hf:2608.00100")
        self.assertTrue(all(paper.source == HUGGINGFACE_PAPERS_SOURCE_NAME for paper in papers))
        self.assertEqual(papers[0].arxiv_id, "2608.00000")
        self.assertEqual(papers[0].pdf_url, "https://arxiv.org/pdf/2608.00000.pdf")
        self.assertEqual(len(requests_seen), 3)
        self.assertEqual(requests_seen[0][1], {"date": day})

    def test_nonempty_page_without_next_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self._source(temp_dir)
            source._api_request = lambda *_args, **_kwargs: _FakeResponse([_entry(1)])
            with self.assertRaisesRegex(HuggingFacePapersFetchError, "未提供 next"):
                source.fetch_papers(days=1, now=datetime(2026, 8, 12, tzinfo=timezone.utc))

    def test_invalid_response_and_link_variants_fail_closed(self):
        cases = [
            (_FakeResponse({"not": "a list"}), "响应不是列表"),
            (_FakeResponse(ValueError("bad json")), "不是有效 JSON"),
            (_FakeResponse([_entry(1)], '<https://evil.example/api/daily_papers?date=2026-08-12&p=1>; rel="next"'), "next 分页链接无效"),
            (_FakeResponse([_entry(1)], _next_link("2026-08-11", 1)), "next 分页链接无效"),
            (_FakeResponse([_entry(1)], _next_link("2026-08-12", 0)), "分页循环"),
            (_FakeResponse([_entry(1)], _next_link("2026-08-12", 2)), "不是连续的下一页"),
        ]
        for response, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp_dir:
                source = self._source(temp_dir)
                source._api_request = lambda *_args, response=response, **_kwargs: response
                with self.assertRaisesRegex(HuggingFacePapersFetchError, message):
                    source.fetch_papers(days=1, now=datetime(2026, 8, 12, tzinfo=timezone.utc))

    def test_invalid_required_entry_fields_fail_closed(self):
        malformed = _entry(1)
        malformed["paper"] = {"id": "not-an-arxiv-id", "title": "x", "authors": []}

        with tempfile.TemporaryDirectory() as temp_dir:
            source = self._source(temp_dir)
            source._api_request = lambda *_args, **_kwargs: _FakeResponse(
                [malformed], _next_link("2026-08-12", 1)
            )
            with self.assertRaisesRegex(HuggingFacePapersFetchError, "arXiv ID 无效"):
                source.fetch_papers(days=1, now=datetime(2026, 8, 12, tzinfo=timezone.utc))

    def test_feed_lag_and_grace_select_only_stable_dates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = HuggingFacePapersSource(
                Path(temp_dir),
                availability_lag_days=2,
                lookback_grace_days=2,
                request_interval_seconds=0,
            )
            dates = source._feed_dates(3, now=datetime(2026, 8, 13, 12, tzinfo=timezone.utc))

        self.assertEqual(
            [day.isoformat() for day in dates],
            ["2026-08-11", "2026-08-10", "2026-08-09", "2026-08-08", "2026-08-07"],
        )

    def test_historical_range_fetches_each_feed_and_persists_source_date(self):
        requested_dates = []
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self._source(temp_dir)

            def entries(feed_date):
                requested_dates.append(feed_date)
                return [_entry(int(feed_date.strftime("%d")))]

            source._entries_for_feed_date = entries
            papers = source.fetch_papers_between(
                date(2026, 8, 10), date(2026, 8, 12)
            )

        self.assertEqual(
            requested_dates,
            [date(2026, 8, 12), date(2026, 8, 11), date(2026, 8, 10)],
        )
        self.assertEqual(
            {paper.source_date for paper in papers},
            {date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12)},
        )

    def test_processed_hf_items_use_own_history_and_are_not_returned_again(self):
        day = "2026-08-12"
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self._source(temp_dir)
            source._api_request = lambda *_args, **_kwargs: _FakeResponse(
                [_entry(1)], _next_link(day, 1)
            )
            # The extra empty page is intentionally returned through a small
            # dispatcher so complete pagination remains part of this test.
            calls = []

            def paged(url, params=None):
                calls.append((url, params))
                if len(calls) == 1:
                    return _FakeResponse([_entry(1)], _next_link(day, 1))
                return _FakeResponse([])

            source._api_request = paged
            first = source.fetch_papers(days=1, now=datetime(2026, 8, 12, tzinfo=timezone.utc))
            source.mark_as_processed(first[0].paper_id)
            calls.clear()
            second = source.fetch_papers(days=1, now=datetime(2026, 8, 12, tzinfo=timezone.utc))

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    @patch("sources.search_agent.HuggingFacePapersSource")
    def test_search_agent_initializes_hf_with_its_own_proxy_and_backend_mapping(self, source_cls):
        source_cls.return_value.session.proxies = {}
        fake_settings = type(
            "Settings",
            (),
            {
                "HUGGINGFACE_PAPERS_AVAILABILITY_LAG_DAYS": 4,
                "HUGGINGFACE_PAPERS_LOOKBACK_GRACE_DAYS": 3,
                "HUGGINGFACE_PAPERS_REQUEST_TIMEOUT_SECONDS": 40,
                "HUGGINGFACE_PAPERS_REQUEST_INTERVAL_SECONDS": 0.5,
                "get_proxy_dict": staticmethod(
                    lambda name: {"https": "http://proxy.test"}
                    if name == HUGGINGFACE_PAPERS_SOURCE_NAME
                    else None
                ),
            },
        )()
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.settings", fake_settings):
            agent = SearchAgent(
                Path(temp_dir),
                enabled_sources=[HUGGINGFACE_PAPERS_SOURCE_NAME],
                enable_semantic_scholar=False,
            )

        source_cls.assert_called_once_with(
            history_dir=Path(temp_dir),
            availability_lag_days=4,
            lookback_grace_days=3,
            request_timeout_seconds=40,
            request_interval_seconds=0.5,
        )
        self.assertEqual(agent.get_enabled_sources(), [HUGGINGFACE_PAPERS_SOURCE_NAME])
        self.assertIs(agent.get_source(HUGGINGFACE_PAPERS_SOURCE_NAME), source_cls.return_value)
        self.assertTrue(agent.can_download_pdf(HUGGINGFACE_PAPERS_SOURCE_NAME))
        self.assertEqual(source_cls.return_value.session.proxies["https"], "http://proxy.test")

    def test_search_agent_history_routing_keeps_hf_out_of_openalex_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            agent = SearchAgent(
                Path(temp_dir),
                enabled_sources=[HUGGINGFACE_PAPERS_SOURCE_NAME, "prl"],
                enable_semantic_scholar=False,
            )
            hf = agent.sources[HUGGINGFACE_PAPERS_SOURCE_NAME]
            openalex = agent.sources["openalex"]
            agent.mark_many_as_processed(
                {
                    HUGGINGFACE_PAPERS_SOURCE_NAME: ["hf:2608.00001"],
                    "prl": ["10.9999/example"],
                }
            )

        self.assertTrue(hf.is_processed("hf:2608.00001"))
        self.assertFalse(openalex.is_processed("hf:2608.00001"))
        self.assertTrue(openalex.is_processed("10.9999/example"))

    def test_network_errors_remain_fetch_errors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self._source(temp_dir)
            source._api_request = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                requests.RequestException("network unavailable")
            )
            with self.assertRaisesRegex(HuggingFacePapersFetchError, "network unavailable"):
                source.fetch_papers(days=1, now=datetime(2026, 8, 12, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
