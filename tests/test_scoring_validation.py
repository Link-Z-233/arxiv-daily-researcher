import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.analysis_agent import AnalysisAgent, ScoreValidationError  # noqa: E402
from config import settings  # noqa: E402
from report.daily.modules.base_module import FormatHelper  # noqa: E402
from report.daily.modules.renderers import ScoringRenderer  # noqa: E402


def _score_payload(**overrides):
    payload = {
        "keyword_scores": {"quantum sensing": 8, "noise": 2.5},
        "reasoning": "The paper directly studies quantum sensing under noise.",
        "tldr": "It improves a quantum sensing protocol under realistic noise.",
        "extracted_keywords": ["quantum sensing", "noise", "metrology"],
    }
    payload.update(overrides)
    return json.dumps(payload)


class ScoringValidationTests(unittest.TestCase):
    def _agent_with_response(self, payload):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent._call_cheap_llm = lambda _prompt: payload
        return agent

    def test_rejects_missing_extra_and_out_of_range_keyword_scores(self):
        keywords = {"quantum sensing": 1.0, "noise": 0.5}

        cases = [
            _score_payload(keyword_scores={"quantum sensing": 8}),
            _score_payload(keyword_scores={"quantum sensing": 8, "noise": 2, "fake": 10}),
            _score_payload(keyword_scores={"quantum sensing": 11, "noise": 2}),
            _score_payload(keyword_scores={"quantum sensing": float("nan"), "noise": 2}),
        ]
        for payload in cases:
            with self.subTest(payload=payload), patch.object(settings, "MAX_SCORE_PER_KEYWORD", 10):
                with self.assertRaisesRegex(RuntimeError, "论文评分失败"):
                    self._agent_with_response(payload).score_paper_with_keywords(
                        "title", ["Alice"], "abstract", keywords
                    )

    def test_rejects_missing_tldr_instead_of_persisting_a_placeholder(self):
        payload = _score_payload(tldr="")
        with self.assertRaisesRegex(RuntimeError, "tldr 必须是非空字符串"):
            self._agent_with_response(payload).score_paper_with_keywords(
                "title", ["Alice"], "abstract", {"quantum sensing": 1, "noise": 1}
            )

    def test_expert_bonus_uses_only_real_configured_authors_once(self):
        payload = _score_payload(
            expert_authors_found=["Imaginary Person", "Alice Smith", "Alice Smith"]
        )
        with patch.object(settings, "MAX_SCORE_PER_KEYWORD", 10), patch.object(
            settings, "ENABLE_AUTHOR_BONUS", True
        ), patch.object(settings, "EXPERT_AUTHORS", ["alice-smith", "Bob Jones"]), patch.object(
            settings, "AUTHOR_BONUS_POINTS", 3.0
        ):
            result = self._agent_with_response(payload).score_paper_with_keywords(
                "title",
                ["Alice Smith", "Alice Smith", "Unrelated Author"],
                "abstract",
                {"quantum sensing": 1.0, "noise": 0.5},
            )

        self.assertEqual(result.expert_authors_found, ["Alice Smith"])
        self.assertEqual(result.author_bonus, 3.0)
        self.assertEqual(result.total_score, 12.25)

    def test_invalid_score_configuration_fails_before_an_llm_call(self):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent._call_cheap_llm = lambda _prompt: self.fail("LLM must not be called")
        with patch.object(settings, "MAX_SCORE_PER_KEYWORD", 0):
            with self.assertRaises(ScoreValidationError):
                agent.score_paper_with_keywords("title", ["Alice"], "abstract", {"kw": 1})

    def test_scoring_renderer_uses_configured_maximum_not_hardcoded_ten(self):
        renderer = ScoringRenderer(FormatHelper("mkdocs"))
        response = type(
            "Score", (),
            {
                "total_score": 4.0,
                "passing_score": 3.0,
                "is_qualified": True,
                "keyword_scores": {"kw": 4.0},
                "author_bonus": 0.0,
                "expert_authors_found": [],
                "reasoning": "test",
            },
        )()
        with patch.object(settings, "MAX_SCORE_PER_KEYWORD", 5):
            lines = renderer.render(
                {"score_response": response, "keywords_dict": {"kw": 1.0}},
                {"format": "list", "show_details": True, "show_reasoning": False},
            )
        self.assertIn("4.0/5", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
