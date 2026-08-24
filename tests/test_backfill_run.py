"""过去时间段每日报告：过去日期补跑、时间戳与触发校验。"""

import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modes.daily_research import DailyResearchPipeline  # noqa: E402
from sources.base_source import PaperMetadata  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402
from config import settings  # noqa: E402
from utils.webui_trigger import (  # noqa: E402
    TriggerValidationError,
    build_main_command,
    build_trigger_payload,
)


def _paper(pid: str, published: datetime) -> PaperMetadata:
    return PaperMetadata(
        paper_id=pid,
        title=f"Paper {pid}",
        authors=["Alice"],
        abstract=f"Abstract {pid}",
        published_date=published,
        url=f"https://arxiv.org/abs/{pid}",
        source="arxiv",
    )


class _Agent:
    deep_template = {}

    def score_paper_with_keywords(self, title, authors, abstract, keywords_dict, learned_terms=None):
        from agents.analysis_agent import WeightedScoreResponse

        return WeightedScoreResponse(
            total_score=10.0,
            keyword_scores={"quantum": 5.0},
            author_bonus=0.0,
            expert_authors_found=[],
            passing_score=4.0,
            is_qualified=False,
            reasoning="r",
            tldr="t",
            extracted_keywords=["quantum"],
        )

    def translate_abstract(self, abstract):
        return "中文摘要"


class BackfillTriggerValidationTests(unittest.TestCase):
    def test_valid_past_date_round_trips_into_command(self):
        target = (date.today() - timedelta(days=3)).isoformat()
        payload = build_trigger_payload("backfill_run", target_date=target)
        command = build_main_command(payload, Path("/worker"))
        self.assertIn("--target-date", command)
        self.assertIn(target, command)

    def test_date_range_round_trips_into_queue_command(self):
        start = (date.today() - timedelta(days=5)).isoformat()
        end = (date.today() - timedelta(days=3)).isoformat()
        payload = build_trigger_payload(
            "backfill_run", date_from=start, date_to=end
        )
        command = build_main_command(payload, Path("/worker"))
        self.assertEqual(payload["args"], {"date_from": start, "date_to": end})
        self.assertIn("--date-from", command)
        self.assertIn("--date-to", command)
        self.assertIn(start, command)
        self.assertIn(end, command)

        with self.assertRaises(TriggerValidationError):
            build_trigger_payload(
                "backfill_run", date_from=end, date_to=start
            )
        with self.assertRaises(TriggerValidationError):
            build_trigger_payload(
                "backfill_run", target_date=start, date_from=start, date_to=end
            )

    def test_today_and_future_dates_are_rejected(self):
        with self.assertRaises(TriggerValidationError):
            build_trigger_payload("backfill_run", target_date=date.today().isoformat())
        with self.assertRaises(TriggerValidationError):
            build_trigger_payload(
                "backfill_run",
                target_date=(date.today() + timedelta(days=1)).isoformat(),
            )

    def test_malformed_date_is_rejected(self):
        with self.assertRaises(TriggerValidationError):
            build_trigger_payload("backfill_run", target_date="not-a-date")
        with self.assertRaises(TriggerValidationError):
            build_trigger_payload("backfill_run")


