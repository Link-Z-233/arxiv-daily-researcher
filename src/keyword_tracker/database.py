"""
SQLite 数据库操作模块（寄宿于每日研究状态库 daily_research.db）。

原始关键词不再单独收集：每篇已完成论文的评分记录里已经带有
``extracted_keywords``（daily_papers.score_json），本模块直接从那里读取。
同一个数据库文件里只保存派生的标准化表：规范关键词、别名映射与每日统计。
"""

import sqlite3
import logging
from pathlib import Path
from datetime import date, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class NormalizedKeyword:
    """标准化关键词"""

    id: int
    canonical_keyword: str
    category: Optional[str] = None


@dataclass
class KeywordTrendData:
    """关键词趋势数据"""

    keyword: str
    daily_counts: Dict[date, int]


# 论文库中的原始关键词视图：每篇已完成论文 × 其提取关键词。
# json_type 守卫 score_json 缺失/损坏/字段不是数组的情况。
_PAPER_KEYWORDS_SQL = """
SELECT LOWER(je.value) AS keyword,
       dp.source AS source,
       dp.paper_id AS paper_id,
       substr(dp.completed_at, 1, 10) AS day
FROM daily_papers dp, json_each(dp.score_json, '$.extracted_keywords') je
WHERE dp.completed_at IS NOT NULL
  AND dp.score_json IS NOT NULL
  AND json_valid(dp.score_json)
  AND json_type(dp.score_json, '$.extracted_keywords') = 'array'
  AND je.type = 'text'
"""


