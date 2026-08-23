"""关键词维护任务：每日 0 点静默执行的标准化 + 趋势报告。"""

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modes import keyword_maintenance  # noqa: E402


class ReportFrequencyTests(unittest.TestCase):
    def test_frequency_rules(self):
        monday = date(2026, 8, 24)  # 周一
        tuesday = date(2026, 8, 25)
        first_of_month = date(2026, 9, 1)

        for frequency in ("always", "daily"):
            self.assertTrue(
                keyword_maintenance.should_generate_report(frequency, tuesday)
            )
        self.assertTrue(keyword_maintenance.should_generate_report("weekly", monday))
        self.assertFalse(keyword_maintenance.should_generate_report("weekly", tuesday))
        self.assertTrue(
            keyword_maintenance.should_generate_report("monthly", first_of_month)
        )
        self.assertFalse(
            keyword_maintenance.should_generate_report("monthly", tuesday)
        )
        self.assertFalse(keyword_maintenance.should_generate_report("unknown", monday))
        self.assertFalse(keyword_maintenance.should_generate_report("", monday))


class _FakeTracker:
    def __init__(self, stats=None, fail=False):
        self.calls = []
        self._stats = stats or {"processed": 3, "new_canonical": 1, "merged": 2}
        self._fail = fail

    def run_daily_normalization(self):
        self.calls.append("normalize")
        if self._fail:
            raise RuntimeError("LLM 中转不可用")
        return self._stats

    def get_top_keywords(self):
        self.calls.append("top_keywords")
        return []

    def get_trends(self):
        self.calls.append("trends")
        return {}

    def generate_bar_chart(self):
        self.calls.append("bar_chart")
        return None

    def generate_trend_chart(self):
        self.calls.append("trend_chart")
        return None

    default_days = 30


class KeywordMaintenanceJobTests(unittest.TestCase):
    def _run(self, tracker, **setting_overrides):
        values = {
            "KEYWORD_TRACKER_ENABLED": True,
            "KEYWORD_NORMALIZATION_ENABLED": True,
            "KEYWORD_REPORT_ENABLED": True,
            "KEYWORD_REPORT_FREQUENCY": "daily",
        }
        values.update(setting_overrides)
        with patch.multiple(
            "modes.keyword_maintenance.settings", **values
        ), patch(
            "keyword_tracker.KeywordTracker", return_value=tracker
        ) as _tracker_cls:
            code = keyword_maintenance.run_keyword_maintenance(today=date(2026, 8, 25))
        return code

    def test_disabled_flags_skip_silently(self):
        tracker = _FakeTracker()
        self.assertEqual(self._run(tracker, KEYWORD_TRACKER_ENABLED=False), 0)
        self.assertEqual(self._run(tracker, KEYWORD_NORMALIZATION_ENABLED=False), 0)
        self.assertEqual(tracker.calls, [])

    def test_normalization_runs_and_report_generated_when_due(self):
        tracker = _FakeTracker()
        rendered = {"markdown": "reports/keyword_trend/markdown/x.md"}
        with patch(
            "report.keyword_trend.KeywordTrendReporter.render",
            return_value=rendered,
        ) as render:
            code = self._run(tracker)
        self.assertEqual(code, 0)
        self.assertIn("normalize", tracker.calls)
        self.assertIn("top_keywords", tracker.calls)
        render_kwargs = render.call_args.kwargs
        self.assertEqual(render_kwargs["today"], date(2026, 8, 25))

    def test_report_skipped_when_frequency_not_due(self):
        tracker = _FakeTracker()
        code = self._run(tracker, KEYWORD_REPORT_FREQUENCY="weekly")
        self.assertEqual(code, 0)
        self.assertIn("normalize", tracker.calls)
        self.assertNotIn("top_keywords", tracker.calls)

    def test_normalization_failure_exits_nonzero_without_raising(self):
        tracker = _FakeTracker(fail=True)
        code = self._run(tracker)
        self.assertEqual(code, 1)
        # 标准化失败后不再基于旧数据生成趋势报告
        self.assertNotIn("top_keywords", tracker.calls)

    def test_report_failure_exits_nonzero_without_raising(self):
        tracker = _FakeTracker()
        with patch(
            "report.keyword_trend.KeywordTrendReporter.render",
            side_effect=RuntimeError("disk full"),
        ):
            code = self._run(tracker)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