class BackfillPipelineTests(unittest.TestCase):
    def test_backfill_run_stamps_report_with_past_date(self):
        class _KeywordAgent:
            def get_all_keywords(self):
                return {"quantum": 1.0}

        class _AnalysisAgent(_Agent):
            pass

        target = date.today() - timedelta(days=10)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "daily.db"
            reports_root = root / "reports"
            reporter_calls = []

            class _Reporter:
                def generate_reports_by_source(self, **kwargs):
                    reporter_calls.append(kwargs)
                    stamp = kwargs["report_timestamp"].strftime("%Y-%m-%d_%H-%M-%S_%f")
                    path = reports_root / f"arxiv" / f"ARXIV_Report_{stamp}.html"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("<html>backfill</html>", encoding="utf-8")
                    return {"arxiv_html": path}

            scanned = [
                _paper("2601.1v1", datetime(2026, 1, 1, tzinfo=timezone.utc)),
                _paper("2601.2v1", datetime(2026, 1, 1, tzinfo=timezone.utc)),
            ]

            overrides = {
                "TOKEN_TRACKING_ENABLED": False,
                "DAILY_RESEARCH_DB_PATH": db_path,
                "ENABLE_NOTIFICATIONS": False,
                "ENABLED_SOURCES": ["arxiv"],
                "TARGET_DOMAINS": ["quant-ph"],
                "TARGET_JOURNALS": [],
                "ENABLE_REFERENCE_EXTRACTION": False,
                "PRIMARY_KEYWORDS": ["quantum"],
                "PRIMARY_KEYWORD_WEIGHT": 1.0,
                "SCORE_STRATEGY": "legacy_weighted_keyword_v1",
                "HISTORY_DIR": root / "history",
                "OPENALEX_EMAIL": "",
                "OPENALEX_API_KEY": "",
                "ENABLE_SEMANTIC_SCHOLAR_TLDR": False,
                "SEMANTIC_SCHOLAR_API_KEY": "",
                "KEYWORD_TRACKER_ENABLED": False,
                "DAILY_ENABLE_DEEP_ANALYSIS": False,
                "DAILY_MAX_PAPERS_PER_RUN": 1,
                "ENABLE_CONCURRENCY": False,
                "ENABLE_MARKDOWN_REPORT": False,
                "ENABLE_HTML_REPORT": True,
                "REPORTS_DIR": reports_root,
            }
            with ExitStack() as stack:
                for name, value in overrides.items():
                    stack.enter_context(patch.object(settings, name, value))
                stack.enter_context(
                    patch("modes.daily_research.KeywordAgent", _KeywordAgent)
                )
                stack.enter_context(
                    patch("modes.daily_research.AnalysisAgent", _AnalysisAgent)
                )
                stack.enter_context(patch("modes.daily_research.Reporter", _Reporter))
                stack.enter_context(
                    patch(
                        "modes.daily_research.deliver_pending_after_report_syncs",
                        return_value={"claimed": 0},
                    )
                )
                stack.enter_context(
                    patch(
                        "modes.daily_research.after_report_sync_maintenance_entry",
                        return_value=None,
                    )
                )
                stack.enter_context(
                    patch(
                        "sources.arxiv_source.ArxivSource.fetch_domain_papers_between",
                        return_value=scanned,
                    )
                )
                result = DailyResearchPipeline().run(
                    run_kind="backfill", target_date=target
                )

            self.assertTrue(result.success)
            # 上限 1：只交付当天 1 篇，另一篇留在候选状态。
            self.assertEqual(result.total_papers_fetched, 1)
            self.assertEqual(len(reporter_calls), 1)
            stamp_date = reporter_calls[0]["report_timestamp"].date()
            self.assertEqual(stamp_date, target)
            self.assertEqual(reporter_calls[0]["report_kind"], "daily")

            store = DailyResearchStore(db_path)
            self.assertTrue(store.is_paper_delivered_strict("arxiv", "2601.1v1"))
            self.assertFalse(store.is_paper_delivered_strict("arxiv", "2601.2v1"))
            # Deferred past-date work remains durable, but an ordinary daily
            # run must not pick it up and place it in today's report.
            ordinary_pending, ordinary_count = store.select_pending_papers(
                ["arxiv"], limit=0
            )
            self.assertEqual(ordinary_pending, {})
            self.assertEqual(ordinary_count, 0)
            with store._connect() as conn:
                deferred_scope = conn.execute(
                    "SELECT queue_scope FROM daily_papers "
                    "WHERE source = 'arxiv' AND paper_id = '2601.2v1'"
                ).fetchone()[0]
            self.assertEqual(deferred_scope, "backfill")
            # 补跑不推进扫描水位线。
            self.assertIsNone(store.get_scan_watermark("arxiv"))
            with store._connect() as conn:
                run_kind = conn.execute(
                    "SELECT run_kind FROM daily_runs ORDER BY started_at DESC LIMIT 1"
                ).fetchone()[0]
            self.assertEqual(run_kind, "backfill")

    def test_backfill_requires_target_date(self):
        with self.assertRaises(ValueError):
            DailyResearchPipeline().run(run_kind="backfill")


if __name__ == "__main__":
    unittest.main()
