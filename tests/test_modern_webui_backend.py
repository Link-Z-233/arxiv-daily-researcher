"""Focused contract tests for the modern WebUI backend helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from modern_webui import backend
from utils.webui_trigger import enqueue_trigger


class ModernBackendTests(unittest.TestCase):
    def test_public_settings_never_returns_a_secret_value(self) -> None:
        with patch.object(backend, "flat_config", return_value={"daily_run_time": "12:00"}), patch.object(
            backend,
            "read_env",
            return_value={
                "CHEAP_LLM__API_KEY": "private-value",
                "CHEAP_LLM__MODEL_NAME": "small-model",
            },
        ):
            payload = backend.public_settings()

        self.assertNotIn("CHEAP_LLM__API_KEY", payload["env"])
        self.assertTrue(payload["secrets"]["CHEAP_LLM__API_KEY"])
        self.assertEqual(payload["env"]["CHEAP_LLM__MODEL_NAME"], "small-model")
        categories = {item["code"]: item["label"] for item in payload["arxiv_categories"]}
        self.assertGreater(len(categories), 100)
        self.assertEqual(categories["quant-ph"], "quant-ph · Quantum Physics")

    def test_save_settings_rejects_unknown_fields_before_writing(self) -> None:
        with self.assertRaisesRegex(backend.ModernWebUIError, "不支持"):
            backend.save_settings({"not_a_real_config_option": True}, {})

    def test_task_records_read_the_same_trigger_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            queued = enqueue_trigger(data_dir, "legacy_import", full_repair=False)
            with patch.object(backend, "DEFAULT_DATA_DIR", data_dir):
                rows = backend.task_records({"legacy_import"})

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["request_id"], queued.stem.rsplit("_", 1)[-1])
        self.assertEqual(rows[0]["state"], "queued")
        self.assertFalse(rows[0]["args"]["full_repair"])

    def test_active_locks_includes_parameterized_trend_locks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            run_dir = data_dir / "run"
            run_dir.mkdir()
            trend_lock = run_dir / "trend_research_a1b2c3d4.lock"
            trend_lock.write_text("PID=42\n", encoding="utf-8")
            with patch.object(backend, "DEFAULT_DATA_DIR", data_dir), patch.object(
                backend, "configured_data_dir", return_value=data_dir
            ), patch.object(backend, "is_lock_held", return_value=True):
                locks = backend.active_locks()

        self.assertEqual(locks, [{"name": trend_lock.name, "pid": 42}])

    def test_run_status_surfaces_a_live_lock_without_a_receipt(self) -> None:
        with patch.object(backend, "flat_config", return_value={}), patch.object(
            backend, "active_locks", return_value=[{"name": "trend_research_a1b2.lock", "pid": 42}]
        ), patch.object(backend, "task_records", return_value=[]), patch.object(
            backend, "open_store", return_value=None
        ), patch.object(backend, "_live_log_tail", return_value=None):
            status = backend.run_status("trend")

        self.assertTrue(status["is_active"])
        self.assertFalse(status["can_start"])
        self.assertEqual(status["task"]["label"], "趋势任务")
        self.assertEqual(status["relevant_locks"][0]["pid"], 42)

    def test_log_categories_match_the_streamlit_three_picker_layout(self) -> None:
        self.assertEqual(backend._log_category("system.log"), "system")
        self.assertEqual(backend._log_category("daily_20260829.log"), "run")
        self.assertEqual(backend._log_category("history_data_repair_20260829.log"), "run")
        self.assertEqual(backend._log_category("trend_20260829.log"), "other")

    def test_report_tokens_are_bound_to_the_report_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "daily.html"
            report.write_text("<html></html>", encoding="utf-8")
            token = backend._report_token(report, root)
            self.assertEqual(backend._report_path(token, root), report)
            with self.assertRaises(backend.ModernWebUIError):
                backend._report_path("Li4vZXRjL3Bhc3N3ZA", root)

    def test_daily_report_source_preserves_legacy_and_nested_source_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            daily_root = root / "daily_research" / "html"
            legacy = daily_root / "arxiv_Report_2026-08-01_12-00-00.html"
            nested = daily_root / "openalex" / "OpenAlex_Report_2026-08-02_12-00-00.html"
            nested.parent.mkdir(parents=True)
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.touch()
            nested.touch()

            self.assertEqual(backend._daily_report_source(legacy, root), "arxiv")
            self.assertEqual(backend._daily_report_source(nested, root), "openalex")

    def test_report_rows_use_streamlit_friendly_labels_and_disambiguate_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "daily_research" / "html" / "arxiv" / "arxiv_Report_2026-08-02_10-11-12_123.html"
            second = root / "daily_research" / "html" / "arxiv" / "arxiv_Report_2026-08-02_10-11-12_456.html"
            first.parent.mkdir(parents=True)
            first.write_text("<html></html>", encoding="utf-8")
            second.write_text("<html></html>", encoding="utf-8")
            with patch.object(backend, "_report_source_labels", return_value={"arxiv": "arXiv"}):
                rows = [
                    backend._report_row(first, root, "daily", "arxiv"),
                    backend._report_row(second, root, "daily", "arxiv"),
                ]
            backend._disambiguate_report_labels(rows)

        self.assertEqual(rows[0]["source_label"], "arXiv")
        self.assertEqual(rows[0]["label"], "2026-08-02  10:11:12.123")
        self.assertEqual(rows[1]["label"], "2026-08-02  10:11:12.456")

    def test_report_preference_can_initialise_the_shared_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "daily_research.db"
            with patch.object(backend, "configured_db_path", return_value=database):
                result = backend.set_preference(
                    {
                        "source": "arxiv",
                        "paper_id": "2608.00001",
                        "title": "A saved report paper",
                        "preference": "like",
                    }
                )
                store = backend.open_store()

            self.assertTrue(database.is_file())
            self.assertEqual(result, {"ok": True, "preference": "like"})
            self.assertEqual(
                store.get_paper_preference("arxiv", "2608.00001")["preference"],
                "like",
            )

    def test_trend_prompt_templates_round_trip_with_bounded_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trend_prompt_templates.json"
            with patch.object(backend, "TREND_PROMPT_TEMPLATES_PATH", path):
                rows = backend.save_trend_prompt_template("量子计算", "关注实验进展")
                self.assertEqual(rows, [{"name": "量子计算", "text": "关注实验进展"}])
                self.assertEqual(
                    backend.list_trend_prompt_templates(),
                    [{"name": "量子计算", "text": "关注实验进展"}],
                )
                self.assertEqual(backend.delete_trend_prompt_template("量子计算"), [])
                with self.assertRaisesRegex(backend.ModernWebUIError, "名称不能为空"):
                    backend.save_trend_prompt_template("", "内容")

    def test_webdav_client_uses_current_saved_panel_values(self) -> None:
        settings = {
            "webdav_enabled": True,
            "webdav_remote_path": "/research/",
            "proxy_enabled": True,
            "proxy_webdav": True,
            "proxy_url": "http://proxy.example:7890",
        }
        env = {
            "WEBDAV_URL": "https://dav.example.test/root/",
            "WEBDAV_USERNAME": "operator",
            "WEBDAV_PASSWORD": "saved-password",
        }
        client = MagicMock()
        with patch.object(backend, "WebDAVSync", return_value=client) as construct:
            result = backend._configured_webdav_client(settings, env)

        self.assertIs(result, client)
        construct.assert_called_once_with(
            url=env["WEBDAV_URL"],
            username=env["WEBDAV_USERNAME"],
            password=env["WEBDAV_PASSWORD"],
            remote_path="/research/",
            proxy_url="http://proxy.example:7890",
        )

    def test_manual_webdav_sync_uses_saved_scope(self) -> None:
        client = MagicMock()
        client.sync_all.return_value = {"success": 2, "total": 2}
        settings = {
            "webdav_enabled": True,
            "webdav_sync_configs": False,
            "webdav_sync_history": True,
            "webdav_sync_keywords": False,
            "webdav_sync_reports": True,
        }
        with patch.object(backend, "flat_config", return_value=settings), patch.object(
            backend, "_configured_webdav_client", return_value=client
        ):
            result = backend.webdav_operation("upload")

        self.assertTrue(result["ok"])
        client.sync_all.assert_called_once_with(
            direction="upload",
            include_reports=True,
            include_configs=False,
            include_history=True,
            include_keywords=False,
        )

    def test_manual_backup_keeps_local_snapshot_and_mirrors_when_configured(self) -> None:
        settings = {
            "webdav_enabled": True,
            "backup_local_retention_days": 31,
            "backup_local_same_day_max_count": 4,
        }
        webdav_client = MagicMock()
        with patch.object(backend, "flat_config", return_value=settings), patch.object(
            backend, "configured_data_dir", return_value=Path("/data")
        ), patch.object(backend, "configured_db_path", return_value=Path("/data/db.sqlite")), patch.object(
            backend, "_configured_webdav_client", return_value=webdav_client
        ), patch.object(backend, "create_backup", return_value={"created": True, "name": "snapshot.db.gz"}) as create:
            result = backend.create_local_backup()

        self.assertTrue(result["created"])
        create.assert_called_once_with(
            Path("/data"),
            database=Path("/data/db.sqlite"),
            retention_days=31,
            same_day_max_count=4,
            webdav_sync=webdav_client,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
