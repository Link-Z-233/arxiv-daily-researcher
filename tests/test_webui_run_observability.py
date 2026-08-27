"""Regression coverage for the read-only, non-secret Run Manager diagnostics."""

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.daily_research_store import DailyResearchStore  # noqa: E402
from utils.scoring_evaluation import (  # noqa: E402
    SCAN_OBSERVABILITY_SCHEMA,
    build_recent_scan_receipt_summaries,
)
from webui.tabs import analytics, run_manager  # noqa: E402


class _FakeColumn:
    def __init__(self, parent):
        self.parent = parent

    def metric(self, *args, **kwargs):
        self.parent.calls.append(("metric", args, kwargs))

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return False


class _FakeExpander:
    def __init__(self, parent, label):
        self.parent = parent
        self.label = label

    def __enter__(self):
        self.parent.calls.append(("expander", (self.label,), {}))
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return False


class _FakeStreamlit:
    """Small rendering spy; deliberately no ``code`` method for raw receipts."""

    def __init__(self):
        self.calls = []

    def container(self, *args, **kwargs):
        self.calls.append(("container", args, kwargs))

        class _Box:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Box()

    def markdown(self, *args, **kwargs):
        self.calls.append(("markdown", args, kwargs))

    def caption(self, *args, **kwargs):
        self.calls.append(("caption", args, kwargs))

    def info(self, *args, **kwargs):
        self.calls.append(("info", args, kwargs))

    def warning(self, *args, **kwargs):
        self.calls.append(("warning", args, kwargs))

    def button(self, *args, **kwargs):
        self.calls.append(("button", args, kwargs))
        return False

    def toggle(self, *args, **kwargs):
        # 测试走静态渲染路径（不经过 fragment 装饰器）。
        self.calls.append(("toggle", args, kwargs))
        return False

    class _SessionState(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    @property
    def session_state(self):
        return self._SessionState()

    def dataframe(self, *args, **kwargs):
        self.calls.append(("dataframe", args, kwargs))

    def columns(self, count):
        column_count = count if isinstance(count, int) else len(count)
        return [_FakeColumn(self) for _ in range(column_count)]

    def expander(self, label, **_kwargs):
        return _FakeExpander(self, label)


def _seed_scan_receipts(db_path: Path) -> None:
    store = DailyResearchStore(db_path)
    run_id = store.start_run(2)
    store.prepare_scan(
        run_id,
        2,
        ["arxiv", "huggingface_papers"],
        now=datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc),
    )
    store.record_scan_receipt(
        run_id,
        "arxiv",
        {
            "source": "arxiv",
            "status": "succeeded",
            "scanned_at": "2026-08-20T08:00:00+00:00",
            "requested_scan_days": 2,
            "announcement_lookback_grace_days": 2,
            "effective_days": 4,
            "window_start": "2026-08-16T08:00:00+00:00",
            "window_end": "2026-08-20T08:00:00+00:00",
            "total_new_candidates": 3,
            "error": "supersecret-run-error https://upstream.example/?token=leak",
            "domain_receipts": [
                {
                    "domain": "quant-ph",
                    "status": "succeeded",
                    "new_candidates": 3,
                    "error": "supersecret-domain-error",
                    "queries": {
                        "submitted": {
                            "api_entries_checked": 5,
                            "window_entries": 3,
                            "pages_observed": 1,
                            "attempts": 1,
                            "error": "supersecret-query-error",
                            "query": "cat:quant-ph AND https://leaky.example/",
                        },
                        "updated": {
                            "api_entries_checked": 2,
                            "window_entries": 1,
                            "pages_observed": 1,
                            "attempts": 2,
                        },
                    },
                }
            ],
        },
    )
    store.record_scan_receipt(
        run_id,
        "huggingface_papers",
        {
            "source": "huggingface_papers",
            "status": "succeeded",
            "scanned_at": "2026-08-20T08:01:00+00:00",
            "total_new_candidates": 2,
            "error": "supersecret-hf-error",
            "domain_receipts": [],
        },
    )
    store.complete_run(run_id)


