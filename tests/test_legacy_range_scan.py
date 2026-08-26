"""旧历史时间段扫描：分块扫描、已知身份过滤与遗漏入积压。"""

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sources.base_source import PaperMetadata  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402
from utils.legacy_range_scan import SCAN_CHUNK_DAYS, scan_legacy_range  # noqa: E402


def _paper(pid: str, published: datetime | None = None) -> PaperMetadata:
    return PaperMetadata(
        paper_id=pid,
        title=f"Paper {pid}",
        authors=["Alice"],
        abstract="abs",
        published_date=published or datetime(2026, 3, 1, tzinfo=timezone.utc),
        url=f"https://arxiv.org/abs/{pid}",
        source="arxiv",
    )


class LegacyRangeScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.history_dir = self.root / "history"
        self.history_dir.mkdir()
        self.store = DailyResearchStore(self.root / "db.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def _record_delivered(self, pid: str, published: datetime) -> None:
        paper = _paper(pid, published)
        run_id = self.store.start_run(0, run_kind="legacy_import")
        self.store.import_legacy_paper(
            {
                "source": "arxiv",
                "paper_id": paper.paper_id,
                "canonical_id": paper.canonical_id,
                "version": paper.version,
                "paper_json": paper.to_dict(),
                "score_status": "pending",
                "tldr_status": "pending",
                "translation_status": "not_required",
                "analysis_status": "not_required",
                "completed_at": published.isoformat(),
                "delivered_at": published.isoformat(),
                "delivery_run_id": run_id,
                "report_path": "legacy.html",
            },
            delivered=True,
        )

    def test_missing_history_skips_scan(self):
        summary = scan_legacy_range(
            self.store,
            history_dir=self.history_dir,
            fetch_between=lambda a, b: [],
        )
        self.assertIsNotNone(summary["skipped_reason"])
        self.assertEqual(summary["chunks_scanned"], 0)

    def test_unknown_papers_in_range_are_queued_as_missed(self):
        self._record_delivered("2602.00001v1", datetime(2026, 2, 1, tzinfo=timezone.utc))
        self._record_delivered("2602.00002v1", datetime(2026, 3, 15, tzinfo=timezone.utc))
        run_id = self.store.start_run(0)
        # 已知：v4 行 + 旧历史缺卡片积压行都会被识别为已知身份。
        self.store.upsert_paper_seen(run_id, "arxiv", _paper("2602.00009v1"))
        self.store.record_supplement_backlog([{
            "source": "arxiv", "canonical_id": "2602.00010", "version": 1,
            "paper_id": "2602.00010v1", "reason": "missing_data",
        }])
        fetched = [_paper("2602.00009v1"), _paper("2602.00010v1"),
                   _paper("2602.00077v1"), _paper("2602.00078v2")]
        summary = scan_legacy_range(
            self.store,
            history_dir=self.history_dir,
            fetch_between=lambda a, b: fetched,
        )
        self.assertEqual(summary["missed_found"], 2)
        self.assertEqual(summary["backlog_queued"], 2)
        rows = self.store.claim_supplement_backlog(10)
        reasons = {row["canonical_id"]: row["reason"] for row in rows}
        self.assertEqual(reasons["2602.00077"], "missed_scan")
        self.assertEqual(reasons["2602.00078"], "missed_scan")
        # 遗漏行带上抓取到的元数据，补充运行无需再次抓取。
        row_77 = next(row for row in rows if row["canonical_id"] == "2602.00077")
        self.assertEqual(row_77["paper_json"]["paper_id"], "2602.00077v1")

    def test_range_is_chunked_and_idle_checked_per_chunk(self):
        self._record_delivered("2601.00001v1", datetime(2026, 1, 1, tzinfo=timezone.utc))
        self._record_delivered("2605.00001v1", datetime(2026, 5, 31, tzinfo=timezone.utc))
        calls = []
        windows = []

        def fake_fetch(start: date, end: date):
            windows.append((start, end))
            return []

        summary = scan_legacy_range(
            self.store,
            history_dir=self.history_dir,
            fetch_between=fake_fetch,
            idle_check=lambda: calls.append(1),
        )
        # 1/1 → 5/31 共 151 天，31 天一块 → 5 块。
        self.assertEqual(summary["chunks_scanned"], 5)
        self.assertEqual(len(calls), 5)
        self.assertEqual(len(windows), 5)
        self.assertEqual(windows[0][0], date(2026, 1, 1))
        self.assertEqual(windows[-1][1], date(2026, 5, 31))

    def test_repeated_scan_is_idempotent(self):
        self._record_delivered("2602.00001v1", datetime(2026, 2, 1, tzinfo=timezone.utc))
        for _ in range(2):
            summary = scan_legacy_range(
                self.store,
                history_dir=self.history_dir,
                fetch_between=lambda a, b: [_paper("2602.00077v1")],
            )
        self.assertEqual(summary["missed_found"], 0)
        self.assertEqual(self.store.supplement_backlog_summary()["pending"], 1)

    def test_failed_chunk_is_recorded_while_later_chunks_continue(self):
        self._record_delivered("2601.00001v1", datetime(2026, 1, 1, tzinfo=timezone.utc))
        self._record_delivered("2602.00001v1", datetime(2026, 2, 15, tzinfo=timezone.utc))
        calls = []

        def fetch(start, _end):
            calls.append(start)
            if len(calls) == 1:
                raise RuntimeError("temporary upstream outage")
            return [_paper("2602.00999v1")]

        summary = scan_legacy_range(
            self.store,
            history_dir=self.history_dir,
            fetch_between=fetch,
        )

        self.assertEqual(summary["failed_chunks"], 1)
        self.assertEqual(summary["chunks_scanned"], 1)
        self.assertEqual(summary["missed_found"], 1)
        self.assertEqual(summary["backlog_queued"], 1)
        self.assertIn("后续历史遗漏扫描会重试", summary["skipped_reason"])


class FetchBetweenTests(unittest.TestCase):
    def test_fetch_domain_papers_between_dedupes_and_maps_metadata(self):
        from datetime import date as date_cls
        from unittest.mock import patch
        from sources.arxiv_source import ArxivSource

        class _Result:
            def __init__(self, pid):
                self._pid = pid

            def get_short_id(self):
                return self._pid

            title = "T"
            authors = []
            summary = "abs"
            published = datetime(2026, 2, 1, tzinfo=timezone.utc)
            entry_id = "https://arxiv.org/abs/x"
            pdf_url = "https://arxiv.org/pdf/x.pdf"
            doi = None
            categories = []
            updated = datetime(2026, 2, 2, tzinfo=timezone.utc)

        source = ArxivSource.__new__(ArxivSource)
        with patch.object(
            ArxivSource,
            "_fetch_query_results",
            return_value=([_Result("2602.1v1"), _Result("2602.1v1"), _Result("2602.2v1")], {}),
        ):
            papers = source.fetch_domain_papers_between(
                date_cls(2026, 2, 1), date_cls(2026, 2, 28), ["quant-ph"]
            )
        self.assertEqual(sorted(p.paper_id for p in papers), ["2602.1v1", "2602.2v1"])
        self.assertEqual(papers[0].source, "arxiv")

    def test_fetch_domain_papers_between_raises_after_retries(self):
        from datetime import date as date_cls
        from unittest.mock import patch
        from sources.arxiv_source import ArxivFetchError, ArxivSource

        source = ArxivSource.__new__(ArxivSource)
        with patch.object(
            ArxivSource,
            "_fetch_query_results",
            side_effect=RuntimeError("boom"),
        ), patch("sources.arxiv_source.time.sleep"):
            with self.assertRaises(ArxivFetchError):
                source.fetch_domain_papers_between(
                    date_cls(2026, 2, 1), date_cls(2026, 2, 28), ["quant-ph"]
                )


if __name__ == "__main__":
    unittest.main()


    unittest.main()
