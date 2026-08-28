"""Regression coverage for the SQLite/trigger-backed history task list."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.webui_trigger import enqueue_trigger, trigger_status_directory  # noqa: E402
from webui.tabs import data_management  # noqa: E402


class _ProgressStore:
    def __init__(self, payload):
        self.payload = payload

    def active_run_progress(self):
        return self.payload


class HistoryTaskListTests(unittest.TestCase):
    def test_unfinished_view_hides_completed_receipts_but_keeps_retryable_work(self):
        records = [
            {"state": "succeeded", "request_id": "done"},
            {"state": "queued", "request_id": "waiting"},
            {"state": "failed", "request_id": "retry"},
        ]
        with patch.object(data_management, "_read_history_task_records", return_value=records):
            visible = data_management._unfinished_history_tasks({})

        self.assertEqual([item["request_id"] for item in visible], ["waiting", "retry"])
        self.assertTrue(data_management._history_status_needs_polling(visible))
        self.assertFalse(
            data_management._history_status_needs_polling(
                [{"state": "failed", "request_id": "retry"}]
            )
        )

    def test_task_list_reads_queued_and_failed_receipts_with_sanitized_issue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            queued = enqueue_trigger(data_dir, "history_data_repair")
            status_dir = trigger_status_directory(data_dir)
            status_dir.mkdir(parents=True, exist_ok=True)
            request_id = "a" * 32
            (status_dir / f"{request_id}.json").write_text(
                json.dumps(
                    {
                        "request_id": request_id,
                        "mode": "legacy_import",
                        "state": "failed",
                        "created_at": "2026-08-28T00:00:00+00:00",
                        "started_at": "2026-08-28T00:01:00+00:00",
                        "updated_at": "2026-08-28T00:02:00+00:00",
                        "error_summary": "password=secret https://example.test/private",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.object(data_management, "t", side_effect=lambda key: key):
                rows = data_management._read_history_task_records(
                    {},
                    queue_dir=queued.parent,
                    status_dir=status_dir,
                    store=_ProgressStore(None),
                )

        self.assertEqual({row["mode"] for row in rows}, {"legacy_import", "history_data_repair"})
        failed = next(row for row in rows if row["mode"] == "legacy_import")
        pending = next(row for row in rows if row["mode"] == "history_data_repair")
        self.assertEqual(failed["state"], "failed")
        self.assertTrue(failed["retryable"])
        self.assertNotIn("secret", failed["issue"])
        self.assertNotIn("https://", failed["issue"])
        self.assertEqual(pending["state"], "queued")
        self.assertEqual(pending["progress"], "dm_task_progress_queued")

    def test_running_task_uses_matching_sqlite_progress(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            status_dir = Path(temp_dir) / "status"
            status_dir.mkdir()
            request_id = "b" * 32
            (status_dir / f"{request_id}.json").write_text(
                json.dumps(
                    {
                        "request_id": request_id,
                        "mode": "history_data_repair",
                        "state": "running",
                        "created_at": "2026-08-28T00:00:00+00:00",
                        "started_at": "2026-08-28T00:01:00+00:00",
                        "updated_at": "2026-08-28T00:01:03+00:00",
                    }
                ),
                encoding="utf-8",
            )
            store = _ProgressStore(
                {
                    "run_kind": "history_data_repair",
                    "phase": "history_repair",
                    "detail": "正在补全 TLDR",
                    "current": 2,
                    "total": 5,
                }
            )
            with patch.object(data_management, "t", side_effect=lambda key: key):
                rows = data_management._read_history_task_records(
                    {},
                    queue_dir=Path(temp_dir) / "queue",
                    status_dir=status_dir,
                    store=store,
                )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "running")
        self.assertIn("history_repair", rows[0]["progress"])
        self.assertIn("正在补全 TLDR", rows[0]["progress"])
        self.assertIn("2/5", rows[0]["progress"])


if __name__ == "__main__":
    unittest.main()
