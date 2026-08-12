import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.analysis_agent import WeightedScoreResponse  # noqa: E402
from config import settings  # noqa: E402
from modes.daily_research import _score_or_hydrate_paper  # noqa: E402
from sources.base_source import PaperMetadata  # noqa: E402
from utils.daily_research_errors import PaperStageError  # noqa: E402
from utils.daily_research_fingerprints import build_stage_input_fingerprints  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402


def _paper():
    return PaperMetadata(
        paper_id="2501.12345v1",
        title="A paper",
        authors=["Alice"],
        abstract="An abstract",
        published_date=datetime.now(timezone.utc),
        url="https://arxiv.org/abs/2501.12345v1",
        source="arxiv",
    )


def _score():
    return WeightedScoreResponse(
        total_score=4,
        keyword_scores={"quantum": 4},
        author_bonus=0,
        expert_authors_found=[],
        passing_score=3,
        is_qualified=True,
        reasoning="relevant",
        tldr="A concise TLDR",
        extracted_keywords=["quantum"],
    )


class _Agent:
    deep_template = {"modules": []}

    def __init__(self, score_result=None, translation_result="中文摘要"):
        self.score_result = score_result or _score()
        self.translation_result = translation_result
        self.score_calls = 0
        self.translation_calls = 0

    def score_paper_with_keywords(self, **_kwargs):
        self.score_calls += 1
        if isinstance(self.score_result, BaseException):
            raise self.score_result
        return self.score_result

    def translate_abstract(self, _abstract):
        self.translation_calls += 1
        if isinstance(self.translation_result, BaseException):
            raise self.translation_result
        return self.translation_result


class DailyResearchStateTests(unittest.TestCase):
    def _run_score_or_hydrate(self, store, run_id, paper, agent, keywords):
        return _score_or_hydrate_paper(
            run_id,
            "arxiv",
            paper,
            agent,
            keywords,
            {},
            __import__("threading").Lock(),
            None,
            store,
        )

    def test_translation_failure_is_typed_and_does_not_mark_score_failed(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            settings, "DAILY_RESEARCH_PERSISTENCE_ENABLED", True
        ):
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            paper = _paper()
            agent = _Agent(translation_result=RuntimeError("backend is down"))
            run_id = store.start_run(1)

            with self.assertRaises(PaperStageError) as raised:
                self._run_score_or_hydrate(store, run_id, paper, agent, {"quantum": 1.0})
            self.assertEqual(raised.exception.stage, "translation")
            store.update_error(run_id, "arxiv", paper.paper_id, str(raised.exception), raised.exception.stage)

            record = store.get_paper_record("arxiv", paper.paper_id)
            self.assertEqual(record["score_status"], "succeeded")
            self.assertEqual(record["translation_status"], "failed")
            self.assertIsNotNone(record["score_json"])

    def test_score_fingerprint_change_rescores_an_incomplete_paper(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            paper = _paper()
            first_agent = _Agent()
            first_run = store.start_run(1)
            self._run_score_or_hydrate(store, first_run, paper, first_agent, {"quantum": 1.0})
            store.update_error(first_run, "arxiv", paper.paper_id, "analysis later", "analysis")

            changed_agent = _Agent()
            retry_run = store.start_run(1)
            self._run_score_or_hydrate(
                store, retry_run, paper, changed_agent, {"quantum": 0.5, "sensing": 1.0}
            )

            self.assertEqual(changed_agent.score_calls, 1)
            self.assertEqual(changed_agent.translation_calls, 1)
            record = store.get_paper_record("arxiv", paper.paper_id)
            self.assertEqual(record["score_status"], "succeeded")
            self.assertEqual(record["translation_status"], "succeeded")

    def test_stable_inputs_reuse_persisted_score_and_translation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            paper = _paper()
            first_agent = _Agent()
            first_run = store.start_run(1)
            self._run_score_or_hydrate(store, first_run, paper, first_agent, {"quantum": 1.0})
            store.update_error(first_run, "arxiv", paper.paper_id, "analysis later", "analysis")

            retry_agent = _Agent()
            retry_run = store.start_run(1)
            result = self._run_score_or_hydrate(store, retry_run, paper, retry_agent, {"quantum": 1.0})

            self.assertEqual(retry_agent.score_calls, 0)
            self.assertEqual(retry_agent.translation_calls, 0)
            self.assertEqual(result["abstract_cn"], "中文摘要")

    def test_new_scores_persist_non_secret_audit_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            paper = _paper()
            run_id = store.start_run(1)

            self._run_score_or_hydrate(store, run_id, paper, _Agent(), {"quantum": 1.0})
            record = store.get_paper_record("arxiv", paper.paper_id)
            audit = json.loads(record["score_audit_json"])

        self.assertEqual(audit["strategy_id"], settings.normalized_score_strategy())
        self.assertIn("policy_fingerprint", audit)
        serialized = json.dumps(audit).lower()
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("base_url", serialized)
        self.assertNotIn("research_context\"", serialized)

    def test_primary_keyword_membership_invalidates_unfinished_score_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            settings, "SCORE_STRATEGY", "core_relevance_v2"
        ), patch.object(settings, "PRIMARY_KEYWORDS", ["quantum"]):
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            paper = _paper()
            first_agent = _Agent()
            first_run = store.start_run(1)
            self._run_score_or_hydrate(store, first_run, paper, first_agent, {"quantum": 1.0})
            store.update_error(first_run, "arxiv", paper.paper_id, "analysis later", "analysis")

            with patch.object(settings, "PRIMARY_KEYWORDS", ["sensing"]):
                retry_agent = _Agent()
                retry_run = store.start_run(1)
                self._run_score_or_hydrate(
                    store, retry_run, paper, retry_agent, {"quantum": 1.0}
                )

        self.assertEqual(retry_agent.score_calls, 1)

    def test_v2_fingerprint_changes_when_primary_membership_changes(self):
        paper = _paper()
        with patch.object(settings, "SCORE_STRATEGY", "core_relevance_v2"), patch.object(
            settings, "PRIMARY_KEYWORDS", ["quantum"]
        ):
            first = build_stage_input_fingerprints(paper, {"quantum": 1.0}, {})["score"]
        with patch.object(settings, "SCORE_STRATEGY", "core_relevance_v2"), patch.object(
            settings, "PRIMARY_KEYWORDS", ["other"]
        ):
            second = build_stage_input_fingerprints(paper, {"quantum": 1.0}, {})["score"]
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
