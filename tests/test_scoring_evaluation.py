import json
import io
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.analysis_agent import WeightedScoreResponse  # noqa: E402
from config import settings  # noqa: E402
from sources.base_source import PaperMetadata  # noqa: E402
from utils.daily_research_fingerprints import build_score_audit_metadata  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402
from utils.scoring_evaluation import (  # noqa: E402
    DIAGNOSTICS_SCHEMA,
    LABEL_SCHEMA,
    ScoringEvaluationError,
    build_operational_diagnostics,
    evaluate_labels,
    export_review_candidates,
    iter_scored_papers,
    load_labels,
    main,
    render_operational_diagnostics_markdown,
    write_operational_diagnostics_report,
    write_evaluation_report,
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


def _persist_score(store: DailyResearchStore, paper: PaperMetadata, total: float, qualified: bool):
    run_id = store.start_run(1)
    store.upsert_paper_seen(run_id, "arxiv", paper)
    score = _score(total, qualified)
    store.update_score(
        run_id,
        "arxiv",
        {"paper_metadata": paper, "paper_id": paper.paper_id, "score_response": score},
        score_input_fingerprint="score-fingerprint",
        score_audit_metadata=build_score_audit_metadata(
            paper, {"quantum sensing": 1.0, "noise": 0.5}, "score-fingerprint"
        ),
    )


class ScoringEvaluationTests(unittest.TestCase):
    def _make_store(self, root: Path) -> tuple[DailyResearchStore, Path]:
        db_path = root / "daily.db"
        store = DailyResearchStore(db_path)
        _persist_score(store, _paper("2501.00001v1", "True positive"), 9, True)
        _persist_score(store, _paper("2501.00002v1", "False positive"), 8, True)
        _persist_score(store, _paper("2501.00003v1", "False negative"), 4, False)
        _persist_score(store, _paper("2501.00004v1", "True negative"), 2, False)
        return store, db_path

    def _write_labels(self, path: Path, rows: list[dict]):
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

    @staticmethod
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
        self,
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
            self._scan_receipt(
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

    def test_export_is_explicit_non_secret_and_contains_audit_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _store, db_path = self._make_store(Path(temp_dir))
            output_path = Path(temp_dir) / "review.jsonl"
            summary = export_review_candidates(db_path, output_path)
            rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(summary["count"], 4)
        self.assertEqual(len(rows), 4)
        row = next(item for item in rows if item["paper_id"] == "2501.00001v1")
        self.assertEqual(row["production_score"]["total_score"], 9.0)
        self.assertEqual(row["score_audit"]["strategy_id"], settings.normalized_score_strategy())
        self.assertIn("policy_fingerprint", row["score_audit"])
        serialized = json.dumps(rows, ensure_ascii=False)
        self.assertNotIn("api_key", serialized.lower())
        self.assertNotIn("base_url", serialized.lower())
        self.assertNotIn("research_context\"", serialized.lower())

    def test_legacy_rows_export_as_legacy_instead_of_fabricated_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _store, db_path = self._make_store(root)
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "UPDATE daily_papers SET score_audit_json = NULL WHERE paper_id = ?",
                    ("2501.00004v1",),
                )
            rows = list(iter_scored_papers(db_path))

        legacy = next(row for row in rows if row["paper_id"] == "2501.00004v1")
        self.assertEqual(legacy["score_audit"], {"legacy": True})

    def test_readonly_diagnostics_report_score_drift_scan_health_and_no_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "daily.db"
            store = DailyResearchStore(db_path)
            baseline_policy = "a" * 64
            recent_policy = "b" * 64
            for day, value in enumerate((1.0, 2.0, 3.0), 1):
                self._diagnostic_run(
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
                self._diagnostic_run(
                    store,
                    db_path,
                    when=datetime(2026, 1, day, tzinfo=timezone.utc),
                    paper_id=f"2501.1000{day}v1",
                    score=v2_score,
                    policy_fingerprint=recent_policy,
                    candidates=day * 3,
                )

            failed_run = self._diagnostic_run(
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

    def test_v2_export_and_threshold_scan_use_content_relevance_not_ranking(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DailyResearchStore(root / "daily.db")
            paper = _paper("2501.10000v1", "V2 score")
            run_id = store.start_run(1)
            store.upsert_paper_seen(run_id, "arxiv", paper)
            v2_score = WeightedScoreResponse(
                total_score=10.0,
                keyword_scores={"core": 5.0, "reference": 10.0},
                author_bonus=3.0,
                expert_authors_found=["Alice"],
                passing_score=6.0,
                is_qualified=False,
                reasoning="Core topic is not strong enough.",
                tldr="A weak core paper.",
                extracted_keywords=["core"],
                strategy_id="core_relevance_v2",
                relevance_score=5.0,
                qualification_threshold=6.0,
                core_keyword_scores={"core": 5.0},
                core_keywords_used=["core"],
                reference_score=10.0,
                author_preference_bonus=3.0,
                ranking_score=10.0,
                qualification_reason="core relevance below threshold",
            )
            store.update_score(
                run_id,
                "arxiv",
                {"paper_metadata": paper, "paper_id": paper.paper_id, "score_response": v2_score},
                score_audit_metadata={"strategy_id": "core_relevance_v2"},
            )
            review = list(iter_scored_papers(root / "daily.db"))[0]
            self.assertEqual(review["production_score"]["relevance_score"], 5.0)
            self.assertEqual(review["production_score"]["ranking_score"], 10.0)
            labels_path = root / "labels.jsonl"
            self._write_labels(
                labels_path,
                [{"source": "arxiv", "paper_id": paper.paper_id, "label": "not_relevant"}],
            )
            result = evaluate_labels(root / "daily.db", labels_path, thresholds=[6.0])

        self.assertEqual(result["threshold_scan"][0]["pass_rate"], 0.0)

    def test_evaluation_reports_production_metrics_thresholds_and_errors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _store, db_path = self._make_store(root)
            labels_path = root / "labels.jsonl"
            self._write_labels(
                labels_path,
                [
                    {"schema": LABEL_SCHEMA, "source": "arxiv", "paper_id": "2501.00001v1", "label": "relevant"},
                    {"schema": LABEL_SCHEMA, "source": "arxiv", "paper_id": "2501.00002v1", "label": "not_relevant", "note": "off topic"},
                    {"schema": LABEL_SCHEMA, "source": "arxiv", "paper_id": "2501.00003v1", "label": "relevant"},
                    {"schema": LABEL_SCHEMA, "source": "arxiv", "paper_id": "2501.00004v1", "label": "not_relevant"},
                    {"schema": LABEL_SCHEMA, "source": "arxiv", "paper_id": "2501.00001v1", "label": "unsure"},
                ][0:4],
            )
            result = evaluate_labels(db_path, labels_path, thresholds=[5, 9])
            written = write_evaluation_report(result, root / "result.json", root / "result.md")

            self.assertEqual(result["production_rule"]["metrics"], {
                "total": 4,
                "tp": 1,
                "tn": 1,
                "fp": 1,
                "fn": 1,
                "accuracy": 0.5,
                "precision": 0.5,
                "recall": 0.5,
                "f1": 0.5,
                "pass_rate": 0.5,
            })
            self.assertEqual([row["threshold"] for row in result["threshold_scan"]], [5.0, 9.0])
            self.assertEqual(result["threshold_scan"][1]["tp"], 1)
            self.assertEqual(result["threshold_scan"][1]["fp"], 0)
            self.assertEqual(result["false_positives"][0]["paper_id"], "2501.00002v1")
            self.assertEqual(result["false_negatives"][0]["paper_id"], "2501.00003v1")
            self.assertTrue(Path(written["json"]).is_file())
            self.assertIn("False positives", Path(written["markdown"]).read_text(encoding="utf-8"))

    def test_unsure_is_counted_but_excluded_from_binary_metrics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _store, db_path = self._make_store(root)
            labels_path = root / "labels.jsonl"
            self._write_labels(
                labels_path,
                [
                    {"source": "arxiv", "paper_id": "2501.00001v1", "label": "relevant"},
                    {"source": "arxiv", "paper_id": "2501.00002v1", "label": "unsure"},
                ],
            )
            result = evaluate_labels(db_path, labels_path)

        self.assertEqual(result["label_counts"], {"relevant": 1, "unsure": 1})
        self.assertEqual(result["binary_labels_used"], 1)
        self.assertEqual(result["production_rule"]["metrics"]["total"], 1)

    def test_duplicate_unknown_and_invalid_labels_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _store, db_path = self._make_store(root)
            duplicate_path = root / "duplicate.jsonl"
            self._write_labels(
                duplicate_path,
                [
                    {"source": "arxiv", "paper_id": "2501.00001v1", "label": "relevant"},
                    {"source": "arxiv", "paper_id": "2501.00001v1", "label": "not_relevant"},
                ],
            )
            with self.assertRaisesRegex(ScoringEvaluationError, "重复身份"):
                load_labels(duplicate_path)

            unknown_path = root / "unknown.jsonl"
            self._write_labels(
                unknown_path,
                [{"source": "arxiv", "paper_id": "not-in-db", "label": "relevant"}],
            )
            with self.assertRaisesRegex(ScoringEvaluationError, "不存在"):
                evaluate_labels(db_path, unknown_path)

            invalid_path = root / "invalid.jsonl"
            self._write_labels(
                invalid_path,
                [{"source": "arxiv", "paper_id": "2501.00001v1", "label": "maybe"}],
            )
            with self.assertRaisesRegex(ScoringEvaluationError, "label 必须是"):
                load_labels(invalid_path)

    def test_cli_returns_nonzero_for_bad_labels_and_does_not_create_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _store, db_path = self._make_store(root)
            labels_path = root / "bad-labels.jsonl"
            self._write_labels(
                labels_path,
                [{"source": "arxiv", "paper_id": "2501.00001v1", "label": "bad"}],
            )
            json_output = root / "result.json"
            self.assertEqual(
                main(
                    [
                        "evaluate",
                        "--db",
                        str(db_path),
                        "--labels",
                        str(labels_path),
                        "--json-output",
                        str(json_output),
                    ]
                ),
                2,
            )
            self.assertFalse(json_output.exists())


if __name__ == "__main__":
    unittest.main()