class KeywordDatabase:
    """
    SQLite 数据库管理器（与 DailyResearchStore 共用同一 db 文件）

    用于查询论文库关键词和管理标准化结果，支持：
    - 从论文评分记录读取原始关键词
    - 标准化关键词管理
    - 别名映射
    - 趋势统计
    """

    def __init__(self, db_path: Path):
        """
        初始化数据库连接

        Args:
            db_path: SQLite 数据库文件路径（每日研究状态库）
        """
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_tables()

    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接（使用 WAL 模式以支持并发读写）"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_tables(self) -> None:
        """创建派生表（如不存在；与论文库表共存于同一文件）"""
        with self._get_connection() as conn:
            conn.executescript(
                """
                -- 标准化关键词表
                CREATE TABLE IF NOT EXISTS normalized_keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    canonical_keyword TEXT NOT NULL UNIQUE,
                    category TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                -- 别名映射表：原始关键词 → 标准形式
                CREATE TABLE IF NOT EXISTS keyword_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    raw_keyword TEXT NOT NULL UNIQUE,
                    normalized_keyword_id INTEGER NOT NULL,
                    confidence REAL DEFAULT 1.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (normalized_keyword_id) REFERENCES normalized_keywords(id)
                );

                -- 每日统计表（派生数据，每次标准化后全量重建）
                CREATE TABLE IF NOT EXISTS keyword_daily_counts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    normalized_keyword_id INTEGER NOT NULL,
                    count_date DATE NOT NULL,
                    paper_count INTEGER DEFAULT 0,
                    FOREIGN KEY (normalized_keyword_id) REFERENCES normalized_keywords(id),
                    UNIQUE(normalized_keyword_id, count_date)
                );

                -- 索引
                CREATE INDEX IF NOT EXISTS idx_daily_counts_date ON keyword_daily_counts(count_date);
                CREATE INDEX IF NOT EXISTS idx_aliases_raw ON keyword_aliases(raw_keyword);
                """
            )
            conn.commit()

    def get_unique_unnormalized_keywords(self, limit: int = 100) -> List[str]:
        """
        获取论文库中唯一、尚未建立别名的关键词（去重）

        Args:
            limit: 最大返回数量

        Returns:
            关键词字符串列表
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT DISTINCT keyword FROM ({_PAPER_KEYWORDS_SQL})
                WHERE keyword != ''
                  AND keyword NOT IN (SELECT raw_keyword FROM keyword_aliases)
                ORDER BY keyword
                LIMIT ?
                """,
                (limit,),
            )
            return [row["keyword"] for row in cursor.fetchall()]

    def count_papers_with_keyword(self, raw_keyword: str) -> int:
        """包含某关键词的论文数（用于标准化统计）。"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT COUNT(DISTINCT paper_id) FROM ({_PAPER_KEYWORDS_SQL})
                WHERE keyword = ?
                """,
                (raw_keyword.strip().lower(),),
            )
            return int(cursor.fetchone()[0])

    def get_or_create_normalized_keyword(
        self, canonical_keyword: str, category: Optional[str] = None
    ) -> int:
        """
        获取或创建标准化关键词

        Args:
            canonical_keyword: 标准形式
            category: 分类

        Returns:
            标准化关键词ID
        """
        canonical_lower = canonical_keyword.strip().lower()

        with self._get_connection() as conn:
            # 先查找是否存在
            cursor = conn.execute(
                "SELECT id FROM normalized_keywords WHERE canonical_keyword = ?", (canonical_lower,)
            )
            row = cursor.fetchone()
            if row:
                return row[0]

            # 创建新的
            cursor = conn.execute(
                "INSERT INTO normalized_keywords (canonical_keyword, category) VALUES (?, ?)",
                (canonical_lower, category),
            )
            conn.commit()
            return cursor.lastrowid

    def add_keyword_alias(
        self, raw_keyword: str, normalized_id: int, confidence: float = 1.0
    ) -> None:
        """
        添加关键词别名映射（原始关键词 → 标准形式）

        Args:
            raw_keyword: 原始关键词
            normalized_id: 标准化关键词ID
            confidence: 置信度
        """
        raw_lower = raw_keyword.strip().lower()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO keyword_aliases
                (raw_keyword, normalized_keyword_id, confidence)
                VALUES (?, ?, ?)
                """,
                (raw_lower, normalized_id, confidence),
            )
            conn.commit()

    def get_all_canonical_keywords(self) -> List[str]:
        """获取所有标准化关键词"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT canonical_keyword FROM normalized_keywords ORDER BY canonical_keyword"
            )
            return [row["canonical_keyword"] for row in cursor.fetchall()]

    def update_daily_counts(self) -> None:
        """
        全量重建每日统计（别名 × 论文库关键词聚合）。

        统计是派生数据，重建不触碰论文与标记等原始记录。
        """
        with self._get_connection() as conn:
            conn.execute("DELETE FROM keyword_daily_counts")
            conn.execute(
                f"""
                INSERT INTO keyword_daily_counts (normalized_keyword_id, count_date, paper_count)
                SELECT ka.normalized_keyword_id, pk.day, COUNT(DISTINCT pk.paper_id)
                FROM ({_PAPER_KEYWORDS_SQL}) pk
                JOIN keyword_aliases ka ON ka.raw_keyword = pk.keyword
                GROUP BY ka.normalized_keyword_id, pk.day
                """
            )
            conn.commit()

    def get_top_keywords(
        self, days: int = 30, limit: int = 20
    ) -> List[Tuple[str, int, Optional[str]]]:
        """
        获取热门关键词排名

        Args:
            days: 回溯天数
            limit: 返回数量

        Returns:
            列表 [(关键词, 总数, 分类), ...]
        """
        start_date = (date.today() - timedelta(days=days)).isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    nk.canonical_keyword,
                    SUM(kdc.paper_count) as total_count,
                    nk.category
                FROM keyword_daily_counts kdc
                JOIN normalized_keywords nk ON kdc.normalized_keyword_id = nk.id
                WHERE kdc.count_date >= ?
                GROUP BY nk.id
                ORDER BY total_count DESC
                LIMIT ?
                """,
                (start_date, limit),
            )
            return [
                (row["canonical_keyword"], row["total_count"], row["category"])
                for row in cursor.fetchall()
            ]

    def get_keyword_trends(
        self, days: int = 30, keywords: Optional[List[str]] = None, limit: int = 10
    ) -> List[KeywordTrendData]:
        """
        获取关键词趋势数据

        Args:
            days: 回溯天数
            keywords: 指定关键词列表（None则取热门）
            limit: 关键词数量限制

        Returns:
            KeywordTrendData 列表
        """
        start_date = date.today() - timedelta(days=days)

        with self._get_connection() as conn:
            # 确定要查询的关键词
            if keywords:
                kw_list = [kw.lower() for kw in keywords]
            else:
                # 获取热门关键词
                top = self.get_top_keywords(days=days, limit=limit)
                kw_list = [kw for kw, _, _ in top]

            if not kw_list:
                return []

            results = []
            for kw in kw_list:
                cursor = conn.execute(
                    """
                    SELECT kdc.count_date, kdc.paper_count
                    FROM keyword_daily_counts kdc
                    JOIN normalized_keywords nk ON kdc.normalized_keyword_id = nk.id
                    WHERE nk.canonical_keyword = ?
                    AND kdc.count_date >= ?
                    ORDER BY kdc.count_date
                    """,
                    (kw, start_date.isoformat()),
                )

                daily_counts = {}
                for row in cursor.fetchall():
                    d = date.fromisoformat(row["count_date"])
                    daily_counts[d] = row["paper_count"]

                if daily_counts:
                    results.append(KeywordTrendData(keyword=kw, daily_counts=daily_counts))

            return results

    def get_stats(self) -> Dict[str, int]:
        """获取统计信息"""
        with self._get_connection() as conn:
            stats = {}

            cursor = conn.execute(
                f"SELECT COUNT(DISTINCT keyword) FROM ({_PAPER_KEYWORDS_SQL})"
            )
            stats["total_keywords"] = cursor.fetchone()[0]

            cursor = conn.execute(
                f"""
                SELECT COUNT(DISTINCT keyword) FROM ({_PAPER_KEYWORDS_SQL}) pk
                WHERE pk.keyword IN (SELECT raw_keyword FROM keyword_aliases)
                """
            )
            stats["normalized_keywords"] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM normalized_keywords")
            stats["canonical_keywords"] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM keyword_aliases")
            stats["aliases"] = cursor.fetchone()[0]

            return stats
