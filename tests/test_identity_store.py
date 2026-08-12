import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sources.arxiv_source import ArxivSource  # noqa: E402
from sources.base_source import PaperMetadata, split_arxiv_version  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402
from report.daily.reporter import Reporter  # noqa: E402
from agents.analysis_agent import WeightedScoreResponse  # noqa: E402
from modes.daily_research import _exclude_sqlite_delivered_papers  # noqa: E402


def _paper(paper_id: str) -> PaperMetadata:
    return PaperMetadata(
        paper_id=paper_id,
        title=paper_id,
        authors=["Author"],
        abstract="abstract",
        published_date=datetime.now(timezone.utc),
        url=f"https://arxiv.org/abs/{paper_id}",
        source="arxiv",
    )


class IdentityStoreTests(unittest.TestCase):
    def test_arxiv_identity_and_legacy_history_are_version_aware(self):
        self.assertEqual(split_arxiv_version("2501.12345v2"), ("2501.12345", 2))
        self.assertEqual(split_arxiv_version("hep-th/9901001"), ("hep-th/9901001", None))

        with tempfile.TemporaryDirectory() as temp_dir:
            source = ArxivSource(Path(temp_dir))
            source.mark_as_processed("2501.12345v1")
            self.assertTrue(source.is_processed("2501.12345v1"))
            self.assertFalse(source.is_processed("2501.12345v2"))
            previous = source.get_previous_processed_version("2501.12345v2")
            self.assertEqual(previous["version"], 1)

            with open(Path(temp_dir) / "arxiv_history.json", "r", encoding="utf-8") as handle:
                history = json.load(handle)
            self.assertIn("2501.12345@v1", history)

    def test_store_migrates_old_schema_and_finds_previous_completed_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "daily.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE daily_papers (
                        source TEXT NOT NULL,
                        paper_id TEXT NOT NULL,
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        run_id TEXT,
                        paper_json TEXT NOT NULL,
                        score_json TEXT,
                        abstract_cn TEXT,
                        analysis_json TEXT,
                        scored_at TEXT,
                        translated_at TEXT,
                        analyzed_at TEXT,
                        completed_at TEXT,
                        last_error TEXT,
                        PRIMARY KEY (source, paper_id)
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO daily_papers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "arxiv",
                        "2501.12345v1",
                        "2026-01-01",
                        "2026-01-01",
                        "run-1",
                        "{}",
                        None,
                        "",
                        None,
                        None,
                        None,
                        None,
                        "2026-01-01T08:00:00",
                        None,
                    ),
                )

            store = DailyResearchStore(db_path)
            old = store.get_paper_record("arxiv", "2501.12345v1")
            self.assertEqual(old["canonical_id"], "2501.12345")
            self.assertEqual(old["version"], 1)

            run_id = store.start_run(1)
            v2 = _paper("2501.12345v2")
            store.upsert_paper_seen(run_id, "arxiv", v2)
            previous = store.get_previous_version_record("arxiv", v2)
            self.assertEqual(previous["paper_id"], "2501.12345v1")
            self.assertEqual(previous["version"], 1)

    def test_report_status_label_identifies_revision_and_retry(self):
        paper = _paper("2501.12345v2")
        self.assertIn("修订版 v2", Reporter._paper_status_label({
            "paper_metadata": paper,
            "revision": {
                "version": 2,
                "previous_version": 1,
                "previous_pushed_at": "2026-01-01T08:00:00",
            },
        }))
        self.assertEqual(
            Reporter._paper_status_label({"paper_metadata": paper, "is_retry": True}),
            "↻ 重试",
        )

    def test_stage_state_keeps_score_when_translation_or_analysis_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_id = store.start_run(1)
            paper = _paper("2501.12345v1")
            store.upsert_paper_seen(run_id, "arxiv", paper)
            score = WeightedScoreResponse(
                total_score=4,
                keyword_scores={"quantum": 4},
                author_bonus=0,
                expert_authors_found=[],
                passing_score=3,
                is_qualified=True,
                reasoning="relevant",
                tldr="A concise TLDR",
                extracted_keywords=["quantum"],
            )
            scored = {"paper_metadata": paper, "paper_id": paper.paper_id, "score_response": score}
            store.update_score(run_id, "arxiv", scored)
            record = store.get_paper_record("arxiv", paper.paper_id)
            self.assertEqual(record["score_status"], "succeeded")
            self.assertEqual(record["translation_status"], "pending")
            self.assertIsNone(store.hydrate_scored_paper(paper, record))
            self.assertEqual(store.hydrate_scored_paper(paper, record, False)["score_response"].tldr,
                             "A concise TLDR")

            store.update_error(run_id, "arxiv", paper.paper_id, "translation down", stage="translation")
            failed = store.get_paper_record("arxiv", paper.paper_id)
            self.assertEqual(failed["translation_status"], "failed")
            self.assertEqual(failed["retry_count"], 1)

            store.update_translation(run_id, "arxiv", paper.paper_id, "中文摘要")
            store.update_analysis(run_id, "arxiv", paper.paper_id, {"summary": "analysis"})
            hydrated = store.hydrate_analysis(store.get_paper_record("arxiv", paper.paper_id))
            self.assertEqual(hydrated, {"summary": "analysis"})

    def test_retry_preserves_optional_semantic_scholar_enrichment(self):
        """A transient S2 failure must not erase a TLDR from a retried paper."""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            first_run = store.start_run(1)
            first = PaperMetadata(
                paper_id="10.9999/test.enriched",
                title="Journal paper",
                authors=["Author"],
                abstract="abstract",
                published_date=datetime.now(timezone.utc),
                url="https://doi.org/10.9999/test.enriched",
                source="prl",
                doi="10.9999/test.enriched",
                semantic_scholar_tldr="Persisted Semantic Scholar TLDR",
                arxiv_id="2501.12345v1",
                arxiv_url="https://arxiv.org/abs/2501.12345v1",
                pdf_url="https://arxiv.org/pdf/2501.12345v1.pdf",
            )
            store.upsert_paper_seen(first_run, "prl", first)

            # This models a restart where OpenAlex succeeds but Semantic
            # Scholar is temporarily unavailable and returns no enrichment.
            retry = PaperMetadata(
                paper_id=first.paper_id,
                title=first.title,
                authors=first.authors,
                abstract=first.abstract,
                published_date=first.published_date,
                url=first.url,
                source="prl",
                doi=first.doi,
            )
            retry_run = store.start_run(1)
            store.upsert_paper_seen(retry_run, "prl", retry)

            self.assertEqual(retry.semantic_scholar_tldr, first.semantic_scholar_tldr)
            self.assertEqual(retry.arxiv_id, first.arxiv_id)
            self.assertEqual(retry.pdf_url, first.pdf_url)
            record = store.get_paper_record("prl", retry.paper_id)
            persisted = json.loads(record["paper_json"])
            self.assertEqual(persisted["semantic_scholar_tldr"], first.semantic_scholar_tldr)

    def test_finalization_atomically_records_delivery_outbox_and_revision_date(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_v1 = store.start_run(1)
            v1 = _paper("2501.12345v1")
            store.upsert_paper_seen(run_v1, "arxiv", v1)
            score = WeightedScoreResponse(
                total_score=4,
                keyword_scores={"quantum": 4},
                author_bonus=0,
                expert_authors_found=[],
                passing_score=3,
                is_qualified=True,
                reasoning="relevant",
                tldr="A concise TLDR",
                extracted_keywords=["quantum"],
            )
            store.update_score(
                run_v1,
                "arxiv",
                {"paper_metadata": v1, "paper_id": v1.paper_id, "score_response": score},
            )
            store.update_translation(run_v1, "arxiv", v1.paper_id, "中文摘要")
            store.finalize_report_delivery(
                run_v1,
                {"arxiv": Path(temp_dir) / "v1.md"},
                {"arxiv": [{"paper_metadata": v1, "paper_id": v1.paper_id, "requires_analysis": False}]},
                [
                    {
                        "event_type": "daily_run_result",
                        "channel": "wechat_work",
                        "payload": {"result": {"run_timestamp": "2026-08-12"}},
                    }
                ],
            )

            self.assertTrue(store.is_paper_delivered("arxiv", v1.paper_id))
            self.assertEqual(store.get_pending_notification_count(), 1)

            run_v2 = store.start_run(1)
            v2 = _paper("2501.12345v2")
            store.upsert_paper_seen(run_v2, "arxiv", v2)
            previous = store.get_previous_version_record("arxiv", v2)
            self.assertEqual(previous["paper_id"], v1.paper_id)
            self.assertIsNotNone(previous["delivered_at"])

    def test_sqlite_delivery_ledger_prevents_duplicate_when_json_history_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_id = store.start_run(1)
            paper = _paper("2501.12345v1")
            store.upsert_paper_seen(run_id, "arxiv", paper)
            score = WeightedScoreResponse(
                total_score=4,
                keyword_scores={"quantum": 4},
                author_bonus=0,
                expert_authors_found=[],
                passing_score=3,
                is_qualified=True,
                reasoning="relevant",
                tldr="A concise TLDR",
                extracted_keywords=["quantum"],
            )
            store.update_score(
                run_id,
                "arxiv",
                {"paper_metadata": paper, "paper_id": paper.paper_id, "score_response": score},
            )
            store.update_translation(run_id, "arxiv", paper.paper_id, "中文摘要")
            store.finalize_report_delivery(
                run_id,
                {"arxiv": Path(temp_dir) / "report.md"},
                {"arxiv": [{"paper_metadata": paper, "paper_id": paper.paper_id, "requires_analysis": False}]},
            )

            filtered = _exclude_sqlite_delivered_papers(store, {"arxiv": [paper]})
            self.assertEqual(filtered, {"arxiv": []})

    def test_finalization_rejects_missing_translation_without_partial_delivery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_id = store.start_run(1)
            paper = _paper("2501.12345v1")
            store.upsert_paper_seen(run_id, "arxiv", paper)
            score = WeightedScoreResponse(
                total_score=4,
                keyword_scores={"quantum": 4},
                author_bonus=0,
                expert_authors_found=[],
                passing_score=3,
                is_qualified=True,
                reasoning="relevant",
                tldr="A concise TLDR",
                extracted_keywords=["quantum"],
            )
            store.update_score(
                run_id,
                "arxiv",
                {"paper_metadata": paper, "paper_id": paper.paper_id, "score_response": score},
            )

            with self.assertRaisesRegex(RuntimeError, "摘要翻译尚未完成"):
                store.finalize_report_delivery(
                    run_id,
                    {"arxiv": Path(temp_dir) / "report.md"},
                    {"arxiv": [{"paper_metadata": paper, "paper_id": paper.paper_id, "requires_analysis": False}]},
                )
            self.assertFalse(store.is_paper_delivered("arxiv", paper.paper_id))
            self.assertEqual(store.get_pending_notification_count(), 0)

    def test_history_batch_write_is_atomic_and_failure_does_not_mutate_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = ArxivSource(Path(temp_dir))
            source.mark_many_as_processed(["2501.12345v1", "2501.12346v1"])
            history_path = Path(temp_dir) / "arxiv_history.json"
            before = history_path.read_text(encoding="utf-8")

            with patch("sources.base_source.os.replace", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    source.mark_many_as_processed(["2501.12347v1"])

            self.assertEqual(history_path.read_text(encoding="utf-8"), before)
            self.assertFalse(source.is_processed("2501.12347v1"))
            self.assertEqual(list(Path(temp_dir).glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
