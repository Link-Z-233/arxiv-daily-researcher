"""关键词趋势追踪融合 SQLite 的回归测试。

原始关键词不再单独收集：KeywordDatabase 直接读每日研究库里
已完成论文的 extracted_keywords，标准化/别名/每日统计表寄宿同一个 db 文件。
"""

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from keyword_tracker.database import KeywordDatabase  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402


def _insert_completed_paper(
    db_path: Path,
    paper_id: str,
    keywords: list[str],
    completed_day: str,
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO daily_papers (source, paper_id, first_seen_at, last_seen_at,"
        " paper_json, score_json, completed_at)"
        " VALUES ('arxiv', ?, ?, ?, ?, ?, ?)",
        (
            paper_id,
            f"{completed_day}T08:00:00",
            f"{completed_day}T08:05:00",
            json.dumps({"title": f"paper {paper_id}", "authors": []}),
            json.dumps({"total_score": 5.0, "extracted_keywords": keywords}),
            f"{completed_day}T09:00:00",
        ),
    )
    conn.commit()
    conn.close()


class KeywordFusionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "daily_research.db"
        # 用真实 store 初始化 schema（keyword 表与论文表共存）
        DailyResearchStore(self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_keywords_are_read_from_paper_store(self):
        _insert_completed_paper(
            self.db_path, "2401.00001", ["Quantum Error Correction", "qubit"], "2026-08-20"
        )
        db = KeywordDatabase(self.db_path)

        unnormalized = db.get_unique_unnormalized_keywords()
        self.assertEqual(
            sorted(unnormalized), ["quantum error correction", "qubit"]
        )
        # 论文库里的关键词不计入未完成记录
        stats = db.get_stats()
        self.assertEqual(stats["total_keywords"], 2)
        self.assertEqual(stats["normalized_keywords"], 0)

    def test_alias_normalization_and_daily_counts(self):
        _insert_completed_paper(
            self.db_path, "2401.00001", ["Quantum Error Correction"], "2026-08-20"
        )
        _insert_completed_paper(
            self.db_path, "2401.00002", ["quantum error correction"], "2026-08-20"
        )
        _insert_completed_paper(
            self.db_path, "2401.00003", ["QEC"], "2026-08-21"
        )
        db = KeywordDatabase(self.db_path)

        canonical_id = db.get_or_create_normalized_keyword("quantum error correction")
        db.add_keyword_alias("quantum error correction", canonical_id)
        db.add_keyword_alias("qec", canonical_id)

        self.assertEqual(db.count_papers_with_keyword("QEC"), 1)
        self.assertEqual(db.get_unique_unnormalized_keywords(), [])

        db.update_daily_counts()

        # 别名覆盖后，8-20 两篇、8-21 一篇
        trends = db.get_keyword_trends(days=30, keywords=["quantum error correction"])
        self.assertEqual(len(trends), 1)
        daily = trends[0].daily_counts
        self.assertEqual(daily[date(2026, 8, 20)], 2)
        self.assertEqual(daily[date(2026, 8, 21)], 1)

        top = db.get_top_keywords(days=30)
        self.assertEqual(top[0][0], "quantum error correction")
        self.assertEqual(top[0][1], 3)

    def test_malformed_score_json_is_skipped(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO daily_papers (source, paper_id, first_seen_at, last_seen_at,"
            " paper_json, score_json, completed_at)"
            " VALUES ('arxiv', 'bad', '2026-08-20T08:00:00', '2026-08-20T08:00:00',"
            " '{}', 'not-json', '2026-08-20T09:00:00')"
        )
        conn.commit()
        conn.close()
        _insert_completed_paper(self.db_path, "ok", ["qubit"], "2026-08-20")

        db = KeywordDatabase(self.db_path)
        self.assertEqual(db.get_unique_unnormalized_keywords(), ["qubit"])
        db.update_daily_counts()  # 不抛异常即可

    def test_trends_respect_window(self):
        old_day = (date.today() - timedelta(days=40)).isoformat()
        _insert_completed_paper(self.db_path, "old", ["qubit"], old_day)
        db = KeywordDatabase(self.db_path)
        canonical_id = db.get_or_create_normalized_keyword("qubit")
        db.add_keyword_alias("qubit", canonical_id)
        db.update_daily_counts()

        self.assertEqual(db.get_top_keywords(days=30), [])
        self.assertEqual(db.get_top_keywords(days=60)[0][1], 1)


if __name__ == "__main__":
    unittest.main()
