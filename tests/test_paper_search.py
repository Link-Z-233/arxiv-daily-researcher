"""论文检索：元数据匹配、过滤条件与分页。"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.daily_research_store import DailyResearchStore  # noqa: E402


def _seed_paper(
    store: DailyResearchStore,
    *,
    paper_id: str,
    title: str,
    authors: list[str],
    completed_at: str,
    total_score: float | None = None,
    qualified: bool | None = None,
    tldr: str = "",
    extracted: list[str] | None = None,
) -> None:
    paper_json = json.dumps(
        {
            "paper_id": paper_id,
            "source": "arxiv",
            "title": title,
            "authors": authors,
            "url": f"https://example.org/{paper_id}",
            "categories": ["cs.LG"],
        },
        ensure_ascii=False,
    )
    score_json = None
    if total_score is not None:
        score_json = json.dumps(
            {
                "total_score": total_score,
                "is_qualified": qualified,
                "strategy_id": "legacy_weighted_keyword_v1",
                "tldr": tldr,
                "extracted_keywords": extracted or [],
            },
            ensure_ascii=False,
        )
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO daily_papers(
                source, paper_id, canonical_id, first_seen_at, last_seen_at,
                paper_json, score_json, completed_at
            ) VALUES (?, ?, '', ?, ?, ?, ?, ?)
            """,
            ("arxiv", paper_id, completed_at, completed_at, paper_json, score_json, completed_at),
        )


class SearchPapersTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = DailyResearchStore(
            Path(self._tmp.name) / "daily_research.db"
        )
        _seed_paper(
            self.store,
            paper_id="a1",
            title="Diffusion Models for Speech Synthesis",
            authors=["Alice Smith", "Bob Jones"],
            completed_at="2026-08-01T08:00:00",
            total_score=12.5,
            qualified=True,
            tldr="本文提出一种语音扩散模型。",
            extracted=["diffusion", "speech synthesis"],
        )
        _seed_paper(
            self.store,
            paper_id="a2",
            title="Transformer Quantization Survey",
            authors=["Carol White"],
            completed_at="2026-08-10T08:00:00",
            total_score=3.0,
            qualified=False,
            tldr="量化方法综述。",
            extracted=["quantization", "transformer"],
        )
        _seed_paper(
            self.store,
            paper_id="a3",
            title="Unscored Draft Paper",
            authors=["Dan Brown"],
            completed_at="2026-08-15T08:00:00",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_query_lists_all_completed(self):
        result = self.store.search_papers()
        self.assertEqual(result["total"], 3)
        # newest first
        self.assertEqual(result["items"][0]["paper_id"], "a3")

    def test_title_query_matches_case_insensitively(self):
        result = self.store.search_papers(query="diffusion")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["paper_id"], "a1")

    def test_query_matches_tldr_and_extracted_keywords(self):
        by_tldr = self.store.search_papers(query="量化")
        self.assertEqual(by_tldr["total"], 1)
        self.assertEqual(by_tldr["items"][0]["paper_id"], "a2")

        by_keyword = self.store.search_papers(query="speech synthesis")
        self.assertEqual(by_keyword["total"], 1)
        self.assertEqual(by_keyword["items"][0]["paper_id"], "a1")

    def test_query_matches_authors(self):
        result = self.store.search_papers(query="Alice Smith")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["paper_id"], "a1")

    def test_like_wildcards_match_literally(self):
        result = self.store.search_papers(query="100%")
        self.assertEqual(result["total"], 0)

    def test_min_score_filter_excludes_unscored(self):
        result = self.store.search_papers(min_score=5.0)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["paper_id"], "a1")

    def test_date_range_filter(self):
        result = self.store.search_papers(
            completed_from="2026-08-05", completed_to="2026-08-12"
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["paper_id"], "a2")

    def test_liked_only_filter(self):
        self.store.set_paper_preference(
            "arxiv",
            "a2",
            preference="like",
            title="Transformer Quantization Survey",
        )
        result = self.store.search_papers(liked_only=True)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["paper_id"], "a2")
        self.assertEqual(result["items"][0]["preference"], "like")

    def test_pagination_limit_and_offset(self):
        page1 = self.store.search_papers(limit=2)
        self.assertEqual(page1["total"], 3)
        self.assertEqual([i["paper_id"] for i in page1["items"]], ["a3", "a2"])

        page2 = self.store.search_papers(limit=2, offset=2)
        self.assertEqual([i["paper_id"] for i in page2["items"]], ["a1"])

    def test_item_metadata_hydration(self):
        result = self.store.search_papers(query="Diffusion")
        item = result["items"][0]
        self.assertEqual(item["source"], "arxiv")
        self.assertEqual(item["title"], "Diffusion Models for Speech Synthesis")
        self.assertEqual(item["authors"], ["Alice Smith", "Bob Jones"])
        self.assertEqual(item["url"], "https://example.org/a1")
        self.assertEqual(item["total_score"], 12.5)
        self.assertTrue(item["is_qualified"])
        self.assertEqual(item["extracted_keywords"], ["diffusion", "speech synthesis"])


if __name__ == "__main__":
    unittest.main()
