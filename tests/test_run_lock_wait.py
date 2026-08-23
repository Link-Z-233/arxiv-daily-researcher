"""后台作业的空闲等待：等待持锁释放、超时返回 False。"""

import fcntl
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.run_lock import busy_lock_files, wait_for_idle  # noqa: E402


class WaitForIdleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lock_dir = Path(self.tmp.name) / "run"
        self.lock_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    @patch("utils.run_lock._lock_dir")
    def test_returns_true_when_no_locks_held(self, mock_dir):
        mock_dir.return_value = self.lock_dir
        self.assertEqual(busy_lock_files(["daily_research"]), [])
        self.assertTrue(wait_for_idle(["daily_research"], poll_seconds=0.01))

    @patch("utils.run_lock._lock_dir")
    def test_waits_until_lock_released(self, mock_dir):
        mock_dir.return_value = self.lock_dir
        lock_file = (self.lock_dir / "daily_research.lock").open("a+")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            # 超时路径：锁始终被持有。
            self.assertFalse(
                wait_for_idle(
                    ["daily_research"], poll_seconds=0.01, timeout_seconds=0.05
                )
            )
            self.assertEqual(
                [p.name for p in busy_lock_files(["daily_research", "trend_research_*"])],
                ["daily_research.lock"],
            )
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
        self.assertTrue(wait_for_idle(["daily_research"], poll_seconds=0.01))


if __name__ == "__main__":
    unittest.main()
