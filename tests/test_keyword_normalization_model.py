"""Keyword normalization uses the model role selected in config.json."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from keyword_tracker.normalizer import KeywordNormalizer  # noqa: E402


class KeywordNormalizationModelTests(unittest.TestCase):
    def test_smart_role_uses_the_high_capability_llm_and_records_it(self):
        fake_settings = SimpleNamespace(
            KEYWORD_NORMALIZATION_LLM_ROLE="smart",
            CHEAP_LLM=SimpleNamespace(
                api_key="cheap-key", base_url="https://cheap.example/v1", model_name="cheap-model"
            ),
            SMART_LLM=SimpleNamespace(
                api_key="smart-key", base_url="https://smart.example/v1", model_name="smart-model"
            ),
        )
        events = []
        with patch("config.settings", fake_settings), patch(
            "utils.llm_resilience.build_llm_client", return_value=object()
        ) as build_client:
            normalizer = KeywordNormalizer(
                health_recorder=lambda role, model, success, error: events.append(
                    (role, model, success, error)
                )
            )

        build_client.assert_called_once_with("smart-key", "https://smart.example/v1")
        normalizer._record_llm_health(True)
        self.assertEqual(events, [("smart", "smart-model", True, None)])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
