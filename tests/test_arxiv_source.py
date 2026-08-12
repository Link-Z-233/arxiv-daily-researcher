import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import arxiv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sources.arxiv_source import ArxivSource, normalize_arxiv_domains  # noqa: E402
from sources.search_agent import SearchAgent  # noqa: E402


class _FakeResult:
    def __init__(self, paper_id: str, published: datetime, updated: datetime):
        self.entry_id = f"https://arxiv.org/abs/{paper_id}"
        self.published = published
        self.updated = updated
        self.title = paper_id
        self.summary = f"Abstract for {paper_id}"
        self.authors = [SimpleNamespace(name="Test Author")]
        self.pdf_url = f"https://arxiv.org/pdf/{paper_id}.pdf"
        self.doi = None
        self.categories = ["cs.AI"]

    def get_short_id(self):
        return self.entry_id.rsplit("/", 1)[-1]


class _FakeClient:
    def __init__(self, submitted, updated):
        self.submitted = submitted
        self.updated = updated
        self.searches = []

    def results(self, search):
        self.searches.append(search)
        if search.sort_by is arxiv.SortCriterion.SubmittedDate:
            return iter(self.submitted)
        return iter(self.updated)


class _FailingSource:
    display_name = "fake"

    def fetch_papers(self, **_kwargs):
        raise RuntimeError("network unavailable")


