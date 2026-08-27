import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui.tabs import search  # noqa: E402


class _FakeSessionState(dict):
    """Minimal Streamlit session-state stand-in for collection tests."""

    def get(self, key, default=None):
        return super().get(key, default)


class DataSourceCollectTests(unittest.TestCase):
    def _flat_config(self):
        return {
            "enabled_sources": ["arxiv", "prl"],
            "extra_sources_enabled": True,
            "extra_source_definitions": [],
            "domains": ["quant-ph"],
        }

    def test_turning_off_extra_group_stops_prl_too(self):
        session = _FakeSessionState(
            {
                "source_arxiv": True,
                "extra_builtin_selected": ["prl"],
                "extra_sources_enabled": False,
            }
        )

        with patch.object(search.st, "session_state", session):
            updates = search.collect({}, self._flat_config())

        self.assertEqual(updates["enabled_sources"], ["arxiv"])
        self.assertFalse(updates["extra_sources_enabled"])

    def test_enabled_extra_group_can_keep_prl_alongside_arxiv(self):
        session = _FakeSessionState(
            {
                "source_arxiv": True,
                "extra_builtin_selected": ["prl"],
                "extra_sources_enabled": True,
            }
        )

        with patch.object(search.st, "session_state", session):
            updates = search.collect({}, self._flat_config())

        self.assertEqual(updates["enabled_sources"], ["arxiv", "prl"])
        self.assertTrue(updates["extra_sources_enabled"])
        self.assertEqual(updates["extra_source_definitions"], [])

    def test_empty_extra_selection_is_saved_as_disabled(self):
        session = _FakeSessionState(
            {
                "source_arxiv": True,
                "extra_builtin_selected": [],
                "extra_sources_enabled": True,
            }
        )

        with patch.object(search.st, "session_state", session):
            updates = search.collect({}, self._flat_config())

        self.assertEqual(updates["enabled_sources"], ["arxiv"])
        self.assertFalse(updates["extra_sources_enabled"])


if __name__ == "__main__":
    unittest.main()
