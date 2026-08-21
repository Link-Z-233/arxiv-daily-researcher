import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui.tabs import scoring  # noqa: E402


class _FakeSessionState(dict):
    """Minimal session_state stand-in: `.get` over a plain dict."""

    def get(self, key, default=None):
        return super().get(key, default)


def _flat_config(**overrides):
    """Mirror what config_panel.load_config() hands to collect(): a flat dict."""
    flat = {
        "passing_score_base": 1.5,
        "passing_score_weight_coefficient": 2.5,
        "max_score_per_keyword": 7,
    }
    flat.update(overrides)
    return flat


class ScoringCollectTests(unittest.TestCase):
    """Saving an unrelated tab must not rewrite the tuned scoring formula.

    Formula widgets only render for the legacy strategy, so a V2 user has no
    ``passing_score_*`` session state.  ``collect`` must then fall back to the
    values on disk instead of the hardcoded defaults.
    """

    def test_missing_widget_state_keeps_configured_formula_values(self):
        with patch.object(scoring.st, "session_state", _FakeSessionState()):
            updates = scoring.collect({}, _flat_config())

        self.assertEqual(updates["passing_score_base"], 1.5)
        self.assertEqual(updates["passing_score_weight_coefficient"], 2.5)
        self.assertEqual(updates["max_score_per_keyword"], 7)

    def test_widget_state_still_wins_over_stale_config_values(self):
        session = _FakeSessionState(
            {
                "passing_score_base": 2.0,
                "passing_score_weight_coefficient": 4.0,
                "core_relevance_threshold": 6.5,
            }
        )
        with patch.object(scoring.st, "session_state", session):
            updates = scoring.collect({}, _flat_config())

        self.assertEqual(updates["passing_score_base"], 2.0)
        self.assertEqual(updates["passing_score_weight_coefficient"], 4.0)
        self.assertEqual(updates["core_relevance_threshold"], 6.5)

    def test_defaults_apply_only_when_config_has_no_value(self):
        with patch.object(scoring.st, "session_state", _FakeSessionState()):
            updates = scoring.collect({}, {})

        self.assertEqual(updates["passing_score_base"], 5.0)
        self.assertEqual(updates["passing_score_weight_coefficient"], 3.0)


if __name__ == "__main__":
    unittest.main()
