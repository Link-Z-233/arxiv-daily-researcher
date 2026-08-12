"""
SQLite persistence for daily research paper progress.

The legacy JSON history should only mean "fully handled".  This store keeps
paper-level intermediate state so interrupted runs can reuse successful LLM
work without prematurely hiding papers from future runs.
"""

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agents.analysis_agent import Stage2Response
    from sources.base_source import PaperMetadata


class DailyResearchStore:
    """Small SQLite store for daily research runs and paper state."""

    # Source APIs only expose a day-granularity query.  Always rescan one
    # extra day around a completed scan boundary: this protects papers near a
    # boundary as well as a short upstream indexing delay.  Exact-version
    # delivery de-duplication makes the overlap safe and cheap in terms of
    # downstream LLM work.
    SCAN_RECOVERY_OVERLAP_DAYS = 1

    # These values come from optional, best-effort enrichment services.  They
    # must not disappear merely because a later retry happens while the
    # enrichment service is rate-limited or temporarily unavailable.  Core
    # bibliographic metadata deliberately remains fresh on every scan.
    _OPTIONAL_ENRICHMENT_FIELDS = (
        "semantic_scholar_tldr",
        "arxiv_id",
        "arxiv_url",
        "pdf_url",
    )

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
                    scan_started_at TEXT,
                    scan_days INTEGER,
                    scanned_sources_json TEXT,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    total_papers INTEGER DEFAULT 0,
                    error TEXT,
                    report_paths_json TEXT
                )
                """
            )
            self._migrate_run_scan_state(conn)
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

            # An outbox makes notification delivery independent from paper completion.
            # A notification can be retried without allowing the paper back into a
            # later daily scan as a supposedly new result.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_outbox (
                    outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    claimed_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    UNIQUE(run_id, event_type, channel)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_notification_outbox_pending "
                "ON notification_outbox(status, next_attempt_at)"
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS maintenance_outbox (
                    task_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    claimed_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_maintenance_outbox_pending "
                "ON maintenance_outbox(status, next_attempt_at)"
            )

            # A per-source checkpoint is advanced only in the same
            # transaction that completes a successful daily scan/report.  If
            # any fetch, scoring, translation, analysis, report generation,
            # or final delivery commit fails, its old checkpoint remains in
            # place and the next run expands its lookback window instead of
            # letting those papers age out of SEARCH_DAYS.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_scan_watermarks (
                    source TEXT PRIMARY KEY,
                    successful_scan_started_at TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _migrate_run_scan_state(conn):
        """Add scan audit columns to databases created before recovery watermarks."""
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(daily_runs)").fetchall()
        }
        additions = {
            "scan_started_at": "TEXT",
            "scan_days": "INTEGER",
            "scanned_sources_json": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE daily_runs ADD COLUMN {name} {definition}")

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

    @staticmethod
    def _parse_checkpoint_timestamp(value: Optional[str]) -> Optional[datetime]:
        """Parse a stored local/UTC ISO timestamp into a UTC-aware value.

        Existing databases use ``datetime.now().isoformat()`` (without an
        offset), while future callers may persist offset-aware timestamps.
        Treat legacy naive values as local time, matching their original
        meaning, then compare everything in UTC.
        """
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            return parsed.astimezone()
        return parsed.astimezone(timezone.utc)

    def prepare_scan(
        self,
        run_id: str,
        configured_days: int,
        sources: list[str],
        now: Optional[datetime] = None,
    ) -> int:
        """Record a scan plan and return a recovery-safe lookback length.

        ``SEARCH_DAYS`` remains the normal overlap window.  When an enabled
        source has not completed a successful scan recently (because a run
        failed or the scheduler was offline), extend the window back to that
        source's last successful scan start plus a one-day overlap.  The
        SQLite delivery ledger filters already delivered exact versions, so
        this never turns an expanded recovery scan into duplicate reports.

        A new source has no checkpoint by definition; its configured window is
        used rather than silently attempting an unbounded historical import.
        Call this immediately before fetching, rather than at process start,
        so the checkpoint represents the actual source-query boundary.
        """
        base_days = max(1, int(configured_days))
        normalized_sources = sorted(
            {str(source).strip().lower() for source in sources if str(source).strip()}
        )
        now_dt = now or datetime.now().astimezone()
        if now_dt.tzinfo is None:
            now_dt = now_dt.astimezone()
        now_utc = now_dt.astimezone(timezone.utc)
        scan_started_at = now_dt.isoformat()

        with self._lock, self._connect() as conn:
            run = conn.execute(
                "SELECT run_id FROM daily_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"daily run does not exist: {run_id}")

            recovery_days = base_days
            if normalized_sources:
                placeholders = ", ".join("?" for _ in normalized_sources)
                rows = conn.execute(
                    "SELECT source, successful_scan_started_at "
                    "FROM daily_scan_watermarks WHERE source IN ("
                    + placeholders
                    + ")",
                    normalized_sources,
                ).fetchall()
                checkpoints = {
                    row["source"]: self._parse_checkpoint_timestamp(
                        row["successful_scan_started_at"]
                    )
                    for row in rows
                }
                for source in normalized_sources:
                    checkpoint = checkpoints.get(source)
                    if checkpoint is None:
                        continue
                    elapsed_seconds = (now_utc - checkpoint).total_seconds()
                    elapsed_days = max(0, int(elapsed_seconds / 86400))
                    recovery_days = max(
                        recovery_days,
                        elapsed_days + self.SCAN_RECOVERY_OVERLAP_DAYS,
                    )

            conn.execute(
                """
                UPDATE daily_runs
                SET scan_started_at = ?, scan_days = ?, scanned_sources_json = ?
                WHERE run_id = ?
                """,
                (
                    scan_started_at,
                    recovery_days,
                    json.dumps(normalized_sources, ensure_ascii=False),
                    run_id,
                ),
            )
        return recovery_days

    @staticmethod
    def _scan_sources_from_run(conn, run_id: str) -> list[str]:
        row = conn.execute(
            "SELECT scanned_sources_json FROM daily_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None or not row["scanned_sources_json"]:
            return []
        try:
            sources = json.loads(row["scanned_sources_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(sources, list):
            return []
        return sorted(
            {str(source).strip().lower() for source in sources if str(source).strip()}
        )

    @staticmethod
    def _scan_started_at_from_run(conn, run_id: str, fallback: str) -> str:
        row = conn.execute(
            "SELECT scan_started_at, started_at FROM daily_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return fallback
        return row["scan_started_at"] or row["started_at"] or fallback

    def _advance_scan_watermarks(self, conn, run_id: str, now: str) -> None:
        """Advance all planned source checkpoints inside a successful commit."""
        sources = self._scan_sources_from_run(conn, run_id)
        if not sources:
            return
        scan_started_at = self._scan_started_at_from_run(conn, run_id, now)
        for source in sources:
            conn.execute(
                """
                INSERT INTO daily_scan_watermarks(
                    source, successful_scan_started_at, run_id, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    successful_scan_started_at = excluded.successful_scan_started_at,
                    run_id = excluded.run_id,
                    updated_at = excluded.updated_at
                """,
                (source, scan_started_at, run_id, now),
            )

    def get_scan_watermark(self, source: str) -> Optional[sqlite3.Row]:
        """Return one source scan checkpoint for diagnostics and tests."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM daily_scan_watermarks WHERE source = ?", (source,)
            ).fetchone()

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
            self._advance_scan_watermarks(conn, run_id, now)

    def finalize_report_delivery(
        self,
        run_id: str,
        report_paths: Dict[str, Any],
        delivered_papers_by_source: Dict[str, list[Dict[str, Any]]],
        notification_entries: Optional[list[Dict[str, Any]]] = None,
        maintenance_entries: Optional[list[Dict[str, Any]]] = None,
    ) -> None:
        """Atomically record report delivery and all follow-up outbox rows.

        A report is considered delivered only after every included paper has
        completed its required analysis.  The same transaction records paper
        delivery, completes the run, queues one notification per channel, and
        queues maintenance work such as the post-report WebDAV upload.  This
        removes crash windows where a paper was hidden from future scans but a
        required follow-up task had not yet been persisted.
        """
        now = datetime.now().isoformat()
        entries = notification_entries or []
        maintenance = maintenance_entries or []
        normalized_paths = {key: str(value) for key, value in report_paths.items()}

        with self._lock, self._connect() as conn:
            run = conn.execute(
                "SELECT run_id FROM daily_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"daily run does not exist: {run_id}")

            for source, papers in delivered_papers_by_source.items():
                report_path = normalized_paths.get(source) or normalized_paths.get(
                    f"{source}_html"
                )
                for paper_info in papers:
                    paper = paper_info.get("paper_metadata")
                    paper_id = paper_info.get("paper_id")
                    if paper is None or not paper_id:
                        raise ValueError(f"无法记录缺少元数据的日报论文: {source}:{paper_id}")

                    record = conn.execute(
                        """
                        SELECT score_status, translation_status, analysis_status,
                               score_json, abstract_cn, paper_json
                        FROM daily_papers
                        WHERE source = ? AND paper_id = ?
                        """,
                        (source, paper_id),
                    ).fetchone()
                    if record is None:
                        raise RuntimeError(f"日报论文尚未持久化: {source}:{paper_id}")

                    if record["score_status"] != "succeeded" or not record["score_json"]:
                        raise RuntimeError(f"评分尚未完成，不能交付日报: {source}:{paper_id}")
                    if paper.abstract and paper.abstract.strip() and (
                        record["translation_status"] != "succeeded"
                        or not (record["abstract_cn"] or "").strip()
                    ):
                        raise RuntimeError(f"摘要翻译尚未完成，不能交付日报: {source}:{paper_id}")

                    requires_analysis = bool(
                        paper_info.get("requires_analysis", False)
                    )
                    if requires_analysis and record["analysis_status"] != "succeeded":
                        raise RuntimeError(
                            f"深度分析尚未完成，不能交付日报: {source}:{paper_id}"
                        )

                    if not requires_analysis:
                        conn.execute(
                            """
                            UPDATE daily_papers
                            SET analysis_status = 'not_required'
                            WHERE source = ? AND paper_id = ?
                              AND analysis_status != 'succeeded'
                            """,
                            (source, paper_id),
                        )

                    conn.execute(
                        """
                        INSERT INTO paper_deliveries(
                            run_id, source, paper_id, canonical_id, version,
                            report_path, delivered_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(run_id, source, paper_id) DO UPDATE SET
                            report_path = excluded.report_path
                        """,
                        (
                            run_id,
                            source,
                            paper_id,
                            paper.canonical_id or paper.paper_id,
                            paper.version or 0,
                            report_path,
                            now,
                        ),
                    )
                    conn.execute(
                        """
                        UPDATE daily_papers
                        SET run_id = ?, completed_at = COALESCE(completed_at, ?), last_error = NULL
                        WHERE source = ? AND paper_id = ?
                        """,
                        (run_id, now, source, paper_id),
                    )

            for entry in entries:
                try:
                    event_type = entry["event_type"]
                    channel = entry["channel"]
                    payload = entry["payload"]
                except KeyError as exc:
                    raise ValueError(f"无效通知 outbox 条目: {entry!r}") from exc
                conn.execute(
                    """
                    INSERT INTO notification_outbox(
                        run_id, event_type, channel, payload_json, status,
                        attempt_count, next_attempt_at, created_at
                    )
                    VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)
                    ON CONFLICT(run_id, event_type, channel) DO NOTHING
                    """,
                    (
                        run_id,
                        event_type,
                        channel,
                        json.dumps(payload, ensure_ascii=False),
                        now,
                        now,
                    ),
                )

            for entry in maintenance:
                try:
                    task_key = entry["task_key"]
                    payload = entry["payload"]
                except KeyError as exc:
                    raise ValueError(f"无效维护 outbox 条目: {entry!r}") from exc
                if not isinstance(task_key, str) or not task_key.strip():
                    raise ValueError(f"维护 outbox task_key 无效: {task_key!r}")
                conn.execute(
                    """
                    INSERT INTO maintenance_outbox(
                        task_key, payload_json, status, attempt_count,
                        next_attempt_at, created_at
                    )
                    VALUES (?, ?, 'pending', 0, ?, ?)
                    ON CONFLICT(task_key) DO NOTHING
                    """,
                    (task_key, json.dumps(payload, ensure_ascii=False), now, now),
                )

            conn.execute(
                """
                UPDATE daily_runs
                SET completed_at = ?, status = 'completed', error = NULL, report_paths_json = ?
                WHERE run_id = ?
                """,
                (now, json.dumps(normalized_paths, ensure_ascii=False), run_id),
            )
            self._advance_scan_watermarks(conn, run_id, now)

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

    # ------------------------------------------------------------------
    # Notification outbox
    # ------------------------------------------------------------------

    def enqueue_notification(
        self,
        run_id: str,
        event_type: str,
        channel: str,
        payload: Dict[str, Any],
    ) -> bool:
        """Persist one channel delivery request without overwriting an existing one.

        The unique key provides idempotence when the process restarts between
        report completion and notification dispatch.
        """
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO notification_outbox(
                    run_id, event_type, channel, payload_json, status,
                    attempt_count, next_attempt_at, created_at
                )
                VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)
                ON CONFLICT(run_id, event_type, channel) DO NOTHING
                """,
                (
                    run_id,
                    event_type,
                    channel,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            return cursor.rowcount == 1

    def claim_due_notifications(
        self,
        event_type: Optional[str] = None,
        limit: int = 100,
        stale_claim_seconds: int = 900,
    ) -> list[sqlite3.Row]:
        """Claim due outbox rows for one sender process.

        Claims protect against duplicate concurrent delivery.  A process crash can
        leave a row in ``sending``; the next run safely recovers an old claim.
        External notification protocols cannot guarantee exactly-once delivery
        across a crash after the remote side accepted a request, so this gives
        durable at-least-once delivery with a visible attempt history.
        """
        now_dt = datetime.now()
        now = now_dt.isoformat()
        stale_before = (now_dt - timedelta(seconds=max(1, stale_claim_seconds))).isoformat()
        max_rows = max(1, int(limit))
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE notification_outbox
                SET status = 'pending', claimed_at = NULL, next_attempt_at = ?
                WHERE status = 'sending' AND claimed_at IS NOT NULL AND claimed_at <= ?
                """,
                (now, stale_before),
            )

            clauses = ["status = 'pending'", "next_attempt_at <= ?"]
            params: list[Any] = [now]
            if event_type is not None:
                clauses.append("event_type = ?")
                params.append(event_type)
            query = (
                "SELECT outbox_id FROM notification_outbox WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at ASC, outbox_id ASC LIMIT ?"
            )
            params.append(max_rows)
            outbox_ids = [row["outbox_id"] for row in conn.execute(query, params).fetchall()]
            claimed = []
            for outbox_id in outbox_ids:
                cursor = conn.execute(
                    """
                    UPDATE notification_outbox
                    SET status = 'sending', claimed_at = ?, attempt_count = attempt_count + 1
                    WHERE outbox_id = ? AND status = 'pending'
                    """,
                    (now, outbox_id),
                )
                if cursor.rowcount:
                    claimed.append(
                        conn.execute(
                            "SELECT * FROM notification_outbox WHERE outbox_id = ?", (outbox_id,)
                        ).fetchone()
                    )
            return claimed

    def increment_notification_attempt(self, outbox_id: int) -> int:
        """Record an additional immediate delivery attempt for a claimed row."""
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE notification_outbox
                SET attempt_count = attempt_count + 1, claimed_at = ?
                WHERE outbox_id = ? AND status = 'sending'
                """,
                (now, outbox_id),
            )
            row = conn.execute(
                "SELECT attempt_count FROM notification_outbox WHERE outbox_id = ?", (outbox_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"notification outbox row does not exist: {outbox_id}")
            return row["attempt_count"]

    def mark_notification_sent(self, outbox_id: int) -> None:
        """Finalize a successful external notification delivery."""
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE notification_outbox
                SET status = 'sent', sent_at = ?, claimed_at = NULL,
                    last_error = NULL
                WHERE outbox_id = ?
                """,
                (now, outbox_id),
            )

    def reschedule_notification(
        self, outbox_id: int, error: str, retry_after_seconds: int
    ) -> None:
        """Release a failed claim for a later retry while retaining its payload."""
        now_dt = datetime.now()
        next_attempt = (now_dt + timedelta(seconds=max(1, retry_after_seconds))).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE notification_outbox
                SET status = 'pending', claimed_at = NULL, next_attempt_at = ?, last_error = ?
                WHERE outbox_id = ?
                """,
                (next_attempt, error[:4000], outbox_id),
            )

    def get_notification_outbox(self, outbox_id: int) -> Optional[sqlite3.Row]:
        """Return an outbox row for diagnostics and tests."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM notification_outbox WHERE outbox_id = ?", (outbox_id,)
            ).fetchone()

    def get_pending_notification_count(self, event_type: Optional[str] = None) -> int:
        """Return the number of notification rows that still need delivery."""
        with self._connect() as conn:
            if event_type is None:
                row = conn.execute(
                    "SELECT count(*) AS count FROM notification_outbox WHERE status != 'sent'"
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT count(*) AS count FROM notification_outbox
                    WHERE status != 'sent' AND event_type = ?
                    """,
                    (event_type,),
                ).fetchone()
            return int(row["count"])

    # ------------------------------------------------------------------
    # Durable post-report maintenance tasks (currently WebDAV upload)
    # ------------------------------------------------------------------

    def enqueue_maintenance_task(self, task_key: str, payload: Dict[str, Any]) -> bool:
        """Persist an idempotent post-report task for restart-safe execution."""
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO maintenance_outbox(
                    task_key, payload_json, status, attempt_count,
                    next_attempt_at, created_at
                )
                VALUES (?, ?, 'pending', 0, ?, ?)
                ON CONFLICT(task_key) DO NOTHING
                """,
                (task_key, json.dumps(payload, ensure_ascii=False), now, now),
            )
            return cursor.rowcount == 1

    def claim_due_maintenance_tasks(
        self, prefix: Optional[str] = None, limit: int = 20, stale_claim_seconds: int = 900
    ) -> list[sqlite3.Row]:
        """Claim due maintenance tasks; stale in-progress claims are recovered."""
        now_dt = datetime.now()
        now = now_dt.isoformat()
        stale_before = (now_dt - timedelta(seconds=max(1, stale_claim_seconds))).isoformat()
        max_rows = max(1, int(limit))
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE maintenance_outbox
                SET status = 'pending', claimed_at = NULL, next_attempt_at = ?
                WHERE status = 'running' AND claimed_at IS NOT NULL AND claimed_at <= ?
                """,
                (now, stale_before),
            )
            clauses = ["status = 'pending'", "next_attempt_at <= ?"]
            params: list[Any] = [now]
            if prefix is not None:
                clauses.append("task_key LIKE ?")
                params.append(f"{prefix}%")
            params.append(max_rows)
            query = (
                "SELECT task_key FROM maintenance_outbox WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at ASC, task_key ASC LIMIT ?"
            )
            task_keys = [row["task_key"] for row in conn.execute(query, params).fetchall()]
            claimed = []
            for task_key in task_keys:
                cursor = conn.execute(
                    """
                    UPDATE maintenance_outbox
                    SET status = 'running', claimed_at = ?, attempt_count = attempt_count + 1
                    WHERE task_key = ? AND status = 'pending'
                    """,
                    (now, task_key),
                )
                if cursor.rowcount:
                    claimed.append(
                        conn.execute(
                            "SELECT * FROM maintenance_outbox WHERE task_key = ?", (task_key,)
                        ).fetchone()
                    )
            return claimed

    def mark_maintenance_task_completed(self, task_key: str) -> None:
        """Mark a claimed maintenance task complete."""
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE maintenance_outbox
                SET status = 'completed', completed_at = ?, claimed_at = NULL, last_error = NULL
                WHERE task_key = ?
                """,
                (now, task_key),
            )

    def reschedule_maintenance_task(
        self, task_key: str, error: str, retry_after_seconds: int
    ) -> None:
        """Preserve a failed maintenance task for a later attempt."""
        next_attempt = (
            datetime.now() + timedelta(seconds=max(1, retry_after_seconds))
        ).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE maintenance_outbox
                SET status = 'pending', claimed_at = NULL, next_attempt_at = ?, last_error = ?
                WHERE task_key = ?
                """,
                (next_attempt, error[:4000], task_key),
            )

    def get_maintenance_task(self, task_key: str) -> Optional[sqlite3.Row]:
        """Return maintenance task state for diagnostics/tests."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM maintenance_outbox WHERE task_key = ?", (task_key,)
            ).fetchone()

    def get_paper_record(self, source: str, paper_id: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM daily_papers WHERE source = ? AND paper_id = ?",
                (source, paper_id),
            ).fetchone()

    def is_paper_delivered(self, source: str, paper_id: str) -> bool:
        """Return whether this exact paper version has entered a completed daily report."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM paper_deliveries
                WHERE source = ? AND paper_id = ?
                LIMIT 1
                """,
                (source, paper_id),
            ).fetchone()
            if row is not None:
                return True
            # Backward-compatible fallback for pre-delivery-table databases.
            row = conn.execute(
                """
                SELECT 1 FROM daily_papers
                WHERE source = ? AND paper_id = ? AND completed_at IS NOT NULL
                LIMIT 1
                """,
                (source, paper_id),
            ).fetchone()
            return row is not None

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
            delivered = conn.execute(
                """
                SELECT daily_papers.*, paper_deliveries.delivered_at
                FROM paper_deliveries
                JOIN daily_papers
                  ON daily_papers.source = paper_deliveries.source
                 AND daily_papers.paper_id = paper_deliveries.paper_id
                WHERE paper_deliveries.source = ?
                  AND paper_deliveries.canonical_id = ?
                  AND paper_deliveries.version < ?
                ORDER BY paper_deliveries.version DESC, paper_deliveries.delivered_at DESC
                LIMIT 1
                """,
                (source, paper.canonical_id, paper.version),
            ).fetchone()
            if delivered is not None:
                return delivered
            # Fallback for databases produced before paper_deliveries existed.
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

    @classmethod
    def _restore_optional_enrichment(
        cls, paper: "PaperMetadata", persisted_paper_json: Optional[str]
    ) -> None:
        """Fill absent best-effort enrichment fields from a prior attempt.

        Semantic Scholar is intentionally non-blocking for a daily run.  A
        transient 429/network failure on a retry must therefore not erase a
        TLDR or arXiv PDF URL that was already obtained and persisted for the
        exact same paper identity.  Invalid legacy JSON is ignored so it
        cannot turn a recovery attempt into a failed report.
        """
        if not persisted_paper_json:
            return
        try:
            persisted = json.loads(persisted_paper_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        if not isinstance(persisted, dict):
            return

        for field in cls._OPTIONAL_ENRICHMENT_FIELDS:
            current_value = getattr(paper, field, None)
            persisted_value = persisted.get(field)
            if not current_value and persisted_value:
                setattr(paper, field, persisted_value)

    def upsert_paper_seen(self, run_id: str, source: str, paper: "PaperMetadata"):
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT paper_json FROM daily_papers WHERE source = ? AND paper_id = ?",
                (source, paper.paper_id),
            ).fetchone()
            if existing is not None:
                self._restore_optional_enrichment(paper, existing["paper_json"])

            paper_json = json.dumps(paper.to_dict(), ensure_ascii=False)
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

        # Callers can hydrate a score directly (without having first called
        # upsert_paper_seen in this process), so restore the optional fields
        # here as well.  This keeps report rendering and deep-analysis retry
        # decisions stable across process restarts.
        self._restore_optional_enrichment(paper, record["paper_json"])

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
