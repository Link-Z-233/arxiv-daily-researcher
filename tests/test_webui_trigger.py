import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.webui_trigger import (  # noqa: E402
    TriggerValidationError,
    build_main_command,
    build_trigger_payload,
    enqueue_trigger,
    execute_trigger_request,
    read_trigger_payload,
    trigger_status_directory,
    validate_trigger_payload,
)


class _CompletedProcess:
    def __init__(self, return_code: int):
        self.return_code = return_code
        self.pid = 4321

    def wait(self, timeout=None):
        return self.return_code

    def poll(self):
        return self.return_code


class WebUITriggerTests(unittest.TestCase):
    def test_daily_request_is_atomic_and_has_no_arguments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            request_path = enqueue_trigger(data_dir, "daily_research")
            self.assertTrue(request_path.is_file())
            self.assertEqual(list(request_path.parent.glob("*.tmp")), [])
            payload = read_trigger_payload(request_path)
            self.assertEqual(payload["mode"], "daily_research")
            self.assertEqual(payload["args"], {})
            self.assertEqual(oct(request_path.stat().st_mode & 0o777), "0o600")


    def test_legacy_import_request_has_no_arguments(self):
        payload = build_trigger_payload("legacy_import")
        self.assertEqual(payload["mode"], "legacy_import")
        self.assertEqual(payload["args"], {})
        command = build_main_command(payload, Path("/worker"))
        self.assertEqual(command[:4], [sys.executable, "/worker/main.py", "--mode", "legacy_import"])
        with self.assertRaises(TriggerValidationError):
            build_trigger_payload("legacy_import", anything=1)

    def test_trend_request_uses_argument_list_and_preserves_quoted_phrase(self):
        payload = build_trigger_payload(
            "trend_research",
            keywords=["quantum error correction", "surface code"],
            date_from="2025-01-01",
            date_to="2025-12-31",
            categories=["quant-ph", "cs.AI"],
            sort_order="descending",
            max_results=123,
        )
        command = build_main_command(payload, Path("/worker"))
        self.assertEqual(command[:4], [sys.executable, "/worker/main.py", "--mode", "trend_research"])
        keywords_index = command.index("--keywords")
        self.assertEqual(command[keywords_index + 1 : keywords_index + 3], [
            "quantum error correction",
            "surface code",
        ])
        self.assertIn("--categories", command)
        self.assertIn("quant-ph", command)
        self.assertIn("123", command)

    def test_trend_analysis_prompt_is_optional_bounded_and_forwarded(self):
        base = dict(
            keywords=["quantum"],
            sort_order="ascending",
            max_results=10,
        )
        # 缺省：不带 --analysis-prompt
        command = build_main_command(
            build_trigger_payload("trend_research", **base), Path("/worker")
        )
        self.assertNotIn("--analysis-prompt", command)

        prompt = "请重点分析纠错码实验进展，按主题分节输出。" * 20
        payload = build_trigger_payload("trend_research", analysis_prompt=prompt, **base)
        self.assertEqual(payload["args"]["analysis_prompt"], prompt.strip())
        command = build_main_command(payload, Path("/worker"))
        prompt_index = command.index("--analysis-prompt")
        self.assertEqual(command[prompt_index + 1], prompt)

        with self.assertRaises(TriggerValidationError):
            build_trigger_payload(
                "trend_research", analysis_prompt="x" * 8001, **base
            )
        with self.assertRaises(TriggerValidationError):
            build_trigger_payload("trend_research", analysis_prompt=42, **base)

    def test_invalid_request_is_rejected_before_a_command_can_be_built(self):
        payload = {
            "schema_version": 1,
            "request_id": "00000000-0000-0000-0000-000000000001",
            "created_at": "now",
            "mode": "trend_research",
            "args": {
                "keywords": ["valid"],
                "categories": ["quant-ph; rm -rf /"],
                "sort_order": "ascending",
                "max_results": 10,
            },
        }
        with self.assertRaises(TriggerValidationError):
            validate_trigger_payload(payload)

    def test_failed_or_malformed_request_is_consumed_and_records_durable_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            request_path = enqueue_trigger(data_dir, "daily_research")
            claimed_path = request_path.with_suffix(".running")
            os.replace(request_path, claimed_path)
            (root / "main.py").write_text("# worker placeholder\n", encoding="utf-8")

            with patch("utils.webui_trigger.subprocess.Popen", return_value=_CompletedProcess(1)):
                self.assertEqual(execute_trigger_request(claimed_path, project_root=root), 1)

            self.assertFalse(claimed_path.exists())
            statuses = list(trigger_status_directory(data_dir).glob("*.json"))
            self.assertEqual(len(statuses), 1)
            status = json.loads(statuses[0].read_text(encoding="utf-8"))
            self.assertEqual(status["state"], "failed")
            self.assertEqual(status["return_code"], 1)

            malformed = claimed_path.with_name("malformed.running")
            malformed.write_text("not json", encoding="utf-8")
            self.assertEqual(execute_trigger_request(malformed, project_root=root), 1)
            self.assertFalse(malformed.exists())
            statuses = list(trigger_status_directory(data_dir).glob("*.json"))
            self.assertEqual(len(statuses), 2)
            self.assertIn("rejected", {json.loads(path.read_text())["state"] for path in statuses})

    def test_pid_file_is_removed_after_worker_exits(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            request_path = enqueue_trigger(data_dir, "daily_research")
            claimed_path = request_path.with_suffix(".running")
            os.replace(request_path, claimed_path)
            (root / "main.py").write_text("# worker placeholder\n", encoding="utf-8")
            pid_file = data_dir / "run" / "webui_triggered.pid"

            with patch("utils.webui_trigger.subprocess.Popen", return_value=_CompletedProcess(0)):
                self.assertEqual(
                    execute_trigger_request(claimed_path, project_root=root, pid_file=pid_file), 0
                )
            self.assertFalse(pid_file.exists())


if __name__ == "__main__":
    unittest.main()


class StopRequestTests(unittest.TestCase):
    def test_monitor_stop_requests_signals_matching_child(self):
        import subprocess
        import threading
        import time

        from utils import webui_trigger

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            # 子进程模拟 main.py：把 SIGTERM 当中断处理，以 130 退出。
            child_code = (
                "import signal, sys, time\n"
                "signal.signal(signal.SIGTERM, lambda *a: sys.exit(130))\n"
                "time.sleep(60)\n"
            )
            child = subprocess.Popen(
                [sys.executable, "-c", child_code],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            monitor = threading.Thread(
                target=webui_trigger._monitor_stop_requests,
                args=(child, data_dir),
                daemon=True,
            )
            monitor.start()
            time.sleep(0.5)

            webui_trigger.request_stop(data_dir, child.pid)

            child.wait(timeout=15)
            monitor.join(timeout=5)
            self.assertEqual(child.returncode, 130)
            # 停止请求被消费，不残留。
            self.assertEqual(
                list(webui_trigger.stop_request_directory(data_dir).glob("stop_*.json")),
                [],
            )

    def test_request_stop_writes_atomic_json_with_pid(self):
        from utils import webui_trigger

        with tempfile.TemporaryDirectory() as temp_dir:
            target = webui_trigger.request_stop(Path(temp_dir), 4242)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["pid"], 4242)
            self.assertEqual(target.name, "stop_4242.json")


class SkippedBusyMappingTests(unittest.TestCase):
    def test_exit_75_maps_to_skipped_busy_status(self):
        """被锁跳过的触发不得伪装成 succeeded。"""
        from utils import webui_trigger
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            from utils.webui_trigger import trigger_directory

            requests = trigger_directory(data_dir)
            requests.mkdir(parents=True, exist_ok=True)
            request_path = requests / "20260101T000000000000Z_c0ffee0000000000000000000000eeee.json"
            webui_trigger._atomic_write_json(
                request_path,
                {
                    "schema_version": 1,
                    "request_id": "c0ffee0000000000000000000000eeee",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "mode": "daily_research",
                    "args": {},
                },
            )
            claimed = request_path.with_suffix(".running")
            request_path.rename(claimed)

            # 伪造一个总是以 75 退出的 main.py。
            with patch.object(
                webui_trigger.subprocess, "Popen"
            ) as popen:
                popen.return_value.returncode = 75
                popen.return_value.poll.return_value = 75
                popen.return_value.pid = 4242
                popen.return_value.wait.return_value = 75
                with patch.object(webui_trigger.threading, "Thread"):
                    rc = webui_trigger.execute_trigger_request(
                        claimed, project_root=Path.cwd()
                    )
            self.assertEqual(rc, 75)
            status_dir = trigger_status_directory(data_dir)
            status_files = list(status_dir.glob("c0ffee0000000000000000000000eeee.json"))
            self.assertEqual(len(status_files), 1)
            state = json.loads(status_files[0].read_text(encoding="utf-8"))["state"]
            self.assertEqual(state, "skipped_busy")
