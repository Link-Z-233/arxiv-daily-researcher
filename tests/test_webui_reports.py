import html
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui.tabs.reports import (  # noqa: E402
    _build_sandboxed_preview_html,
    _discover_reports,
    _filter_visible_reports,
)


class _IframeAttributeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.attributes = None

    def handle_starttag(self, tag, attrs):
        if tag == "iframe" and self.attributes is None:
            self.attributes = dict(attrs)


class WebUiReportPreviewTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()


class DailyPaperCardTests(unittest.TestCase):
    """日报正文以论文卡片内联呈现，标记按钮挂在卡片上。"""

    def _report(self, db_path: Path):
        from webui.tabs.reports import ReportFile

        return ReportFile(
            path=db_path.parent / "arxiv_report.html",
            display="2026-08-22 12:00:00",
            source="arxiv",
            report_type="daily",
            date_key="2026-08-22",
        )

    def test_missing_store_falls_back_to_raw_html(self):
        from webui.tabs import reports as reports_tab

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "daily_research.db"
            report = self._report(db_path)
            self.assertFalse(reports_tab._render_daily_paper_cards(report, {}))

    def test_cards_render_and_mark_is_applied(self):
        import json
        import sqlite3
        from types import SimpleNamespace
        from unittest.mock import patch

        from webui.tabs import reports as reports_tab

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "daily_research.db"
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
                    json.dumps(
                        {
                            "total_score": 7.5,
                            "is_qualified": True,
                            "extracted_keywords": ["error correction"],
                            "tldr": "A big TLDR",
                        }
                    ),
                ),
            )
            conn.commit()
            conn.close()

            report = self._report(db_path)
            rendered = {"caption": False, "expanders": [], "saw_button": False}

            class _Ctx:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def __getattr__(self, name):
                    return lambda *a, **k: None

            class FakeST:
                session_state = {}

                def caption(self, *a, **k):
                    rendered["caption"] = True

                def columns(self, spec):
                    count = spec if isinstance(spec, int) else len(spec)
                    return [_Ctx() for _ in range(count)]

                def expander(self, label, **k):
                    rendered["expanders"].append(label)
                    return _Ctx()

                def button(self, label, *, key=None, **k):
                    if key and key.startswith("rsm_like_"):
                        rendered["saw_button"] = True
                        return True  # 模拟点击 👍
                    return False

                def __getattr__(self, name):
                    return lambda *a, **k: None

            from utils.daily_research_store import DailyResearchStore

            fake = FakeST()
            import webui.tabs.run_manager as rm

            with (
                patch.object(reports_tab, "st", fake),
                patch.object(rm, "_daily_db_path_from_config", lambda cfg: db_path),
            ):
                ok = reports_tab._render_daily_paper_cards(report, {})

            self.assertTrue(ok)
            self.assertTrue(rendered["caption"])
            self.assertTrue(rendered["expanders"])
            self.assertTrue(rendered["saw_button"])
            # 点击 👍 后偏好已落库
            store = DailyResearchStore(db_path)
            prefs = store.list_preferences(limit=10)
            self.assertEqual(prefs[0]["preference"], "like")
            self.assertEqual(prefs[0]["paper_id"], "2401.00001")
