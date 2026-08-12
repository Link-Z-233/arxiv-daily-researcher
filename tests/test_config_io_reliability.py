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


if __name__ == "__main__":
    unittest.main()
