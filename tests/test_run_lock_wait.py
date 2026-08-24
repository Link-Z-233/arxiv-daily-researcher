"""后台作业的空闲等待：等待持锁释放、超时返回 False。"""

import fcntl
import multiprocessing
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.run_lock import (  # noqa: E402
    busy_lock_files,
    daily_workflow_gate,
    legacy_import_activity_gate,
    wait_for_idle,
)


def _hold_shared_gate(lock_path: str, ready, release) -> None:
    """Hold the normal-worker side of the import gate in another process."""
    with open(lock_path, "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        ready.set()
        release.wait(10)


def _hold_exclusive_gate(lock_path: str, ready, release) -> None:
    """Hold a daily-workflow gate in another process."""
    with open(lock_path, "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        ready.set()
        release.wait(10)


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

    @patch("utils.run_lock._lock_dir")
    def test_exclusive_import_gate_waits_for_existing_worker_activity(self, mock_dir):
        mock_dir.return_value = self.lock_dir
        ready = multiprocessing.Event()
        release = multiprocessing.Event()
        holder = multiprocessing.Process(
            target=_hold_shared_gate,
            args=(str(self.lock_dir / ".legacy_import_activity.gate"), ready, release),
        )
        holder.start()
        self.addCleanup(lambda: release.set())
        self.addCleanup(lambda: holder.join(timeout=5))
        self.assertTrue(ready.wait(5))

        acquired = threading.Event()

        def acquire_import_gate():
            with legacy_import_activity_gate(exclusive=True):
                acquired.set()

        waiter = threading.Thread(target=acquire_import_gate)
        waiter.start()
        self.assertFalse(acquired.wait(0.1))

        release.set()
        waiter.join(timeout=5)
        self.assertFalse(waiter.is_alive())
        self.assertTrue(acquired.is_set())

    @patch("utils.run_lock._lock_dir")
    def test_daily_workflow_gate_serializes_daily_style_runs(self, mock_dir):
        mock_dir.return_value = self.lock_dir
        ready = multiprocessing.Event()
        release = multiprocessing.Event()
        holder = multiprocessing.Process(
            target=_hold_exclusive_gate,
            args=(str(self.lock_dir / ".daily_workflow.gate"), ready, release),
        )
        holder.start()
        self.addCleanup(lambda: release.set())
        self.addCleanup(lambda: holder.join(timeout=5))
        self.assertTrue(ready.wait(5))

        acquired = threading.Event()
        waiter = threading.Thread(
            target=lambda: _enter_daily_gate(acquired)
        )
        waiter.start()
        self.assertFalse(acquired.wait(0.1))

        release.set()
        waiter.join(timeout=5)
        self.assertFalse(waiter.is_alive())
        self.assertTrue(acquired.is_set())


def _enter_daily_gate(acquired) -> None:
    with daily_workflow_gate():
        acquired.set()


if __name__ == "__main__":
    unittest.main()
