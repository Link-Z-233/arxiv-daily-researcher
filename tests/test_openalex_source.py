import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sources.openalex_source import OpenAlexFetchError, OpenAlexSource  # noqa: E402
from sources.search_agent import SearchAgent  # noqa: E402
from notifications.notifier import RunResult  # noqa: E402
from modes.daily_research import DailyResearchPipeline  # noqa: E402
from config import settings  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402


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


def _arxiv_metadata(arxiv_id: str, journal_code: str, journal_name: str, doi: str):
    """Return a journal record enriched with arXiv metadata."""
    from sources.base_source import PaperMetadata

    return PaperMetadata(
        paper_id=doi,
        title="arXiv enriched title",
        authors=["Test Author"],
        abstract="arXiv enriched abstract",
        published_date=datetime.now(),
        url=f"https://arxiv.org/abs/{arxiv_id}",
        source=journal_code,
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
        doi=doi,
        journal=journal_name,
        arxiv_id=arxiv_id,
        arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
    )


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

    def test_missing_abstract_is_valid_but_malformed_required_metadata_fails_closed(self):
        missing_abstract = _work(1)
        missing_abstract["abstract_inverted_index"] = None

        with tempfile.TemporaryDirectory() as temp_dir:
            source = OpenAlexSource(Path(temp_dir), journals=["prl"])
            source._api_request = lambda _url, _params: {"results": [missing_abstract]}
            papers = source.fetch_papers(days=1)

        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].abstract, "")

        malformed_cases = [
            ("id", None, "缺少 DOI 和有效 OpenAlex work ID"),
            ("id", "https://example.test/not-openalex", "OpenAlex work ID 无效"),
            ("doi", "", "DOI 不是非空字符串"),
            ("title", "<b> </b>", "缺少标题"),
            ("publication_date", "not-a-date", "publication_date 格式无效"),
            ("authorships", {"not": "a list"}, "authorships 不是列表"),
            ("abstract_inverted_index", {"bad": [0, "1"]}, "摘要倒排索引"),
        ]
        for field, value, error in malformed_cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp_dir:
                work = _work(2)
                if field == "id":
                    work["doi"] = None
                work[field] = value
                source = OpenAlexSource(Path(temp_dir), journals=["prl"])
                source._api_request = lambda _url, _params, work=work: {"results": [work]}
                with self.assertRaisesRegex(OpenAlexFetchError, error):
                    source.fetch_papers(days=1)

    def test_valid_openalex_id_is_a_stable_doi_fallback(self):
        work = _work(1)
        work["doi"] = None
        work["id"] = "W12345"

        with tempfile.TemporaryDirectory() as temp_dir:
            source = OpenAlexSource(Path(temp_dir), journals=["prl"])
            source._api_request = lambda _url, _params: {"results": [work]}
            papers = source.fetch_papers(days=1)

        self.assertEqual(papers[0].paper_id, "openalex:W12345")
        self.assertEqual(papers[0].url, "https://openalex.org/W12345")
        self.assertIsNone(papers[0].doi)

    def test_malformed_entry_fails_entire_journal_before_a_watermark_can_advance(self):
        valid = _work(1)
        invalid = _work(2)
        invalid["publication_date"] = "2026-99-99"

        with tempfile.TemporaryDirectory() as temp_dir:
            source = OpenAlexSource(Path(temp_dir), journals=["prl"])
            source._api_request = lambda _url, _params: {"results": [valid, invalid]}
            with self.assertRaisesRegex(OpenAlexFetchError, "第 1 页条目 2"):
                source.fetch_papers(days=1)

        # The exception is the important contract: no partial list escapes
        # for a caller to mistake as a complete source scan.

    def test_arxiv_enriched_journal_paper_keeps_doi_as_its_history_identity(self):
        work = _work(1)
        work["locations"] = [
            {
                "source": {"display_name": "arXiv"},
                "landing_page_url": "https://arxiv.org/abs/2501.12345v2",
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            source = OpenAlexSource(Path(temp_dir), journals=["prl"])
            source._api_request = lambda _url, _params: {"results": [work]}
            source._fetch_from_arxiv = lambda arxiv_id, journal_code, journal_name, doi: _arxiv_metadata(
                arxiv_id, journal_code, journal_name, doi
            )

            first_scan = source.fetch_papers(days=1)
            self.assertEqual(first_scan[0].paper_id, work["doi"])
            source.mark_as_processed(first_scan[0].paper_id)
            second_scan = source.fetch_papers(days=1)

        self.assertEqual(second_scan, [])

    def test_legacy_arxiv_key_suppresses_one_time_duplicate_journal_delivery(self):
        work = _work(1)
        work["locations"] = [
            {
                "source": {"display_name": "arXiv"},
                "landing_page_url": "https://arxiv.org/abs/2501.12345v2",
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            source = OpenAlexSource(Path(temp_dir), journals=["prl"])
            source.history["2501.12345v2"] = "2026-08-01T00:00:00"
            source._api_request = lambda _url, _params: {"results": [work]}
            source._fetch_from_arxiv = lambda *_args: self.fail("legacy item must be skipped")

            papers = source.fetch_papers(days=1)

        self.assertEqual(papers, [])

    def test_sqlite_mode_does_not_let_legacy_arxiv_history_hide_a_candidate(self):
        work = _work(1)
        work["locations"] = [
            {
                "source": {"display_name": "arXiv"},
                "landing_page_url": "https://arxiv.org/abs/2501.12345v2",
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            source = OpenAlexSource(Path(temp_dir), journals=["prl"])
            source.history["2501.12345v2"] = "2026-08-01T00:00:00"
            source.set_history_filtering_enabled(False)
            source._api_request = lambda _url, _params: {"results": [work]}
            source._fetch_from_arxiv = (
                lambda arxiv_id, journal_code, journal_name, doi: _arxiv_metadata(
                    arxiv_id, journal_code, journal_name, doi
                )
            )

            papers = source.fetch_papers(days=1)

        self.assertEqual([work["doi"]], [paper.paper_id for paper in papers])

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

    def test_pipeline_returns_failed_result_and_keeps_openalex_watermark_on_fetch_error(self):
        class _SearchAgent:
            def __init__(self, **_kwargs):
                pass

            def get_enabled_sources(self):
                return ["prl"]

            def fetch_all_papers(self, **_kwargs):
                raise OpenAlexFetchError("malformed upstream work")

        class _KeywordAgent:
            def get_all_keywords(self):
                return {"quantum": 1.0}

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "daily.db"
            fake_settings = SimpleNamespace(
                TOKEN_TRACKING_ENABLED=False,
                DAILY_RESEARCH_PERSISTENCE_ENABLED=True,
                DAILY_RESEARCH_DB_PATH=db_path,
                ENABLE_NOTIFICATIONS=False,
                ENABLED_SOURCES=["prl"],
                TARGET_DOMAINS=[],
                TARGET_JOURNALS=["prl"],
                SEARCH_DAYS=1,
                ENABLE_REFERENCE_EXTRACTION=False,
                PRIMARY_KEYWORDS=["quantum"],
                PRIMARY_KEYWORD_WEIGHT=1.0,
                SCORE_STRATEGY="core_relevance_v2",
                CORE_RELEVANCE_THRESHOLD=6.0,
                CORE_KEYWORD_MIN_SCORE=8.0,
                HISTORY_DIR=Path(temp_dir) / "history",
                OPENALEX_EMAIL="",
                OPENALEX_API_KEY="",
                ENABLE_SEMANTIC_SCHOLAR_TLDR=False,
                SEMANTIC_SCHOLAR_API_KEY="",
                KEYWORD_TRACKER_ENABLED=False,
                DAILY_ENABLE_DEEP_ANALYSIS=False,
                normalized_score_strategy=lambda: "core_relevance_v2",
            )
            with (
                patch("modes.daily_research.settings", fake_settings),
                patch("modes.daily_research.SearchAgent", _SearchAgent),
                patch("modes.daily_research.KeywordAgent", _KeywordAgent),
                patch("modes.daily_research.deliver_pending_after_report_syncs", return_value={"claimed": 0}),
            ):
                result = DailyResearchPipeline().run()

            self.assertIsInstance(result, RunResult)
            self.assertFalse(result.success)
            self.assertIn("OpenAlex 期刊抓取失败", result.error_message)
            store = DailyResearchStore(db_path)
            recent = store.get_recent_runs(1)[0]
            self.assertEqual(recent["status"], "failed")
            self.assertEqual(recent["scanned_sources"], ["prl"])
            self.assertIsNone(store.get_scan_watermark("prl"))


if __name__ == "__main__":
    unittest.main()
