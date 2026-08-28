"""Focused contract tests for the modern WebUI backend helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
