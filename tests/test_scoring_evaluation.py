"""只读运行诊断：分数漂移、扫描健康、发件箱与机密不泄漏。

原「人工标注反馈闭环」（export/evaluate）已按产品决策移除；收藏偏好
驱动的学习模式评分取代了它。这里仅保留 diagnose 诊断的回归测试。
"""

import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.analysis_agent import WeightedScoreResponse  # noqa: E402
from sources.base_source import PaperMetadata  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402
from utils.scoring_evaluation import (  # noqa: E402
    DIAGNOSTICS_SCHEMA,
    build_operational_diagnostics,
    main,
    render_operational_diagnostics_markdown,
    write_operational_diagnostics_report,
)


def _paper(paper_id: str, title: str) -> PaperMetadata:
    return PaperMetadata(
        paper_id=paper_id,
        title=title,
        authors=["Alice", "Bob"],
        abstract=f"Abstract for {title}",
        published_date=datetime(2026, 8, 12, tzinfo=timezone.utc),
        url=f"https://arxiv.org/abs/{paper_id}",
        source="arxiv",
        categories=["quant-ph"],
    )


def _score(total: float, qualified: bool) -> WeightedScoreResponse:
    return WeightedScoreResponse(
        total_score=total,
        keyword_scores={"quantum sensing": total / 2, "noise": total / 4},
        author_bonus=0,
        expert_authors_found=[],
        passing_score=5,
        is_qualified=qualified,
        reasoning="Stored scoring rationale.",
        tldr="A stored one-sentence summary.",
        extracted_keywords=["quantum sensing", "noise"],
    )


def _scan_receipt(when: datetime, *, status: str, candidates: int, attempts: int = 1) -> dict:
    return {
        "source": "arxiv",
        "status": status,
        "scanned_at": when.isoformat(),
        "total_new_candidates": candidates,
        "domain_receipts": [
            {
                "domain": "quant-ph",
                "status": status,
                "queries": {
                    "submitted": {"attempts": attempts},
                    "updated": {"attempts": 1},
                },
            }
        ],
    }


def _diagnostic_run(
    store: DailyResearchStore,
    db_path: Path,
    *,
    when: datetime,
    paper_id: str,
    score: WeightedScoreResponse | None,
    policy_fingerprint: str,
    candidates: int,
    failed: bool = False,
) -> str:
    run_id = store.start_run(1)
    store.prepare_scan(run_id, 1, ["arxiv"], now=when)
    receipt_status = "failed" if failed else "succeeded"
    store.record_scan_receipt(
        run_id,
        "arxiv",
        _scan_receipt(
            when, status=receipt_status, candidates=candidates, attempts=2 if failed else 1
        ),
    )
    paper = _paper(paper_id, f"Diagnostic {paper_id}")
    store.upsert_paper_seen(run_id, "arxiv", paper)
    if score is None:
        store.update_error(
            run_id,
            "arxiv",
            paper.paper_id,
            "supersecret-stage-error",
            stage="score",
        )
    else:
        store.update_score(
            run_id,
            "arxiv",
            {"paper_metadata": paper, "paper_id": paper.paper_id, "score_response": score},
            score_audit_metadata={
                "strategy_id": score.strategy_id,
                "policy_fingerprint": policy_fingerprint,
                "model": {"model_name": "gpt-4.1-mini", "temperature": 0.2},
                "private_context": "supersecret-research-context",
            },
        )
        store.mark_translation_not_required(run_id, "arxiv", paper.paper_id)
        store.mark_analysis_not_required(run_id, "arxiv", paper.paper_id)
    if failed:
        store.fail_run(run_id, "supersecret-run-error")
    else:
        store.complete_run(run_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE daily_runs SET started_at = ? WHERE run_id = ?",
            (when.isoformat(), run_id),
        )
    return run_id


