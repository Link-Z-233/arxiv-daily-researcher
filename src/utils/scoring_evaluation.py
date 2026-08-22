"""Read-only diagnostics for persisted daily-paper scores and delivery health.

The production daily pipeline deliberately scores every fetched paper.  This
module does not change that decision and never calls an LLM.  It reads the
local SQLite score ledger and turns it into operational evidence:

* score-profile drift between a recent and a baseline run window;
* per-source scan receipt summaries and outbox health;
* the read-only run diagnostics rendered by the WebUI.

All exported fields are explicitly selected.  In particular, this module
never serializes ``.env`` values, provider credentials, webhook URLs, or
private free-text research context.  The former human-label feedback loop
(export/evaluate) was removed by design: preference learning in the scoring
pipeline replaced it, and it will not come back.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
DIAGNOSTICS_SCHEMA = "arxiv-daily-researcher-diagnostics-v1"
SCAN_OBSERVABILITY_SCHEMA = "arxiv-daily-researcher-scan-observability-v1"
_PUBLIC_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


class ScoringEvaluationError(ValueError):
    """Raised when a diagnostics input is unsafe or inconsistent."""




def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()



def _finite_float(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScoringEvaluationError(f"{field} 必须是有限数字")
    result = float(value)
    if not math.isfinite(result):
        raise ScoringEvaluationError(f"{field} 必须是有限数字")
    return result







def _strict_bool(value: Any, *, field: str) -> bool:
    """Accept only JSON booleans for a persisted classification decision."""
    if not isinstance(value, bool):
        raise ScoringEvaluationError(f"{field} 必须是布尔值")
    return value


def _optional_finite_float(value: Any, *, field: str) -> Optional[float]:
    """Validate an optional persisted number without fabricating a value."""
    if value is None:
        return None
    return _finite_float(value, field=field)



















def _open_readonly_database(db_path: Path) -> sqlite3.Connection:
    """Open the persisted ledger without creating, migrating, or changing it."""
    path = Path(db_path)
    if not path.is_file():
        raise ScoringEvaluationError(f"评分数据库不存在: {path}")
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        # Keep every aggregate in one SQLite snapshot when the scheduled
        # worker happens to be committing a paper or an outbox retry.
        conn.execute("BEGIN")
        return conn
    except (OSError, ValueError, sqlite3.Error) as exc:
        raise ScoringEvaluationError(f"无法以只读方式打开评分数据库: {exc}") from exc


def _database_table_columns(conn: sqlite3.Connection) -> dict[str, set[str]]:
    try:
        table_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    except sqlite3.Error as exc:
        raise ScoringEvaluationError(f"无法读取评分数据库结构: {exc}") from exc

    tables: dict[str, set[str]] = {}
    for row in table_rows:
        name = row["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue
        try:
            columns = conn.execute(f"PRAGMA table_info({name})").fetchall()
        except sqlite3.Error as exc:
            raise ScoringEvaluationError(f"无法读取数据表结构: {name}") from exc
        tables[name] = {str(column["name"]) for column in columns}
    return tables


def _require_database_columns(
    tables: Mapping[str, set[str]], table: str, required: set[str]
) -> set[str]:
    columns = tables.get(table)
    if columns is None:
        raise ScoringEvaluationError(f"评分数据库缺少必要数据表: {table}")
    missing = sorted(required.difference(columns))
    if missing:
        raise ScoringEvaluationError(
            f"评分数据库表 {table} 缺少必要字段: {', '.join(missing)}"
        )
    return columns


def _column_or_null(columns: set[str], name: str) -> str:
    """Select an optional known column while supporting an older read-only DB."""
    return name if name in columns else f"NULL AS {name}"


def _safe_public_identifier(value: Any, *, fallback: str = "unknown") -> str:
    """Keep diagnostic labels useful without echoing arbitrary database text."""
    if not isinstance(value, str):
        return fallback
    normalized = value.strip()
    if not normalized:
        return fallback
    # Model/strategy labels are intentionally public configuration evidence,
    # but never reproduce a URL, query string, credential-like value, or an
    # unconstrained corrupt database string.
    if (
        _PUBLIC_IDENTIFIER_RE.fullmatch(normalized)
        and "://" not in normalized
        and "?" not in normalized
        and "=" not in normalized
        and "@" not in normalized
    ):
        return normalized
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"fingerprint:{digest}"


def _safe_policy_fingerprint(value: Any) -> str:
    if value is None or value == "":
        return "legacy_unknown"
    if isinstance(value, str) and _FINGERPRINT_RE.fullmatch(value.strip()):
        return value.strip().lower()
    return "invalid_fingerprint"


def _safe_temperature(value: Any) -> Optional[float]:
    try:
        return _finite_float(value, field="temperature")
    except ScoringEvaluationError:
        return None


def _safe_nonnegative_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value) if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _rounded(value: Optional[float]) -> Optional[float]:
    return round(value, 6) if value is not None else None


def _quantile(values: Sequence[float], quantile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _numeric_summary(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "p25": None,
            "median": None,
            "p75": None,
            "max": None,
            "mean": None,
        }
    return {
        "count": len(values),
        "min": _rounded(min(values)),
        "p25": _rounded(_quantile(values, 0.25)),
        "median": _rounded(_quantile(values, 0.5)),
        "p75": _rounded(_quantile(values, 0.75)),
        "max": _rounded(max(values)),
        "mean": _rounded(sum(values) / len(values)),
    }


def _score_histogram(values: Sequence[float]) -> list[dict[str, Any]]:
    """Use fixed, interpretable bins while retaining scores outside 0–10."""
    buckets = [
        ("< 0", lambda value: value < 0),
        ("0–2", lambda value: 0 <= value <= 2),
        ("(2, 4]", lambda value: 2 < value <= 4),
        ("(4, 6]", lambda value: 4 < value <= 6),
        ("(6, 8]", lambda value: 6 < value <= 8),
        ("(8, 10]", lambda value: 8 < value <= 10),
        ("> 10", lambda value: value > 10),
    ]
    return [
        {"range": label, "count": sum(1 for value in values if predicate(value))}
        for label, predicate in buckets
    ]


def _stage_state(value: Any, *, stage: str, legacy_score_payload: bool = False) -> str:
    allowed = {
        "score": {"pending", "succeeded", "failed"},
        "translation": {"pending", "succeeded", "failed", "not_required"},
        "analysis": {"pending", "succeeded", "failed", "not_required"},
    }
    if isinstance(value, str) and value.strip().lower() in allowed[stage]:
        return value.strip().lower()
    if value is None and stage == "score" and legacy_score_payload:
        return "legacy_unknown"
    return "unknown"


def _counter_as_dict(counter: Counter[str]) -> dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter)}


def _parse_audit_payload(value: Any) -> tuple[dict[str, Any], str]:
    if value in (None, ""):
        return {}, "legacy"
    try:
        payload = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}, "invalid"
    if not isinstance(payload, dict):
        return {}, "invalid"
    return payload, "available"


def _profile_from_score(
    score: Mapping[str, Any], audit: Mapping[str, Any]
) -> tuple[str, str, str, Optional[float]]:
    strategy = _safe_public_identifier(
        audit.get("strategy_id") or score.get("strategy_id"), fallback="legacy_unknown"
    )
    policy_fingerprint = _safe_policy_fingerprint(audit.get("policy_fingerprint"))
    model_payload = audit.get("model")
    if not isinstance(model_payload, Mapping):
        return strategy, policy_fingerprint, "legacy_unknown", None
    return (
        strategy,
        policy_fingerprint,
        _safe_public_identifier(model_payload.get("model_name"), fallback="legacy_unknown"),
        _safe_temperature(model_payload.get("temperature")),
    )


def _render_profiles(
    profiles: Counter[tuple[str, str, str, Optional[float]]]
) -> list[dict[str, Any]]:
    rows = []
    for (strategy, policy_fingerprint, model_name, temperature), count in profiles.items():
        rows.append(
            {
                "strategy_id": strategy,
                "policy_fingerprint": policy_fingerprint,
                "model_name": model_name,
                "temperature": _rounded(temperature),
                "count": int(count),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            -row["count"],
            row["strategy_id"],
            row["policy_fingerprint"],
            row["model_name"],
            -1.0 if row["temperature"] is None else row["temperature"],
        ),
    )


def _summarize_window_papers(rows: Sequence[sqlite3.Row]) -> tuple[dict[str, Any], Counter]:
    """Aggregate only non-secret paper-state evidence for one run window."""
    score_states: Counter[str] = Counter()
    translation_states: Counter[str] = Counter()
    analysis_states: Counter[str] = Counter()
    score_basis: Counter[str] = Counter()
    strategies: Counter[str] = Counter()
    policy_fingerprints: Counter[str] = Counter()
    model_names: Counter[str] = Counter()
    profiles: Counter[tuple[str, str, str, Optional[float]]] = Counter()
    scores: list[float] = []
    thresholds: list[float] = []
    qualified_count = 0
    invalid_score_records = 0
    invalid_audit_records = 0
    stale_score_payloads = 0
    retry_total = 0
    retried_papers = 0
    invalid_retry_counts = 0
    last_error_records = 0

    for row in rows:
        raw_score = row["score_json"]
        score_state = _stage_state(
            row["score_status"], stage="score", legacy_score_payload=raw_score not in (None, "")
        )
        score_states[score_state] += 1
        translation_states[_stage_state(row["translation_status"], stage="translation")] += 1
        analysis_states[_stage_state(row["analysis_status"], stage="analysis")] += 1

        retry_count = _safe_nonnegative_int(row["retry_count"])
        if retry_count is None and row["retry_count"] not in (None, ""):
            invalid_retry_counts += 1
        elif retry_count:
            retry_total += retry_count
            retried_papers += 1
        if isinstance(row["last_error"], str) and row["last_error"].strip():
            last_error_records += 1

        if raw_score in (None, ""):
            continue
        if score_state not in {"succeeded", "legacy_unknown"}:
            stale_score_payloads += 1
            continue
        try:
            payload = json.loads(raw_score) if isinstance(raw_score, str) else raw_score
            if not isinstance(payload, dict):
                raise ScoringEvaluationError("score_json 必须是对象")
            total_score = _finite_float(payload.get("total_score"), field="total_score")
            qualified = _strict_bool(payload.get("is_qualified"), field="is_qualified")
            relevance_score = _optional_finite_float(
                payload.get("relevance_score"), field="relevance_score"
            )
            threshold = _optional_finite_float(
                payload.get("qualification_threshold"), field="qualification_threshold"
            )
        except (ScoringEvaluationError, TypeError, ValueError, json.JSONDecodeError):
            invalid_score_records += 1
            continue

        audit, audit_status = _parse_audit_payload(row["score_audit_json"])
        if audit_status == "invalid":
            invalid_audit_records += 1
        profile = _profile_from_score(payload, audit)
        profiles[profile] += 1
        strategies[profile[0]] += 1
        policy_fingerprints[profile[1]] += 1
        model_names[profile[2]] += 1
        if relevance_score is not None:
            selected_score = relevance_score
            score_basis["content_relevance"] += 1
        else:
            selected_score = total_score
            score_basis["legacy_total"] += 1
        scores.append(selected_score)
        if threshold is not None:
            thresholds.append(threshold)
        if qualified:
            qualified_count += 1

    valid_score_records = len(scores)
    return (
        {
            "paper_records_linked_to_runs": len(rows),
            "stage_status_counts": {
                "score": _counter_as_dict(score_states),
                "translation": _counter_as_dict(translation_states),
                "analysis": _counter_as_dict(analysis_states),
            },
            # retry_count is a durable per-paper total, not a per-run event
            # log.  Name it explicitly so a restart does not look like a
            # fabricated exact retry count for a window.
            "persisted_retry_state": {
                "papers_with_retries": retried_papers,
                "retry_total": retry_total,
                "invalid_retry_count_records": invalid_retry_counts,
                "papers_with_last_error": last_error_records,
            },
            "scoring": {
                "valid_score_records": valid_score_records,
                "invalid_score_records": invalid_score_records,
                "score_payloads_outside_succeeded_state": stale_score_payloads,
                "invalid_audit_records": invalid_audit_records,
                "qualified_papers": qualified_count,
                "qualification_rate": _rounded(
                    qualified_count / valid_score_records if valid_score_records else None
                ),
                "score_basis_counts": _counter_as_dict(score_basis),
                "score_distribution": _numeric_summary(scores),
                "qualification_threshold_distribution": _numeric_summary(thresholds),
                "score_histogram": _score_histogram(scores),
                "strategy_counts": _counter_as_dict(strategies),
                "policy_fingerprint_counts": _counter_as_dict(policy_fingerprints),
                "model_counts": _counter_as_dict(model_names),
                "profiles": _render_profiles(profiles),
            },
        },
        profiles,
    )


def _parse_scan_plan(value: Any) -> tuple[list[str], bool]:
    if value is None:
        return [], False
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return [], False
    if not isinstance(decoded, list):
        return [], False
    normalized = []
    for source in decoded:
        if not isinstance(source, str) or not source.strip():
            return [], False
        normalized.append(source.strip().lower())
    if len(set(normalized)) != len(normalized):
        return [], False
    return sorted(normalized), True


def _source_key(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return "invalid_source"


def _new_source_scan_stats() -> dict[str, int]:
    return {
        "planned_runs": 0,
        "receipt_rows": 0,
        "succeeded_receipts": 0,
        "failed_receipts": 0,
        "missing_receipts": 0,
        "corrupt_receipts": 0,
        "unplanned_receipts": 0,
        "successful_zero_candidate_receipts": 0,
        "candidate_count_unavailable_receipts": 0,
        "candidate_total": 0,
        "query_attempts": 0,
        "retried_queries": 0,
        "failed_domain_receipts": 0,
    }


def _scan_summary(
    conn: sqlite3.Connection,
    tables: Mapping[str, set[str]],
    run_rows: Sequence[sqlite3.Row],
) -> dict[str, Any]:
    """Summarize receipts without exposing query text or source error details."""
    expected_pairs: set[tuple[str, str]] = set()
    source_stats: dict[str, dict[str, int]] = {}
    plan_missing_runs = 0
    plan_corrupt_runs = 0
    for run in run_rows:
        sources, valid_plan = _parse_scan_plan(run["scanned_sources_json"])
        if not valid_plan:
            if run["scanned_sources_json"] is None and run["scan_days"] is None:
                plan_missing_runs += 1
            else:
                plan_corrupt_runs += 1
            continue
        for source in sources:
            expected_pairs.add((run["run_id"], source))
            source_stats.setdefault(source, _new_source_scan_stats())["planned_runs"] += 1

    receipt_columns = tables.get("daily_scan_receipts")
    if receipt_columns is None or not {
        "run_id",
        "source",
        "status",
        "receipt_json",
    }.issubset(receipt_columns):
        return {
            "receipt_table_available": False,
            "planned_source_runs": len(expected_pairs),
            "runs_without_scan_plan": plan_missing_runs,
            "runs_with_corrupt_scan_plan": plan_corrupt_runs,
            "sources": [],
            "anomalies": [
                {
                    "kind": "scan_receipt_table_unavailable",
                    "count": len(expected_pairs),
                }
            ]
            if expected_pairs
            else [],
        }

    run_ids = [str(run["run_id"]) for run in run_rows]
    if run_ids:
        placeholders = ", ".join("?" for _ in run_ids)
        try:
            receipt_rows = conn.execute(
                "SELECT run_id, source, status, receipt_json FROM daily_scan_receipts "
                f"WHERE run_id IN ({placeholders})",
                run_ids,
            ).fetchall()
        except sqlite3.Error as exc:
            raise ScoringEvaluationError(f"无法读取扫描收据: {exc}") from exc
    else:
        receipt_rows = []

    received_pairs: set[tuple[str, str]] = set()
    for row in receipt_rows:
        source = _source_key(row["source"])
        stats = source_stats.setdefault(source, _new_source_scan_stats())
        stats["receipt_rows"] += 1
        pair = (str(row["run_id"]), source)
        received_pairs.add(pair)
        if pair not in expected_pairs:
            stats["unplanned_receipts"] += 1

        valid = True
        try:
            receipt = json.loads(row["receipt_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            receipt = None
            valid = False
        db_status = row["status"] if row["status"] in {"succeeded", "failed"} else None
        if not isinstance(receipt, dict):
            valid = False
        elif (
            _source_key(receipt.get("source")) != source
            or receipt.get("status") not in {"succeeded", "failed"}
            or receipt.get("status") != db_status
        ):
            valid = False
        if not valid:
            stats["corrupt_receipts"] += 1
            continue

        if db_status == "succeeded":
            stats["succeeded_receipts"] += 1
        else:
            stats["failed_receipts"] += 1

        total_candidates = _safe_nonnegative_int(receipt.get("total_new_candidates"))
        if receipt.get("total_new_candidates") is None:
            stats["candidate_count_unavailable_receipts"] += 1
        elif total_candidates is None:
            stats["corrupt_receipts"] += 1
        elif db_status == "succeeded":
            stats["candidate_total"] += total_candidates
            if total_candidates == 0:
                stats["successful_zero_candidate_receipts"] += 1

        domain_receipts = receipt.get("domain_receipts")
        if not isinstance(domain_receipts, list):
            stats["corrupt_receipts"] += 1
            continue
        for domain_receipt in domain_receipts:
            if not isinstance(domain_receipt, dict):
                stats["corrupt_receipts"] += 1
                continue
            if domain_receipt.get("status") == "failed":
                stats["failed_domain_receipts"] += 1
            queries = domain_receipt.get("queries")
            if not isinstance(queries, dict):
                continue
            for query in queries.values():
                if not isinstance(query, dict):
                    stats["corrupt_receipts"] += 1
                    continue
                attempts = _safe_nonnegative_int(query.get("attempts"))
                if attempts is None:
                    stats["corrupt_receipts"] += 1
                    continue
                stats["query_attempts"] += attempts
                if attempts > 1:
                    stats["retried_queries"] += 1

    for pair in expected_pairs.difference(received_pairs):
        source = pair[1]
        source_stats.setdefault(source, _new_source_scan_stats())["missing_receipts"] += 1

    source_rows = []
    anomalies = []
    for source in sorted(source_stats):
        stats = source_stats[source]
        succeeded = stats["succeeded_receipts"]
        source_rows.append(
            {
                "source": _safe_public_identifier(source, fallback="invalid_source"),
                **stats,
                "candidates_per_successful_receipt": _rounded(
                    stats["candidate_total"] / succeeded if succeeded else None
                ),
            }
        )
        for field, kind in (
            ("failed_receipts", "failed_scan_receipt"),
            ("missing_receipts", "missing_scan_receipt"),
            ("corrupt_receipts", "corrupt_scan_receipt"),
            ("unplanned_receipts", "unplanned_scan_receipt"),
            ("successful_zero_candidate_receipts", "successful_zero_candidate_receipt"),
            ("retried_queries", "retried_source_query"),
        ):
            if stats[field]:
                anomalies.append(
                    {
                        "kind": kind,
                        "source": _safe_public_identifier(source, fallback="invalid_source"),
                        "count": stats[field],
                    }
                )

    return {
        "receipt_table_available": True,
        "planned_source_runs": len(expected_pairs),
        "runs_without_scan_plan": plan_missing_runs,
        "runs_with_corrupt_scan_plan": plan_corrupt_runs,
        "sources": source_rows,
        "anomalies": sorted(anomalies, key=lambda item: (item["kind"], item["source"])),
    }


def _safe_observability_timestamp(value: Any) -> Optional[str]:
    """Return a canonical timestamp or omit untrusted persisted text.

    Run and receipt timestamps are normally generated locally, but this
    diagnostic surface must still avoid reflecting arbitrary corrupt database
    text into a browser.  A canonical ISO representation is both useful and
    bounded; timezone-naive legacy values deliberately remain naive instead
    of pretending their timezone is known.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw or len(raw) > 80:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.isoformat(timespec="seconds")


