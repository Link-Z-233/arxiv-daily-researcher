"""
SQLite persistence for daily research paper progress.

The legacy JSON history should only mean "fully handled".  This store keeps
paper-level intermediate state so interrupted runs can reuse successful LLM
work without prematurely hiding papers from future runs.
"""

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agents.analysis_agent import Stage2Response
    from sources.base_source import PaperMetadata


class DailyResearchStore:
    """Small SQLite store for daily research runs and paper state."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_runs (
                    run_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    total_papers INTEGER DEFAULT 0,
                    error TEXT,
                    report_paths_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_papers (
                    source TEXT NOT NULL,
                    paper_id TEXT NOT NULL,
                    canonical_id TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 0,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    run_id TEXT,
                    paper_json TEXT NOT NULL,
                    score_json TEXT,
                    abstract_cn TEXT,
                    analysis_json TEXT,
                    scored_at TEXT,
                    translated_at TEXT,
                    analyzed_at TEXT,
                    completed_at TEXT,
                    last_error TEXT,
                    PRIMARY KEY (source, paper_id)
                )
                """
            )
            self._migrate_paper_identity(conn)
            self._migrate_stage_state(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_papers_run ON daily_papers(run_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_papers_completed ON daily_papers(completed_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_papers_identity "
                "ON daily_papers(source, canonical_id, version)"
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_deliveries (
                    delivery_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    paper_id TEXT NOT NULL,
                    canonical_id TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0,
                    report_path TEXT,
                    delivered_at TEXT NOT NULL,
                    UNIQUE(run_id, source, paper_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_paper_deliveries_identity "
                "ON paper_deliveries(source, canonical_id, version)"
            )

    @staticmethod
    def _migrate_paper_identity(conn):
        """Add identity columns to databases created by the first persistence patch."""
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(daily_papers)").fetchall()
        }
        if "canonical_id" not in columns:
            conn.execute(
                "ALTER TABLE daily_papers ADD COLUMN canonical_id TEXT NOT NULL DEFAULT ''"
            )
        if "version" not in columns:
            conn.execute(
                "ALTER TABLE daily_papers ADD COLUMN version INTEGER NOT NULL DEFAULT 0"
            )

        from sources.base_source import paper_identity

        rows = conn.execute(
            "SELECT source, paper_id, canonical_id, version FROM daily_papers"
        ).fetchall()
        for row in rows:
            canonical_id, version = paper_identity(row["source"], row["paper_id"])
            desired_version = version if version is not None else 0
            if row["canonical_id"] != canonical_id or row["version"] != desired_version:
                conn.execute(
                    "UPDATE daily_papers SET canonical_id = ?, version = ? "
                    "WHERE source = ? AND paper_id = ?",
                    (canonical_id, desired_version, row["source"], row["paper_id"]),
                )

    @staticmethod
    def _migrate_stage_state(conn):
        """Add explicit stage states to databases created before the state model."""
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(daily_papers)").fetchall()
        }
        additions = {
            "score_status": "TEXT NOT NULL DEFAULT 'pending'",
            "translation_status": "TEXT NOT NULL DEFAULT 'pending'",
            "analysis_status": "TEXT NOT NULL DEFAULT 'pending'",
            "retry_count": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE daily_papers ADD COLUMN {name} {definition}")

        conn.execute(
            "UPDATE daily_papers SET score_status = 'succeeded' "
            "WHERE score_json IS NOT NULL AND score_status = 'pending'"
        )
        conn.execute(
            "UPDATE daily_papers SET translation_status = 'succeeded' "
            "WHERE abstract_cn IS NOT NULL AND trim(abstract_cn) <> '' "
            "AND translation_status = 'pending'"
        )
        conn.execute(
            "UPDATE daily_papers SET analysis_status = 'succeeded' "
            "WHERE analysis_json IS NOT NULL AND analysis_status = 'pending'"
        )

    def start_run(self, total_papers: int) -> str:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_runs(run_id, started_at, status, total_papers)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, now, "running", total_papers),
            )
        return run_id

    def set_run_total(self, run_id: str, total_papers: int):
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE daily_runs SET total_papers = ? WHERE run_id = ?",
                (total_papers, run_id),
            )

    def complete_run(self, run_id: str, report_paths: Optional[Dict[str, Any]] = None):
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_runs
                SET completed_at = ?, status = ?, report_paths_json = ?
                WHERE run_id = ?
                """,
                (
                    now,
                    "completed",
                    json.dumps(report_paths or {}, ensure_ascii=False),
                    run_id,
                ),
            )

    def fail_run(self, run_id: str, error: str):
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_runs
                SET completed_at = ?, status = ?, error = ?
                WHERE run_id = ?
                """,
                (now, "failed", error[:4000], run_id),
            )

    def get_paper_record(self, source: str, paper_id: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM daily_papers WHERE source = ? AND paper_id = ?",
                (source, paper_id),
            ).fetchone()

    @staticmethod
    def _analysis_json(analysis: Any) -> str:
        if hasattr(analysis, "model_dump"):
            payload = analysis.model_dump(mode="json")
        elif isinstance(analysis, dict):
            payload = analysis
        else:
            payload = dict(analysis)
        return json.dumps(payload, ensure_ascii=False)

    def get_previous_version_record(
        self, source: str, paper: "PaperMetadata"
    ) -> Optional[sqlite3.Row]:
        """Return the latest completed earlier version of an arXiv paper."""
        if getattr(paper, "version", None) is None:
            return None
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT * FROM daily_papers
                WHERE source = ? AND canonical_id = ? AND version < ?
                  AND completed_at IS NOT NULL
                ORDER BY version DESC, completed_at DESC
                LIMIT 1
                """,
                (source, paper.canonical_id, paper.version),
            ).fetchone()

    def get_version_records(self, source: str, canonical_id: str) -> list[sqlite3.Row]:
        """Return all persisted versions for one canonical paper."""
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT * FROM daily_papers
                WHERE source = ? AND canonical_id = ?
                ORDER BY version ASC, first_seen_at ASC
                """,
                (source, canonical_id),
            ).fetchall()

    def upsert_paper_seen(self, run_id: str, source: str, paper: "PaperMetadata"):
        now = datetime.now().isoformat()
        paper_json = json.dumps(paper.to_dict(), ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_papers(
                    source, paper_id, canonical_id, version,
                    first_seen_at, last_seen_at, run_id, paper_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, paper_id) DO UPDATE SET
                    canonical_id = excluded.canonical_id,
                    version = excluded.version,
                    last_seen_at = excluded.last_seen_at,
                    run_id = excluded.run_id,
                    paper_json = excluded.paper_json
                """,
                (
                    source,
                    paper.paper_id,
                    paper.canonical_id or paper.paper_id,
                    paper.version or 0,
                    now,
                    now,
                    run_id,
                    paper_json,
                ),
            )

    def update_scored_paper(self, run_id: str, source: str, scored: Dict[str, Any]):
        """Persist a complete score result for backward-compatible callers."""
        now = datetime.now().isoformat()
        paper = scored["paper_metadata"]
        score_response = scored["score_response"]
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_papers(
                    source, paper_id, canonical_id, version,
                    first_seen_at, last_seen_at, run_id, paper_json,
                    score_json, abstract_cn, scored_at, translated_at,
                    score_status, translation_status, last_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(source, paper_id) DO UPDATE SET
                    canonical_id = excluded.canonical_id,
                    version = excluded.version,
                    last_seen_at = excluded.last_seen_at,
                    run_id = excluded.run_id,
                    paper_json = excluded.paper_json,
                    score_json = excluded.score_json,
                    abstract_cn = excluded.abstract_cn,
                    scored_at = excluded.scored_at,
                    translated_at = excluded.translated_at,
                    score_status = excluded.score_status,
                    translation_status = excluded.translation_status,
                    last_error = NULL
                """,
                (
                    source,
                    scored["paper_id"],
                    paper.canonical_id or paper.paper_id,
                    paper.version or 0,
                    now,
                    now,
                    run_id,
                    json.dumps(paper.to_dict(), ensure_ascii=False),
                    score_response.model_dump_json(),
                    scored.get("abstract_cn", ""),
                    now,
                    now if scored.get("abstract_cn") else None,
                    "succeeded",
                    "succeeded" if scored.get("abstract_cn") else "pending",
                ),
            )

    def update_score(
        self, run_id: str, source: str, scored: Dict[str, Any]
    ) -> None:
        """Persist score/TLDR before attempting translation."""
        now = datetime.now().isoformat()
        paper = scored["paper_metadata"]
        score_response = scored["score_response"]
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, paper_json = ?, score_json = ?, scored_at = ?,
                    score_status = 'succeeded', last_error = NULL
                WHERE source = ? AND paper_id = ?
                """,
                (
                    run_id,
                    json.dumps(paper.to_dict(), ensure_ascii=False),
                    score_response.model_dump_json(),
                    now,
                    source,
                    scored["paper_id"],
                ),
            )

    def update_translation(self, run_id: str, source: str, paper_id: str, translation: str):
        """Persist a successful non-empty abstract translation."""
        if not translation or not translation.strip():
            raise ValueError("translation must be non-empty")
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, abstract_cn = ?, translated_at = ?,
                    translation_status = 'succeeded', last_error = NULL
                WHERE source = ? AND paper_id = ?
                """,
                (run_id, translation.strip(), now, source, paper_id),
            )

    def mark_translation_not_required(self, run_id: str, source: str, paper_id: str):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, translation_status = 'not_required', last_error = NULL
                WHERE source = ? AND paper_id = ?
                """,
                (run_id, source, paper_id),
            )

    def update_analysis(self, run_id: str, source: str, paper_id: str, analysis: "Stage2Response"):
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, analysis_json = ?, analyzed_at = ?,
                    analysis_status = 'succeeded', last_error = NULL
                WHERE source = ? AND paper_id = ?
                """,
                (run_id, self._analysis_json(analysis), now, source, paper_id),
            )

    def mark_analysis_not_required(self, run_id: str, source: str, paper_id: str):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, analysis_status = 'not_required', last_error = NULL
                WHERE source = ? AND paper_id = ?
                """,
                (run_id, source, paper_id),
            )

    def update_error(
        self, run_id: str, source: str, paper_id: str, error: str, stage: str = "general"
    ):
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, last_seen_at = ?, last_error = ?, retry_count = retry_count + 1,
                    score_status = CASE WHEN ? = 'score' THEN 'failed' ELSE score_status END,
                    translation_status = CASE WHEN ? = 'translation' THEN 'failed' ELSE translation_status END,
                    analysis_status = CASE WHEN ? = 'analysis' THEN 'failed' ELSE analysis_status END
                WHERE source = ? AND paper_id = ?
                """,
                (run_id, now, error[:4000], stage, stage, stage, source, paper_id),
            )

    def mark_completed(self, run_id: str, source: str, paper_id: str):
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, completed_at = ?, last_error = NULL
                WHERE source = ? AND paper_id = ?
                """,
                (run_id, now, source, paper_id),
            )

    def hydrate_scored_paper(
        self, paper: "PaperMetadata", record: sqlite3.Row, require_translation: bool = True
    ) -> Optional[Dict[str, Any]]:
        if not record or not record["score_json"]:
            return None
        if record["score_status"] != "succeeded":
            return None
        if require_translation and record["translation_status"] not in (
            "succeeded",
            "not_required",
        ):
            return None

        from agents.analysis_agent import WeightedScoreResponse

        score_response = WeightedScoreResponse.model_validate_json(record["score_json"])
        return {
            "paper_metadata": paper,
            "paper_id": paper.paper_id,
            "title": paper.title,
            "authors": paper.get_authors_string(),
            "abstract": paper.abstract,
            "abstract_cn": record["abstract_cn"] or "",
            "url": paper.url,
            "pdf_url": paper.pdf_url,
            "published": paper.published_date.strftime("%Y-%m-%d")
            if paper.published_date
            else "N/A",
            "canonical_id": paper.canonical_id,
            "version": paper.version,
            "score_response": score_response,
        }

    def hydrate_analysis(self, record: sqlite3.Row) -> Optional["Stage2Response"]:
        if not record or not record["analysis_json"]:
            return None
        if record["analysis_status"] != "succeeded":
            return None
        return json.loads(record["analysis_json"])
