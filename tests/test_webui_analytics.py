"""数据分析页用量统计改版的回归测试：近一年热力图 + 静态自适应折线图。"""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui.tabs import analytics  # noqa: E402


def _row(prompt: int, completion: int, runs: int = 1) -> dict:
    return {
        "prompt": prompt,
        "completion": completion,
        "total": prompt + completion,
        "runs": runs,
    }


class NiceCeilingTests(unittest.TestCase):
    def test_ceiling_is_rounded_up_to_nice_steps(self):
        self.assertEqual(analytics._nice_ceiling(0), 1.0)
        self.assertEqual(analytics._nice_ceiling(3), 5.0)
        self.assertEqual(analytics._nice_ceiling(60), 100.0)
        self.assertEqual(analytics._nice_ceiling(4_800), 5_000.0)
        self.assertEqual(analytics._nice_ceiling(1_200_000), 2_000_000.0)


class HeatmapTests(unittest.TestCase):
    def test_heatmap_covers_the_last_year_in_week_columns(self):
        today = date.today()
        inside = today - timedelta(days=300)
        outside = today - timedelta(days=400)
        daily = {
            today.isoformat(): _row(100, 50),
            inside.isoformat(): _row(200, 80),
            outside.isoformat(): _row(9_999, 9_999),  # 一年之外，不应出现
        }
        html = analytics._render_heatmap_html(daily)

        self.assertIn('id="usage-heatmap"', html)
        self.assertNotIn(outside.isoformat(), html)
        self.assertIn(today.isoformat(), html)
        self.assertIn(inside.isoformat(), html)
        # 53 列周网格：header 行有 53 个月度标签单元格
        header_row = html.split("<tr>")[1]
        self.assertEqual(header_row.count("<td"), 54)  # 1 空占位 + 53 周

    def test_heatmap_document_autoscrolls_to_the_latest(self):
        document = analytics._scroll_right_document(analytics._render_heatmap_html({}))
        self.assertIn("scrollLeft=w.scrollWidth", document)


class TrendChartTests(unittest.TestCase):
    def _rows(self, count: int, *, gap_after: int | None = None) -> list[dict]:
        start = date(2026, 8, 1)
        rows = []
        for i in range(count):
            if gap_after is not None and i == gap_after:
                start += timedelta(days=5)  # 制造 5 天缺口
            rows.append(
                {
                    "date": (start + timedelta(days=i)).isoformat(),
                    "prompt": 100 * (i + 1),
                    "completion": 20 * (i + 1),
                }
            )
        return rows

    def test_chart_is_stacked_svg_with_adaptive_axis_labels(self):
        html = analytics._render_trend_chart_html(self._rows(30))
        self.assertIn("<svg", html)
        self.assertIn("<polygon", html)
        # 最大总量 3600 → 好看刻度 5000 出现在 Y 轴标签里
        self.assertIn("5.0k", html)
        self.assertNotIn("vega", html.lower())

    def test_missing_days_are_filled_with_zero_usage(self):
        rows = analytics._fill_daily_gaps(self._rows(4, gap_after=1))
        dates = [row["date"] for row in rows]
        # 缺口内日期补 0，x 轴按真实日期等距
        self.assertEqual(dates[0], "2026-08-01")
        self.assertIn("2026-08-04", dates)
        self.assertIn("2026-08-07", dates)
        gap_row = next(
            row for row in rows if row["date"] == "2026-08-03"
        )
        self.assertEqual(gap_row["prompt"], 0)
        self.assertEqual(gap_row["completion"], 0)

    def test_many_dates_are_downsampled(self):
        html = analytics._render_trend_chart_html(self._rows(400))
        # 抽稀后仍可渲染；首日标签来自第一条数据
        self.assertIn("<polygon", html)
        self.assertIn("08-01", html)


if __name__ == "__main__":
    unittest.main()
