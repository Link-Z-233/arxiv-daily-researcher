"""Past daily-report ranges are persisted and drained one calendar day at a time."""

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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

    def test_one_failed_day_stays_visible_while_later_dates_continue(self):
        self.store.enqueue_backfill_range(self.start, self.start + timedelta(days=1))
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


if __name__ == "__main__":
    unittest.main()