class ArxivFetchTests(unittest.TestCase):
    def test_empty_or_malformed_arxiv_domains_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "未配置目标领域"):
            normalize_arxiv_domains([])
        with self.assertRaisesRegex(ValueError, "无效的 ArXiv 领域代码"):
            normalize_arxiv_domains(["cs.AI OR all:electron"])

        with tempfile.TemporaryDirectory() as temp_dir:
            source = ArxivSource(Path(temp_dir))
            with self.assertRaisesRegex(ValueError, "未配置目标领域"):
                source.fetch_papers(days=1, domains=[], fetch_timeout_seconds=10)

    def test_search_agent_rejects_empty_enabled_sources_or_arxiv_domains(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "至少启用一个"):
                SearchAgent(
                    history_dir=Path(temp_dir),
                    enabled_sources=[],
                    enable_semantic_scholar=False,
                )
            with self.assertRaisesRegex(ValueError, "未配置目标领域"):
                SearchAgent(
                    history_dir=Path(temp_dir),
                    enabled_sources=["arxiv"],
                    arxiv_domains=[],
                    enable_semantic_scholar=False,
                )

    @patch("sources.search_agent.ArxivSource")
    def test_search_agent_uses_default_domain_only_when_omitted(self, arxiv_source_cls):
        fake_source = arxiv_source_cls.return_value
        fake_source.display_name = "ArXiv"
        fake_source.fetch_papers.return_value = []
        with tempfile.TemporaryDirectory() as temp_dir:
            agent = SearchAgent(
                history_dir=Path(temp_dir),
                enabled_sources=["arxiv"],
                enable_semantic_scholar=False,
            )
            agent.fetch_all_papers(days=1)

        fake_source.fetch_papers.assert_called_once_with(
            days=1, domains=["quant-ph"], scan_receipt_callback=None
        )

    def test_daily_scan_is_unbounded_and_includes_recent_revision(self):
        now = datetime.now(timezone.utc)
        recent = now - timedelta(days=1)
        old = now - timedelta(days=3)

        submitted = [
            _FakeResult(f"new-{index}", recent, recent)
            for index in range(120)
        ] + [_FakeResult("old-submission", old, old)]
        updated = [
            _FakeResult("old-paper-v2", now - timedelta(days=30), recent),
            _FakeResult("old-update", old, old),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            # This regression exercises the normal scan boundary itself.  The
            # delayed-announcement grace behaviour is covered separately.
            source = ArxivSource(
                Path(temp_dir), max_results=1, announcement_lookback_grace_days=0
            )
            source.history = {f"new-{index}": "complete" for index in range(60)}
            fake_client = _FakeClient(submitted, updated)
            source.client = fake_client

            papers = source.fetch_papers(days=2, domains=["cs.AI"], fetch_timeout_seconds=10)

        paper_ids = {paper.paper_id for paper in papers}
        self.assertEqual(len(papers), 61)
        self.assertIn("old-paper-v2", paper_ids)
        self.assertNotIn("old-submission", paper_ids)
        self.assertTrue(all(search.max_results is None for search in fake_client.searches))
        self.assertEqual(
            [search.sort_by for search in fake_client.searches],
            [arxiv.SortCriterion.SubmittedDate, arxiv.SortCriterion.LastUpdatedDate],
        )
        self.assertIn("submittedDate:[", fake_client.searches[0].query)

    def test_scan_receipt_records_query_scope_paging_and_deduplication(self):
        now = datetime.now(timezone.utc)
        recent = now - timedelta(hours=6)
        old = now - timedelta(days=9)
        submitted = [
            _FakeResult("same-v1", recent, recent),
            _FakeResult("submitted-v1", recent, recent),
            _FakeResult("older-v1", old, old),
        ]
        updated = [
            _FakeResult("same-v1", recent, recent),
            _FakeResult("updated-v2", now - timedelta(days=30), recent),
            _FakeResult("older-update-v1", old, old),
        ]

        receipts = []
        with tempfile.TemporaryDirectory() as temp_dir:
            source = ArxivSource(
                Path(temp_dir), announcement_lookback_grace_days=2
            )
            source.client = _FakeClient(submitted, updated)
            papers = source.fetch_papers(
                days=3,
                domains=["cs.AI"],
                fetch_timeout_seconds=10,
                scan_receipt_callback=receipts.append,
            )

        self.assertEqual({"same-v1", "submitted-v1", "updated-v2"}, {p.paper_id for p in papers})
        self.assertEqual(1, len(receipts))
        receipt = receipts[0]
        self.assertEqual("succeeded", receipt["status"])
        self.assertEqual(3, receipt["requested_scan_days"])
        self.assertEqual(2, receipt["announcement_lookback_grace_days"])
        self.assertEqual(5, receipt["effective_days"])
        self.assertEqual(["cs.AI"], receipt["domains"])
        domain = receipt["domain_receipts"][0]
        self.assertEqual("succeeded", domain["status"])
        self.assertEqual(1, domain["deduplicated_within_domain"])
        self.assertEqual(3, domain["new_candidates"])
        self.assertEqual(3, domain["queries"]["submitted"]["api_entries_checked"])
        self.assertEqual(3, domain["queries"]["updated"]["api_entries_checked"])
        self.assertEqual(2, domain["queries"]["submitted"]["window_entries"])
        self.assertEqual(2, domain["queries"]["updated"]["window_entries"])
        self.assertEqual(1, domain["queries"]["submitted"]["pages_observed"])
        self.assertEqual(1, domain["queries"]["submitted"]["attempts"])

    def test_failed_domain_still_emits_failed_scan_receipt(self):
        now = datetime.now(timezone.utc)

        class _AlwaysFailingClient:
            page_size = 100

            def results(self, _search):
                raise RuntimeError("upstream unavailable")

        receipts = []
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "sources.arxiv_source.time.sleep"
        ):
            source = ArxivSource(Path(temp_dir))
            source.client = _AlwaysFailingClient()
            with self.assertRaisesRegex(Exception, "抓取未完成"):
                source.fetch_papers(
                    days=1,
                    domains=["cs.AI"],
                    fetch_timeout_seconds=10,
                    scan_receipt_callback=receipts.append,
                )

        self.assertEqual(1, len(receipts))
        domain = receipts[0]["domain_receipts"][0]
        self.assertEqual("failed", receipts[0]["status"])
        self.assertEqual("failed", domain["status"])
        self.assertIn("upstream unavailable", domain["error"])
        self.assertEqual(4, domain["queries"]["submitted"]["attempts"])

    def test_scan_receipt_persistence_callback_failure_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = ArxivSource(Path(temp_dir))
            source.client = _FakeClient([], [])
            with self.assertRaisesRegex(Exception, "无法持久化 ArXiv 扫描收据"):
                source.fetch_papers(
                    days=1,
                    domains=["cs.AI"],
                    fetch_timeout_seconds=10,
                    scan_receipt_callback=lambda _receipt: (_ for _ in ()).throw(
                        OSError("database read-only")
                    ),
                )

    def test_search_agent_propagates_source_failures(self):
        agent = SearchAgent.__new__(SearchAgent)
        agent.sources = {"fake": _FailingSource()}
        with self.assertRaisesRegex(RuntimeError, "network unavailable"):
            agent.fetch_all_papers(days=1)

    def test_announcement_grace_catches_late_indexed_submission_without_result_cap(self):
        now = datetime.now(timezone.utc)
        delayed = _FakeResult("late-paper-v1", now - timedelta(days=4), now - timedelta(days=4))

        with tempfile.TemporaryDirectory() as temp_dir:
            source = ArxivSource(
                Path(temp_dir), max_results=1, announcement_lookback_grace_days=2
            )
            client = _FakeClient([delayed], [])
            source.client = client

            papers = source.fetch_papers(days=3, domains=["cs.AI"], fetch_timeout_seconds=10)

        self.assertEqual(["late-paper-v1"], [paper.paper_id for paper in papers])
        self.assertIsNone(client.searches[0].max_results)
        self.assertIn("submittedDate:[", client.searches[0].query)

    def test_no_announcement_grace_does_not_widen_normal_submission_window(self):
        now = datetime.now(timezone.utc)
        delayed = _FakeResult("late-paper-v1", now - timedelta(days=4), now - timedelta(days=4))

        with tempfile.TemporaryDirectory() as temp_dir:
            source = ArxivSource(
                Path(temp_dir), announcement_lookback_grace_days=0
            )
            source.client = _FakeClient([delayed], [])

            papers = source.fetch_papers(days=3, domains=["cs.AI"], fetch_timeout_seconds=10)

        self.assertEqual([], papers)

    @patch("sources.search_agent.ArxivSource")
    def test_search_agent_passes_announcement_grace_to_arxiv_source(self, arxiv_source_cls):
        fake_settings = SimpleNamespace(
            ARXIV_ANNOUNCEMENT_LOOKBACK_GRACE_DAYS=4,
            get_proxy_dict=lambda _source: None,
        )
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.settings", fake_settings):
            SearchAgent(
                history_dir=Path(temp_dir),
                enabled_sources=["arxiv"],
                enable_semantic_scholar=False,
            )

        arxiv_source_cls.assert_called_once_with(
            history_dir=Path(temp_dir),
            proxy_dict=None,
            announcement_lookback_grace_days=4,
        )


if __name__ == "__main__":
    unittest.main()
