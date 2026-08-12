import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.analysis_agent import WeightedScoreResponse  # noqa: E402
from config import settings  # noqa: E402
from notifications.notifier import NotifierAgent, RunResult  # noqa: E402
from report.daily.reporter import Reporter  # noqa: E402
from report.trend.reporter import TrendReporter  # noqa: E402
from sources.base_source import PaperMetadata  # noqa: E402
from utils.safe_markdown import markdown_link, markdown_text  # noqa: E402


class MarkdownSafetyTests(unittest.TestCase):
    def test_dynamic_text_cannot_introduce_markdown_or_html_structure(self):
        escaped = markdown_text(
            '# heading\n<script>alert(1)</script> | **bold** [label](javascript:alert(1))'
        )

        self.assertIn("\\# heading", escaped)
        self.assertIn("&lt;script&gt;alert\\(1\\)&lt;/script&gt;", escaped)
        self.assertIn("\\|", escaped)
        self.assertIn("\\*\\*bold\\*\\*", escaped)
        self.assertNotIn("<script>", escaped)

    def test_backticks_are_safe_inside_program_generated_code_spans(self):
        escaped = markdown_text("source`\n# injected", multiline=False)

        self.assertNotIn("`", escaped)
        self.assertIn("&#96; # injected", escaped)

    def test_only_safe_http_urls_become_markdown_links(self):
        self.assertEqual(markdown_link("paper](x)", "javascript:alert(1)"), "")
        self.assertEqual(
            markdown_link("paper](x)", "https://example.test/a)b?q=hello world"),
            "[paper\\]\\(x\\)](https://example.test/a%29b?q=hello%20world)",
        )

    def test_daily_markdown_escapes_llm_content_and_rejects_unsafe_paper_links(self):
        paper = PaperMetadata(
            paper_id="2501.12345v1",
            title="# title <img src=x onerror=alert(1)>",
            authors=["Author | injected"],
            abstract="<script>alert(1)</script>",
            published_date=datetime.now(timezone.utc),
            url="javascript:alert(1)",
            source="arxiv",
        )
        score = WeightedScoreResponse(
            total_score=9.0,
            keyword_scores={"kw|break": 9.0},
            author_bonus=0.0,
            expert_authors_found=[],
            passing_score=5.0,
            is_qualified=True,
            reasoning="<details open>injected</details>",
            tldr="<script>alert(1)</script>",
            extracted_keywords=["kw|break"],
        )
        data = {
            "paper_metadata": paper,
            "paper_id": paper.paper_id,
            "title": paper.title,
            "authors": paper.get_authors_string(),
            "abstract": paper.abstract,
            "abstract_cn": "<img src=x onerror=alert(1)>",
            "url": paper.url,
            "published": paper.published_date.strftime("%Y-%m-%d"),
            "score_response": score,
        }

        reporter = Reporter()
        reporter.basic_template = {
            "layout": {"show_config_section": False, "show_stats_section": False},
            "modules": [
                {"id": "metadata", "format": "list", "fields": {}},
                {"id": "scoring", "format": "list", "collapsible": False},
                {"id": "tldr_ai", "format": "inline"},
                {"id": "abstract_cn", "format": "plain", "collapsible": False},
            ],
        }

        rendered = "\n".join(reporter._render_paper_section(data, {"kw|break": 1}, [], 1))

        self.assertNotIn("javascript:alert", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img ", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("\\|", rendered)
        self.assertNotIn("<details open>injected</details>", rendered)

    def test_trend_markdown_escapes_external_content_and_rejects_unsafe_link(self):
        paper = PaperMetadata(
            paper_id="2501.12345v1",
            title="# title",
            authors=["Author"],
            abstract="<script>alert(1)</script>",
            published_date=datetime.now(timezone.utc),
            url="data:text/html,<script>alert(1)</script>",
            source="arxiv",
        )

        rendered = "\n".join(
            TrendReporter()._render_paper_md(
                paper,
                1,
                {paper.paper_id: "<img src=x onerror=alert(1)>"},
            )
        )

        self.assertNotIn("data:text/html", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img ", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_markdown_notification_escapes_top_paper_and_error_content(self):
        result = RunResult(
            run_timestamp="2026-01-01 <script>alert(1)</script>",
            papers_by_source={"arxiv`\n# injected": 1},
            qualified_by_source={"arxiv`\n# injected": 1},
            analyzed_by_source={"arxiv`\n# injected": 0},
            report_paths={"arxiv": "/tmp/<report>.md"},
            error_message="<img src=x onerror=alert(1)>",
            top_papers=[
                {
                    "title": "# title <script>alert(1)</script>",
                    "source": "arxiv`\n# injected",
                    "tldr": "<details>injected</details>",
                    "score": 9.0,
                    "url": "javascript:alert(1)",
                }
            ],
        )
        agent = NotifierAgent()

        with patch("notifications.notifier._load_template", return_value="{timestamp}\n{source_summary}\n{top_papers}\n{error_message}"):
            body = agent._format_body(result)

        self.assertNotIn("javascript:alert", body)
        self.assertNotIn("<script>", body)
        self.assertNotIn("<details>", body)
        self.assertNotIn("<img ", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertIn("# INJECTED", body)


if __name__ == "__main__":
    unittest.main()
