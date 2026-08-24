import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.webui_trigger import build_trigger_payload  # noqa: E402
from webui.tabs.data_management import _legacy_import_already_queued  # noqa: E402


class LegacyImportQueueUiTests(unittest.TestCase):
    def test_other_running_webui_task_does_not_disable_legacy_import(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_dir = Path(temp_dir)
            (queue_dir / "daily.running").write_text(
                json.dumps(build_trigger_payload("daily_research")), encoding="utf-8"
            )
            self.assertFalse(_legacy_import_already_queued(queue_dir))

    def test_same_legacy_import_request_disables_duplicate_click(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_dir = Path(temp_dir)
            (queue_dir / "legacy.running").write_text(
                json.dumps(build_trigger_payload("legacy_import")), encoding="utf-8"
            )
            self.assertTrue(_legacy_import_already_queued(queue_dir))

    def test_malformed_request_does_not_block_legacy_import(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_dir = Path(temp_dir)
            (queue_dir / "bad.json").write_text("not-json", encoding="utf-8")
            self.assertFalse(_legacy_import_already_queued(queue_dir))


if __name__ == "__main__":
    unittest.main()
