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

    def container(self, **kwargs):
        self.containers.append(kwargs)
        return nullcontext()

    def table(self, value):
        self.tables.append(value)

    def dataframe(self, value, **kwargs):
        self.dataframes.append((value, kwargs))

    def markdown(self, value, **_kwargs):
        self.markdowns.append(value)

    def caption(self, *_args, **_kwargs):
        pass


class LongListViewportTests(unittest.TestCase):
    def test_backup_history_keeps_every_row_inside_a_native_scroll_container(self):
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

        self.assertEqual(
            fake_st.containers,
            [{"height": data_management._TABLE_SCROLL_HEIGHT_PX, "border": True}],
        )
        self.assertEqual(len(fake_st.tables[0]), 11)

    def test_learned_preference_terms_do_not_drop_the_eleventh_row(self):
        fake_st = _FakeStreamlit()
        terms = [
            {"term": f"term {index}", "weight": 1.0}
            for index in range(11)
        ]
        with patch.object(scoring, "st", fake_st):
            scoring._render_learned_term_rows(terms)

        self.assertEqual(
            fake_st.containers,
            [{"height": scoring._LIBRARY_SCROLL_HEIGHT_PX, "border": True}],
        )
        self.assertEqual(len(fake_st.markdowns), 11)

    def test_analytics_tables_use_the_same_ten_row_viewport_policy(self):
        fake_st = _FakeStreamlit()
        rows = [{"source": f"source-{index}"} for index in range(11)]
        with patch.object(analytics, "st", fake_st):
            analytics._render_bounded_table(pd.DataFrame(rows))
            analytics._render_bounded_dataframe(rows)

        self.assertEqual(
            fake_st.containers,
            [
                {"height": analytics._TABLE_SCROLL_HEIGHT_PX, "border": True},
                {"height": analytics._TABLE_SCROLL_HEIGHT_PX, "border": True},
            ],
        )
        self.assertEqual(len(fake_st.tables[0]), 11)
        self.assertEqual(len(fake_st.dataframes[0][0]), 11)


if __name__ == "__main__":
    unittest.main()