def _safe_run_status(value: Any) -> str:
    if isinstance(value, str) and value in {"running", "completed", "failed"}:
        return value
    return "unknown"


def _safe_receipt_status(value: Any) -> Optional[str]:
    if isinstance(value, str) and value in {"succeeded", "failed"}:
        return value
    return None


def _safe_domain_receipt_summary(value: Any) -> Optional[dict[str, Any]]:
    """Select bounded arXiv evidence while intentionally dropping errors/query text."""
    if not isinstance(value, Mapping):
        return None
    queries = value.get("queries")
    query_summaries: dict[str, dict[str, Optional[int]]] = {}
    if isinstance(queries, Mapping):
        # The only public arXiv query kinds currently owned by this project.
        # Ignore arbitrary future/corrupt keys rather than reflecting them.
        for query_kind in ("submitted", "updated"):
            query = queries.get(query_kind)
            if not isinstance(query, Mapping):
                continue
            query_summaries[query_kind] = {
                field: _safe_nonnegative_int(query.get(field))
                for field in (
                    "api_entries_checked",
                    "window_entries",
                    "pages_observed",
                    "attempts",
                )
            }

    raw_status = value.get("status")
    status = raw_status if raw_status in {"succeeded", "failed"} else "unknown"
    return {
        "domain": _safe_public_identifier(value.get("domain"), fallback="unknown_domain"),
        "status": status,
        "queries": query_summaries,
        "new_candidates": _safe_nonnegative_int(value.get("new_candidates")),
        "skipped_legacy_history": _safe_nonnegative_int(
            value.get("skipped_legacy_history")
        ),
        "skipped_already_collected": _safe_nonnegative_int(
            value.get("skipped_already_collected")),
        "deduplicated_within_domain": _safe_nonnegative_int(
            value.get("deduplicated_within_domain")),
    }


