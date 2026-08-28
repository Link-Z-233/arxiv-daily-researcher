import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui.tabs import analytics, data_management, scoring  # noqa: E402


class _FakeStreamlit:
    def __init__(self):
        self.containers = []
        self.tables = []
        self.dataframes = []
        self.markdowns = []
        self.session_state = {}

    def container(self, **kwargs):
        self.containers.append(kwargs)
        return nullcontext()

    def table(self, value):
        self.tables.append(value)

    def dataframe(self, value, **kwargs):
        self.dataframes.append((value, kwargs))

    def selectbox(self, _label, options, index=0, **_kwargs):
        return options[index]

    def columns(self, spec):
        count = spec if isinstance(spec, int) else len(spec)
        return [_FakeColumn() for _ in range(count)]

    def markdown(self, value, **_kwargs):
        self.markdowns.append(value)

    def caption(self, *_args, **_kwargs):
        pass


class _FakeColumn:
    def button(self, *_args, **_kwargs):
        return False

    def caption(self, *_args, **_kwargs):
        pass


class LongListViewportTests(unittest.TestCase):
    def test_backup_history_uses_a_five_row_first_page_without_scroll_container(self):
        fake_st = _FakeStreamlit()
        backups = [
            {
                "name": f"daily_research_{index:02d}.db.gz",
                "size_bytes": 1024,
                "modified_at": "2026-08-24T12:00:00",
            }
            for index in range(11)
        ]
        with (
            patch.object(data_management, "st", fake_st),
            patch.object(data_management, "t", side_effect=lambda key: key),
        ):
            data_management._render_backup_list(backups)

        self.assertEqual(fake_st.containers, [])
        self.assertEqual(len(fake_st.tables[0]), 5)

    def test_learned_preference_terms_use_the_shared_first_page_policy(self):
        fake_st = _FakeStreamlit()
        terms = [
            {"term": f"term {index}", "weight": 1.0}
            for index in range(11)
        ]
        with (
            patch.object(scoring, "st", fake_st),
            patch.object(scoring, "t", side_effect=lambda key: key),
        ):
            scoring._render_learned_term_rows(terms, key="test_terms")

        self.assertEqual(fake_st.containers, [])
        self.assertEqual(len(fake_st.dataframes[0][0]), 5)

    def test_analytics_tables_use_five_ten_row_paging_instead_of_scroll_frames(self):
        fake_st = _FakeStreamlit()
        rows = [{"source": f"source-{index}"} for index in range(11)]
        with (
            patch.object(analytics, "st", fake_st),
            patch.object(analytics, "t", side_effect=lambda key: key),
        ):
            analytics._render_bounded_table(pd.DataFrame(rows))
            analytics._render_bounded_dataframe(rows)

        self.assertEqual(fake_st.containers, [])
        self.assertEqual(len(fake_st.tables[0]), 5)
        self.assertEqual(len(fake_st.dataframes[0][0]), 5)


if __name__ == "__main__":
    unittest.main()
