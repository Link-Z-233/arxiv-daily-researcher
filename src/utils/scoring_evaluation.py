"""Offline human-feedback and calibration tools for persisted daily scores.

The production daily pipeline deliberately scores every fetched paper.  This
module does not change that decision and never calls an LLM.  It turns the
local SQLite score ledger into a reviewable, reproducible feedback loop:

* export scored papers as JSONL for annotation;
* validate and import exact-version human labels;
* calculate binary metrics and threshold scans from the frozen evidence.

All exported fields are explicitly selected.  In particular, this module
never serializes ``.env`` values, provider credentials, webhook URLs, or
private free-text research context.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence


EXPORT_SCHEMA = "arxiv-daily-researcher-score-review-v1"
LABEL_SCHEMA = "arxiv-daily-researcher-score-label-v1"
EVALUATION_SCHEMA = "arxiv-daily-researcher-score-evaluation-v1"
ALLOWED_LABELS = frozenset({"relevant", "not_relevant", "unsure"})
_BINARY_LABELS = frozenset({"relevant", "not_relevant"})


class ScoringEvaluationError(ValueError):
    """Raised when an export, label set, or evaluation input is unsafe."""


@dataclass(frozen=True)
class Label:
    """One validated human relevance label for an exact source/paper ID."""

    source: str
    paper_id: str
    label: str
    note: str = ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_load(value: Any, *, field: str, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ScoringEvaluationError(f"数据库中的 {field} 不是有效 JSON") from exc


def _finite_float(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScoringEvaluationError(f"{field} 必须是有限数字")
    result = float(value)
    if not math.isfinite(result):
        raise ScoringEvaluationError(f"{field} 必须是有限数字")
    return result


def _nonempty_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScoringEvaluationError(f"{field} 必须是非空字符串")
    return value.strip()


def _paper_payload(row: sqlite3.Row) -> dict[str, Any]:
    paper = _json_load(row["paper_json"], field="paper_json")
    if not isinstance(paper, dict):
        raise ScoringEvaluationError("数据库中的 paper_json 必须是对象")
    return paper


def _score_payload(row: sqlite3.Row) -> dict[str, Any]:
    score = _json_load(row["score_json"], field="score_json")
    if not isinstance(score, dict):
        raise ScoringEvaluationError("数据库中的 score_json 必须是对象")
    return score


def _audit_payload(row: sqlite3.Row) -> dict[str, Any]:
    audit = _json_load(row["score_audit_json"], field="score_audit_json", default={})
    if not isinstance(audit, dict):
        raise ScoringEvaluationError("数据库中的 score_audit_json 必须是对象")
    return audit


def _as_string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ScoringEvaluationError(f"{field} 必须是字符串列表")
    return value


def _strict_bool(value: Any, *, field: str) -> bool:
    """Accept only JSON booleans for a persisted classification decision."""
    if not isinstance(value, bool):
        raise ScoringEvaluationError(f"{field} 必须是布尔值")
    return value


def _review_row(row: sqlite3.Row) -> dict[str, Any]:
    """Convert one database row to an explicitly safe annotation record."""
    paper = _paper_payload(row)
    score = _score_payload(row)
    audit = _audit_payload(row)
    source = _nonempty_text(row["source"], field="source")
    paper_id = _nonempty_text(row["paper_id"], field="paper_id")

    keyword_scores = score.get("keyword_scores")
    if not isinstance(keyword_scores, dict):
        raise ScoringEvaluationError(f"{source}:{paper_id} 的 keyword_scores 必须是对象")
    normalized_keyword_scores = {
        _nonempty_text(keyword, field="keyword_scores 的关键词"): _finite_float(
            value, field=f"{source}:{paper_id} 的关键词相关度"
        )
        for keyword, value in keyword_scores.items()
    }
    expert_authors = _as_string_list(
        score.get("expert_authors_found", []), field="expert_authors_found"
    )
    extracted_keywords = _as_string_list(
        score.get("extracted_keywords", []), field="extracted_keywords"
    )

    return {
        "schema": EXPORT_SCHEMA,
        "source": source,
        "paper_id": paper_id,
        "canonical_id": str(row["canonical_id"] or paper.get("canonical_id") or paper_id),
        "version": int(row["version"] or paper.get("version") or 0),
        "title": str(paper.get("title") or ""),
        "authors": _as_string_list(paper.get("authors", []), field="authors"),
        "abstract": str(paper.get("abstract") or ""),
        "url": str(paper.get("url") or ""),
        "categories": _as_string_list(paper.get("categories", []), field="categories"),
        "published_date": paper.get("published_date"),
        "updated_date": paper.get("updated_date"),
        "scored_at": row["scored_at"],
        "delivered_at": row["delivered_at"],
        "production_score": {
            "total_score": _finite_float(
                score.get("total_score"), field=f"{source}:{paper_id} 的 total_score"
            ),
            "passing_score": _finite_float(
                score.get("passing_score"), field=f"{source}:{paper_id} 的 passing_score"
            ),
            "is_qualified": _strict_bool(
                score.get("is_qualified"), field=f"{source}:{paper_id} 的 is_qualified"
            ),
            "keyword_scores": normalized_keyword_scores,
            "author_bonus": _finite_float(
                score.get("author_bonus", 0), field=f"{source}:{paper_id} 的 author_bonus"
            ),
            "expert_authors_found": expert_authors,
            "reasoning": str(score.get("reasoning") or ""),
            "tldr": str(score.get("tldr") or ""),
            "extracted_keywords": extracted_keywords,
        },
        # Older database rows predate audit metadata.  Expose this fact
        # instead of retroactively claiming their original policy is known.
        "score_audit": audit if audit else {"legacy": True},
    }


def iter_scored_papers(db_path: Path) -> Iterable[dict[str, Any]]:
    """Yield scored papers in a stable order, including delivered history."""
    path = Path(db_path)
    if not path.is_file():
        raise ScoringEvaluationError(f"评分数据库不存在: {path}")
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT daily_papers.*, paper_deliveries.delivered_at
            FROM daily_papers
            LEFT JOIN paper_deliveries
              ON paper_deliveries.source = daily_papers.source
             AND paper_deliveries.canonical_id = daily_papers.canonical_id
             AND paper_deliveries.version = daily_papers.version
            WHERE daily_papers.score_status = 'succeeded'
              AND daily_papers.score_json IS NOT NULL
            ORDER BY COALESCE(daily_papers.scored_at, daily_papers.first_seen_at) DESC,
                     daily_papers.source ASC,
                     daily_papers.paper_id ASC
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise ScoringEvaluationError(f"无法读取评分数据库: {exc}") from exc
    finally:
        try:
            conn.close()
        except UnboundLocalError:
            pass
    for row in rows:
        yield _review_row(row)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
                count += 1
            handle.flush()
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return count


def export_review_candidates(
    db_path: Path,
    output_path: Path,
    *,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """Export scored papers as JSONL for manual relevance review.

    The optional limit is solely a review-file convenience; it never affects
    the daily pipeline or the underlying score ledger.
    """
    if limit is not None and limit < 0:
        raise ScoringEvaluationError("limit 不能小于 0")
    rows = []
    for row in iter_scored_papers(db_path):
        if limit is not None and len(rows) >= limit:
            break
        rows.append(row)
    count = _write_jsonl(output_path, rows)
    return {
        "schema": EXPORT_SCHEMA,
        "generated_at": _utc_now(),
        "db_path": str(Path(db_path)),
        "output_path": str(Path(output_path)),
        "count": count,
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        raise ScoringEvaluationError(f"JSONL 文件不存在: {source}")
    rows: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for number, raw_line in enumerate(handle, 1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ScoringEvaluationError(f"{source}:{number} 不是有效 JSON") from exc
            if not isinstance(row, dict):
                raise ScoringEvaluationError(f"{source}:{number} 必须是 JSON 对象")
            rows.append(row)
    return rows


def _validate_labels(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], Label]:
    labels: dict[tuple[str, str], Label] = {}
    for number, raw in enumerate(rows, 1):
        schema = raw.get("schema", LABEL_SCHEMA)
        if schema != LABEL_SCHEMA:
            raise ScoringEvaluationError(f"标签第 {number} 行 schema 不受支持: {schema!r}")
        source = _nonempty_text(raw.get("source"), field=f"标签第 {number} 行 source")
        paper_id = _nonempty_text(raw.get("paper_id"), field=f"标签第 {number} 行 paper_id")
        label = _nonempty_text(raw.get("label"), field=f"标签第 {number} 行 label").lower()
        if label not in ALLOWED_LABELS:
            raise ScoringEvaluationError(
                f"标签第 {number} 行 label 必须是 {', '.join(sorted(ALLOWED_LABELS))} 之一"
            )
        note = raw.get("note", "")
        if note is None:
            note = ""
        if not isinstance(note, str):
            raise ScoringEvaluationError(f"标签第 {number} 行 note 必须是字符串")
        key = (source, paper_id)
        if key in labels:
            raise ScoringEvaluationError(
                f"标签存在重复身份: source={source!r}, paper_id={paper_id!r}"
            )
        labels[key] = Label(source=source, paper_id=paper_id, label=label, note=note.strip())
    return labels


def load_labels(path: Path) -> dict[tuple[str, str], Label]:
    """Load a strict JSONL feedback set keyed by an exact paper version."""
    return _validate_labels(_read_jsonl(path))


def _metrics(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    materialized = list(rows)
    tp = sum(1 for row in materialized if row["actual"] and row["predicted"])
    tn = sum(1 for row in materialized if not row["actual"] and not row["predicted"])
    fp = sum(1 for row in materialized if not row["actual"] and row["predicted"])
    fn = sum(1 for row in materialized if row["actual"] and not row["predicted"])
    total = len(materialized)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    accuracy = (tp + tn) / total if total else 0.0
    pass_rate = sum(1 for row in materialized if row["predicted"]) / total if total else 0.0
    return {
        "total": total,
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "accuracy": round(accuracy, 6),
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "pass_rate": round(pass_rate, 6),
    }


def _comparison_rows(
    papers: Iterable[Mapping[str, Any]], labels: Mapping[tuple[str, str], Label]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    compared: list[dict[str, Any]] = []
    label_counts: Counter[str] = Counter()
    seen = set()
    for paper in papers:
        key = (str(paper["source"]), str(paper["paper_id"]))
        if key in seen:
            raise ScoringEvaluationError(f"导出的评分候选出现重复身份: {key[0]}:{key[1]}")
        seen.add(key)
        label = labels.get(key)
        if label is None:
            continue
        label_counts[label.label] += 1
        if label.label not in _BINARY_LABELS:
            continue
        score = paper["production_score"]
        compared.append(
            {
                "source": key[0],
                "paper_id": key[1],
                "title": paper["title"],
                "actual": label.label == "relevant",
                "label": label.label,
                "note": label.note,
                "total_score": _finite_float(score["total_score"], field="total_score"),
                "passing_score": _finite_float(score["passing_score"], field="passing_score"),
                "production_predicted": bool(score["is_qualified"]),
                "keyword_scores": score["keyword_scores"],
                "score_audit": paper["score_audit"],
            }
        )

    unknown = sorted(set(labels).difference(seen))
    if unknown:
        examples = ", ".join(f"{source}:{paper_id}" for source, paper_id in unknown[:5])
        suffix = " …" if len(unknown) > 5 else ""
        raise ScoringEvaluationError(
            f"标签包含当前导出/数据库中不存在的论文 ({len(unknown)} 条): {examples}{suffix}"
        )
    return compared, dict(label_counts)


def _threshold_values(rows: Iterable[Mapping[str, Any]], supplied: Optional[Sequence[float]]) -> list[float]:
    if supplied is not None:
        values = sorted({_finite_float(value, field="threshold") for value in supplied})
        if not values:
            raise ScoringEvaluationError("thresholds 不能为空")
        return values
    # Include every observed score and one value above the maximum so the
    # report contains both extremes.  This is an exact, reproducible scan,
    # not an arbitrary grid whose answer changes with step size.
    scores = sorted({float(row["total_score"]) for row in rows})
    if not scores:
        return []
    return [*scores, math.nextafter(scores[-1], math.inf)]


def _misclassification_record(row: Mapping[str, Any], predicted: bool) -> dict[str, Any]:
    return {
        "source": row["source"],
        "paper_id": row["paper_id"],
        "title": row["title"],
        "label": row["label"],
        "note": row["note"],
        "predicted": predicted,
        "total_score": row["total_score"],
        "passing_score": row["passing_score"],
        "keyword_scores": row["keyword_scores"],
        "score_audit": row["score_audit"],
    }


def evaluate_labels(
    db_path: Path,
    labels_path: Path,
    *,
    thresholds: Optional[Sequence[float]] = None,
) -> dict[str, Any]:
    """Evaluate persisted production scores against an exact human label set."""
    papers = list(iter_scored_papers(db_path))
    labels = load_labels(labels_path)
    rows, label_counts = _comparison_rows(papers, labels)
    if not rows:
        raise ScoringEvaluationError("没有可用于二分类评测的标签；至少需要 relevant 或 not_relevant")

    production_rows = [
        {**row, "predicted": row["production_predicted"]} for row in rows
    ]
    production_metrics = _metrics(production_rows)
    scan = []
    for threshold in _threshold_values(rows, thresholds):
        evaluated = [{**row, "predicted": row["total_score"] >= threshold} for row in rows]
        scan.append({"threshold": threshold, **_metrics(evaluated)})

    false_positives = [
        _misclassification_record(row, True)
        for row in production_rows
        if row["predicted"] and not row["actual"]
    ]
    false_negatives = [
        _misclassification_record(row, False)
        for row in production_rows
        if not row["predicted"] and row["actual"]
    ]
    false_positives.sort(key=lambda row: (-row["total_score"], row["source"], row["paper_id"]))
    false_negatives.sort(key=lambda row: (row["total_score"], row["source"], row["paper_id"]))

    strategies = Counter(
        str((row["score_audit"] or {}).get("strategy_id", "legacy_unknown"))
        for row in rows
    )
    policy_fingerprints = Counter(
        str((row["score_audit"] or {}).get("policy_fingerprint", "legacy_unknown"))
        for row in rows
    )
    return {
        "schema": EVALUATION_SCHEMA,
        "generated_at": _utc_now(),
        "db_path": str(Path(db_path)),
        "labels_path": str(Path(labels_path)),
        "scored_papers_available": len(papers),
        "labels_loaded": len(labels),
        "label_counts": dict(sorted(label_counts.items())),
        "binary_labels_used": len(rows),
        "score_strategies": dict(sorted(strategies.items())),
        "policy_fingerprints": dict(sorted(policy_fingerprints.items())),
        "production_rule": {
            "name": "persisted_is_qualified",
            "metrics": production_metrics,
        },
        "threshold_scan": scan,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
    }


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_evaluation_markdown(result: Mapping[str, Any]) -> str:
    """Render a small, safe-to-share summary without secrets or raw config."""
    production = result["production_rule"]["metrics"]
    lines = [
        "# Daily scoring evaluation",
        "",
        f"- Generated at: `{result['generated_at']}`",
        f"- Scored papers available: {result['scored_papers_available']}",
        f"- Labels loaded: {result['labels_loaded']}",
        f"- Binary labels used: {result['binary_labels_used']}",
        f"- Label counts: `{json.dumps(result['label_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- Score strategies: `{json.dumps(result['score_strategies'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Persisted production rule",
        "",
        "| TP | TN | FP | FN | Precision | Recall | F1 | Accuracy | Pass rate |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        "| {tp} | {tn} | {fp} | {fn} | {precision:.4f} | {recall:.4f} | {f1:.4f} | {accuracy:.4f} | {pass_rate:.4f} |".format(
            **production
        ),
        "",
        "## Total-score threshold scan",
        "",
        "| Threshold | Precision | Recall | F1 | FP | FN | Pass rate |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in result["threshold_scan"]:
        lines.append(
            "| {threshold:.6g} | {precision:.4f} | {recall:.4f} | {f1:.4f} | {fp} | {fn} | {pass_rate:.4f} |".format(
                **row
            )
        )

    for heading, rows in (
        ("False positives", result["false_positives"]),
        ("False negatives", result["false_negatives"]),
    ):
        lines.extend(["", f"## {heading}", ""])
        if not rows:
            lines.append("None.")
            continue
        lines.extend(
            [
                "| Source | Paper ID | Score | Passing score | Title | Note |",
                "|---|---|---:|---:|---|---|",
            ]
        )
        for row in rows:
            lines.append(
                "| {source} | {paper_id} | {total_score:.4g} | {passing_score:.4g} | {title} | {note} |".format(
                    source=_markdown_cell(row["source"]),
                    paper_id=_markdown_cell(row["paper_id"]),
                    total_score=row["total_score"],
                    passing_score=row["passing_score"],
                    title=_markdown_cell(row["title"]),
                    note=_markdown_cell(row["note"]),
                )
            )
    return "\n".join(lines) + "\n"


def write_evaluation_report(
    result: Mapping[str, Any], json_path: Path, markdown_path: Optional[Path] = None
) -> dict[str, str]:
    """Persist machine-readable and optional human-readable evaluation output."""
    json_target = Path(json_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    json_content = json.dumps(dict(result), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    json_temporary = json_target.with_name(f".{json_target.name}.tmp")
    try:
        json_temporary.write_text(json_content, encoding="utf-8")
        json_temporary.replace(json_target)
    finally:
        json_temporary.unlink(missing_ok=True)
    written = {"json": str(json_target)}
    if markdown_path is not None:
        markdown_target = Path(markdown_path)
        markdown_target.parent.mkdir(parents=True, exist_ok=True)
        markdown_temporary = markdown_target.with_name(f".{markdown_target.name}.tmp")
        try:
            markdown_temporary.write_text(render_evaluation_markdown(result), encoding="utf-8")
            markdown_temporary.replace(markdown_target)
        finally:
            markdown_temporary.unlink(missing_ok=True)
        written["markdown"] = str(markdown_target)
    return written


def _parse_thresholds(value: Optional[str]) -> Optional[list[float]]:
    if value is None:
        return None
    raw_items = [part.strip() for part in value.split(",") if part.strip()]
    if not raw_items:
        raise ScoringEvaluationError("--thresholds 不能为空")
    try:
        return [float(item) for item in raw_items]
    except ValueError as exc:
        raise ScoringEvaluationError("--thresholds 必须是逗号分隔的数字") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export, label and evaluate persisted daily-paper scores without changing production decisions."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    export = subparsers.add_parser("export", help="export scored papers as review JSONL")
    export.add_argument("--db", required=True, type=Path, help="daily_research SQLite database")
    export.add_argument("--output", required=True, type=Path, help="review JSONL output")
    export.add_argument("--limit", type=int, default=None, help="optional review export limit")

    evaluate = subparsers.add_parser("evaluate", help="evaluate strict human labels against stored scores")
    evaluate.add_argument("--db", required=True, type=Path, help="daily_research SQLite database")
    evaluate.add_argument("--labels", required=True, type=Path, help="label JSONL input")
    evaluate.add_argument("--json-output", required=True, type=Path, help="evaluation JSON output")
    evaluate.add_argument("--markdown-output", type=Path, default=None, help="optional Markdown output")
    evaluate.add_argument(
        "--thresholds",
        default=None,
        help="optional comma-separated total-score thresholds; default scans all observed values",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint; return a nonzero code for malformed feedback/evidence."""
    args = build_parser().parse_args(argv)
    try:
        if args.command == "export":
            summary = export_review_candidates(args.db, args.output, limit=args.limit)
        else:
            result = evaluate_labels(
                args.db, args.labels, thresholds=_parse_thresholds(args.thresholds)
            )
            summary = {**result, "written": write_evaluation_report(
                result, args.json_output, args.markdown_output
            )}
    except ScoringEvaluationError as exc:
        print(f"scoring evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
