"""Regression coverage for the shared 5/10-row Streamlit pager."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui import pagination  # noqa: E402


class _Column:
    def __init__(self, parent):
        self.parent = parent

    def button(self, *args, **kwargs):
        self.parent.calls.append(("button", args, kwargs))
        return False

    def caption(self, *args, **kwargs):
        self.parent.calls.append(("caption", args, kwargs))


class _FakeStreamlit:
    def __init__(self):
        self.calls = []
        self.session_state = {}

    def selectbox(self, *args, **kwargs):
        self.calls.append(("selectbox", args, kwargs))
        return kwargs["options"][kwargs["index"]]

    def dataframe(self, *args, **kwargs):
        self.calls.append(("dataframe", args, kwargs))

    def table(self, *args, **kwargs):
        self.calls.append(("table", args, kwargs))

    def columns(self, count):
        return [_Column(self) for _ in range(count if isinstance(count, int) else len(count))]


class PaginationTests(unittest.TestCase):
    def test_page_window_clamps_invalid_requests(self):
        self.assertEqual(pagination.page_window(11, 9, 5), (10, 11, 2, 3))
        self.assertEqual(pagination.page_window(11, -3, 10), (0, 10, 0, 2))
        self.assertEqual(pagination.page_window(0, 0, 5), (0, 0, 0, 1))

    def test_dataframe_uses_saved_page_and_five_row_default(self):
        fake_st = _FakeStreamlit()
        fake_st.session_state["example_page"] = 1
        rows = [{"index": index} for index in range(11)]
        original = pagination.st
        original_t = pagination.t
        try:
            pagination.st = fake_st
            pagination.t = lambda key: key
            pagination.render_paginated_dataframe(rows, key="example", hide_index=True)
        finally:
            pagination.st = original
            pagination.t = original_t

        dataframes = [args[0] for name, args, _kwargs in fake_st.calls if name == "dataframe"]
        self.assertEqual(dataframes, [rows[5:10]])
        selectbox = next(call for call in fake_st.calls if call[0] == "selectbox")
        self.assertEqual(selectbox[2]["options"], [5, 10])
        buttons = [kwargs["key"] for name, _args, kwargs in fake_st.calls if name == "button"]
        self.assertEqual(buttons, ["example_previous", "example_next"])


if __name__ == "__main__":
    unittest.main()
