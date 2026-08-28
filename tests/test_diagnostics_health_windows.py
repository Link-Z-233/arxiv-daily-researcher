"""Regression coverage for time-scoped diagnostics health summaries."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.daily_research_store import DailyResearchStore  # noqa: E402


class DiagnosticsHealthWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.store = DailyResearchStore(Path(self._temp_dir.name) / "daily.db")

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_source_health_days_include_all_task_kinds_and_keep_recent_error(self):
        self.store.record_source_health_event(
            "arxiv",
            True,
            task_kind="daily",
            candidate_count=4,
            origin_key="old-arxiv",
        )
        self.store.record_source_health_event(
            "semantic_scholar",
            False,
            task_kind="history_omission_scan",
            error_summary="HTTP 429 at https://example.test/?token=secret",
            origin_key="recent-semantic",
        )
        old_at = (datetime.now() - timedelta(days=8)).isoformat()
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE source_health_events SET occurred_at = ? WHERE origin_key = ?",
                (old_at, "old-arxiv"),
            )

        summaries = self.store.get_source_health_for_days(7)

        self.assertEqual(set(summaries), {"semantic_scholar"})
        event = summaries["semantic_scholar"]
        self.assertEqual(event["last_task_kind"], "history_omission_scan")
        self.assertEqual(event["last_status"], "failed")
        self.assertIn("429", event["last_error"])
        self.assertNotIn("secret", event["last_error"])
        self.assertNotIn("https://", event["last_error"])

    def test_llm_health_groups_complete_window_by_concrete_model(self):
        self.store.record_llm_health_event("cheap", "fast-model", True)
        self.store.record_llm_health_event(
            "cheap", "fast-model", False, "provider temporarily unavailable"
        )
        self.store.record_llm_health_event("smart", "deep-model", True)

        rows = self.store.get_llm_health_by_model(7)
        by_model = {row["model"]: row for row in rows}

        self.assertEqual(set(by_model), {"fast-model", "deep-model"})
        self.assertEqual(by_model["fast-model"]["events_in_window"], 2)
        self.assertEqual(by_model["fast-model"]["succeeded_in_window"], 1)
        self.assertEqual(by_model["fast-model"]["last_status"], "failed")
        self.assertIn("provider", by_model["fast-model"]["last_error"])

    def test_recent_operational_runs_excludes_history_and_supplement_workflows(self):
        daily = self.store.start_run(2, run_kind="daily")
        self.store.complete_run(daily)
        backfill = self.store.start_run(3, run_kind="backfill")
        self.store.fail_run(backfill, "历史日期数据源失败: api_key=hidden")
        self.store.start_run(0, run_kind="legacy_import")
        self.store.start_run(0, run_kind="history_data_repair")
        self.store.start_run(0, run_kind="supplement")

        runs = self.store.get_recent_operational_runs()

        self.assertEqual([row["run_kind"] for row in runs], ["backfill", "daily"])
        self.assertEqual(runs[0]["status"], "failed")
        self.assertNotIn("hidden", runs[0]["error_summary"])


if __name__ == "__main__":
    unittest.main()
