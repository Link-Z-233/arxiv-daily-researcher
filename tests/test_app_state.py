import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.daily_research_store import DailyResearchStore  # noqa: E402


class AppStateTests(unittest.TestCase):
    def test_app_state_round_trip_and_overwrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            self.assertIsNone(store.get_app_state("update_notified_version"))
            store.set_app_state("update_notified_version", "4.1")
            self.assertEqual(store.get_app_state("update_notified_version"), "4.1")
            store.set_app_state("update_notified_version", "4.2")
            self.assertEqual(store.get_app_state("update_notified_version"), "4.2")

    def test_app_state_table_added_to_existing_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.db"
            store = DailyResearchStore(db_path)
            store.start_run(total_papers=0)
            # 模拟旧库：删表后重连，_init_db 应补建 app_state。
            import sqlite3

            with sqlite3.connect(db_path) as conn:
                conn.execute("DROP TABLE app_state")
            reopened = DailyResearchStore(db_path)
            reopened.set_app_state("k", "v")
            self.assertEqual(reopened.get_app_state("k"), "v")


if __name__ == "__main__":
    unittest.main()
