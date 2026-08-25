import html
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui.tabs.reports import (  # noqa: E402
    _build_sandboxed_preview_html,
    _configured_reports_dir,
    _discover_reports,
    _filter_visible_reports,
    _fmt_daily,
    _latest_visible_report,
    _strip_legacy_title_badges,
)


class _IframeAttributeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.attributes = None

    def handle_starttag(self, tag, attrs):
        if tag == "iframe" and self.attributes is None:
            self.attributes = dict(attrs)


class WebUiReportPreviewTests(unittest.TestCase):
    def test_custom_reports_path_is_resolved_from_persisted_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reports_root = Path(temp_dir) / "custom-reports"
            with (
                patch(
                    "utils.config_io.read_config_json",
                    return_value={"paths": {"reports": "data/custom-reports"}},
                ),
                patch(
                    "utils.config_io._resolve_project_relative_config_path",
                    return_value=reports_root,
                ),
            ):
                self.assertEqual(_configured_reports_dir(), reports_root)

    def test_preview_uses_fixed_800px_frame_without_dynamic_height_protocol(self):
        from webui.tabs import reports as reports_tab

        self.assertNotIn("arxiv-report-height", reports_tab._MARK_BAR_JS)
        self.assertFalse(hasattr(reports_tab, "_append_preview_height_reporter"))
        component = (
            Path(reports_tab.__file__).resolve().parent.parent
            / "report_component" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn("var PREVIEW_HEIGHT = 800", component)
        self.assertIn('frame.style.height = PREVIEW_HEIGHT + "px"', component)
        self.assertNotIn("arxiv-report-height", component)

    def test_preview_uses_an_origin_isolated_inner_iframe(self):
        report_html = (
            '<!doctype html><html><body><script>window.parent.pwned = true;</script>'
            '<a href="https://example.test">paper</a></body></html>'
        )

        wrapper = _build_sandboxed_preview_html(report_html)
        parser = _IframeAttributeParser()
        parser.feed(wrapper)

        self.assertIsNotNone(parser.attributes)
        self.assertEqual(parser.attributes["sandbox"], "allow-scripts allow-popups")
        self.assertEqual(parser.attributes["referrerpolicy"], "no-referrer")
        self.assertNotIn("allow-same-origin", parser.attributes["sandbox"])
        self.assertNotIn("allow-forms", parser.attributes["sandbox"])
        self.assertNotIn("allow-downloads", parser.attributes["sandbox"])
        self.assertNotIn("allow-popups-to-escape-sandbox", parser.attributes["sandbox"])
        self.assertEqual(parser.attributes["srcdoc"], report_html)

    def test_report_cannot_break_out_of_srcdoc_attribute(self):
        payload = '</iframe><script>window.parent.pwned = true;</script><img src=x onerror=alert(1)>'

        wrapper = _build_sandboxed_preview_html(payload)

        self.assertNotIn(payload, wrapper)
        self.assertIn(html.escape(payload, quote=True), wrapper)
        parser = _IframeAttributeParser()
        parser.feed(wrapper)
        self.assertEqual(parser.attributes["srcdoc"], payload)

    def test_custom_non_arxiv_source_is_auto_discovered_and_filterable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reports_root = Path(temp_dir)
            arxiv_dir = reports_root / "daily_research" / "html" / "arxiv"
            custom_dir = reports_root / "daily_research" / "html" / "custom_physics"
            arxiv_dir.mkdir(parents=True)
            custom_dir.mkdir(parents=True)
            (arxiv_dir / "ARXIV_Report_2026-08-21_08-00-00.html").write_text(
                "arxiv", encoding="utf-8"
            )
            custom_path = custom_dir / "CUSTOM_PHYSICS_Report_2026-08-21_08-00-00.html"
            custom_path.write_text("custom", encoding="utf-8")

            discovered = _discover_reports(reports_root)
            arxiv_only = _filter_visible_reports(discovered, False)
            all_sources = _filter_visible_reports(discovered, True)

            self.assertEqual([item.source for item in arxiv_only["daily"]], ["arxiv"])
            self.assertEqual(
                {item.source for item in all_sources["daily"]},
                {"arxiv", "custom_physics"},
            )
            self.assertIn(custom_path, [item.path for item in all_sources["daily"]])

    def test_flat_daily_report_layout_is_discovered_without_source_specific_code(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reports_root = Path(temp_dir)
            daily_dir = reports_root / "daily_research" / "html"
            daily_dir.mkdir(parents=True)
            custom_path = daily_dir / "CUSTOM_PHYSICS_Report_2026-08-21_08-00-00.html"
            custom_path.write_text("custom", encoding="utf-8")

            discovered = _discover_reports(reports_root)

            self.assertEqual(len(discovered["daily"]), 1)
            self.assertEqual(discovered["daily"][0].source, "custom_physics")
            self.assertEqual(discovered["daily"][0].path, custom_path)

    def test_bare_version_badges_are_hidden_but_revision_labels_kept(self):
        report_html = (
            '<div class="card-title"><a href="#">1. Paper One</a>'
            '<span class="revision-label">v1</span></div>'
            '<div class="card-title"><a href="#">2. Paper Two</a>'
            '<span class="revision-label">🔁 修订版 v2（上一版本 v1）</span></div>'
            '<div class="card-title"><a href="#">3. Paper Three</a>'
            '<span class="revision-label">↻ 重试</span></div>'
        )

        stripped = _strip_legacy_title_badges(report_html)

        self.assertNotIn(">v1<", stripped)
        self.assertNotIn("↻ 重试", stripped)
        self.assertIn("🔁 修订版 v2", stripped)
        # 沙箱预览同样过滤
        wrapper = _build_sandboxed_preview_html(report_html)
        self.assertNotIn("&gt;v1&lt;", wrapper)
        self.assertNotIn("重试", wrapper)
        self.assertIn("修订版 v2", wrapper)


if __name__ == "__main__":
    unittest.main()


class DailyReportTimestampOrderTests(unittest.TestCase):
    def test_daily_reports_sort_by_filename_timestamp_not_mtime(self):
        import os

        with tempfile.TemporaryDirectory() as temp_dir:
            reports_root = Path(temp_dir)
            arxiv_dir = reports_root / "daily_research" / "html" / "arxiv"
            arxiv_dir.mkdir(parents=True)
            newest = arxiv_dir / "ARXIV_Report_2026-08-22_01-57-34_392421.html"
            newest.write_text("n", encoding="utf-8")
            legacy = arxiv_dir / "ARXIV_Report_2026-03-03_16-10-08.html"
            legacy.write_text("l", encoding="utf-8")
            backfill = arxiv_dir / "ARXIV_Report_2026-03-15_14-23-05_000001.html"
            backfill.write_text("b", encoding="utf-8")
            # 误导性 mtime：补跑文件刚刚生成，但日期属于过去。
            os.utime(backfill, (1900000000, 1900000000))

            discovered = _discover_reports(reports_root)
            order = [item.path.name for item in discovered["daily"]]
            self.assertEqual(order, [
                "ARXIV_Report_2026-08-22_01-57-34_392421.html",
                "ARXIV_Report_2026-03-15_14-23-05_000001.html",
                "ARXIV_Report_2026-03-03_16-10-08.html",
            ])
            visible = _filter_visible_reports(discovered, False)
            self.assertEqual(_latest_visible_report(visible).path, newest)

    def test_fmt_daily_accepts_microsecond_filenames(self):
        self.assertEqual(
            _fmt_daily("ARXIV_Report_2026-08-22_01-57-34_392421"),
            "2026-08-22  01:57:34",
        )
        self.assertEqual(
            _fmt_daily("ARXIV_Report_2026-03-03_16-10-08"),
            "2026-03-03  16:10:08",
        )
        self.assertEqual(_fmt_daily("weird_name"), "weird_name")

    def test_same_second_daily_reports_keep_distinct_picker_labels(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reports_root = Path(temp_dir)
            arxiv_dir = reports_root / "daily_research" / "html" / "arxiv"
            arxiv_dir.mkdir(parents=True)
            for micro in ("100001", "200002"):
                (arxiv_dir / f"ARXIV_Report_2026-08-22_01-57-34_{micro}.html").write_text(
                    micro, encoding="utf-8"
                )

            reports = _discover_reports(reports_root)["daily"]

            self.assertEqual(len(reports), 2)
            self.assertEqual(len({report.display for report in reports}), 2)
            self.assertIn(".200002", reports[0].display)
            self.assertIn(".100001", reports[1].display)


class DailyReportInlineMarkTests(unittest.TestCase):
    """日报保持原始 HTML 样式，收藏按钮注入卡片，点击经组件回传落库。"""

    REPORT_HTML = (
        "<!DOCTYPE html><html><head><title>Report</title></head><body>"
        '<div class="stats-bar"></div><h2>Papers</h2>'
        '<div class="card pass">'
        '<div class="card-title"><a href="https://arxiv.org/abs/2401.00001">'
        "1. Quantum Error Correction at Scale</a></div>"
        '<div class="field"><span class="score">7.5</span></div></div>'
        '<div class="card fail">'
        '<div class="card-title">2. Classical Shadows Revisited</div>'
        '<div class="field"><span class="score">3.2</span></div></div>'
        "</body></html>"
    )

    def _papers(self):
        return [
            {
                "source": "arxiv",
                "paper_id": "2401.00001",
                "title": "Quantum Error Correction at Scale",
                "preference": "like",
                "authors": ["A. Author"],
                "categories": ["quant-ph"],
            },
            {
                "source": "arxiv",
                "paper_id": "2401.00002",
                "title": "Classical Shadows Revisited",
                "preference": None,
                "authors": [],
                "categories": [],
            },
        ]

    def test_injection_adds_bars_and_keeps_report_body(self):
        from webui.tabs.reports import _inject_mark_controls

        enriched = _inject_mark_controls(self.REPORT_HTML, self._papers())

        # 报告原有内容原样保留
        self.assertIn("Quantum Error Correction at Scale</a>", enriched)
        self.assertIn('<div class="card fail">', enriched)
        self.assertEqual(enriched.count('<div class="card '), 2)
        # 两张卡片都注入了标记条，标题配对正确
        self.assertEqual(enriched.count('class="arxiv-mark-bar"'), 2)
        # 标记条位于评分行内（第一个 field 行的开头，浮动到最右侧）
        bar1 = enriched.index('data-paper="2401.00001"')
        field1 = enriched.index('<div class="field">', enriched.index("Quantum Error"))
        self.assertGreater(bar1, field1)
        self.assertLess(bar1, enriched.index('<span class="score">7.5', field1))
        bar2 = enriched.index('data-paper="2401.00002"')
        field2 = enriched.index('<div class="field">', enriched.index("Classical Shadows"))
        self.assertGreater(bar2, field2)
        # 只有两个按钮：👍/👎（没有清除按钮）
        bar_block = enriched[bar1 : enriched.index("</div>", bar1)]
        self.assertEqual(bar_block.count("arxiv-mark-btn"), 2)
        self.assertNotIn('data-pref="clear"', enriched)
        # active 态不写入 HTML：状态由宿主经 arxiv-report-states 消息下发，
        # 标记变化时报告 HTML 逐字节不变、iframe 不重建（不闪烁）。
        self.assertNotIn("arxiv-mark-btn active", enriched)
        self.assertIn('data-current="none"', enriched)
        self.assertIn("arxiv-report-states", enriched)
        # 样式与脚本注入在 </body> 之前
        self.assertIn(".arxiv-mark-btn{", enriched)
        self.assertIn("__arxivMarkInjected", enriched)
        self.assertLess(enriched.rfind("__arxivMarkInjected"), enriched.rfind("</body>"))

    def test_card_without_field_row_gets_no_bar(self):
        from webui.tabs.reports import _inject_mark_controls

        report = (
            '<div class="card pass"><div class="card-title">'
            "1. Quantum Error Correction at Scale</div></div>"
        )
        # 卡片没有评分行（field）时不注入，避免破坏布局
        self.assertIs(_inject_mark_controls(report, self._papers()), report)

    def test_no_matching_cards_returns_original(self):
        from webui.tabs.reports import _inject_mark_controls

        papers = [dict(p, title="Completely Unrelated Title") for p in self._papers()]
        self.assertIs(_inject_mark_controls(self.REPORT_HTML, papers), self.REPORT_HTML)
        self.assertIs(_inject_mark_controls("<p>no cards</p>", self._papers()), "<p>no cards</p>")

    def test_paper_ids_and_sources_are_escaped(self):
        from webui.tabs.reports import _inject_mark_controls

        hostile = [dict(p, paper_id='"><script>alert(1)</script>') for p in self._papers()]
        enriched = _inject_mark_controls(self.REPORT_HTML, hostile)
        self.assertNotIn("<script>alert(1)</script>", enriched)
        self.assertIn("data-paper=", enriched)

    def _seed_db(self, db_path: Path):
        import json
        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE daily_papers (
                source TEXT NOT NULL, paper_id TEXT NOT NULL,
                canonical_id TEXT NOT NULL DEFAULT '',
                version INTEGER NOT NULL DEFAULT 0,
                first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                run_id TEXT, paper_json TEXT NOT NULL, score_json TEXT,
                score_audit_json TEXT, abstract_cn TEXT, analysis_json TEXT,
                scored_at TEXT, translated_at TEXT, analyzed_at TEXT,
                score_input_fingerprint TEXT,
                translation_input_fingerprint TEXT,
                analysis_input_fingerprint TEXT,
                completed_at TEXT, last_error TEXT,
                PRIMARY KEY (source, paper_id)
            );
            CREATE TABLE paper_preferences (
                source TEXT NOT NULL, paper_id TEXT NOT NULL,
                canonical_id TEXT, version INTEGER,
                preference TEXT NOT NULL CHECK (preference IN ('like','dislike','none')),
                title TEXT NOT NULL,
                authors_json TEXT NOT NULL DEFAULT '[]',
                categories_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (source, paper_id)
            );
            """
        )
        conn.execute(
            "INSERT INTO daily_papers (source, paper_id, first_seen_at,"
            " last_seen_at, paper_json, score_json, completed_at)"
            " VALUES ('arxiv', '2401.00001', '2026-08-22T08:00:00',"
            " '2026-08-22T08:05:00', ?, ?, '2026-08-22T09:00:00')",
            (
                json.dumps(
                    {
                        "title": "Quantum Error Correction at Scale",
                        "authors": ["A. Author"],
                        "url": "https://arxiv.org/abs/2401.00001",
                        "categories": ["quant-ph"],
                        "published_date": "2026-08-20",
                    }
                ),
                json.dumps({"total_score": 7.5, "is_qualified": True}),
            ),
        )
        conn.commit()
        conn.close()

    def test_component_mark_value_is_persisted_once(self):
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch

        from utils.daily_research_store import DailyResearchStore
        from webui.tabs import reports as reports_tab

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "daily_research.db"
            self._seed_db(db_path)
            report_path = Path(temp_dir) / "arxiv_report.html"
            report_path.write_text(self.REPORT_HTML, encoding="utf-8")
            report = reports_tab.ReportFile(
                path=report_path,
                display="2026-08-22 12:00:00",
                source="arxiv",
                report_type="daily",
                date_key="2026-08-22",
            )

            reruns = []

            class _Ctx:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def __getattr__(self, name):
                    return lambda *a, **k: None

            class FakeST:
                session_state = {}

                def rerun(self):
                    reruns.append(1)

                def expander(self, *a, **k):
                    return _Ctx()

                def columns(self, spec):
                    count = spec if isinstance(spec, int) else len(spec)
                    return [_Ctx() for _ in range(count)]

                def __getattr__(self, name):
                    return lambda *a, **k: None

            mark_value = {
                "source": "arxiv",
                "paper_id": "2401.00001",
                "pref": "like",
                "nonce": "n-1",
            }
            seen_calls = []
            fake = FakeST()
            import webui.tabs.run_manager as rm

            def _fake_component(report_html, states, key):
                seen_calls.append((report_html, dict(states), key))
                return mark_value

            with (
                patch.object(reports_tab, "st", fake),
                patch.object(rm, "_daily_db_path_from_config", lambda cfg: db_path),
                patch.object(
                    reports_tab,
                    "_render_report_component",
                    side_effect=_fake_component,
                ),
            ):
                self.assertTrue(reports_tab._render_daily_report(report, {}))
                # 同一 nonce 再次回传（组件状态保留）不再重复落库或 rerun
                self.assertTrue(reports_tab._render_daily_report(report, {}))

            self.assertEqual(len(seen_calls), 2)
            self.assertEqual(len(reruns), 1)
            store = DailyResearchStore(db_path)
            prefs = store.list_preferences(limit=10)
            self.assertEqual(prefs[0]["preference"], "like")
            self.assertEqual(prefs[0]["paper_id"], "2401.00001")
            # 报告 HTML 不含偏好状态且两次渲染逐字节一致（不闪烁的前提），
            # 偏好通过独立的 states 通道下发并在落库后更新。
            self.assertEqual(seen_calls[0][0], seen_calls[1][0])
            self.assertEqual(seen_calls[0][1].get("2401.00001"), "none")
            self.assertEqual(seen_calls[1][1].get("2401.00001"), "like")

    def test_legacy_report_gets_marks_when_delivery_day_differs_from_filename(self):
        """Imported v3.2 reports must not depend on completed_at date matching."""
        from webui.tabs import reports as reports_tab
        import webui.tabs.run_manager as rm

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "daily_research.db"
            self._seed_db(db_path)
            report_path = Path(temp_dir) / "ARXIV_Report_2026-03-03_16-10-08.html"
            report_path.write_text(self.REPORT_HTML, encoding="utf-8")
            report = reports_tab.ReportFile(
                path=report_path,
                display="2026-03-03 16:10:08",
                source="arxiv",
                report_type="daily",
                # Deliberately unlike the delivery date stored in _seed_db.
                date_key="2026-03-03",
            )
            rendered = []

            class FakeST:
                session_state = {}

                def __getattr__(self, _name):
                    return lambda *args, **kwargs: None

            def _component(report_html, states, _key):
                rendered.append((report_html, states))
                return None

            with (
                patch.object(reports_tab, "st", FakeST()),
                patch.object(rm, "_daily_db_path_from_config", lambda _cfg: db_path),
                patch.object(reports_tab, "_render_report_component", side_effect=_component),
            ):
                self.assertTrue(reports_tab._render_daily_report(report, {}))

            self.assertEqual(len(rendered), 1)
            self.assertIn('data-paper="2401.00001"', rendered[0][0])
            self.assertIn("arxiv-mark-btn", rendered[0][0])

    def test_missing_store_falls_back_to_raw_html(self):
        import tempfile

        from webui.tabs import reports as reports_tab

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "daily_research.db"
            report = reports_tab.ReportFile(
                path=Path(temp_dir) / "arxiv_report.html",
                display="2026-08-22 12:00:00",
                source="arxiv",
                report_type="daily",
                date_key="2026-08-22",
            )
            self.assertFalse(reports_tab._render_daily_report(report, {}))


if __name__ == "__main__":
    unittest.main()
