"""长任务进度反馈：阶段心跳与活跃 run 的进度聚合。"""

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sources.base_source import PaperMetadata  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402


def _paper(pid: str) -> PaperMetadata:
    return PaperMetadata(
        paper_id=pid,
        title=f"Paper {pid}",
        authors=["Alice"],
        abstract="An abstract",
        published_date=datetime.now(timezone.utc),
        url=f"https://arxiv.org/abs/{pid}",
        source="arxiv",
    )


def _seed_paper(store: DailyResearchStore, run_id: str, pid: str) -> None:
    store.upsert_paper_seen(run_id, "arxiv", _paper(pid))


def _set_stage(store: DailyResearchStore, pid: str, column: str, value: str) -> None:
    with store._connect() as conn:
        conn.execute(
            f"UPDATE daily_papers SET {column} = ? WHERE paper_id = ?", (value, pid)
        )


class RunPhaseHeartbeatTests(unittest.TestCase):
    def test_heartbeat_roundtrip_and_cleanup_on_completion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_id = store.start_run(1)
            store.record_run_phase(run_id, "score")

            progress = store.active_run_progress()
            self.assertIsNotNone(progress)
            self.assertEqual(progress["phase"], "score")
            self.assertEqual(progress["run_id"], run_id)
            self.assertEqual(progress["registered"], 0)

            store.complete_run(run_id, {})
            self.assertIsNone(store.active_run_progress())
            self.assertIsNone(store.get_app_state("daily_run_phase"))

    def test_fail_run_clears_heartbeat_and_other_run_heartbeat_survives(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_a = store.start_run(1)
            store.record_run_phase(run_a, "scan")
            run_b = store.start_run(1)
            store.complete_run(run_b, {})

            # 只清理属于 run_b 的心跳；run_a 的心跳保留。
            progress = store.active_run_progress()
            self.assertIsNotNone(progress)
            self.assertEqual(progress["run_id"], run_a)

            store.fail_run(run_a, "boom")
            self.assertIsNone(store.active_run_progress())
            self.assertIsNone(store.get_app_state("daily_run_phase"))

    def test_stale_heartbeat_of_another_run_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            old_run = store.start_run(1)
            store.record_run_phase(old_run, "analyze")
            # 模拟旧进程留下的心跳：新 run 不应读到旧阶段。
            new_run = store.start_run(1)
            progress = store.active_run_progress()
            self.assertEqual(progress["run_id"], new_run)
            self.assertNotEqual(progress["phase"], "analyze")


class RunProgressAggregationTests(unittest.TestCase):
    def test_counts_and_inference_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_id = store.start_run(2)
            for pid in ("2501.00001v1", "2501.00002v1", "2501.00003v1"):
                _seed_paper(store, run_id, pid)

            # 无心跳：有未评分论文 → 推断为评分阶段。
            progress = store.active_run_progress()
            self.assertEqual(progress["phase"], "score")
            self.assertEqual(progress["registered"], 3)
            self.assertEqual(progress["scored"], 0)
            self.assertEqual(progress["awaiting_score"], 3)

            for pid in ("2501.00001v1", "2501.00002v1", "2501.00003v1"):
                _set_stage(store, pid, "score_status", "succeeded")
                _set_stage(store, pid, "translation_status", "succeeded")
            _set_stage(store, "2501.00001v1", "analysis_status", "succeeded")
            _set_stage(store, "2501.00002v1", "analysis_status", "failed")

            progress = store.active_run_progress()
            self.assertEqual(progress["phase"], "analyze")
            self.assertEqual(progress["scored"], 3)
            self.assertEqual(progress["analyzed"], 1)
            self.assertEqual(progress["failed"], 1)
            # 一篇待分析（00003）；00002 是 failed，不计入待分析。
            self.assertEqual(progress["awaiting_analysis"], 1)

    def test_heartbeat_wins_over_inference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_id = store.start_run(2)
            _seed_paper(store, run_id, "2501.00001v1")
            store.record_run_phase(run_id, "report")

            progress = store.active_run_progress()
            self.assertEqual(progress["phase"], "report")

    def test_phase_write_is_ignored_after_run_reaches_terminal_state(self):
        # 回归：交付提交后的收尾步骤不允许再写入心跳，否则终态 run
        # 会留下永不清除的陈旧阶段。
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_id = store.start_run(1)
            store.record_run_phase(run_id, "report")
            store.complete_run(run_id, {})

            store.record_run_phase(run_id, "deliver")
            self.assertIsNone(store.get_app_state("daily_run_phase"))
            self.assertIsNone(store.active_run_progress())


if __name__ == "__main__":
    unittest.main()
