"""The WebUI legacy-import action includes its automatic supplement phase."""

import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modes import legacy_import  # noqa: E402
from notifications.notifier import RunResult  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402
from utils.legacy_history import LEGACY_IMPORT_STATE_KEY  # noqa: E402


@contextmanager
def _no_lock(*_args, **_kwargs):
    yield


class LegacyImportWorkflowTests(unittest.TestCase):
    def test_successful_import_automatically_runs_a_supplement_batch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "daily.db"
            pipeline_calls = []

            def fake_import(store, **_kwargs):
                store.record_supplement_backlog(
                    [
                        {
                            "source": "arxiv",
                            "canonical_id": "2608.1",
                            "version": 1,
                            "paper_id": "2608.1v1",
                            "reason": "missing_analysis",
                        }
                    ]
                )
                return {
                    "finished_at": "2026-08-24T12:00:00",
                    "reports_scanned": 1,
                    "cards_found": 1,
                    "delivered_ledger_rows": 0,
                    "backlog_queued": 1,
                }

            class _Pipeline:
                def run(self, *, run_kind):
                    pipeline_calls.append(run_kind)
                    DailyResearchStore(db_path).resolve_supplement_backlog(
                        "fake-supplement",
                        [("arxiv", "2608.1", 1)],
                        status="delivered",
                    )
                    return RunResult(
                        success=True,
                        total_papers_fetched=1,
                        report_paths={"arxiv_html": "supplement.html"},
                    )

            with (
                patch.object(legacy_import.settings, "DAILY_RESEARCH_DB_PATH", db_path),
                patch.object(legacy_import.settings, "HISTORY_DIR", root / "history"),
                patch.object(legacy_import.settings, "REPORTS_DIR", root / "reports"),
                patch.object(legacy_import, "import_legacy_history", side_effect=fake_import),
                patch.object(legacy_import, "_scan_phase", side_effect=lambda _store, summary: summary),
                patch.object(legacy_import, "run_lock", side_effect=_no_lock),
                patch.object(legacy_import, "daily_workflow_gate", side_effect=_no_lock),
                patch.object(
                    legacy_import,
                    "legacy_import_activity_gate",
                    side_effect=_no_lock,
                ),
                patch("modes.daily_research.DailyResearchPipeline", _Pipeline),
            ):
                self.assertEqual(legacy_import.main(), 0)

            self.assertEqual(pipeline_calls, ["supplement"])
            store = DailyResearchStore(db_path)
            summary = json.loads(store.get_app_state(LEGACY_IMPORT_STATE_KEY))
            self.assertEqual(summary["supplement"]["state"], "completed")
            self.assertEqual(summary["supplement"]["processed"], 1)
            self.assertEqual(summary["supplement"]["pending_before"], 1)

    def test_successful_import_drains_multiple_capped_supplement_batches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "daily.db"
            pipeline_calls = []

            def fake_import(store, **_kwargs):
                store.record_supplement_backlog(
                    [
                        {
                            "source": "arxiv",
                            "canonical_id": f"2608.{index}",
                            "version": 1,
                            "paper_id": f"2608.{index}v1",
                            "reason": "missed_scan",
                        }
                        for index in range(1, 4)
                    ]
                )
                return {"finished_at": "2026-08-24T12:00:00"}

            class _CappedPipeline:
                def run(self, *, run_kind):
                    pipeline_calls.append(run_kind)
                    store = DailyResearchStore(db_path)
                    row = store.claim_supplement_backlog(1)[0]
                    store.resolve_supplement_backlog(
                        "fake-supplement",
                        [(row["source"], row["canonical_id"], row["version"])],
                        status="delivered",
                    )
                    return RunResult(success=True, total_papers_fetched=1)

            with (
                patch.object(legacy_import.settings, "DAILY_RESEARCH_DB_PATH", db_path),
                patch.object(legacy_import.settings, "HISTORY_DIR", root / "history"),
                patch.object(legacy_import.settings, "REPORTS_DIR", root / "reports"),
                patch.object(legacy_import, "import_legacy_history", side_effect=fake_import),
                patch.object(legacy_import, "_scan_phase", side_effect=lambda _store, summary: summary),
                patch.object(legacy_import, "run_lock", side_effect=_no_lock),
                patch.object(legacy_import, "daily_workflow_gate", side_effect=_no_lock),
                patch.object(
                    legacy_import,
                    "legacy_import_activity_gate",
                    side_effect=_no_lock,
                ),
                patch("modes.daily_research.DailyResearchPipeline", _CappedPipeline),
            ):
                self.assertEqual(legacy_import.main(), 0)

            self.assertEqual(pipeline_calls, ["supplement", "supplement", "supplement"])
            store = DailyResearchStore(db_path)
            self.assertEqual(store.supplement_backlog_summary()["pending"], 0)
            summary = json.loads(store.get_app_state(LEGACY_IMPORT_STATE_KEY))
            self.assertEqual(summary["supplement"]["state"], "completed")
            self.assertEqual(summary["supplement"]["processed"], 3)
            self.assertEqual(len(summary["supplement"]["batches"]), 3)

    def test_unfetchable_batch_remains_retryable_without_looping_forever(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "daily.db"
            pipeline_calls = []

            def fake_import(store, **_kwargs):
                store.record_supplement_backlog(
                    [
                        {
                            "source": "arxiv",
                            "canonical_id": "2608.99",
                            "version": 1,
                            "paper_id": "2608.99v1",
                            "reason": "missing_data",
                        }
                    ]
                )
                return {"finished_at": "2026-08-24T12:00:00"}

            class _NoProgressPipeline:
                def run(self, *, run_kind):
                    pipeline_calls.append(run_kind)
                    return RunResult(success=True)

            with (
                patch.object(legacy_import.settings, "DAILY_RESEARCH_DB_PATH", db_path),
                patch.object(legacy_import.settings, "HISTORY_DIR", root / "history"),
                patch.object(legacy_import.settings, "REPORTS_DIR", root / "reports"),
                patch.object(legacy_import, "import_legacy_history", side_effect=fake_import),
                patch.object(legacy_import, "_scan_phase", side_effect=lambda _store, summary: summary),
                patch.object(legacy_import, "run_lock", side_effect=_no_lock),
                patch.object(legacy_import, "daily_workflow_gate", side_effect=_no_lock),
                patch.object(
                    legacy_import,
                    "legacy_import_activity_gate",
                    side_effect=_no_lock,
                ),
                patch("modes.daily_research.DailyResearchPipeline", _NoProgressPipeline),
            ):
                self.assertEqual(legacy_import.main(), 0)

            self.assertEqual(pipeline_calls, ["supplement"])
            store = DailyResearchStore(db_path)
            self.assertEqual(store.supplement_backlog_summary()["pending"], 1)
            summary = json.loads(store.get_app_state(LEGACY_IMPORT_STATE_KEY))
            self.assertEqual(summary["supplement"]["state"], "retry_pending")

    def test_complete_import_skips_the_unneeded_supplement_phase(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "daily.db"

            def fake_import(_store, **_kwargs):
                return {
                    "finished_at": "2026-08-24T12:00:00",
                    "reports_scanned": 1,
                    "cards_found": 1,
                    "delivered_ledger_rows": 1,
                    "backlog_queued": 0,
                }

            with (
                patch.object(legacy_import.settings, "DAILY_RESEARCH_DB_PATH", db_path),
                patch.object(legacy_import.settings, "HISTORY_DIR", root / "history"),
                patch.object(legacy_import.settings, "REPORTS_DIR", root / "reports"),
                patch.object(legacy_import, "import_legacy_history", side_effect=fake_import),
                patch.object(legacy_import, "_scan_phase", side_effect=lambda _store, summary: summary),
                patch.object(legacy_import, "run_lock", side_effect=_no_lock),
                patch.object(legacy_import, "daily_workflow_gate", side_effect=_no_lock),
                patch.object(
                    legacy_import,
                    "legacy_import_activity_gate",
                    side_effect=_no_lock,
                ),
                patch("modes.daily_research.DailyResearchPipeline") as pipeline,
            ):
                self.assertEqual(legacy_import.main(), 0)

            pipeline.assert_not_called()
            store = DailyResearchStore(db_path)
            summary = json.loads(store.get_app_state(LEGACY_IMPORT_STATE_KEY))
            self.assertEqual(summary["supplement"]["state"], "not_needed")

    def test_failed_automatic_supplement_keeps_backlog_retryable(self):
        """A failed second phase must not consume repair entries permanently."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "daily.db"

            def fake_import(store, **_kwargs):
                store.record_supplement_backlog(
                    [
                        {
                            "source": "arxiv",
                            "canonical_id": "2608.2",
                            "version": 1,
                            "paper_id": "2608.2v1",
                            "reason": "missing_analysis",
                            "paper_json": {
                                "paper_id": "2608.2v1",
                                "title": "Retry Me",
                                "authors": [],
                                "abstract": "a",
                                "published_date": "2026-08-01T00:00:00",
                                "url": "https://arxiv.org/abs/2608.2v1",
                                "source": "arxiv",
                            },
                        }
                    ]
                )
                return {"finished_at": "2026-08-24T12:00:00"}

            class _FailingPipeline:
                def run(self, *, run_kind):
                    self.run_kind = run_kind
                    return RunResult(
                        success=False,
                        error_message="temporary LLM outage",
                    )

            with (
                patch.object(legacy_import.settings, "DAILY_RESEARCH_DB_PATH", db_path),
                patch.object(legacy_import.settings, "HISTORY_DIR", root / "history"),
                patch.object(legacy_import.settings, "REPORTS_DIR", root / "reports"),
                patch.object(legacy_import, "import_legacy_history", side_effect=fake_import),
                patch.object(legacy_import, "_scan_phase", side_effect=lambda _store, summary: summary),
                patch.object(legacy_import, "run_lock", side_effect=_no_lock),
                patch.object(legacy_import, "daily_workflow_gate", side_effect=_no_lock),
                patch.object(
                    legacy_import,
                    "legacy_import_activity_gate",
                    side_effect=_no_lock,
                ),
                patch("modes.daily_research.DailyResearchPipeline", _FailingPipeline),
            ):
                self.assertEqual(legacy_import.main(), 1)

            store = DailyResearchStore(db_path)
            self.assertEqual(store.supplement_backlog_summary()["pending"], 1)
            summary = json.loads(store.get_app_state(LEGACY_IMPORT_STATE_KEY))
            self.assertEqual(summary["supplement"]["state"], "failed")

    def test_interrupted_import_marks_its_run_failed_before_exiting(self):
        """The WebUI stop action must not leave a phantom running import."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "daily.db"

            with (
                patch.object(legacy_import.settings, "DAILY_RESEARCH_DB_PATH", db_path),
                patch.object(legacy_import.settings, "HISTORY_DIR", root / "history"),
                patch.object(legacy_import.settings, "REPORTS_DIR", root / "reports"),
                patch.object(
                    legacy_import,
                    "import_legacy_history",
                    side_effect=KeyboardInterrupt,
                ),
                patch.object(legacy_import, "run_lock", side_effect=_no_lock),
                patch.object(legacy_import, "daily_workflow_gate", side_effect=_no_lock),
                patch.object(
                    legacy_import,
                    "legacy_import_activity_gate",
                    side_effect=_no_lock,
                ),
            ):
                self.assertEqual(legacy_import.main(), 130)

            store = DailyResearchStore(db_path)
            with store._connect() as conn:
                row = conn.execute(
                    "SELECT status, completed_at, error FROM daily_runs"
                ).fetchone()
            self.assertEqual(row["status"], "failed")
            self.assertIsNotNone(row["completed_at"])
            self.assertIn("用户中断旧历史导入", row["error"])


if __name__ == "__main__":
    unittest.main()
