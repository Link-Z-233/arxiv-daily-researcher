"""数据源健康：scan receipts 的按源聚合。"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.daily_research_store import DailyResearchStore  # noqa: E402


def _receipt(
    scanned_at: str, *, failed: bool = False, new_candidates: int = 0, error=None
) -> dict:
    return {
        "source": "arxiv",
        "status": "failed" if failed else "succeeded",
        "scanned_at": scanned_at,
        "domain_receipts": [
            {
                "domain": "physics.optics",
                "status": "failed" if failed else "succeeded",
                "new_candidates": 0 if failed else new_candidates,
                "error": error if failed else None,
            }
        ],
    }


class SourceHealthTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = DailyResearchStore(Path(self._tmp.name) / "daily_research.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _record(self, source: str, receipt: dict):
        run_id = self.store.start_run(0)
        # record_scan_receipt 校验 receipt.source 与传入 source 一致
        receipt = dict(receipt, source=source)
        self.store.record_scan_receipt(run_id, source, receipt)

    def test_no_receipts_returns_empty(self):
        self.assertEqual(self.store.get_source_health(), {})

    def test_summary_aggregates_status_rate_and_candidates(self):
        base = datetime(2026, 8, 20, 8, 0, 0)
        for index in range(4):
            stamp = (base + timedelta(minutes=index)).isoformat()
            self._record(
                "arxiv",
                _receipt(
                    stamp,
                    failed=(index == 1),
                    new_candidates=3,
                    error="HTTP 503" if index == 1 else None,
                ),
            )
        for index in range(2):
            self._record(
                "huggingface_papers",
                _receipt((base + timedelta(minutes=index)).isoformat()),
            )

        health = self.store.get_source_health(window=10)
        self.assertEqual(set(health), {"arxiv", "huggingface_papers"})

        arxiv = health["arxiv"]
        self.assertEqual(arxiv["last_status"], "succeeded")
        self.assertEqual(arxiv["scans_in_window"], 4)
        self.assertEqual(arxiv["succeeded_in_window"], 3)
        self.assertAlmostEqual(arxiv["success_rate"], 0.75)
        self.assertEqual(arxiv["last_new_candidates"], 3)
        self.assertIn("503", arxiv["last_error"])

        hf = health["huggingface_papers"]
        self.assertEqual(hf["last_status"], "succeeded")
        self.assertEqual(hf["scans_in_window"], 2)
        self.assertEqual(hf["last_new_candidates"], 0)

    def test_last_error_taken_from_newest_failed(self):
        base = datetime(2026, 8, 21, 8, 0, 0)
        self._record(
            "arxiv", _receipt(base.isoformat(), failed=True, error="old error")
        )
        self._record(
            "arxiv",
            _receipt(
                (base + timedelta(minutes=1)).isoformat(),
                failed=True,
                error="new error",
            ),
        )
        health = self.store.get_source_health()
        self.assertIn("new error", health["arxiv"]["last_error"])

    def test_failed_latest_marks_status(self):
        base = datetime(2026, 8, 22, 8, 0, 0)
        self._record("arxiv", _receipt(base.isoformat(), new_candidates=5))
        self._record(
            "arxiv",
            _receipt(
                (base + timedelta(minutes=1)).isoformat(), failed=True, error="boom"
            ),
        )
        health = self.store.get_source_health()
        self.assertEqual(health["arxiv"]["last_status"], "failed")
        # 最近新增取最近一次“成功”扫描，而不是最近一次扫描
        self.assertEqual(health["arxiv"]["last_new_candidates"], 5)

    def test_window_bounds(self):
        base = datetime(2026, 8, 19, 8, 0, 0)
        for index in range(6):
            self._record(
                "arxiv",
                _receipt((base + timedelta(minutes=index)).isoformat()),
            )
        health = self.store.get_source_health(window=3)
        self.assertEqual(health["arxiv"]["scans_in_window"], 3)


if __name__ == "__main__":
    unittest.main()
