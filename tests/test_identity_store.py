import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sources.arxiv_source import ArxivSource  # noqa: E402
from sources.base_source import PaperMetadata, split_arxiv_version  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402
from report.daily.reporter import Reporter  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
