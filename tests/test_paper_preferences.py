import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.daily_research_store import DailyResearchStore  # noqa: E402


def _deliver_paper(store, source="arxiv", paper_id="2401.00001", title="A paper", authors=None, categories=None):
    store.set_paper_preference(
        source,
        paper_id,
        preference="like",
        title=title,
        authors=authors or ["Alice", "Bob"],
        categories=categories or ["quant-ph"],
    )


class PaperPreferenceTests(unittest.TestCase):
    def test_like_dislike_clear_never_deletes_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            store.set_paper_preference(
                "arxiv", "2401.00001", preference="like", title="T", authors=["A"], categories=["quant-ph"]
            )
            store.set_paper_preference(
                "arxiv", "2401.00001", preference="dislike", title="T", authors=["A"], categories=["quant-ph"]
            )
            store.set_paper_preference(
                "arxiv", "2401.00001", preference="none", title="T", authors=["A"], categories=["quant-ph"]
            )

            # 行仍在，状态是 none：清除是更新，不是删除。
            pref = store.get_paper_preference("arxiv", "2401.00001")
            self.assertEqual(pref["preference"], "none")
            self.assertEqual(store.get_preference_counts(), {"like": 0, "dislike": 0, "none": 1})
            self.assertEqual(store.list_preferences(), [])  # 默认跳过 none

    def test_invalid_preference_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            with self.assertRaises(ValueError):
                store.set_paper_preference("arxiv", "x", preference="meh", title="T")

    def test_aggregate_ranks_liked_authors_and_categories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            _deliver_paper(store, paper_id="1", authors=["Alice", "Bob"], categories=["quant-ph"])
            _deliver_paper(store, paper_id="2", authors=["Alice"], categories=["cond-mat"])
            _deliver_paper(store, paper_id="3", authors=["Alice"], categories=["quant-ph"])
            store.set_paper_preference(
                "arxiv", "4", preference="dislike", title="Disliked", authors=["Alice"], categories=["quant-ph"]
            )

            agg = store.aggregate_liked_preferences()
            self.assertEqual(agg["authors"][0], {"name": "Alice", "count": 3})
            self.assertEqual(agg["authors"][1], {"name": "Bob", "count": 1})
            self.assertEqual(agg["categories"][0], {"name": "quant-ph", "count": 2})
            # dislike 不进汇总
            self.assertEqual(sum(a["count"] for a in agg["authors"]), 4)

    def test_preference_map_skips_cleared(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            _deliver_paper(store, paper_id="1")
            store.set_paper_preference("arxiv", "2", preference="none", title="T")
            mapping = store.get_preference_map(
                [{"source": "arxiv", "paper_id": "1"}, {"source": "arxiv", "paper_id": "2"}]
            )
            self.assertEqual(mapping, {("arxiv", "1"): "like"})


if __name__ == "__main__":
    unittest.main()
