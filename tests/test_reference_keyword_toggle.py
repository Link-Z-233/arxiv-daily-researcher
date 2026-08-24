import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.keyword_agent import KeywordAgent  # noqa: E402
from config import settings  # noqa: E402
from webui.tabs import keywords as keywords_tab  # noqa: E402


class _FakeStreamlit:
    def __init__(self, session_state=None):
        self.session_state = session_state or {}
        self.containers = []
        self.markdown_calls = []

    def markdown(self, *args, **kwargs):
        self.markdown_calls.append((args, kwargs))

    def text_area(self, *args, **kwargs):
        return kwargs.get("value", "")

    def divider(self):
        return None

    def slider(self, *args, **kwargs):
        return kwargs.get("value")

    def toggle(self, *args, **kwargs):
        key = kwargs["key"]
        self.session_state.setdefault(key, kwargs["value"])
        return self.session_state[key]

    def info(self, *args, **kwargs):
        return None

    def caption(self, *args, **kwargs):
        return None

    def container(self, **kwargs):
        self.containers.append(kwargs)
        return nullcontext()


class ReferenceKeywordToggleTests(unittest.TestCase):
    def test_disabled_ui_hides_the_cached_keyword_box(self):
        fake_st = _FakeStreamlit({"enable_reference_extraction": False})
        with patch.object(keywords_tab, "st", fake_st), patch.object(
            keywords_tab, "_render_extracted_keywords_box"
        ) as rendered_box:
            keywords_tab.render(
                {},
                {
                    "enable_reference_extraction": True,
                    "primary_keywords": [],
                },
            )

        rendered_box.assert_not_called()

    def test_long_keyword_list_uses_a_bounded_native_scroll_container(self):
        fake_st = _FakeStreamlit()
        extracted = {f"keyword {index}": 1.0 for index in range(30)}
        with patch.object(keywords_tab, "st", fake_st), patch.object(
            keywords_tab, "_load_extracted_keywords", return_value=extracted
        ):
            keywords_tab._render_extracted_keywords_box()

        self.assertEqual(fake_st.containers, [{"height": 320, "border": True}])
        rendered_html = fake_st.markdown_calls[-1][0][0]
        self.assertNotIn("overflow-y", rendered_html)
        self.assertIn("keyword 0", rendered_html)

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
