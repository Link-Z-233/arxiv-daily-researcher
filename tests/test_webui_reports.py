import html
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui.tabs.reports import _build_sandboxed_preview_html  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
