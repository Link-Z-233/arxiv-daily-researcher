import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui.tabs import favorites  # noqa: E402


class _Context:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FakeStreamlit:
    def __init__(self):
        self.containers = []
        self.tables = []

    def columns(self, count):
        return [_Context() for _ in range(count)]

    def markdown(self, *_args, **_kwargs):
        pass

    def caption(self, *_args, **_kwargs):
        pass

    def container(self, **kwargs):
        self.containers.append(kwargs)
        return _Context()

    def table(self, value):
        self.tables.append(value)


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


class FavoriteStatsTests(unittest.TestCase):
    def test_long_statistics_keep_all_rows_in_native_scroll_containers(self):
        fake_st = _FakeStreamlit()

        with patch.object(favorites, "st", fake_st):
            favorites._render_preference_stats(_Store())

        self.assertEqual(
            fake_st.containers,
            [
                {"height": favorites._TABLE_SCROLL_HEIGHT_PX, "border": True},
                {"height": favorites._TABLE_SCROLL_HEIGHT_PX, "border": True},
            ],
        )
        self.assertEqual([len(table) for table in fake_st.tables], [11, 11])


if __name__ == "__main__":
    unittest.main()
