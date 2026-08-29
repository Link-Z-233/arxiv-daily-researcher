import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.keyword_agent import KeywordAgent  # noqa: E402
from config import settings  # noqa: E402
class ReferenceKeywordToggleTests(unittest.TestCase):
    def test_disabled_runtime_does_not_read_or_merge_cached_keywords(self):
        agent = KeywordAgent.__new__(KeywordAgent)
        with patch.object(settings, "ENABLE_REFERENCE_EXTRACTION", False), patch.object(
            type(settings), "get_merged_keywords", return_value={"primary": 1.0}
        ), patch.object(
            agent,
            "generate_weighted_keywords",
            side_effect=AssertionError("disabled extraction must not read the cache"),
        ):
            result = agent.get_all_keywords()

        self.assertEqual(result, {"primary": 1.0})


if __name__ == "__main__":
    unittest.main()
