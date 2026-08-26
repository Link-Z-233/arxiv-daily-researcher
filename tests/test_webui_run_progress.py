"""WebUI 长任务进度面板：阶段文案与进度条比例。"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui.tabs import run_manager  # noqa: E402


class _FakeStreamlit:
    def __init__(self):
        self.captions = []
        self.progress_values = []

    def caption(self, text, *args, **kwargs):
        self.captions.append(text)

    def progress(self, value, *args, **kwargs):
        self.progress_values.append(value)


def _progress(**overrides):
    base = {
        "run_id": "r1",
        "started_at": None,
        "scan_days": 2,
        "phase": "score",
        "registered": 10,
        "scored": 4,
        "analyzed": 0,
        "completed": 0,
        "failed": 1,
        "awaiting_score": 6,
        "awaiting_analysis": 0,
    }
    base.update(overrides)
    return base


class RunProgressRenderTests(unittest.TestCase):
    def _render(self, progress):
        fake_st = _FakeStreamlit()

        def fake_t(key):
            if key == "rm_progress_caption":
                return "{phase}|reg {registered}|scored {scored}|ana {analyzed}|done {completed}|fail {failed}|{elapsed}"
            return key

        with (
            patch.object(run_manager, "st", fake_st),
            patch.object(run_manager, "t", side_effect=fake_t),
        ):
            run_manager._render_run_progress(progress)
        return fake_st

    def test_score_phase_shows_ratio_bar_and_counts(self):
        fake_st = self._render(_progress(phase="score", scored=4, registered=10))
        self.assertEqual(len(fake_st.captions), 1)
        caption = fake_st.captions[0]
        self.assertIn("rm_progress_phase_score", caption)
        self.assertIn("4", caption)
        self.assertIn("10", caption)
        self.assertEqual(fake_st.progress_values, [0.4])

    def test_analyze_phase_bar_uses_pending_analysis_denominator(self):
        fake_st = self._render(
            _progress(phase="analyze", scored=10, analyzed=3, awaiting_analysis=3)
        )
        self.assertIn("rm_progress_phase_analyze", fake_st.captions[0])
        self.assertEqual(fake_st.progress_values, [0.5])

    def test_report_phase_has_no_ratio_bar(self):
        fake_st = self._render(
            _progress(phase="report", scored=10, analyzed=2, completed=5)
        )
        self.assertIn("rm_progress_phase_report", fake_st.captions[0])
        self.assertEqual(fake_st.progress_values, [])

    def test_zero_registered_renders_without_bar(self):
        fake_st = self._render(_progress(phase="score", registered=0, scored=0))
        self.assertEqual(fake_st.progress_values, [])

    def test_legacy_detail_uses_its_own_progress_ratio(self):
        fake_st = self._render(
            _progress(
                phase="legacy_reports",
                detail="解析旧 HTML 报告",
                current=3,
                total=8,
                registered=0,
            )
        )
        self.assertIn("rm_progress_phase_legacy_reports", fake_st.captions[0])
        self.assertIn("解析旧 HTML 报告 (3/8)", fake_st.captions[0])
        self.assertEqual(fake_st.progress_values, [0.375])

    def test_elapsed_from_started_at_is_formatted(self):
        fake_st = self._render(
            _progress(phase="scan", started_at="2026-08-23T12:00:00")
        )
        # started_at 很久以前 → 小时格式 "XhYYm"
        self.assertRegex(fake_st.captions[0], r"\dh\d{2}m")


if __name__ == "__main__":
    unittest.main()
