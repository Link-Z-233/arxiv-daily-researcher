import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui.navigation import NAVIGATION_GROUPS, NAVIGATION_GROUP_IDS  # noqa: E402


class WebUINavigationTests(unittest.TestCase):
    def test_sidebar_has_only_the_four_workflow_groups(self):
        self.assertEqual(
            [group_id for group_id, _label_key, _items in NAVIGATION_GROUPS],
            ["run", "content", "configuration", "system"],
        )
        self.assertEqual(
            NAVIGATION_GROUP_IDS,
            frozenset({"run", "content", "configuration", "system"}),
        )

    def test_each_page_is_a_top_tab_of_exactly_one_group(self):
        pages = [
            page_id
            for _group_id, _label_key, items in NAVIGATION_GROUPS
            for page_id, _page_label_key in items
        ]

        self.assertEqual(len(pages), len(set(pages)))
        self.assertEqual(
            pages,
            [
                "daily_research",
                "past_daily",
                "trend_tasks",
                "reports",
                "favorites",
                "paper_search",
                "analytics",
                "keywords",
                "data_sources",
                "scoring",
                "api",
                "notifications",
                "advanced",
                "accounts",
                "backup_sync",
                "history_tasks",
                "diagnostics",
                "logs",
            ],
        )


if __name__ == "__main__":
    unittest.main()
