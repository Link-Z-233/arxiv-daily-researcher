import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import main  # noqa: E402
from notifications.notifier import RunResult, TrendRunResult  # noqa: E402


class ProcessExitCodeTests(unittest.TestCase):
    def test_explicit_result_outcomes_have_stable_exit_codes(self):
        self.assertEqual(main._result_exit_code(RunResult(success=True)), 0)
        self.assertEqual(main._result_exit_code(RunResult(success=False, error_message="failed")), 1)
        self.assertEqual(
            main._result_exit_code(
                TrendRunResult(success=False, interrupted=True, error_message="stopped")
            ),
            130,
        )
        self.assertEqual(main._result_exit_code(None), 1)

    def test_main_returns_pipeline_failure_instead_of_succeeding_silently(self):
        class _Pipeline:
            def run(self):
                return RunResult(success=False, error_message="arxiv unavailable")

        class _Lock:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        fake_settings = SimpleNamespace(
            ensure_directories=lambda: None,
            AUTO_UPDATE_ENABLED=False,
        )
        with patch("main.settings", fake_settings), patch(
            "main.setup_run_log", return_value=None
        ), patch("main.run_lock", return_value=_Lock()), patch(
            "main.daily_workflow_gate", return_value=_Lock()
        ), patch(
            "main.legacy_import_activity_gate", return_value=_Lock()
        ), patch(
            "modes.daily_research.DailyResearchPipeline", _Pipeline
        ):
            self.assertEqual(main.main([]), 1)

    def test_main_returns_zero_for_a_successful_trend_result(self):
        class _Pipeline:
            def __init__(self, **_kwargs):
                pass

            def run(self):
                return TrendRunResult(success=True)

        class _Lock:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        fake_settings = SimpleNamespace(
            ensure_directories=lambda: None,
            AUTO_UPDATE_ENABLED=False,
            RESEARCH_DEFAULT_DATE_RANGE_DAYS=30,
            RESEARCH_SORT_ORDER="descending",
            RESEARCH_MAX_RESULTS=20,
        )
        with patch("main.settings", fake_settings), patch("main.setup_run_log", return_value=None), patch(
            "main.run_lock", return_value=_Lock()
        ), patch(
            "main.daily_workflow_gate", return_value=_Lock()
        ), patch(
            "main.legacy_import_activity_gate", return_value=_Lock()
        ), patch("modes.trend_research.TrendResearchPipeline", _Pipeline):
            self.assertEqual(main.main(["--mode", "trend_research", "--keywords", "quantum"]), 0)


if __name__ == "__main__":
    unittest.main()
