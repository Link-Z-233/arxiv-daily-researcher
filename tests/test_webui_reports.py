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
