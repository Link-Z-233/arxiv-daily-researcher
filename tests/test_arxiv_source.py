import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import arxiv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sources.arxiv_source import ArxivSource  # noqa: E402
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
            source = ArxivSource(Path(temp_dir), max_results=1)
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

    def test_search_agent_propagates_source_failures(self):
        agent = SearchAgent.__new__(SearchAgent)
        agent.sources = {"fake": _FailingSource()}
        with self.assertRaisesRegex(RuntimeError, "network unavailable"):
            agent.fetch_all_papers(days=1)


if __name__ == "__main__":
    unittest.main()
