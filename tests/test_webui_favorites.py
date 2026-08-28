import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui.tabs import favorites  # noqa: E402


class _Context:
    def __init__(self, parent=None):
        self.parent = parent

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def button(self, *_args, **_kwargs):
        return False

    def caption(self, *_args, **_kwargs):
        return None


class _FakeStreamlit:
    def __init__(self):
        self.containers = []
        self.tables = []
        self.markdowns = []
        self.session_state = {}

    def columns(self, count):
        return [_Context() for _ in range(count if isinstance(count, int) else len(count))]

    def markdown(self, value, **_kwargs):
        self.markdowns.append(value)

    def caption(self, *_args, **_kwargs):
        pass

    def container(self, **kwargs):
        self.containers.append(kwargs)
        return _Context()

    def table(self, value):
        self.tables.append(value)

    def selectbox(self, _label, options, index=0, **_kwargs):
        return options[index]


class _Store:
    def aggregate_liked_preferences(self):
        return {
            "authors": [
                {"name": f"Author {index}", "count": 12 - index}
                for index in range(11)
            ]
        }

    def aggregate_liked_keywords(self):
        return [
            {"keyword": f"keyword {index}", "count": 12 - index}
            for index in range(11)
        ]


class _FavoriteListStore:
    def liked_paper_urls(self):
        return {}


class FavoriteStatsTests(unittest.TestCase):
    def test_long_statistics_use_the_shared_five_ten_row_pager(self):
        fake_st = _FakeStreamlit()

        with (
            patch.object(favorites, "st", fake_st),
            patch.object(favorites, "t", side_effect=lambda key: key),
        ):
            favorites._render_preference_stats(_Store())

        self.assertEqual(fake_st.containers, [])
        self.assertEqual([len(table) for table in fake_st.tables], [5, 5])

    def test_long_favorite_list_uses_the_same_five_ten_row_pager(self):
        fake_st = _FakeStreamlit()
        liked = [
            {
                "source": "arxiv",
                "paper_id": str(index),
                "title": f"Paper {index}",
                "updated_at": "2026-08-24T12:00:00",
            }
            for index in range(11)
        ]

        with (
            patch.object(favorites, "st", fake_st),
            patch.object(favorites, "t", side_effect=lambda key: key),
        ):
            favorites._render_favorites_list(_FavoriteListStore(), liked)

        self.assertEqual(fake_st.containers, [{"border": True}])
        self.assertEqual(len(fake_st.markdowns), 5)


if __name__ == "__main__":
    unittest.main()
