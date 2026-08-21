import io
import multiprocessing
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils import run_lock as run_lock_module  # noqa: E402


def _hold_lock(lock_path: str, ready, release) -> None:
    """Child process used to prove the OS lock, not the PID text, is decisive."""
    import fcntl

    with open(lock_path, "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        ready.set()
        release.wait(10)


class RunLockSafetyTests(unittest.TestCase):
    def test_stale_diagnostic_file_does_not_block_or_get_deleted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "daily_research.lock"
            old_time = datetime.now() - timedelta(days=3)
            lock_path.write_text(
                f"PID={os.getpid()}, started={old_time:%Y-%m-%d %H:%M:%S}",
                encoding="utf-8",
            )

            with patch.object(run_lock_module, "_lock_dir", return_value=Path(temp_dir)), patch(
                "config.settings", SimpleNamespace(RUN_LOCK_MAX_AGE_HOURS=1)
            ):
                with run_lock_module.run_lock("daily_research"):
                    self.assertTrue(run_lock_module.is_lock_held(lock_path))

            self.assertTrue(lock_path.exists())
            self.assertFalse(run_lock_module.is_lock_held(lock_path))

    def test_expired_held_lock_never_sends_a_signal_to_diagnostic_pid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "daily_research.lock"
            old_time = datetime.now() - timedelta(days=3)
            lock_path.write_text(
                f"PID={os.getpid()}, started={old_time:%Y-%m-%d %H:%M:%S}",
                encoding="utf-8",
            )
            ready = multiprocessing.Event()
            release = multiprocessing.Event()
            holder = multiprocessing.Process(target=_hold_lock, args=(str(lock_path), ready, release))
            holder.start()
            self.addCleanup(lambda: holder.join(timeout=5))
            self.addCleanup(lambda: release.set())
            self.assertTrue(ready.wait(5))

            output = io.StringIO()
            with patch.object(run_lock_module, "_lock_dir", return_value=Path(temp_dir)), patch(
                "config.settings", SimpleNamespace(RUN_LOCK_MAX_AGE_HOURS=1)
            ), patch.object(run_lock_module.os, "kill") as kill:
                with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
                    with run_lock_module.run_lock("daily_research"):
                        self.fail("a held lock must not be acquired")

            # 被锁跳过必须用专用退出码，WebUI 触发链路才能与真跑完区分。
            self.assertEqual(raised.exception.code, run_lock_module.LOCK_SKIPPED_EXIT_CODE)
            self.assertEqual(run_lock_module.LOCK_SKIPPED_EXIT_CODE, 75)
            kill.assert_not_called()
            self.assertIn("不会自动终止进程", output.getvalue())
            self.assertTrue(holder.is_alive())

    def test_kernel_lock_state_is_not_inferred_from_reused_or_dead_pid_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "daily_research.lock"
            lock_path.write_text("PID=999999, started=2020-01-01 00:00:00", encoding="utf-8")

            self.assertFalse(run_lock_module.is_lock_held(lock_path))

            ready = multiprocessing.Event()
            release = multiprocessing.Event()
            holder = multiprocessing.Process(target=_hold_lock, args=(str(lock_path), ready, release))
            holder.start()
            self.addCleanup(lambda: holder.join(timeout=5))
            self.addCleanup(lambda: release.set())
            self.assertTrue(ready.wait(5))
            self.assertTrue(run_lock_module.is_lock_held(lock_path))


if __name__ == "__main__":
    unittest.main()