class ScoringDiagnosticsTests(unittest.TestCase):
    def test_readonly_diagnostics_report_score_drift_scan_health_and_no_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "daily.db"
            store = DailyResearchStore(db_path)
            baseline_policy = "a" * 64
            recent_policy = "b" * 64
            for day, value in enumerate((1.0, 2.0, 3.0), 1):
                _diagnostic_run(
                    store,
                    db_path,
                    when=datetime(2026, 1, day, tzinfo=timezone.utc),
                    paper_id=f"2501.1000{day}v1",
                    score=_score(value, False),
                    policy_fingerprint=baseline_policy,
                    candidates=day + 1,
                )

            for day, relevance in ((4, 8.0), (5, 9.0)):
                v2_score = WeightedScoreResponse(
                    total_score=10.0,
                    keyword_scores={"core": relevance, "reference": 10.0},
                    author_bonus=1.0,
                    expert_authors_found=["Alice"],
                    passing_score=6.0,
                    is_qualified=True,
                    reasoning="Relevant stored rationale.",
                    tldr="Relevant stored summary.",
                    extracted_keywords=["core"],
                    strategy_id="core_relevance_v2",
                    relevance_score=relevance,
                    qualification_threshold=6.0,
                    core_keyword_scores={"core": relevance},
                    core_keywords_used=["core"],
                    reference_score=10.0,
                    author_preference_bonus=1.0,
                    ranking_score=10.0,
                    qualification_reason="core match",
                )
                _diagnostic_run(
                    store,
                    db_path,
                    when=datetime(2026, 1, day, tzinfo=timezone.utc),
                    paper_id=f"2501.1000{day}v1",
                    score=v2_score,
                    policy_fingerprint=recent_policy,
                    candidates=day * 3,
                )

            failed_run = _diagnostic_run(
                store,
                db_path,
                when=datetime(2026, 1, 6, tzinfo=timezone.utc),
                paper_id="2501.10006v1",
                score=None,
                policy_fingerprint=recent_policy,
                candidates=0,
                failed=True,
            )
            store.enqueue_notification(
                failed_run,
                "daily_report",
                "webhook",
                {"authorization": "supersecret-notification-payload"},
            )
            store.enqueue_maintenance_task(
                "supersecret-maintenance-key",
                {"password": "supersecret-maintenance-payload"},
            )
            modified_before = db_path.stat().st_mtime_ns

            result = build_operational_diagnostics(db_path, recent_runs=3, baseline_runs=3)
            modified_after = db_path.stat().st_mtime_ns
            markdown = render_operational_diagnostics_markdown(result)
            written = write_operational_diagnostics_report(
                result, root / "diagnostics.json", root / "diagnostics.md"
            )
            written_json_exists = Path(written["json"]).is_file()
            written_markdown_exists = Path(written["markdown"]).is_file()
            with redirect_stdout(io.StringIO()):
                cli_status = main(
                    [
                        "diagnose",
                        "--db",
                        str(db_path),
                        "--recent-runs",
                        "3",
                        "--baseline-runs",
                        "3",
                        "--json-output",
                        str(root / "cli-diagnostics.json"),
                        "--markdown-output",
                        str(root / "cli-diagnostics.md"),
                    ]
                )
            cli_json_exists = (root / "cli-diagnostics.json").is_file()
            cli_markdown_exists = (root / "cli-diagnostics.md").is_file()

        self.assertEqual(modified_before, modified_after)
        self.assertEqual(result["schema"], DIAGNOSTICS_SCHEMA)
        self.assertTrue(result["read_only"])
        recent = result["windows"]["recent"]
        baseline = result["windows"]["baseline"]
        self.assertEqual(recent["runs"]["status_counts"], {"completed": 2, "failed": 1})
        self.assertEqual(recent["papers"]["scoring"]["valid_score_records"], 2)
        self.assertEqual(recent["papers"]["scoring"]["qualification_rate"], 1.0)
        self.assertEqual(baseline["papers"]["scoring"]["score_distribution"]["median"], 2.0)
        self.assertEqual(result["drift"]["score"]["median_delta"], 6.5)
        self.assertEqual(
            result["drift"]["score_profiles"]["new_in_recent_window"][0]["policy_fingerprint"],
            recent_policy,
        )
        recent_source = recent["scans"]["sources"][0]
        self.assertEqual(recent_source["failed_receipts"], 1)
        self.assertEqual(recent_source["retried_queries"], 1)
        self.assertIn(
            "failed_scan_receipt",
            {item["kind"] for item in recent["scans"]["anomalies"]},
        )
        self.assertEqual(result["outbox"]["notifications"]["open_rows"], 1)
        self.assertEqual(result["outbox"]["maintenance"]["open_rows"], 1)
        self.assertTrue(written_json_exists)
        self.assertTrue(written_markdown_exists)
        self.assertEqual(cli_status, 0)
        self.assertTrue(cli_json_exists)
        self.assertTrue(cli_markdown_exists)
        serialized = json.dumps(result, ensure_ascii=False) + markdown
        self.assertNotIn("supersecret", serialized)
        self.assertNotIn("private_context", serialized)

    def test_cli_rejects_removed_feedback_subcommands(self):
        # 反馈闭环子命令已按决策移除；diagnose 是唯一的 CLI 入口。
        with self.assertRaises(SystemExit):
            main(["export", "--db", "/dev/null", "--output", "/dev/null"])


if __name__ == "__main__":
    unittest.main()