def _safe_receipt_observability_summary(row: sqlite3.Row) -> dict[str, Any]:
    """Convert one receipt row to a browser-safe, non-secret summary.

    The durable receipt intentionally preserves source-owned error details for
    server-side recovery logs.  This function is a different contract: it
    exports only status, counters, timestamps, and fixed arXiv query metrics.
    """
    source_key = _source_key(row["source"])
    source = _safe_public_identifier(source_key, fallback="invalid_source")
    db_status = _safe_receipt_status(row["status"])
    try:
        payload = json.loads(row["receipt_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = None

    valid = (
        isinstance(payload, Mapping)
        and _source_key(payload.get("source")) == source_key
        and payload.get("status") == db_status
        and db_status is not None
        and isinstance(payload.get("domain_receipts", []), list)
    )
    if not valid:
        return {
            "source": source,
            "status": "corrupt",
            "scanned_at": None,
            "candidate_count": None,
            "domain_receipts": [],
            "window": {},
        }

    raw_candidate_count = payload.get("total_new_candidates")
    candidate_count = _safe_nonnegative_int(raw_candidate_count)
    if raw_candidate_count is not None and candidate_count is None:
        return {
            "source": source,
            "status": "corrupt",
            "scanned_at": None,
            "candidate_count": None,
            "domain_receipts": [],
            "window": {},
        }

    domain_summaries = []
    for domain_receipt in payload.get("domain_receipts", []):
        summary = _safe_domain_receipt_summary(domain_receipt)
        if summary is not None:
            domain_summaries.append(summary)

    # Only explicit numeric/time-window evidence is surfaced.  This keeps the
    # generic source-summary receipts compact while retaining the arXiv proof
    # that is useful when investigating a suspected coverage gap.
    window = {
        "requested_days": _safe_nonnegative_int(payload.get("requested_scan_days")),
        "announcement_lookback_grace_days": _safe_nonnegative_int(
            payload.get("announcement_lookback_grace_days")
        ),
        "effective_days": _safe_nonnegative_int(payload.get("effective_days")),
        "start": _safe_observability_timestamp(payload.get("window_start")),
        "end": _safe_observability_timestamp(payload.get("window_end")),
    }
    return {
        "source": source,
        "status": db_status,
        "scanned_at": _safe_observability_timestamp(payload.get("scanned_at")),
        # A failed source may have fetched a partial list before it failed.
        # Do not present that number as a trustworthy coverage count.
        "candidate_count": candidate_count if db_status == "succeeded" else None,
        "domain_receipts": domain_summaries,
        "window": window,
    }


def build_recent_scan_receipt_summaries(
    db_path: Path,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    """Read recent scan evidence without changing the production SQLite ledger.

    This is deliberately a narrower public contract than
    :class:`DailyResearchStore`: it opens SQLite with ``mode=ro``, does not run
    schema migrations, and never returns raw errors, source query text, URLs,
    report paths, paper content, notification payloads, or credentials.  It
    is intended for the local WebUI and can also be used by tests/diagnostic
    tooling that need per-run evidence rather than only aggregates.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
        raise ScoringEvaluationError("scan receipt limit 必须是 1 到 200 的整数")

    conn = _open_readonly_database(db_path)
    try:
        tables = _database_table_columns(conn)
        run_columns = _require_database_columns(
            tables, "daily_runs", {"run_id", "started_at", "status"}
        )
        run_query = ", ".join(
            [
                "run_id",
                "started_at",
                "status",
                _column_or_null(run_columns, "scan_started_at"),
                _column_or_null(run_columns, "scan_days"),
                _column_or_null(run_columns, "scanned_sources_json"),
            ]
        )
        try:
            run_rows = conn.execute(
                f"SELECT {run_query} FROM daily_runs "
                "ORDER BY started_at DESC, run_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise ScoringEvaluationError(f"无法读取日报运行记录: {exc}") from exc

        receipt_columns = tables.get("daily_scan_receipts")
        receipt_table_available = receipt_columns is not None and {
            "run_id",
            "source",
            "status",
            "receipt_json",
        }.issubset(receipt_columns)
        receipt_rows: list[sqlite3.Row] = []
        if receipt_table_available and run_rows:
            run_ids = [str(row["run_id"]) for row in run_rows]
            placeholders = ", ".join("?" for _ in run_ids)
            try:
                receipt_rows = conn.execute(
                    "SELECT run_id, source, status, receipt_json FROM daily_scan_receipts "
                    f"WHERE run_id IN ({placeholders}) ORDER BY source ASC",
                    run_ids,
                ).fetchall()
            except sqlite3.Error as exc:
                raise ScoringEvaluationError(f"无法读取扫描收据: {exc}") from exc
    finally:
        conn.close()

    # Keep raw normalized source keys only inside this function so a planned
    # source without a durable receipt can be represented explicitly in the
    # returned safe view.  The final public payload never exposes the raw DB
    # value; it uses _safe_public_identifier below.
    receipts_by_run: dict[str, dict[str, dict[str, Any]]] = {}
    for row in receipt_rows:
        run_receipts = receipts_by_run.setdefault(str(row["run_id"]), {})
        run_receipts[_source_key(row["source"])] = _safe_receipt_observability_summary(row)

    runs = []
    for row in run_rows:
        planned_sources, valid_plan = _parse_scan_plan(row["scanned_sources_json"])
        if valid_plan:
            plan_state = "available"
        elif row["scanned_sources_json"] is None and row["scan_days"] is None:
            plan_state = "not_recorded"
        else:
            plan_state = "corrupt"
        receipt_map = dict(receipts_by_run.get(str(row["run_id"]), {}))
        if valid_plan:
            for source in planned_sources:
                receipt_map.setdefault(
                    source,
                    {
                        "source": _safe_public_identifier(source, fallback="invalid_source"),
                        "status": "missing",
                        "scanned_at": None,
                        "candidate_count": None,
                        "domain_receipts": [],
                        "window": {},
                    },
                )
        receipts = [receipt_map[key] for key in sorted(receipt_map)]
        runs.append(
            {
                "started_at": _safe_observability_timestamp(row["started_at"]),
                "scan_started_at": _safe_observability_timestamp(row["scan_started_at"]),
                "status": _safe_run_status(row["status"]),
                "scan_days": _safe_nonnegative_int(row["scan_days"]),
                "scan_plan_state": plan_state,
                "planned_source_count": len(planned_sources) if valid_plan else None,
                "receipts": receipts,
            }
        )

    return {
        "schema": SCAN_OBSERVABILITY_SCHEMA,
        "read_only": True,
        "receipt_table_available": receipt_table_available,
        "runs": runs,
    }


def _outbox_summary(
    conn: sqlite3.Connection, tables: Mapping[str, set[str]], table: str
) -> dict[str, Any]:
    columns = tables.get(table)
    required = {"status", "attempt_count"}
    if columns is None or not required.issubset(columns):
        return {"available": False}
    try:
        rows = conn.execute(
            f"SELECT status, attempt_count FROM {table}"
        ).fetchall()
    except sqlite3.Error as exc:
        raise ScoringEvaluationError(f"无法读取 {table} 状态: {exc}") from exc
    status_counts: Counter[str] = Counter()
    total_attempts = 0
    retrying_rows = 0
    open_rows = 0
    max_attempts = 0
    for row in rows:
        raw_status = row["status"]
        status = raw_status if raw_status in {"pending", "sending", "sent", "running", "completed"} else "unknown"
        status_counts[status] += 1
        attempts = _safe_nonnegative_int(row["attempt_count"])
        attempts = attempts if attempts is not None else 0
        total_attempts += attempts
        max_attempts = max(max_attempts, attempts)
        if status not in {"sent", "completed"}:
            open_rows += 1
            if attempts:
                retrying_rows += 1
    return {
        "available": True,
        "total_rows": len(rows),
        "open_rows": open_rows,
        "retrying_rows": retrying_rows,
        "total_attempts": total_attempts,
        "max_attempts": max_attempts,
        "status_counts": _counter_as_dict(status_counts),
    }


def _profile_changes(
    recent_profiles: Counter[tuple[str, str, str, Optional[float]]],
    baseline_profiles: Counter[tuple[str, str, str, Optional[float]]],
) -> dict[str, Any]:
    recent_keys = set(recent_profiles)
    baseline_keys = set(baseline_profiles)
    return {
        "new_in_recent_window": _render_profiles(
            Counter({key: recent_profiles[key] for key in recent_keys.difference(baseline_keys)})
        ),
        "absent_from_recent_window": _render_profiles(
            Counter({key: baseline_profiles[key] for key in baseline_keys.difference(recent_keys)})
        ),
    }


def _delta(recent: Optional[float], baseline: Optional[float]) -> Optional[float]:
    if recent is None or baseline is None:
        return None
    return _rounded(recent - baseline)


def _source_volume_changes(recent: Mapping[str, Any], baseline: Mapping[str, Any]) -> list[dict[str, Any]]:
    recent_sources = {row["source"]: row for row in recent.get("sources", [])}
    baseline_sources = {row["source"]: row for row in baseline.get("sources", [])}
    rows = []
    for source in sorted(set(recent_sources).union(baseline_sources)):
        recent_row = recent_sources.get(source, {})
        baseline_row = baseline_sources.get(source, {})
        recent_average = recent_row.get("candidates_per_successful_receipt")
        baseline_average = baseline_row.get("candidates_per_successful_receipt")
        rows.append(
            {
                "source": source,
                "recent_candidate_total": int(recent_row.get("candidate_total", 0)),
                "baseline_candidate_total": int(baseline_row.get("candidate_total", 0)),
                "recent_candidates_per_successful_receipt": recent_average,
                "baseline_candidates_per_successful_receipt": baseline_average,
                "candidate_average_delta": _delta(recent_average, baseline_average),
            }
        )
    return rows


def build_operational_diagnostics(
    db_path: Path,
    *,
    recent_runs: int = 14,
    baseline_runs: int = 28,
) -> dict[str, Any]:
    """Build a read-only score-drift and delivery-health report.

    The report deliberately aggregates persisted evidence only.  It never
    sends notifications, changes a score, creates a database, or serializes
    paper content, error text, report paths, source query text, or outbox
    payloads.  It is therefore safe to run against the production ledger
    while a scheduled worker is idle or active.
    """
    if not isinstance(recent_runs, int) or isinstance(recent_runs, bool) or recent_runs < 1:
        raise ScoringEvaluationError("recent_runs 必须是正整数")
    if not isinstance(baseline_runs, int) or isinstance(baseline_runs, bool) or baseline_runs < 1:
        raise ScoringEvaluationError("baseline_runs 必须是正整数")
    if recent_runs + baseline_runs > 200:
        raise ScoringEvaluationError("recent_runs 与 baseline_runs 之和不能超过 200")

    conn = _open_readonly_database(db_path)
    try:
        tables = _database_table_columns(conn)
        run_columns = _require_database_columns(
            tables, "daily_runs", {"run_id", "started_at", "status"}
        )
        paper_columns = _require_database_columns(
            tables, "daily_papers", {"source", "paper_id", "run_id", "score_json"}
        )
        run_limit = recent_runs + baseline_runs
        run_query = ", ".join(
            [
                "run_id",
                "started_at",
                "status",
                _column_or_null(run_columns, "scan_days"),
                _column_or_null(run_columns, "scanned_sources_json"),
            ]
        )
        try:
            all_runs = conn.execute(
                f"SELECT {run_query} FROM daily_runs "
                "ORDER BY started_at DESC, run_id DESC LIMIT ?",
                (run_limit,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise ScoringEvaluationError(f"无法读取日报运行记录: {exc}") from exc

        recent_run_rows = all_runs[:recent_runs]
        baseline_run_rows = all_runs[recent_runs:]

        paper_query = ", ".join(
            [
                "source",
                "paper_id",
                "run_id",
                "score_json",
                _column_or_null(paper_columns, "score_audit_json"),
                _column_or_null(paper_columns, "score_status"),
                _column_or_null(paper_columns, "translation_status"),
                _column_or_null(paper_columns, "analysis_status"),
                _column_or_null(paper_columns, "retry_count"),
                _column_or_null(paper_columns, "last_error"),
            ]
        )

        def papers_for_runs(run_rows: Sequence[sqlite3.Row]) -> list[sqlite3.Row]:
            run_ids = [str(row["run_id"]) for row in run_rows]
            if not run_ids:
                return []
            placeholders = ", ".join("?" for _ in run_ids)
            try:
                return conn.execute(
                    f"SELECT {paper_query} FROM daily_papers WHERE run_id IN ({placeholders})",
                    run_ids,
                ).fetchall()
            except sqlite3.Error as exc:
                raise ScoringEvaluationError(f"无法读取评分状态: {exc}") from exc

        def run_summary(rows: Sequence[sqlite3.Row], requested: int) -> dict[str, Any]:
            statuses: Counter[str] = Counter()
            for row in rows:
                raw_status = row["status"]
                status = raw_status if raw_status in {"running", "completed", "failed"} else "unknown"
                statuses[status] += 1
            return {
                "requested_run_count": requested,
                "available_run_count": len(rows),
                "status_counts": _counter_as_dict(statuses),
            }

        recent_papers, recent_profiles = _summarize_window_papers(
            papers_for_runs(recent_run_rows)
        )
        baseline_papers, baseline_profiles = _summarize_window_papers(
            papers_for_runs(baseline_run_rows)
        )
        recent_scans = _scan_summary(conn, tables, recent_run_rows)
        baseline_scans = _scan_summary(conn, tables, baseline_run_rows)
        notification_outbox = _outbox_summary(conn, tables, "notification_outbox")
        maintenance_outbox = _outbox_summary(conn, tables, "maintenance_outbox")
    finally:
        conn.close()

    recent_window = {
        "runs": run_summary(recent_run_rows, recent_runs),
        "papers": recent_papers,
        "scans": recent_scans,
    }
    baseline_window = {
        "runs": run_summary(baseline_run_rows, baseline_runs),
        "papers": baseline_papers,
        "scans": baseline_scans,
    }
    recent_scoring = recent_papers["scoring"]
    baseline_scoring = baseline_papers["scoring"]
    return {
        "schema": DIAGNOSTICS_SCHEMA,
        "generated_at": _utc_now(),
        "db_path": str(Path(db_path)),
        "read_only": True,
        "windows": {"recent": recent_window, "baseline": baseline_window},
        "drift": {
            "score": {
                "mean_delta": _delta(
                    recent_scoring["score_distribution"]["mean"],
                    baseline_scoring["score_distribution"]["mean"],
                ),
                "median_delta": _delta(
                    recent_scoring["score_distribution"]["median"],
                    baseline_scoring["score_distribution"]["median"],
                ),
                "qualification_rate_delta": _delta(
                    recent_scoring["qualification_rate"],
                    baseline_scoring["qualification_rate"],
                ),
            },
            "score_profiles": _profile_changes(recent_profiles, baseline_profiles),
            "source_candidate_volume": _source_volume_changes(recent_scans, baseline_scans),
        },
        # These are all-ledger aggregates.  A pending notification must stay
        # visible even when its originating report predates the selected run
        # windows, while payloads, channels, and errors stay private.
        "outbox": {
            "notifications": notification_outbox,
            "maintenance": maintenance_outbox,
        },
        "scope_notes": [
            "Only aggregate, persisted ledger state is included; no paper content, error details, URLs, query text, notification payloads, or credentials are exported.",
            "Paper-state totals are current records linked to their latest run_id; daily_papers is a resumable state ledger, not an immutable per-attempt event log.",
            "Persisted retry totals belong to the current paper record and are not an exact event-by-event retry log for the selected window.",
            "A missing scan receipt means the scan coverage is not independently evidenced by a receipt; it does not by itself prove that the source fetch failed.",
        ],
    }


def _format_diagnostic_number(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_operational_diagnostics_markdown(result: Mapping[str, Any]) -> str:
    """Render a safe, compact human-facing view of the diagnostics JSON."""
    recent = result["windows"]["recent"]
    baseline = result["windows"]["baseline"]
    recent_scoring = recent["papers"]["scoring"]
    baseline_scoring = baseline["papers"]["scoring"]
    lines = [
        "# Daily research diagnostics",
        "",
        f"- Generated at: `{result['generated_at']}`",
        "- Mode: read-only; this report does not modify production state.",
        "- Scope: aggregate state only; paper content, error details, URLs, query text, payloads, and credentials are omitted.",
        "",
        "## Score and run windows",
        "",
        "| Window | Runs available/requested | Run states | Paper records | Valid scores | Qualified rate | Mean | Median | Invalid score records |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, window, scoring in (
        ("Recent", recent, recent_scoring),
        ("Baseline", baseline, baseline_scoring),
    ):
        run_info = window["runs"]
        distribution = scoring["score_distribution"]
        lines.append(
            "| {name} | {available}/{requested} | `{states}` | {papers} | {scores} | {rate} | {mean} | {median} | {invalid} |".format(
                name=name,
                available=run_info["available_run_count"],
                requested=run_info["requested_run_count"],
                states=_markdown_cell(json.dumps(run_info["status_counts"], sort_keys=True)),
                papers=window["papers"]["paper_records_linked_to_runs"],
                scores=scoring["valid_score_records"],
                rate=_format_diagnostic_number(scoring["qualification_rate"]),
                mean=_format_diagnostic_number(distribution["mean"]),
                median=_format_diagnostic_number(distribution["median"]),
                invalid=scoring["invalid_score_records"],
            )
        )

    drift = result["drift"]["score"]
    lines.extend(
        [
            "",
            "## Drift against baseline",
            "",
            "| Mean score Δ | Median score Δ | Qualification-rate Δ |",
            "|---:|---:|---:|",
            "| {mean} | {median} | {rate} |".format(
                mean=_format_diagnostic_number(drift["mean_delta"]),
                median=_format_diagnostic_number(drift["median_delta"]),
                rate=_format_diagnostic_number(drift["qualification_rate_delta"]),
            ),
            "",
            "## Score policy/model profiles newly seen in the recent window",
            "",
        ]
    )
    new_profiles = result["drift"]["score_profiles"]["new_in_recent_window"]
    if not new_profiles:
        lines.append("None.")
    else:
        lines.extend(
            [
                "| Strategy | Policy fingerprint | Model | Temperature | Count |",
                "|---|---|---|---:|---:|",
            ]
        )
        for profile in new_profiles:
            lines.append(
                "| {strategy} | {policy} | {model} | {temperature} | {count} |".format(
                    strategy=_markdown_cell(profile["strategy_id"]),
                    policy=_markdown_cell(profile["policy_fingerprint"]),
                    model=_markdown_cell(profile["model_name"]),
                    temperature=_format_diagnostic_number(profile["temperature"]),
                    count=profile["count"],
                )
            )

    lines.extend(["", "## Source scan evidence", ""])
    source_changes = result["drift"]["source_candidate_volume"]
    if not source_changes:
        lines.append("No source scan receipts are available in the selected windows.")
    else:
        lines.extend(
            [
                "| Source | Recent candidates | Baseline candidates | Recent candidates/successful receipt | Baseline candidates/successful receipt | Δ |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in source_changes:
            lines.append(
                "| {source} | {recent_total} | {baseline_total} | {recent_average} | {baseline_average} | {delta} |".format(
                    source=_markdown_cell(row["source"]),
                    recent_total=row["recent_candidate_total"],
                    baseline_total=row["baseline_candidate_total"],
                    recent_average=_format_diagnostic_number(
                        row["recent_candidates_per_successful_receipt"]
                    ),
                    baseline_average=_format_diagnostic_number(
                        row["baseline_candidates_per_successful_receipt"]
                    ),
                    delta=_format_diagnostic_number(row["candidate_average_delta"]),
                )
            )

    lines.extend(["", "## Recent scan observations", ""])
    observations = recent["scans"]["anomalies"]
    if not observations:
        lines.append("None.")
    else:
        lines.extend(["| Observation | Source | Count |", "|---|---|---:|"])
        for observation in observations:
            lines.append(
                "| {kind} | {source} | {count} |".format(
                    kind=_markdown_cell(observation["kind"]),
                    source=_markdown_cell(observation.get("source", "—")),
                    count=observation["count"],
                )
            )

    lines.extend(["", "## Outbox health", ""])
    lines.extend(["| Queue | Available | Open rows | Retrying rows | Max attempts | States |", "|---|---|---:|---:|---:|---|"])
    for name, summary in result["outbox"].items():
        if not summary["available"]:
            lines.append(f"| {name} | no | — | — | — | — |")
            continue
        lines.append(
            "| {name} | yes | {open_rows} | {retrying_rows} | {max_attempts} | `{states}` |".format(
                name=_markdown_cell(name),
                open_rows=summary["open_rows"],
                retrying_rows=summary["retrying_rows"],
                max_attempts=summary["max_attempts"],
                states=_markdown_cell(json.dumps(summary["status_counts"], sort_keys=True)),
            )
        )
    return "\n".join(lines) + "\n"


def write_operational_diagnostics_report(
    result: Mapping[str, Any], json_path: Path, markdown_path: Optional[Path] = None
) -> dict[str, str]:
    """Atomically write diagnostic artifacts without touching the SQLite ledger."""
    json_target = Path(json_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    json_temporary = json_target.with_name(f".{json_target.name}.tmp")
    try:
        json_temporary.write_text(
            json.dumps(dict(result), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        json_temporary.replace(json_target)
    finally:
        json_temporary.unlink(missing_ok=True)
    written = {"json": str(json_target)}
    if markdown_path is not None:
        markdown_target = Path(markdown_path)
        markdown_target.parent.mkdir(parents=True, exist_ok=True)
        markdown_temporary = markdown_target.with_name(f".{markdown_target.name}.tmp")
        try:
            markdown_temporary.write_text(
                render_operational_diagnostics_markdown(result), encoding="utf-8"
            )
            markdown_temporary.replace(markdown_target)
        finally:
            markdown_temporary.unlink(missing_ok=True)
        written["markdown"] = str(markdown_target)
    return written


def _positive_window_size(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("必须是正整数") from exc
    if parsed < 1 or parsed > 200:
        raise argparse.ArgumentTypeError("必须在 1 到 200 之间")
    return parsed



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only diagnostics for persisted daily-paper scores and delivery health."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    diagnose = subparsers.add_parser(
        "diagnose",
        help="read persisted score drift and delivery health without modifying the database",
    )
    diagnose.add_argument("--db", required=True, type=Path, help="daily_research SQLite database")
    diagnose.add_argument(
        "--recent-runs",
        type=_positive_window_size,
        default=14,
        help="number of newest runs to inspect (default: 14)",
    )
    diagnose.add_argument(
        "--baseline-runs",
        type=_positive_window_size,
        default=28,
        help="number of older runs used as the comparison baseline (default: 28)",
    )
    diagnose.add_argument("--json-output", required=True, type=Path, help="diagnostic JSON output")
    diagnose.add_argument(
        "--markdown-output", type=Path, default=None, help="optional diagnostic Markdown output"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint; return a nonzero code for malformed diagnostic input."""
    args = build_parser().parse_args(argv)
    try:
        result = build_operational_diagnostics(
            args.db,
            recent_runs=args.recent_runs,
            baseline_runs=args.baseline_runs,
        )
        summary = {
            **result,
            "written": write_operational_diagnostics_report(
                result, args.json_output, args.markdown_output
            ),
        }
    except ScoringEvaluationError as exc:
        print(f"scoring diagnostics failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
