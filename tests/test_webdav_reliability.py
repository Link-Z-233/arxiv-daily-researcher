import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.daily_research_store import DailyResearchStore  # noqa: E402
from utils.webdav_sync import (  # noqa: E402
    WebDAVSync,
    after_report_sync_maintenance_entry,
    deliver_pending_after_report_syncs,
)


class _FakeClient:
    def __init__(self, remote_database: Path):
        self.remote_database = remote_database

    def download_file(self, _remote_file: str, local_file: str) -> None:
        Path(local_file).write_bytes(self.remote_database.read_bytes())


def _sync_shell(project_root: Path) -> WebDAVSync:
    """Create a WebDAVSync object without importing the optional client library."""
    sync = WebDAVSync.__new__(WebDAVSync)
    sync._project_root = project_root
    sync.remote_root = "arxiv-researcher"
    return sync


class WebDAVReliabilityTests(unittest.TestCase):
    @staticmethod
    def _settings(**overrides):
        defaults = {
            "WEBDAV_ENABLED": True,
            "WEBDAV_SYNC_MODE": "after_report",
            "RETRY_MAX_ATTEMPTS": 3,
            "RETRY_MIN_WAIT": 1,
            "RETRY_MAX_WAIT": 4,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _store(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        return DailyResearchStore(Path(temp_dir.name) / "daily.db")

    def test_report_finalization_atomically_queues_maintenance_task(self):
        store = self._store()
        run_id = store.start_run(0)
        entry = {
            "task_key": f"webdav_after_report:{run_id}",
            "payload": {"run_id": run_id, "task_type": "webdav_after_report"},
        }

        store.finalize_report_delivery(run_id, {}, {}, maintenance_entries=[entry])
        task = store.get_maintenance_task(entry["task_key"])
        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["attempt_count"], 0)

    def test_failed_webdav_upload_is_deferred_without_reopening_report_delivery(self):
        store = self._store()
        run_id = store.start_run(0)
        entry = {
            "task_key": f"webdav_after_report:{run_id}",
            "payload": {"run_id": run_id, "task_type": "webdav_after_report"},
        }
        store.finalize_report_delivery(run_id, {}, {}, maintenance_entries=[entry])

        with patch("config.settings", self._settings()), patch(
            "utils.webdav_sync.sync_after_report", side_effect=RuntimeError("remote offline")
        ):
            summary = deliver_pending_after_report_syncs(store)

        self.assertEqual(summary, {"claimed": 1, "completed": 0, "deferred": 1})
        task = store.get_maintenance_task(entry["task_key"])
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["attempt_count"], 1)
        self.assertIn("remote offline", task["last_error"])
        with store._connect() as conn:
            run = conn.execute("SELECT status FROM daily_runs WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual(run["status"], "completed")

    def test_successful_webdav_upload_completes_only_its_maintenance_task(self):
        store = self._store()
        run_id = store.start_run(0)
        entry = {
            "task_key": f"webdav_after_report:{run_id}",
            "payload": {"run_id": run_id, "task_type": "webdav_after_report"},
        }
        store.finalize_report_delivery(run_id, {}, {}, maintenance_entries=[entry])

        with patch("config.settings", self._settings()), patch(
            "utils.webdav_sync.sync_after_report", return_value={"success": 1, "total": 1, "results": {}}
        ):
            summary = deliver_pending_after_report_syncs(store)

        self.assertEqual(summary, {"claimed": 1, "completed": 1, "deferred": 0})
        task = store.get_maintenance_task(entry["task_key"])
        self.assertEqual(task["status"], "completed")
        self.assertIsNotNone(task["completed_at"])

    def test_maintenance_entry_respects_webdav_configuration(self):
        with patch("config.settings", self._settings()):
            entry = after_report_sync_maintenance_entry("run-1")
        self.assertEqual(entry["task_key"], "webdav_after_report:run-1")

        with patch("config.settings", self._settings(WEBDAV_ENABLED=False)):
            self.assertIsNone(after_report_sync_maintenance_entry("run-1"))

    def test_downloaded_sqlite_snapshot_is_checked_and_preserves_previous_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            local_database = data_dir / "daily_research" / "daily_research.db"
            remote_database = root / "remote.db"
            local_database.parent.mkdir(parents=True)

            with sqlite3.connect(local_database) as conn:
                conn.execute("CREATE TABLE marker(value TEXT)")
                conn.execute("INSERT INTO marker VALUES ('local')")
            with sqlite3.connect(remote_database) as conn:
                conn.execute("CREATE TABLE marker(value TEXT)")
                conn.execute("INSERT INTO marker VALUES ('remote')")

            sync = _sync_shell(root)
            sync.client = _FakeClient(remote_database)
            sync._check_remote = lambda _remote: True
            sync._remote = lambda relative: relative

            self.assertTrue(sync._download_daily_research_snapshot(data_dir))
            with sqlite3.connect(local_database) as conn:
                self.assertEqual(conn.execute("SELECT value FROM marker").fetchone()[0], "remote")
            backup_path = local_database.with_name("daily_research.db.before_webdav_restore")
            with sqlite3.connect(backup_path) as conn:
                self.assertEqual(conn.execute("SELECT value FROM marker").fetchone()[0], "local")

    def test_invalid_download_does_not_replace_local_sqlite_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            local_database = data_dir / "daily_research" / "daily_research.db"
            remote_database = root / "remote.db"
            local_database.parent.mkdir(parents=True)

            with sqlite3.connect(local_database) as conn:
                conn.execute("CREATE TABLE marker(value TEXT)")
                conn.execute("INSERT INTO marker VALUES ('local')")
            remote_database.write_text("not a sqlite database", encoding="utf-8")

            sync = _sync_shell(root)
            sync.client = _FakeClient(remote_database)
            sync._check_remote = lambda _remote: True
            sync._remote = lambda relative: relative

            self.assertFalse(sync._download_daily_research_snapshot(data_dir))
            with sqlite3.connect(local_database) as conn:
                self.assertEqual(conn.execute("SELECT value FROM marker").fetchone()[0], "local")


if __name__ == "__main__":
    unittest.main()