class WebUiRunObservabilityTests(unittest.TestCase):
    def test_readonly_summary_covers_all_sources_and_drops_sensitive_receipt_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "daily.db"
            _seed_scan_receipts(db_path)
            modified_before = db_path.stat().st_mtime_ns

            snapshot = build_recent_scan_receipt_summaries(db_path, limit=10)

            modified_after = db_path.stat().st_mtime_ns

        self.assertEqual(snapshot["schema"], SCAN_OBSERVABILITY_SCHEMA)
        self.assertTrue(snapshot["read_only"])
        self.assertTrue(snapshot["receipt_table_available"])
        self.assertEqual(modified_before, modified_after)
        run = snapshot["runs"][0]
        self.assertEqual(run["planned_source_count"], 2)
        self.assertEqual(run["scan_plan_state"], "available")
        receipts = {receipt["source"]: receipt for receipt in run["receipts"]}
        self.assertEqual(set(receipts), {"arxiv", "huggingface_papers"})
        self.assertEqual(receipts["arxiv"]["candidate_count"], 3)
        self.assertEqual(
            receipts["arxiv"]["domain_receipts"][0]["queries"]["updated"]["attempts"],
            2,
        )
        self.assertEqual(receipts["huggingface_papers"]["candidate_count"], 2)
        rendered = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("supersecret", rendered)
        self.assertNotIn("https://", rendered)
        self.assertNotIn("cat:quant-ph", rendered)

    def test_corrupt_receipts_stay_visible_without_reflecting_their_contents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "daily.db"
            _seed_scan_receipts(db_path)
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "UPDATE daily_scan_receipts SET receipt_json = ? WHERE source = 'arxiv'",
                    ("{not-json-supersecret:https://leaky.example}",),
                )

            snapshot = build_recent_scan_receipt_summaries(db_path)

        receipts = snapshot["runs"][0]["receipts"]
        arxiv = next(receipt for receipt in receipts if receipt["source"] == "arxiv")
        self.assertEqual(arxiv["status"], "corrupt")
        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("supersecret", serialized)
        self.assertNotIn("https://", serialized)

    def test_planned_source_without_a_receipt_is_explicitly_visible_as_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "daily.db"
            store = DailyResearchStore(db_path)
            run_id = store.start_run(1)
            store.prepare_scan(run_id, 1, ["arxiv", "openalex_prl"])
            store.record_scan_receipt(
                run_id,
                "arxiv",
                {
                    "source": "arxiv",
                    "status": "succeeded",
                    "scanned_at": "2026-08-20T08:00:00+00:00",
                    "total_new_candidates": 1,
                    "domain_receipts": [],
                },
            )

            snapshot = build_recent_scan_receipt_summaries(db_path)

        receipts = {receipt["source"]: receipt for receipt in snapshot["runs"][0]["receipts"]}
        self.assertEqual(receipts["arxiv"]["status"], "succeeded")
        self.assertEqual(receipts["openalex_prl"]["status"], "missing")
        self.assertIsNone(receipts["openalex_prl"]["candidate_count"])

    def test_old_database_is_read_without_creating_the_new_receipt_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "legacy.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "CREATE TABLE daily_runs (run_id TEXT PRIMARY KEY, started_at TEXT, status TEXT)"
                )
                conn.execute(
                    "INSERT INTO daily_runs VALUES ('legacy-run', '2026-08-20T08:00:00', 'completed')"
                )
            modified_before = db_path.stat().st_mtime_ns

            snapshot = build_recent_scan_receipt_summaries(db_path)

            modified_after = db_path.stat().st_mtime_ns
            with sqlite3.connect(db_path) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }

        self.assertFalse(snapshot["receipt_table_available"])
        self.assertEqual(len(snapshot["runs"]), 1)
        self.assertNotIn("daily_scan_receipts", tables)
        self.assertEqual(modified_before, modified_after)

    def test_receipt_rendering_never_receives_raw_upstream_errors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "daily.db"
            _seed_scan_receipts(db_path)
            fake_st = _FakeStreamlit()
            with (
                patch.object(analytics, "st", fake_st),
                patch.object(analytics, "t", side_effect=lambda key: key),
                patch.object(analytics, "_daily_db_path_from_config", return_value=db_path),
            ):
                analytics._render_diagnostics_section({})

        rendered = repr(fake_st.calls)
        self.assertNotIn("supersecret", rendered)
        self.assertNotIn("https://", rendered)
        self.assertNotIn("cat:quant-ph", rendered)
        self.assertTrue(any(kind == "dataframe" for kind, _args, _kwargs in fake_st.calls))

    def test_health_rendering_uses_the_safe_aggregate_diagnostic_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "daily.db"
            _seed_scan_receipts(db_path)
            fake_st = _FakeStreamlit()
            with (
                patch.object(analytics, "st", fake_st),
                patch.object(analytics, "t", side_effect=lambda key: key),
                patch.object(analytics, "_daily_db_path_from_config", return_value=db_path),
            ):
                analytics._render_diagnostics_section({})

        rendered = repr(fake_st.calls)
        self.assertNotIn("supersecret", rendered)
        self.assertNotIn("https://", rendered)
        self.assertNotIn("cat:quant-ph", rendered)
        self.assertTrue(any(kind == "metric" for kind, _args, _kwargs in fake_st.calls))

    def test_trigger_failure_status_does_not_echo_worker_exception_text(self):
        fake_st = _FakeStreamlit()
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(run_manager, "st", fake_st),
                patch.object(
                    run_manager,
                    "t",
                    side_effect=lambda key: (
                        "问题摘要：{summary}"
                        if key == "rm_trigger_error_summary"
                        else key
                    ),
                ),
                patch.object(run_manager, "_IS_DOCKER_WEBUI", True),
                patch.object(run_manager, "_trigger_age_seconds", return_value=None),
                patch.object(run_manager, "_get_all_running_locks", return_value=[]),
                # 指向不存在的临时 DB，隔离宿主机 data/ 下的真实队列
                patch.object(
                    run_manager,
                    "_daily_db_path_from_config",
                    return_value=Path(temp_dir) / "absent.db",
                ),
                patch.object(
                    run_manager,
                    "_latest_trigger_status",
                    return_value={
                        "state": "failed",
                        "return_code": 1,
                        "error": "supersecret-worker-error https://leaky.example/?token=1",
                        "error_summary": "评分阶段失败：api_key=supersecret https://leaky.example/?token=1",
                    },
                ),
            ):
                # 状态渲染已并入状态面板；fragment 在裸模式下退化为直接调用
                with patch.object(
                    run_manager,
                    "_live_status_fragment",
                    run_manager._render_live_status_body,
                ):
                    run_manager._render_status_panel({})

        rendered = repr(fake_st.calls)
        self.assertIn("failed", rendered)
        self.assertIn("评分阶段失败", rendered)
        self.assertNotIn("supersecret", rendered)
        self.assertNotIn("https://", rendered)


if __name__ == "__main__":
    unittest.main()
