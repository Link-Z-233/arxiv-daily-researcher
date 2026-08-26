"""SQLite omission scan grouping and natural-calendar-week supplement runs."""

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modes.history_omission_scan import run_history_omission_scan  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402


def _entry(canonical: str, published: str, reason: str = "missed_scan") -> dict:
    return {
        "source": "arxiv",
        "canonical_id": canonical,
        "version": 1,
        "paper_id": f"{canonical}v1",
        "reason": reason,
        "paper_json": {
            "paper_id": f"{canonical}v1",
            "title": f"Paper {canonical}",
            "authors": ["Alice"],
            "abstract": "abstract",
            "published_date": f"{published}T00:00:00",
            "url": f"https://arxiv.org/abs/{canonical}v1",
            "source": "arxiv",
            "pdf_url": f"https://arxiv.org/pdf/{canonical}v1.pdf",
            "canonical_id": canonical,
            "version": 1,
            "categories": [],
        },
    }


class HistoryOmissionScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DailyResearchStore(Path(self.temp.name) / "history.db")
        self.store.record_supplement_backlog(
            [
                _entry("2603.10001", "2026-03-02"),  # Monday of first ISO week
                _entry("2603.10002", "2026-03-04"),
                _entry("2603.10003", "2026-03-09"),  # following Monday
                _entry("2603.20001", "2026-03-04", reason="missing_data"),
            ]
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_groups_only_pending_missed_rows_by_iso_week(self):
        self.assertEqual(
            self.store.missed_scan_week_groups(),
            {
                datetime(2026, 3, 2).date(): 2,
                datetime(2026, 3, 9).date(): 1,
            },
        )

    def test_runs_each_week_in_capped_batches_with_sunday_report_dates(self):
        calls = []

        class _Pipeline:
            def run(self, **kwargs):
                calls.append(kwargs)
                rows = self_store.claim_supplement_backlog(
                    1,
                    reasons=kwargs["supplement_reasons"],
                    published_from=kwargs["supplement_week_start"],
                    published_to=kwargs["supplement_week_end"],
                )
                assert rows
                row = rows[0]
                self_store.resolve_supplement_backlog(
                    "fake-supplement",
                    [(row["source"], row["canonical_id"], row["version"])],
                    status="delivered",
                )
                return SimpleNamespace(
                    success=True,
                    interrupted=False,
                    total_papers_fetched=1,
                    report_paths={"arxiv_html": f"{row['canonical_id']}.html"},
                )

        # The nested fake class gets no direct store argument; bind the test
        # store as a local rather than depending on any global database path.
        self_store = self.store
        with patch(
            "modes.history_omission_scan._scan_sqlite_history",
            return_value={
                "range_start": "2026-03-02",
                "range_end": "2026-03-09",
                "papers_scanned": 3,
                "missed_found": 0,
                "failed_chunks": 0,
                "errors": [],
            },
        ):
            exit_code, _run_id, summary = run_history_omission_scan(
                store=self.store,
                notify=False,
                pipeline_factory=_Pipeline,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(
            [call["supplement_week_start"].isoformat() for call in calls],
            ["2026-03-02", "2026-03-02", "2026-03-09"],
        )
        self.assertEqual(
            [call["report_timestamp"].date().isoformat() for call in calls],
            ["2026-03-08", "2026-03-08", "2026-03-15"],
        )
        self.assertEqual(summary["pending_after"], 0)
        self.assertEqual([week["batches"] for week in summary["weeks"]], [2, 1])
        # A non-omission compatibility repair must stay for its separate flow.
        pending = self.store.supplement_backlog_summary(reasons={"missing_data"})
        self.assertEqual(pending["pending"], 1)

    def test_failed_supplement_batch_marks_scan_run_failed(self):
        class _FailingPipeline:
            def run(self, **_kwargs):
                return SimpleNamespace(
                    success=False,
                    interrupted=False,
                    total_papers_fetched=0,
                    report_paths={},
                    error_message="supplement LLM request failed",
                )

        with patch(
            "modes.history_omission_scan._scan_sqlite_history",
            return_value={
                "range_start": "2026-03-02",
                "range_end": "2026-03-09",
                "papers_scanned": 3,
                "missed_found": 0,
                "failed_chunks": 0,
                "errors": [],
            },
        ):
            exit_code, run_id, summary = run_history_omission_scan(
                store=self.store,
                notify=False,
                pipeline_factory=_FailingPipeline,
            )

        self.assertEqual(exit_code, 1)
        self.assertTrue(summary["issues"])
        self.assertGreater(summary["pending_after"], 0)
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT status, error FROM daily_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertIn("有步骤未完成", row["error"])


if __name__ == "__main__":
    unittest.main()
