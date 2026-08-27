"""SQLite keyword tracking hosted by the daily-research database.

The normal source of raw terms is each completed paper's
``daily_papers.score_json.extracted_keywords``. v3.2 stored those records in
its own ``keywords.db`` though, so an import keeps its original per-paper
dates in ``legacy_keyword_records``. That preservation matters for historical
trend charts and for normalized aliases which cannot be reconstructed from an
HTML report alone.
"""

import sqlite3
import logging
from pathlib import Path
from datetime import date, timedelta
from typing import Any, Callable, List, Dict, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _emit_legacy_progress(
    callback: Optional[Callable[..., None]],
    detail: str,
    current: Optional[int] = None,
    total: Optional[int] = None,
) -> None:
    """Best-effort bridge from legacy keyword migration to WebUI progress."""
    if callback is None:
        return
    try:
        callback(
            phase="legacy_keywords",
            detail=detail,
            current=current,
            total=total,
        )
    except Exception as exc:  # pragma: no cover - diagnostics never block data import
        logger.debug("[LegacyKeywordImport] 进度回调失败: %s", exc)


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


# 论文库中的原始关键词视图。普通 v4 数据来自每篇已完成论文的评分 JSON；
# 已迁移的 v3.2 论文改用旧库原始记录，保留它当时的提取日期并避免同一论文
# 的 HTML 关键词与 keywords.db 关键词重复计数。
_PAPER_KEYWORDS_SQL = """
SELECT LOWER(je.value) AS keyword,
       dp.source AS source,
       dp.paper_id AS paper_id,
       COALESCE(
           NULLIF(dp.entity_id, ''),
           'source:' || LOWER(dp.source) || ':'
               || LOWER(COALESCE(NULLIF(dp.canonical_id, ''), dp.paper_id))
               || '@v' || CAST(dp.version AS TEXT)
       ) AS entity_id,
       substr(dp.completed_at, 1, 10) AS day
FROM daily_papers dp, json_each(dp.score_json, '$.extracted_keywords') je
WHERE dp.completed_at IS NOT NULL
  AND dp.score_json IS NOT NULL
  AND json_valid(dp.score_json)
  AND json_type(dp.score_json, '$.extracted_keywords') = 'array'
  AND je.type = 'text'
  AND NOT EXISTS (
      SELECT 1
      FROM legacy_keyword_records legacy
      WHERE legacy.source = dp.source
        AND legacy.paper_id = dp.paper_id
  )
UNION ALL
SELECT LOWER(legacy.keyword) AS keyword,
       legacy.source AS source,
       legacy.paper_id AS paper_id,
       COALESCE(
           (
               SELECT NULLIF(dp.entity_id, '')
               FROM daily_papers dp
               WHERE dp.source = legacy.source AND dp.paper_id = legacy.paper_id
               LIMIT 1
           ),
           'legacy:' || LOWER(legacy.source) || ':' || LOWER(legacy.paper_id)
       ) AS entity_id,
       legacy.extracted_date AS day
FROM legacy_keyword_records legacy
WHERE legacy.keyword != ''
"""


