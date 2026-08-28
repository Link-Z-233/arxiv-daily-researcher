"""数据分析页用量统计改版的回归测试：近一年热力图 + 静态自适应折线图。"""

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui.tabs import analytics  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402


def _row(prompt: int, completion: int, runs: int = 1) -> dict:
    return {
        "prompt": prompt,
        "completion": completion,
        "total": prompt + completion,
        "runs": runs,
    }


class NiceCeilingTests(unittest.TestCase):
    def test_embedded_visualizations_use_native_streamlit_iframe(self):
        with patch.object(analytics.st, "iframe", create=True) as iframe:
            analytics.components_html("<p>chart</p>", height=160)

        iframe.assert_called_once_with("<p>chart</p>", height=160)

    def test_ceiling_is_rounded_up_to_nice_steps(self):
        self.assertEqual(analytics._nice_ceiling(0), 1.0)
        self.assertEqual(analytics._nice_ceiling(3), 5.0)
        self.assertEqual(analytics._nice_ceiling(60), 100.0)
        self.assertEqual(analytics._nice_ceiling(4_800), 5_000.0)
        self.assertEqual(analytics._nice_ceiling(1_200_000), 2_000_000.0)


class AnalyticsPlacementTests(unittest.TestCase):
    def test_data_analysis_keeps_only_usage_metrics(self):
        with (
            patch.object(analytics, "_render_usage_section") as usage,
            patch.object(analytics, "_render_llm_health_section") as llm_health,
            patch.object(analytics, "_render_source_health_section") as source_health,
            patch.object(analytics.st, "divider") as divider,
        ):
            analytics.render_content({"sample": "env"}, {"sample": "config"})

        usage.assert_called_once_with({"sample": "env"}, {"sample": "config"})
        llm_health.assert_not_called()
        source_health.assert_not_called()
        divider.assert_not_called()

    def test_system_diagnostics_contains_health_sections(self):
        with (
            patch.object(analytics, "_render_diagnostics_section") as diagnostics,
            patch.object(analytics, "_render_llm_health_section") as llm_health,
            patch.object(analytics, "_render_source_health_section") as source_health,
            patch.object(analytics.st, "divider") as divider,
        ):
            analytics.render_diagnostics({"sample": "env"}, {"sample": "config"})

        diagnostics.assert_called_once_with({"sample": "config"})
        llm_health.assert_called_once_with({"sample": "config"})
        source_health.assert_called_once_with({"sample": "env"}, {"sample": "config"})
        self.assertEqual(divider.call_count, 2)


class _HealthPanelStreamlit:
    """Minimal Streamlit spy for health-table rendering regression tests."""

    def __init__(self):
        self.calls = []

    def markdown(self, *args, **kwargs):
        self.calls.append(("markdown", args, kwargs))

    def caption(self, *args, **kwargs):
        self.calls.append(("caption", args, kwargs))

    def info(self, *args, **kwargs):
        self.calls.append(("info", args, kwargs))

    def warning(self, *args, **kwargs):
        self.calls.append(("warning", args, kwargs))

    def error(self, *args, **kwargs):
        self.calls.append(("error", args, kwargs))

    def segmented_control(self, *args, **kwargs):
        self.calls.append(("segmented_control", args, kwargs))
        return kwargs.get("default")

    def dataframe(self, *args, **kwargs):
        self.calls.append(("dataframe", args, kwargs))


class HealthPanelTests(unittest.TestCase):
    def test_health_panels_have_independent_seven_day_table_filters(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "daily.db"
            store = DailyResearchStore(db_path)
            store.record_llm_health_event(
                "cheap", "fast-model", False, "provider unavailable"
            )
            store.record_source_health_event(
                "semantic_scholar",
                False,
                task_kind="history_omission_scan",
                error_summary="HTTP 429 https://api.example.test/?token=must-not-appear",
                origin_key="test-semantic-failure",
            )
            fake_st = _HealthPanelStreamlit()
            with (
                patch.object(analytics, "st", fake_st),
                patch.object(analytics, "t", side_effect=lambda key: key),
                patch.object(
                    analytics, "_daily_db_path_from_config", return_value=db_path
                ),
            ):
                analytics._render_llm_health_section({})
                analytics._render_source_health_section({}, {})

        segmented_keys = [
            kwargs.get("key")
            for name, _args, kwargs in fake_st.calls
            if name == "segmented_control"
        ]
        self.assertEqual(
            segmented_keys,
            ["llm_health_window_days", "source_health_window_days"],
        )
        tables = [args[0] for name, args, _kwargs in fake_st.calls if name == "dataframe"]
        self.assertEqual(len(tables), 2)
        self.assertIn("fast-model", repr(tables[0]))
        self.assertIn("provider unavailable", repr(tables[0]))
        rendered_sources = repr(tables[1])
        self.assertIn("Semantic Scholar", rendered_sources)
        self.assertIn("health_task_history_omission", rendered_sources)
        self.assertIn("429", rendered_sources)
        self.assertNotIn("must-not-appear", rendered_sources)
        self.assertNotIn("https://", rendered_sources)


class HeatmapTests(unittest.TestCase):
    def test_heatmap_includes_today_when_today_is_monday(self):
        class Monday(date):
            @classmethod
            def today(cls):
                return cls(2026, 8, 24)

        with patch.object(analytics, "date", Monday):
            html = analytics._render_heatmap_html({"2026-08-24": _row(100, 50)})

        self.assertIn("2026-08-24", html)

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
        # 固定布局 + 显式宽度：月份标签不占列宽，单元格保持等宽网格
        self.assertIn("table-layout:fixed", html)
        self.assertIn("overflow:visible", header_row)
        self.assertRegex(html, r"width:\d+px;\">")
        self.assertTrue(
            all("width:11px" in cell for cell in header_row.split("<td")[2:])
        )

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
