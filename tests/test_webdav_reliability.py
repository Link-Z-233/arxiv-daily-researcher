import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.daily_research_store import DailyResearchStore  # noqa: E402
from utils.webdav_sync import (  # noqa: E402
    WebDAVSync,
    after_report_sync_maintenance_entry,
    cron_schedule_matches,
    deliver_pending_after_report_syncs,
    validate_cron_schedule,
)
from utils.webdav_scheduler import run_scheduled_webdav_sync  # noqa: E402


class _FakeClient:
    def __init__(self, remote_database: Path):
        self.remote_database = remote_database

    def download_file(self, _remote_file: str, local_file: str) -> None:
        Path(local_file).write_bytes(self.remote_database.read_bytes())


class _ConfigDownloadClient:
    def __init__(self, content: str):
        self.content = content

    def download_file(self, _remote_file: str, local_file: str) -> None:
        Path(local_file).write_text(self.content, encoding="utf-8")


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

    def test_invalid_webdav_config_download_keeps_the_existing_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "configs" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text('{"old": true}\n', encoding="utf-8")

            sync = _sync_shell(root)
            sync.client = _ConfigDownloadClient("<html>not config</html>")
            sync._check_remote = lambda _remote: True
            sync._remote = lambda relative: relative

            result = sync.download_configs()
            self.assertFalse(result["configs/config.json"])
            self.assertEqual(config_path.read_text(encoding="utf-8"), '{"old": true}\n')
            self.assertEqual(list(config_path.parent.glob("*.download")), [])

    def test_scheduled_cron_matching_supports_ranges_steps_names_and_cron_day_rules(self):
        self.assertTrue(
            cron_schedule_matches("*/15 8-18 * jan,jun mon-fri", datetime(2026, 6, 1, 9, 15))
        )
        self.assertFalse(
            cron_schedule_matches("*/15 8-18 * jan,jun mon-fri", datetime(2026, 6, 1, 9, 14))
        )
        self.assertTrue(cron_schedule_matches("0 8 2 * mon", datetime(2026, 6, 1, 8, 0)))
        self.assertTrue(cron_schedule_matches("0 8 2 * mon", datetime(2026, 6, 2, 8, 0)))
        self.assertEqual(validate_cron_schedule("@hourly"), "0 * * * *")
        with self.assertRaisesRegex(ValueError, "5 个字段"):
            validate_cron_schedule("0 8 * *")
        with self.assertRaisesRegex(ValueError, "超出范围"):
            validate_cron_schedule("61 8 * * *")

    def test_scheduled_sync_is_idempotent_and_retries_without_daily_report_state(self):
        store = self._store()
        settings = self._settings(
            WEBDAV_SYNC_MODE="scheduled",
            WEBDAV_CRON_SCHEDULE="0 23 * * *",
        )
        now = datetime(2026, 8, 12, 23, 0)
        calls = []

        def offline(_log):
            calls.append("offline")
            raise RuntimeError("remote offline")

        first = run_scheduled_webdav_sync(
            now,
            store=store,
            settings_override=settings,
            sync_callable=offline,
        )
        self.assertEqual(first["matched"], True)
        self.assertEqual(first["queued"], True)
        self.assertEqual(first["claimed"], 1)
        self.assertEqual(first["deferred"], 1)
        task_key = "webdav_scheduled:202608122300"
        failed_task = store.get_maintenance_task(task_key)
        self.assertEqual(failed_task["status"], "pending")
        self.assertEqual(failed_task["attempt_count"], 1)

        # A duplicate cron invocation in the same minute cannot queue or call
        # WebDAV again before the persisted retry delay is due.
        duplicate = run_scheduled_webdav_sync(
            now,
            store=store,
            settings_override=settings,
            sync_callable=offline,
        )
        self.assertEqual(duplicate["queued"], False)
        self.assertEqual(duplicate["claimed"], 0)
        self.assertEqual(calls, ["offline"])

        with store._connect() as conn:
            conn.execute(
                "UPDATE maintenance_outbox SET next_attempt_at = ? WHERE task_key = ?",
                ("2000-01-01T00:00:00", task_key),
            )

        succeeded = run_scheduled_webdav_sync(
            datetime(2026, 8, 12, 23, 1),
            store=store,
            settings_override=settings,
            sync_callable=lambda _log: {"results": {"snapshot": True}},
        )
        self.assertEqual(succeeded["matched"], False)
        self.assertEqual(succeeded["claimed"], 1)
        self.assertEqual(succeeded["completed"], 1)
        self.assertEqual(store.get_maintenance_task(task_key)["status"], "completed")

    def test_scheduled_sync_does_not_enqueue_when_mode_is_not_scheduled(self):
        store = self._store()
        settings = self._settings(WEBDAV_SYNC_MODE="after_report")
        result = run_scheduled_webdav_sync(
            datetime(2026, 8, 12, 23, 0),
            store=store,
            settings_override=settings,
            sync_callable=lambda _log: self.fail("should not sync"),
        )
        self.assertEqual(result, {
            "enabled": False,
            "matched": False,
            "queued": False,
            "claimed": 0,
            "completed": 0,
            "deferred": 0,
        })

    def test_scheduled_sync_keeps_existing_tasks_pending_after_mode_change(self):
        store = self._store()
        store.enqueue_maintenance_task(
            "webdav_scheduled:202608122300", {"task_type": "webdav_scheduled"}
        )
        settings = self._settings(WEBDAV_SYNC_MODE="manual")
        result = run_scheduled_webdav_sync(
            datetime(2026, 8, 12, 23, 1),
            store=store,
            settings_override=settings,
            sync_callable=lambda _log: self.fail("should not sync"),
        )
        self.assertEqual(result["claimed"], 0)
        self.assertEqual(
            store.get_maintenance_task("webdav_scheduled:202608122300")["status"], "pending"
        )


if __name__ == "__main__":
    unittest.main()