class KeywordDatabase:
    """
    SQLite 数据库管理器（与 DailyResearchStore 共用同一 db 文件）

    用于查询论文库关键词和管理标准化结果，支持：
    - 从论文评分记录读取原始关键词
    - 从 v3.2 ``keywords.db`` 幂等迁移原始关键词与标准化映射
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

                -- v3.2 的独立 keywords.db 原始记录。当前版本的原始词仍然
                -- 留在 daily_papers.score_json；本表只保存无法由 HTML 完整
                -- 恢复的历史记录及其原始提取日期。
                CREATE TABLE IF NOT EXISTS legacy_keyword_records (
                    source TEXT NOT NULL,
                    paper_id TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    extracted_date DATE NOT NULL,
                    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (source, paper_id, keyword)
                );

                -- 索引
                CREATE INDEX IF NOT EXISTS idx_daily_counts_date ON keyword_daily_counts(count_date);
                CREATE INDEX IF NOT EXISTS idx_aliases_raw ON keyword_aliases(raw_keyword);
                CREATE INDEX IF NOT EXISTS idx_legacy_keyword_records_date
                    ON legacy_keyword_records(extracted_date);
                """
            )
            conn.commit()

    @staticmethod
    def _source_table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
        """Return one fixed source table's columns for schema validation."""
        return {
            str(row[1])
            for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        }

    @staticmethod
    def _normalize_legacy_date(value: Any) -> Optional[str]:
        """Accept v3.2 DATE/TIMESTAMP forms and return one ISO calendar day."""
        text = str(value or "").strip()
        if len(text) < 10:
            return None
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            return None

    @staticmethod
    def _open_legacy_database_readonly(
        source_path: Path,
    ) -> tuple[sqlite3.Connection, str]:
        """Open a v3.2 database without ever modifying its source files.

        A normal SQLite ``mode=ro`` connection remains the preferred path: it
        can read a live WAL sidecar consistently.  Some NAS snapshots and
        read-only Docker bind mounts contain a clean main database whose old
        WAL journal mode still makes SQLite try to create a ``-shm`` file;
        that fails before any data can be read.  In that specific case an
        immutable snapshot is safe *only when no non-empty ``-wal`` file is
        present*.  A live/uncheckpointed WAL is never ignored silently.
        """
        resolved = source_path.resolve()
        base_uri = resolved.as_uri()
        connection: Optional[sqlite3.Connection] = None
        try:
            connection = sqlite3.connect(f"{base_uri}?mode=ro", uri=True)
            # SQLite may defer opening a WAL-mode file until the first query.
            # Probe the schema here so a read-only bind-mount failure reaches
            # the immutable-snapshot fallback rather than surfacing later in
            # the middle of a keyword migration.
            connection.execute("PRAGMA schema_version").fetchone()
            return connection, "readonly"
        except sqlite3.Error as normal_error:
            if connection is not None:
                connection.close()
            wal_path = resolved.with_name(resolved.name + "-wal")
            try:
                has_uncheckpointed_wal = (
                    wal_path.is_file() and wal_path.stat().st_size > 0
                )
            except OSError:
                has_uncheckpointed_wal = True
            if has_uncheckpointed_wal:
                raise sqlite3.OperationalError(
                    "旧 keywords.db 无法以只读模式打开，且存在未合并的 -wal 文件；"
                    "请先停止旧版本应用并完成 SQLite 检查点，或同时保留数据库与 WAL 文件后重试"
                ) from normal_error
            try:
                connection = sqlite3.connect(
                    f"{base_uri}?mode=ro&immutable=1", uri=True
                )
                connection.execute("PRAGMA schema_version").fetchone()
                return connection, "immutable_snapshot"
            except sqlite3.Error as immutable_error:
                if connection is not None:
                    connection.close()
                raise sqlite3.OperationalError(
                    f"普通只读与不可变快照均无法打开旧 keywords.db: "
                    f"{normal_error}; {immutable_error}"
                ) from immutable_error

    def import_legacy_database(
        self,
        legacy_db_path: Path,
        *,
        progress_logger: Optional[Any] = None,
        progress_callback: Optional[Callable[..., None]] = None,
    ) -> Dict[str, Any]:
        """Merge a v3.2 ``keywords.db`` into the v4 daily SQLite database.

        The source opens read-only. Existing v4 alias choices win, while the
        source's raw per-paper records, original dates, canonical terms and
        aliases are merged idempotently. Daily counts are rebuilt from the
        merged records rather than copying the old derived cache.
        """
        log = progress_logger or logger
        source_path = Path(legacy_db_path)
        summary: Dict[str, Any] = {
            "state": "not_found",
            "read_mode": None,
            "records_scanned": 0,
            "records_imported": 0,
            "records_invalid": 0,
            "normalized_terms_imported": 0,
            "aliases_imported": 0,
            "aliases_preserved": 0,
        }
        if not source_path.is_file():
            log.info("[LegacyKeywordImport] 未找到旧 keywords.db，跳过关键词库迁移")
            _emit_legacy_progress(progress_callback, "未找到旧 keywords.db，已跳过", 0, 0)
            return summary

        try:
            if source_path.resolve() == self.db_path.resolve():
                summary["state"] = "same_database"
                log.warning("[LegacyKeywordImport] 旧关键词库与目标 SQLite 相同，已跳过")
                _emit_legacy_progress(progress_callback, "旧关键词库与目标 SQLite 相同，已跳过", 0, 0)
                return summary
        except OSError:
            # Let the read-only open below report the concrete filesystem
            # failure. This also keeps network-mounted paths best-effort.
            pass

        try:
            source, read_mode = self._open_legacy_database_readonly(source_path)
            source.row_factory = sqlite3.Row
        except (OSError, sqlite3.Error) as exc:
            summary.update({"state": "unreadable", "error": str(exc)})
            log.warning("[LegacyKeywordImport] 无法读取旧 keywords.db: %s", exc)
            _emit_legacy_progress(progress_callback, "无法读取旧 keywords.db，已跳过")
            return summary

        summary["read_mode"] = read_mode
        if read_mode == "immutable_snapshot":
            log.info(
                "[LegacyKeywordImport] 旧 keywords.db 以不可变快照读取（只读挂载兼容模式）"
            )
            _emit_legacy_progress(
                progress_callback,
                "旧关键词库以只读快照方式读取",
            )

        try:
            source_tables = {
                str(row[0])
                for row in source.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            keyword_columns = (
                self._source_table_columns(source, "keywords")
                if "keywords" in source_tables
                else set()
            )
            normalized_columns = (
                self._source_table_columns(source, "normalized_keywords")
                if "normalized_keywords" in source_tables
                else set()
            )
            alias_columns = (
                self._source_table_columns(source, "keyword_aliases")
                if "keyword_aliases" in source_tables
                else set()
            )
            required_keyword_columns = {"keyword", "paper_id", "source", "extracted_date"}
            if (
                not required_keyword_columns.issubset(keyword_columns)
                and not {"id", "canonical_keyword"}.issubset(normalized_columns)
            ):
                summary["state"] = "unsupported_schema"
                log.warning("[LegacyKeywordImport] 旧 keywords.db 缺少 v3.2 关键词表，已跳过")
                _emit_legacy_progress(progress_callback, "旧 keywords.db 格式不受支持，已跳过")
                return summary

            log.info("[LegacyKeywordImport] 开始迁移旧 keywords.db")
            _emit_legacy_progress(progress_callback, "开始读取旧 keywords.db")
            normalized_id_map: Dict[int, int] = {}
            with self._get_connection() as target:
                if {"id", "canonical_keyword"}.issubset(normalized_columns):
                    normalized_total = int(
                        source.execute("SELECT COUNT(*) FROM normalized_keywords").fetchone()[0]
                    )
                    _emit_legacy_progress(
                        progress_callback,
                        f"迁移规范关键词（共 {normalized_total} 条）",
                        0,
                        normalized_total,
                    )
                    category_expr = "category" if "category" in normalized_columns else "NULL"
                    rows = source.execute(
                        "SELECT id, canonical_keyword, " + category_expr + " AS category "
                        "FROM normalized_keywords ORDER BY rowid"
                    )
                    for index, row in enumerate(rows, start=1):
                        canonical = str(row["canonical_keyword"] or "").strip().lower()
                        if canonical:
                            existing = target.execute(
                                "SELECT id FROM normalized_keywords WHERE canonical_keyword = ?",
                                (canonical,),
                            ).fetchone()
                            if existing is None:
                                target.execute(
                                    "INSERT INTO normalized_keywords (canonical_keyword, category) VALUES (?, ?)",
                                    (canonical, row["category"]),
                                )
                                existing = target.execute(
                                    "SELECT id FROM normalized_keywords WHERE canonical_keyword = ?",
                                    (canonical,),
                                ).fetchone()
                                summary["normalized_terms_imported"] += 1
                            normalized_id_map[int(row["id"])] = int(existing["id"])
                        if index == normalized_total or index % 100 == 0:
                            log.info(
                                "[LegacyKeywordImport] 规范关键词迁移 %s/%s",
                                index,
                                normalized_total,
                            )
                            _emit_legacy_progress(
                                progress_callback,
                                f"迁移规范关键词 {index}/{normalized_total}",
                                index,
                                normalized_total,
                            )

                if {"raw_keyword", "normalized_keyword_id"}.issubset(alias_columns):
                    alias_total = int(
                        source.execute("SELECT COUNT(*) FROM keyword_aliases").fetchone()[0]
                    )
                    _emit_legacy_progress(
                        progress_callback,
                        f"迁移关键词别名（共 {alias_total} 条）",
                        0,
                        alias_total,
                    )
                    confidence_expr = "confidence" if "confidence" in alias_columns else "1.0"
                    rows = source.execute(
                        "SELECT raw_keyword, normalized_keyword_id, " + confidence_expr
                        + " AS confidence FROM keyword_aliases ORDER BY rowid"
                    )
                    for index, row in enumerate(rows, start=1):
                        raw_keyword = str(row["raw_keyword"] or "").strip().lower()
                        target_id = normalized_id_map.get(int(row["normalized_keyword_id"] or 0))
                        if raw_keyword and target_id is not None:
                            existing = target.execute(
                                "SELECT normalized_keyword_id FROM keyword_aliases WHERE raw_keyword = ?",
                                (raw_keyword,),
                            ).fetchone()
                            if existing is not None:
                                summary["aliases_preserved"] += 1
                            else:
                                try:
                                    confidence = float(row["confidence"] or 1.0)
                                except (TypeError, ValueError):
                                    confidence = 1.0
                                target.execute(
                                    "INSERT INTO keyword_aliases (raw_keyword, normalized_keyword_id, confidence) "
                                    "VALUES (?, ?, ?)",
                                    (raw_keyword, target_id, confidence),
                                )
                                summary["aliases_imported"] += 1
                        if index == alias_total or index % 100 == 0:
                            log.info(
                                "[LegacyKeywordImport] 关键词别名迁移 %s/%s",
                                index,
                                alias_total,
                            )
                            _emit_legacy_progress(
                                progress_callback,
                                f"迁移关键词别名 {index}/{alias_total}",
                                index,
                                alias_total,
                            )

                if required_keyword_columns.issubset(keyword_columns):
                    record_total = int(
                        source.execute("SELECT COUNT(*) FROM keywords").fetchone()[0]
                    )
                    _emit_legacy_progress(
                        progress_callback,
                        f"迁移逐论文关键词（共 {record_total} 条）",
                        0,
                        record_total,
                    )
                    rows = source.execute(
                        "SELECT keyword, paper_id, source, extracted_date FROM keywords ORDER BY rowid"
                    )
                    for index, row in enumerate(rows, start=1):
                        summary["records_scanned"] += 1
                        keyword = str(row["keyword"] or "").strip().lower()
                        paper_id = str(row["paper_id"] or "").strip()
                        source_name = str(row["source"] or "").strip().lower()
                        extracted_date = self._normalize_legacy_date(row["extracted_date"])
                        if not keyword or not paper_id or not source_name or not extracted_date:
                            summary["records_invalid"] += 1
                        else:
                            cursor = target.execute(
                                "INSERT OR IGNORE INTO legacy_keyword_records "
                                "(source, paper_id, keyword, extracted_date) VALUES (?, ?, ?, ?)",
                                (source_name, paper_id, keyword, extracted_date),
                            )
                            if cursor.rowcount:
                                summary["records_imported"] += 1
                        if index == record_total or index % 100 == 0:
                            log.info(
                                "[LegacyKeywordImport] 逐论文关键词迁移 %s/%s（新增 %s）",
                                index,
                                record_total,
                                summary["records_imported"],
                            )
                            _emit_legacy_progress(
                                progress_callback,
                                f"迁移逐论文关键词 {index}/{record_total}",
                                index,
                                record_total,
                            )

            self.update_daily_counts()
            summary["state"] = "imported"
            log.info(
                "[LegacyKeywordImport] 迁移完成：扫描 %s 条，新增 %s 条，"
                "规范词 %s 个，别名 %s 个，保留现有别名 %s 个，无效 %s 条",
                summary["records_scanned"],
                summary["records_imported"],
                summary["normalized_terms_imported"],
                summary["aliases_imported"],
                summary["aliases_preserved"],
                summary["records_invalid"],
            )
            _emit_legacy_progress(
                progress_callback,
                f"旧关键词库迁移完成（新增 {summary['records_imported']} 条）",
                summary["records_scanned"],
                summary["records_scanned"],
            )
            return summary
        except (OSError, sqlite3.Error) as exc:
            summary.update({"state": "failed", "error": str(exc)})
            log.warning("[LegacyKeywordImport] 旧关键词库迁移失败: %s", exc)
            _emit_legacy_progress(progress_callback, "旧关键词库迁移失败，继续导入其他历史")
            return summary
        finally:
            source.close()

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
                SELECT COUNT(DISTINCT entity_id) FROM ({_PAPER_KEYWORDS_SQL})
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
                SELECT ka.normalized_keyword_id, pk.day, COUNT(DISTINCT pk.entity_id)
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
