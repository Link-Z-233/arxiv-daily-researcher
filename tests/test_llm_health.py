"""Regression coverage for passive, privacy-safe LLM health observability."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.analysis_agent import AnalysisAgent  # noqa: E402
from agents.trend_agent import _llm_call_with_retry  # noqa: E402
from config import settings  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402
from utils.llm_health import make_llm_health_recorder, safe_llm_error_summary  # noqa: E402
from webui.tabs import analytics  # noqa: E402


class _MetricColumn:
    def __init__(self, parent):
        self.parent = parent

    def metric(self, *args, **kwargs):
        self.parent.calls.append(("metric", args, kwargs))


class _ContextBox:
    def __init__(self, parent, kind):
        self.parent = parent
        self.kind = kind

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _FakeStreamlit:
    def __init__(self):
        self.calls = []

    def markdown(self, *args, **kwargs):
        self.calls.append(("markdown", args, kwargs))

    def caption(self, *args, **kwargs):
        self.calls.append(("caption", args, kwargs))

    def info(self, *args, **kwargs):
        self.calls.append(("info", args, kwargs))

    def warning(self, *args, **kwargs):
        self.calls.append(("warning", args, kwargs))

    def code(self, *args, **kwargs):
        self.calls.append(("code", args, kwargs))

    def dataframe(self, *args, **kwargs):
        self.calls.append(("dataframe", args, kwargs))

    def segmented_control(self, *args, **kwargs):
        self.calls.append(("segmented_control", args, kwargs))
        return kwargs.get("default")

    def container(self, *args, **kwargs):
        self.calls.append(("container", args, kwargs))
        return _ContextBox(self, "container")

    def expander(self, *args, **kwargs):
        self.calls.append(("expander", args, kwargs))
        return _ContextBox(self, "expander")

    def columns(self, count):
        return [_MetricColumn(self) for _ in range(count)]


class LLMHealthTests(unittest.TestCase):
    def test_summary_tracks_final_outcomes_and_redacts_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            recorder = make_llm_health_recorder(store)
            recorder("cheap", "fast-model", True, None)
            recorder("cheap", "fast-model", False, RuntimeError("DNS lookup failed"))
            recorder(
                "cheap",
                "fast-model",
                False,
                RuntimeError(
                    "gateway refused api_key=sk-super-secret "
                    "Authorization: Bearer private-token "
                    "https://alice:password@example.test/v1?token=query-secret"
                ),
            )
            recorder("smart", "deep-model", True, None)

            summaries = store.get_llm_health(window=20)

        cheap = summaries["cheap"]
        self.assertEqual(cheap["last_status"], "failed")
        self.assertEqual(cheap["consecutive_failures"], 2)
        self.assertEqual(cheap["events_in_window"], 3)
        self.assertAlmostEqual(cheap["success_rate"], 1 / 3)
        self.assertIsNotNone(cheap["last_success_at"])
        rendered = str(cheap["last_error"])
        self.assertNotIn("super-secret", rendered)
        self.assertNotIn("private-token", rendered)
        self.assertNotIn("query-secret", rendered)
        self.assertNotIn("alice:password", rendered)
        self.assertEqual(summaries["smart"]["last_status"], "succeeded")

    def test_redaction_keeps_useful_network_context(self):
        error = RuntimeError("relay error: token=sk-not-for-display")
        error.__cause__ = ConnectionError("[Errno -3] Temporary failure in name resolution")
        detail = safe_llm_error_summary(error)
        self.assertIsNotNone(detail)
        self.assertIn("name resolution", detail)
        self.assertNotIn("not-for-display", detail)

    def test_analysis_agent_records_one_final_success_or_failure(self):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent.cheap_client = object()
        events = []
        agent._health_recorder = lambda *args: events.append(args)

        with patch.object(settings, "LLM_RETRY_MAX_ATTEMPTS", 1), patch.object(
            settings, "TOKEN_TRACKING_ENABLED", False
        ), patch.object(
            agent,
            "_call_llm_with_fallback",
            return_value=("{\"ok\": true}", None),
        ):
            self.assertEqual(agent._call_cheap_llm("prompt"), '{"ok": true}')

        with patch.object(settings, "LLM_RETRY_MAX_ATTEMPTS", 1), patch.object(
            settings, "TOKEN_TRACKING_ENABLED", False
        ), patch.object(
            agent,
            "_call_llm_with_fallback",
            side_effect=RuntimeError("final provider failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "final provider failure"):
                agent._call_cheap_llm("prompt")

        self.assertEqual([(event[0], event[2]) for event in events], [("cheap", True), ("cheap", False)])
        self.assertIn("final provider failure", str(events[-1][3]))

    def test_trend_wrapper_records_only_after_retry_boundary(self):
        events = []
        with patch("agents.trend_agent._llm_call_once", return_value="done"):
            self.assertEqual(
                _llm_call_with_retry(
                    object(), "model", 0.2, "prompt", role="smart", health_recorder=lambda *args: events.append(args)
                ),
                "done",
            )
        with patch(
            "agents.trend_agent._llm_call_once", side_effect=RuntimeError("final timeout")
        ):
            with self.assertRaisesRegex(RuntimeError, "final timeout"):
                _llm_call_with_retry(
                    object(), "model", 0.2, "prompt", role="smart", health_recorder=lambda *args: events.append(args)
                )
        self.assertEqual([(event[0], event[2]) for event in events], [("smart", True), ("smart", False)])

    def test_analytics_renders_model_table_and_direct_redacted_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "daily.db"
            store = DailyResearchStore(db_path)
            recorder = make_llm_health_recorder(store)
            recorder("cheap", "fast-model", False, RuntimeError("provider unavailable"))
            recorder("smart", "deep-model", True, None)
            fake_st = _FakeStreamlit()
            with patch.object(analytics, "st", fake_st), patch.object(
                analytics, "t", side_effect=lambda key: key
            ), patch.object(analytics, "_daily_db_path_from_config", return_value=db_path):
                analytics._render_llm_health_section({})

        rendered = repr(fake_st.calls)
        self.assertIn("fast-model", rendered)
        self.assertIn("provider unavailable", rendered)
        self.assertTrue(any(name == "dataframe" for name, _args, _kwargs in fake_st.calls))
        self.assertFalse(any(name == "expander" for name, _args, _kwargs in fake_st.calls))
        self.assertFalse(any(name == "code" for name, _args, _kwargs in fake_st.calls))


if __name__ == "__main__":
    unittest.main()
