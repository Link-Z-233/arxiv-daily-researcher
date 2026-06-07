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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_papers_run ON daily_papers(run_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_papers_completed ON daily_papers(completed_at)"
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

    def upsert_paper_seen(self, run_id: str, source: str, paper: "PaperMetadata"):
        now = datetime.now().isoformat()
        paper_json = json.dumps(paper.to_dict(), ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_papers(
                    source, paper_id, first_seen_at, last_seen_at, run_id, paper_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, paper_id) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at,
                    run_id = excluded.run_id,
                    paper_json = excluded.paper_json
                """,
                (source, paper.paper_id, now, now, run_id, paper_json),
            )

    def update_scored_paper(self, run_id: str, source: str, scored: Dict[str, Any]):
        now = datetime.now().isoformat()
        paper = scored["paper_metadata"]
        score_response = scored["score_response"]
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_papers(
                    source, paper_id, first_seen_at, last_seen_at, run_id, paper_json,
                    score_json, abstract_cn, scored_at, translated_at, last_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(source, paper_id) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at,
                    run_id = excluded.run_id,
                    paper_json = excluded.paper_json,
                    score_json = excluded.score_json,
                    abstract_cn = excluded.abstract_cn,
                    scored_at = excluded.scored_at,
                    translated_at = excluded.translated_at,
                    last_error = NULL
                """,
                (
                    source,
                    scored["paper_id"],
                    now,
                    now,
                    run_id,
                    json.dumps(paper.to_dict(), ensure_ascii=False),
                    score_response.model_dump_json(),
                    scored.get("abstract_cn", ""),
                    now,
                    now if scored.get("abstract_cn") else None,
                ),
            )

    def update_analysis(self, run_id: str, source: str, paper_id: str, analysis: "Stage2Response"):
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, analysis_json = ?, analyzed_at = ?, last_error = NULL
                WHERE source = ? AND paper_id = ?
                """,
                (run_id, analysis.model_dump_json(), now, source, paper_id),
            )

    def update_error(self, run_id: str, source: str, paper_id: str, error: str):
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, last_seen_at = ?, last_error = ?
                WHERE source = ? AND paper_id = ?
                """,
                (run_id, now, error[:4000], source, paper_id),
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
        self, paper: "PaperMetadata", record: sqlite3.Row
    ) -> Optional[Dict[str, Any]]:
        if not record or not record["score_json"]:
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
            "score_response": score_response,
        }

    def hydrate_analysis(self, record: sqlite3.Row) -> Optional["Stage2Response"]:
        if not record or not record["analysis_json"]:
            return None
        from agents.analysis_agent import Stage2Response

        return Stage2Response.model_validate_json(record["analysis_json"])
