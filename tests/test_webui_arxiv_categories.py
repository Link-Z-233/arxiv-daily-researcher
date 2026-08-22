import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui.arxiv_categories import (  # noqa: E402
    ARXIV_CATEGORIES,
    format_arxiv_category,
)


class ArxivCategoryCatalogTests(unittest.TestCase):
    def test_catalog_is_complete_sorted_and_unique(self):
        # arXiv 全部一级分类（2026 年一级主体 ~155 个），按字母序、无重复。
        self.assertGreaterEqual(len(ARXIV_CATEGORIES), 150)
        self.assertEqual(ARXIV_CATEGORIES, sorted(ARXIV_CATEGORIES))
        self.assertEqual(len(ARXIV_CATEGORIES), len(set(ARXIV_CATEGORIES)))

    def test_expected_primary_archives_are_present(self):
        for code in (
            "quant-ph",
            "gr-qc",
            "hep-th",
            "cond-mat.supr-con",
            "cs.AI",
            "cs.LG",
            "stat.ML",
            "math.QA",
            "physics.optics",
            "eess.SY",
            "econ.EM",
            "nlin.CD",
            "astro-ph.CO",
        ):
            self.assertIn(code, ARXIV_CATEGORIES)

    def test_format_shows_code_and_name(self):
        self.assertEqual(
            format_arxiv_category("quant-ph"), "quant-ph · Quantum Physics"
        )
        # 未知代码原样返回（防御旧配置）
        self.assertEqual(format_arxiv_category("old-style"), "old-style")


if __name__ == "__main__":
    unittest.main()
