import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.analysis_agent import WeightedScoreResponse  # noqa: E402
from sources.base_source import PaperMetadata  # noqa: E402
from utils.daily_research_fingerprints import build_score_audit_metadata  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402
from utils.scoring_evaluation import (  # noqa: E402
    LABEL_SCHEMA,
    ScoringEvaluationError,
    evaluate_labels,
    export_review_candidates,
    iter_scored_papers,
    load_labels,
    main,
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
        self.assertEqual(row["score_audit"]["strategy_id"], "legacy_weighted_keyword_v1")
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
