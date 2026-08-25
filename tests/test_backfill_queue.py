"""Past daily-report ranges are persisted and drained one calendar day at a time."""

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modes import backfill_queue  # noqa: E402
from modes.backfill_queue import drain_backfill_queue  # noqa: E402
from notifications.notifier import RunResult  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402


class BackfillQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = DailyResearchStore(Path(self.tmp.name) / "daily.db")
        self.end = date.today() - timedelta(days=2)
        self.start = self.end - timedelta(days=2)

    def tearDown(self):
        self.tmp.cleanup()

    def test_range_is_persisted_and_runs_oldest_day_first(self):
        queued = self.store.enqueue_backfill_range(self.start, self.end)
        self.assertEqual(queued["queued"], 3)

        calls = []

        class _Pipeline:
            def run(self, *, run_kind, target_date):
                calls.append((run_kind, target_date))
                return RunResult(success=True)

        result = drain_backfill_queue(self.store, pipeline_factory=_Pipeline)

        self.assertEqual(result, 0)
        self.assertEqual(
            calls,
            [
                ("backfill", self.start),
                ("backfill", self.start + timedelta(days=1)),
                ("backfill", self.end),
            ],
        )
        summary = self.store.backfill_queue_summary()
        self.assertEqual(summary["completed"], 3)
        self.assertEqual(summary["pending"], 0)
        self.assertEqual(summary["failed"], 0)
        batch = self.store.backfill_batch_summary(queued["batch_id"])
        self.assertEqual(batch["total"], 3)
        self.assertEqual(batch["completed"], 3)
        self.assertEqual(batch["failed"], 0)

    def test_one_failed_day_stays_visible_while_later_dates_continue(self):
        queued = self.store.enqueue_backfill_range(self.start, self.start + timedelta(days=1))
        calls = []
        first_day = self.start

        class _Pipeline:
            def run(self, *, run_kind, target_date):
                calls.append(target_date)
                if target_date == first_day:
                    return RunResult(success=False, error_message="upstream unavailable")
                return RunResult(success=True)

        result = drain_backfill_queue(self.store, pipeline_factory=_Pipeline)

        self.assertEqual(result, 1)
        self.assertEqual(calls, [self.start, self.start + timedelta(days=1)])
        summary = self.store.backfill_queue_summary()
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["failed"], 1)
        batch = self.store.backfill_batch_summary(queued["batch_id"])
        self.assertEqual(batch["completed"], 1)
        self.assertEqual(batch["failed"], 1)
        self.assertEqual(batch["first_failed_date"], self.start.isoformat())
        self.assertEqual(batch["first_error"], "upstream unavailable")

    def test_interrupted_day_returns_to_pending_for_a_later_resume(self):
        self.store.enqueue_backfill_range(self.start, self.start)

        class _InterruptedPipeline:
            def run(self, **_kwargs):
                return RunResult(success=False, interrupted=True, error_message="stopped")

        self.assertEqual(
            drain_backfill_queue(self.store, pipeline_factory=_InterruptedPipeline),
            130,
        )
        self.assertEqual(self.store.backfill_queue_summary()["pending"], 1)

        class _SuccessPipeline:
            def run(self, **_kwargs):
                return RunResult(success=True)

        self.assertEqual(
            drain_backfill_queue(self.store, pipeline_factory=_SuccessPipeline),
            0,
        )
        self.assertEqual(self.store.backfill_queue_summary()["completed"], 1)

    def test_capped_day_is_requeued_until_its_remaining_papers_finish(self):
        self.store.enqueue_backfill_range(self.start, self.start)
        calls = []

        class _CappedPipeline:
            def run(self, *, run_kind, target_date):
                calls.append((run_kind, target_date))
                if len(calls) == 1:
                    return RunResult(success=True, deferred_paper_count=2)
                return RunResult(success=True)

        self.assertEqual(
            drain_backfill_queue(self.store, pipeline_factory=_CappedPipeline),
            0,
        )
        self.assertEqual(
            calls,
            [("backfill", self.start), ("backfill", self.start)],
        )
        summary = self.store.backfill_queue_summary()
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["pending"], 0)

    def test_requested_range_emits_one_consolidated_workflow_notification(self):
        delivered = []

        class _Notifier:
            def enqueue_workflow_result(self, _store, run_id, result):
                delivered.append((run_id, result))
                return 1

            def deliver_pending_workflow_results(self, _store):
                return {"claimed": 1, "sent": 1, "deferred": 0}

        class _Pipeline:
            def run(self, **_kwargs):
                return RunResult(success=True)

        def fake_drain(store):
            return drain_backfill_queue(store, pipeline_factory=_Pipeline)

        with (
            patch.object(backfill_queue.settings, "DAILY_RESEARCH_DB_PATH", self.store.db_path),
            patch.object(backfill_queue.settings, "ENABLE_NOTIFICATIONS", True),
            patch.object(backfill_queue, "NotifierAgent", _Notifier),
            patch.object(backfill_queue, "drain_backfill_queue", side_effect=fake_drain),
        ):
            self.assertEqual(
                backfill_queue.enqueue_and_run_backfill_range(self.start, self.end), 0
            )

        self.assertEqual(len(delivered), 1)
        batch_id, result = delivered[0]
        self.assertTrue(batch_id)
        self.assertEqual(result.workflow, "过去日报补跑")
        self.assertTrue(result.success)
        self.assertEqual(result.summary["已完成日期"], 3)


if __name__ == "__main__":
    unittest.main()
