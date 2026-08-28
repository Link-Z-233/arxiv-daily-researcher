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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
