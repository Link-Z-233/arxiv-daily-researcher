import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.config_io import (  # noqa: E402
    _atomic_write_text,
    build_config_dict,
    flatten_config_dict,
    write_config_json,
    write_env,
)


class ConfigIOReliabilityTests(unittest.TestCase):
    def test_atomic_config_write_keeps_previous_content_when_replace_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text('{"old": true}\n', encoding="utf-8")

            with patch("utils.config_io.os.replace", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    _atomic_write_text(path, '{"new": true}\n')

            self.assertEqual(path.read_text(encoding="utf-8"), '{"old": true}\n')
            self.assertEqual(list(Path(temp_dir).glob(".*.tmp")), [])

    def test_config_and_env_writes_are_atomic_and_keep_expected_permissions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            env_path = root / ".env"

            write_config_json({"search_settings": {"days": 1}}, config_path)
            with patch("utils.config_io.ENV_EXAMPLE_PATH", root / "does-not-exist"):
                write_env({"SECRET": "value"}, env_path)

            self.assertEqual(oct(config_path.stat().st_mode & 0o777), "0o644")
            self.assertEqual(oct(env_path.stat().st_mode & 0o777), "0o600")
            self.assertIn('"search_settings"', config_path.read_text(encoding="utf-8"))
            self.assertEqual(env_path.read_text(encoding="utf-8"), "SECRET=value\n")

    def test_config_write_preserves_an_existing_custom_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text('{"old": true}\n', encoding="utf-8")
            config_path.chmod(0o640)

            write_config_json({"search_settings": {"days": 2}}, config_path)

            self.assertEqual(oct(config_path.stat().st_mode & 0o777), "0o640")

    def test_legacy_daily_result_caps_are_not_written_or_exposed(self):
        """Daily scans must never regain an item budget through config saves."""
        config = build_config_dict(
            search_days=3,
            max_results=1,
            max_results_per_source={"arxiv": 1},
        )
        self.assertEqual(config["search_settings"], {"search_days": 3})

        legacy_flat = flatten_config_dict(
            {
                "search_settings": {
                    "search_days": 3,
                    "max_results": 1,
                    "max_results_per_source": {"arxiv": 1},
                }
            }
        )
        self.assertEqual(legacy_flat["search_days"], 3)
        self.assertNotIn("max_results", legacy_flat)
        self.assertNotIn("max_results_per_source", legacy_flat)

    def test_arxiv_announcement_grace_round_trips_through_config_io(self):
        config = build_config_dict(arxiv_announcement_lookback_grace_days=4)
        self.assertEqual(
            config["data_sources"]["arxiv"]["announcement_lookback_grace_days"], 4
        )

        flat = flatten_config_dict(config)
        self.assertEqual(flat["arxiv_announcement_lookback_grace_days"], 4)

    def test_huggingface_papers_configuration_and_proxy_round_trip(self):
        config = build_config_dict(
            enabled_sources=["arxiv", "huggingface_papers"],
            huggingface_papers_availability_lag_days=3,
            huggingface_papers_lookback_grace_days=4,
            huggingface_papers_request_timeout_seconds=45,
            huggingface_papers_request_interval_seconds=0.5,
            proxy_huggingface_papers=True,
        )
        hf = config["data_sources"]["huggingface_papers"]
        self.assertEqual(hf["availability_lag_days"], 3)
        self.assertEqual(hf["lookback_grace_days"], 4)
        self.assertEqual(hf["request_timeout_seconds"], 45)
        self.assertEqual(hf["request_interval_seconds"], 0.5)
        self.assertTrue(config["proxy"]["scope"]["huggingface_papers"])

        flat = flatten_config_dict(config)
        self.assertEqual(flat["enabled_sources"], ["arxiv", "huggingface_papers"])
        self.assertEqual(flat["huggingface_papers_availability_lag_days"], 3)
        self.assertEqual(flat["huggingface_papers_lookback_grace_days"], 4)
        self.assertEqual(flat["huggingface_papers_request_timeout_seconds"], 45)
        self.assertEqual(flat["huggingface_papers_request_interval_seconds"], 0.5)
        self.assertTrue(flat["proxy_huggingface_papers"])

    def test_legacy_configuration_without_hf_block_stays_compatible(self):
        flat = flatten_config_dict({"data_sources": {"enabled": ["arxiv"]}})
        self.assertEqual(flat["huggingface_papers_availability_lag_days"], 2)
        self.assertEqual(flat["huggingface_papers_lookback_grace_days"], 2)
        self.assertFalse(flat["proxy_huggingface_papers"])

    def test_v2_scoring_strategy_round_trips_and_missing_strategy_is_legacy(self):
        config = build_config_dict(
            score_strategy="core_relevance_v2",
            core_relevance_threshold=6.5,
            core_keyword_min_score=8.0,
            reference_ranking_weight=0.4,
        )
        self.assertEqual(config["scoring_settings"]["strategy"]["id"], "core_relevance_v2")
        flat = flatten_config_dict(config)
        self.assertEqual(flat["score_strategy"], "core_relevance_v2")
        self.assertEqual(flat["core_relevance_threshold"], 6.5)
        self.assertEqual(flat["core_keyword_min_score"], 8.0)
        self.assertEqual(flat["reference_ranking_weight"], 0.4)

        legacy = flatten_config_dict({"scoring_settings": {}})
        self.assertEqual(legacy["score_strategy"], "legacy_weighted_keyword_v1")
        self.assertFalse(legacy["score_strategy_explicit"])
        legacy_round_trip = build_config_dict(**legacy)
        self.assertNotIn("strategy", legacy_round_trip["scoring_settings"])


if __name__ == "__main__":
    unittest.main()
