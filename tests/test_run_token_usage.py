import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.daily_research_store import DailyResearchStore  # noqa: E402


class RunTokenUsageTests(unittest.TestCase):
    def test_record_and_aggregate_by_day_and_model(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            run_id = store.start_run(total_papers=0)
            store.record_token_usage(
                run_id,
                {
                    "cheap-model": {"prompt": 100, "completion": 50, "total": 150},
                    "smart-model": {"prompt": 200, "completion": 80, "total": 280},
                },
            )
            store.record_token_usage(
                "trend_20260822_120000",
                {"cheap-model": {"prompt": 10, "completion": 5, "total": 15}},
                mode="trend_research",
            )

            days = store.get_daily_token_totals()
            self.assertEqual(len(days), 1)
            self.assertEqual(days[0]["prompt"], 310)
            self.assertEqual(days[0]["completion"], 135)
            self.assertEqual(days[0]["total"], 445)
            self.assertEqual(days[0]["runs"], 2)

            models = store.get_token_usage_by_model()
            self.assertEqual(models[0]["model"], "smart-model")
            self.assertEqual(models[0]["total"], 280)
            self.assertEqual(models[1]["model"], "cheap-model")
            self.assertEqual(models[1]["total"], 165)

    def test_rerecording_same_run_replaces_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            run_id = store.start_run(total_papers=0)
            store.record_token_usage(run_id, {"m": {"prompt": 100, "completion": 0}})
            store.record_token_usage(run_id, {"m": {"prompt": 300, "completion": 20}})

            days = store.get_daily_token_totals()
            self.assertEqual(days[0]["total"], 320)

    def test_daily_window_filters_old_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            store.record_token_usage("r1", {"m": {"prompt": 5, "completion": 5}})
            # 手动把这条记录改到一年前，模拟历史数据。
            import sqlite3

            with sqlite3.connect(store.db_path) as conn:
                conn.execute(
                    "UPDATE run_token_usage SET recorded_at = '2020-01-01T00:00:00'"
                )
            self.assertEqual(store.get_daily_token_totals(days=30), [])
            self.assertEqual(len(store.get_daily_token_totals()), 1)


if __name__ == "__main__":
    unittest.main()
