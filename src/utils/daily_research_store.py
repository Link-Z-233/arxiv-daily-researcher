"""Authoritative SQLite history and resumable state for daily research."""

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from agents.analysis_agent import Stage2Response
    from sources.base_source import PaperMetadata


# Learning-signal strengths for the learned preference scoring mode.  An
# explicit like/dislike dominates; a legacy v1 pass contributes a mild positive
# nudge so the original scoring keeps shaping the learned library too.
PREFERENCE_SIGNALS = {"like": 1.0, "dislike": -1.0, "none": 0.0}
V1_PASS_SIGNAL_STRENGTH = 0.25


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

    @staticmethod
    def _paper_identity_or_none():
        """Migration-time identity helper; None in the thin WebUI image.

        The Streamlit image deliberately ships no paper-source modules.  The
        canonical-id backfill below then runs on the worker's next connect
        instead — the schema columns are still added here, so nothing
        diverges.
        """
        try:
            from sources.base_source import paper_identity

            return paper_identity
        except ImportError:
            return None

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
                    score_audit_json TEXT,
                    abstract_cn TEXT,
                    analysis_json TEXT,
                    scored_at TEXT,
                    translated_at TEXT,
                    analyzed_at TEXT,
                    score_input_fingerprint TEXT,
                    translation_input_fingerprint TEXT,
                    analysis_input_fingerprint TEXT,
                    completed_at TEXT,
                    last_error TEXT,
                    PRIMARY KEY (source, paper_id)
                )
                """
            )
            self._migrate_paper_identity(conn)
            self._migrate_stage_state(conn)
            self._migrate_stage_fingerprints(conn)
            self._migrate_score_audit_state(conn)
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
            self._migrate_delivery_identity(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_paper_deliveries_identity "
                "ON paper_deliveries(source, canonical_id, version)"
            )
            self._migrate_delivery_exact_version_constraint(conn)

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
            # Scan receipts complement the checkpoint; they never replace it.
            # A watermark answers "what interval is safe to recover from?",
            # while this table answers "what did this run actually query?".
            # Keep failed receipts too: they are exactly the evidence needed to
            # distinguish a genuinely quiet day from an incomplete scan.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_scan_receipts (
                    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(run_id, source)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_scan_receipts_run "
                "ON daily_scan_receipts(run_id, receipt_id)"
            )
            # Small key/value scratch state for cross-run decisions such as
            # "this remote version was already announced". Values are opaque
            # strings owned by the caller; nothing here is ever deleted.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # LLM token usage per run and model, persisted when a run ends.
            # Trend runs use a synthetic run id and mode='trend_research'.
            # Rows are append-only history; nothing is ever pruned.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_token_usage (
                    run_id TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'daily_research',
                    model TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, model)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_run_token_usage_recorded "
                "ON run_token_usage(recorded_at)"
            )
            # Reader-owned paper preferences (like/dislike). Rows are never
            # deleted: clearing a preference writes 'none' so the history of
            # what was marked stays intact. Snapshots of title/authors keep
            # each row self-contained even if the paper is later re-queued.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_preferences (
                    source TEXT NOT NULL,
                    paper_id TEXT NOT NULL,
                    canonical_id TEXT,
                    version INTEGER,
                    preference TEXT NOT NULL
                        CHECK (preference IN ('like', 'dislike', 'none')),
                    title TEXT NOT NULL,
                    authors_json TEXT NOT NULL DEFAULT '[]',
                    categories_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source, paper_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_paper_preferences_updated "
                "ON paper_preferences(updated_at)"
            )

            # Learned-preference evidence. Rows are upserted in place (never
            # deleted), so a changed opinion updates its own contribution and
            # the aggregate weight evolves with every new signal.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS preference_learning_signals (
                    source TEXT NOT NULL,
                    paper_id TEXT NOT NULL,
                    term TEXT NOT NULL,
                    term_type TEXT NOT NULL
                        CHECK (term_type IN ('keyword', 'author')),
                    signal_kind TEXT NOT NULL
                        CHECK (signal_kind IN ('preference', 'v1_pass')),
                    signal REAL NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (source, paper_id, term, term_type, signal_kind)
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

        paper_identity = DailyResearchStore._paper_identity_or_none()
        if paper_identity is None:
            return

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

    @staticmethod
    def _migrate_stage_fingerprints(conn):
        """Add stage input keys used to invalidate stale incomplete LLM work."""
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(daily_papers)").fetchall()
        }
        additions = {
            "score_input_fingerprint": "TEXT",
            "translation_input_fingerprint": "TEXT",
            "analysis_input_fingerprint": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE daily_papers ADD COLUMN {name} {definition}")

    @staticmethod
    def _migrate_score_audit_state(conn):
        """Add the non-secret score evidence column to existing databases."""
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(daily_papers)").fetchall()
        }
        if "score_audit_json" not in columns:
            conn.execute("ALTER TABLE daily_papers ADD COLUMN score_audit_json TEXT")

    @staticmethod
    def _migrate_delivery_identity(conn):
        """Backfill identity fields for delivery ledgers created by older releases."""
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(paper_deliveries)").fetchall()
        }
        additions = {
            "canonical_id": "TEXT NOT NULL DEFAULT ''",
            "version": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE paper_deliveries ADD COLUMN {name} {definition}")

        paper_identity = DailyResearchStore._paper_identity_or_none()
        if paper_identity is None:
            return

        rows = conn.execute(
            "SELECT delivery_id, source, paper_id, canonical_id, version FROM paper_deliveries"
        ).fetchall()
        for row in rows:
            canonical_id, version = paper_identity(row["source"], row["paper_id"])
            desired_version = version if version is not None else 0
            if row["canonical_id"] != canonical_id or row["version"] != desired_version:
                conn.execute(
                    "UPDATE paper_deliveries SET canonical_id = ?, version = ? WHERE delivery_id = ?",
                    (canonical_id, desired_version, row["delivery_id"]),
                )

    @staticmethod
    def _migrate_delivery_exact_version_constraint(conn):
        """Enforce one completed delivery for each exact source/version.

        Old databases used a run-scoped unique constraint, which permitted an
        accidental second report for the same exact paper if a future caller
        bypassed the normal pre-filter.  A unique index is compatible with the
        existing table and is safer than a table rebuild.  In the unlikely
        event an old DB already contains duplicates, retain its earliest
        delivery as the authoritative one before creating the index.
        """
        duplicates = conn.execute(
            """
            SELECT source, canonical_id, version, MIN(delivery_id) AS keep_delivery_id
            FROM paper_deliveries
            GROUP BY source, canonical_id, version
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        for row in duplicates:
            conn.execute(
                "DELETE FROM paper_deliveries "
                "WHERE source = ? AND canonical_id = ? AND version = ? AND delivery_id != ?",
                (
                    row["source"],
                    row["canonical_id"],
                    row["version"],
                    row["keep_delivery_id"],
                ),
            )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_deliveries_exact_version "
            "ON paper_deliveries(source, canonical_id, version)"
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
                "SELECT run_id, status FROM daily_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"daily run does not exist: {run_id}")
            if run["status"] != "running":
                raise RuntimeError(
                    f"只能为 running 运行准备扫描计划: {run_id} ({run['status']})"
                )

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
                checkpoints = {}
                for row in rows:
                    raw_checkpoint = row["successful_scan_started_at"]
                    checkpoint = self._parse_checkpoint_timestamp(raw_checkpoint)
                    if checkpoint is None:
                        raise RuntimeError(
                            "扫描水位线损坏，已停止本次运行以避免漏抓: "
                            f"{row['source']}: {raw_checkpoint!r}"
                        )
                    checkpoints[row["source"]] = checkpoint
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
            "SELECT scanned_sources_json, scan_days FROM daily_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return []
        if row["scanned_sources_json"] is None:
            if row["scan_days"] is not None:
                raise RuntimeError(f"日报运行扫描计划缺失: {run_id}")
            return []
        raw_sources = row["scanned_sources_json"]
        try:
            sources = json.loads(raw_sources)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"日报运行扫描计划损坏: {run_id}") from exc
        if not isinstance(sources, list) or any(
            not isinstance(source, str) or not source.strip() for source in sources
        ):
            raise RuntimeError(f"日报运行扫描计划格式无效: {run_id}")
        normalized = [source.strip().lower() for source in sources]
        if len(set(normalized)) != len(normalized):
            raise RuntimeError(f"日报运行扫描计划包含重复数据源: {run_id}")
        return sorted(normalized)

    @staticmethod
    def _scan_started_at_from_run(conn, run_id: str, fallback: str) -> str:
        row = conn.execute(
            "SELECT scan_started_at, started_at FROM daily_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return fallback
        value = row["scan_started_at"] or row["started_at"] or fallback
        if DailyResearchStore._parse_checkpoint_timestamp(value) is None:
            raise RuntimeError(f"日报运行扫描开始时间损坏: {run_id}")
        return value

    @staticmethod
    def _require_successful_scan_receipts(conn, run_id: str, sources: list[str]) -> None:
        """Refuse a checkpoint when any planned source lacks terminal success proof.

        A source fetch can be fail-closed and still leave an observability
        hole if a later refactor accidentally omits its receipt callback.
        Checkpoint advancement is the last irreversible-looking transition in
        a run, so require one ``succeeded`` receipt for every source that was
        recorded in ``prepare_scan``.  The caller's transaction rolls back all
        delivery/completion writes if this invariant is not satisfied.
        """
        if not sources:
            return
        placeholders = ", ".join("?" for _ in sources)
        rows = conn.execute(
            "SELECT source, status FROM daily_scan_receipts WHERE run_id = ? "
            f"AND source IN ({placeholders})",
            [run_id, *sources],
        ).fetchall()
        statuses = {row["source"]: row["status"] for row in rows}
        missing = [source for source in sources if source not in statuses]
        unsuccessful = [
            source for source in sources if source in statuses and statuses[source] != "succeeded"
        ]
        if missing or unsuccessful:
            details = []
            if missing:
                details.append(f"缺少收据: {', '.join(missing)}")
            if unsuccessful:
                details.append(f"未成功: {', '.join(unsuccessful)}")
            raise RuntimeError(
                "扫描收据不完整，拒绝推进来源水位线: " + "; ".join(details)
            )

    def _advance_scan_watermarks(self, conn, run_id: str, now: str) -> None:
        """Advance all planned source checkpoints inside a successful commit."""
        sources = self._scan_sources_from_run(conn, run_id)
        if not sources:
            return
        self._require_successful_scan_receipts(conn, run_id, sources)
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

    @staticmethod
    def _validate_scan_receipt(run_id: str, source: str, receipt: Dict[str, Any]) -> Dict[str, Any]:
        """Validate the small public receipt schema before persisting it.

        The source owns detailed fields, but the store must reject a receipt
        that is accidentally attached to the wrong run/source or lacks a
        terminal status.  This keeps the audit trail useful even after future
        source implementations are added.
        """
        if not isinstance(receipt, dict):
            raise ValueError("扫描收据必须是 JSON 对象")
        normalized_source = str(source or "").strip().lower()
        if not normalized_source:
            raise ValueError("扫描收据缺少数据源")
        receipt_source = str(receipt.get("source") or "").strip().lower()
        if receipt_source != normalized_source:
            raise ValueError(
                f"扫描收据来源不匹配: expected {normalized_source}, got {receipt_source or '<empty>'}"
            )
        status = receipt.get("status")
        if status not in {"succeeded", "failed"}:
            raise ValueError(f"扫描收据状态无效: {status!r}")
        if not isinstance(receipt.get("scanned_at"), str) or not receipt["scanned_at"].strip():
            raise ValueError("扫描收据缺少 scanned_at")
        if not isinstance(receipt.get("domain_receipts", []), list):
            raise ValueError("扫描收据 domain_receipts 必须是列表")
        # A JSON round trip both ensures serialisability and detaches callers'
        # mutable dicts from the durable record.
        try:
            encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
            decoded = json.loads(encoded)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"扫描收据不可 JSON 序列化: {exc}") from exc
        return decoded

    def record_scan_receipt(self, run_id: str, source: str, receipt: Dict[str, Any]) -> None:
        """Persist one source receipt for a daily run, replacing retry details.

        A source may retry a domain internally.  Its callback is invoked only
        after the scan reaches a terminal source-level result, so one
        ``(run_id, source)`` row represents the final evidence for that run.
        Failed receipts intentionally remain durable; ``fail_run`` does not
        erase them.
        """
        normalized_source = str(source or "").strip().lower()
        payload = self._validate_scan_receipt(run_id, normalized_source, receipt)
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            run = conn.execute(
                "SELECT run_id FROM daily_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"daily run does not exist: {run_id}")
            conn.execute(
                """
                INSERT INTO daily_scan_receipts(
                    run_id, source, status, receipt_json, recorded_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, source) DO UPDATE SET
                    status = excluded.status,
                    receipt_json = excluded.receipt_json,
                    recorded_at = excluded.recorded_at
                """,
                (
                    run_id,
                    normalized_source,
                    payload["status"],
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )

    def get_scan_receipts(self, run_id: str) -> list[Dict[str, Any]]:
        """Return parsed source receipts in stable source order for diagnostics/UI."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source, status, receipt_json, recorded_at
                FROM daily_scan_receipts
                WHERE run_id = ?
                ORDER BY source ASC
                """,
                (run_id,),
            ).fetchall()
        receipts = []
        for row in rows:
            try:
                receipt = json.loads(row["receipt_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                # A corrupt legacy row must remain visible rather than silently
                # disappearing from a diagnostic screen.
                receipt = {"source": row["source"], "status": "corrupt"}
            if not isinstance(receipt, dict):
                receipt = {"source": row["source"], "status": "corrupt"}
            receipt["source"] = row["source"]
            receipt["status"] = row["status"]
            receipt["recorded_at"] = row["recorded_at"]
            receipts.append(receipt)
        return receipts

    def get_app_state(self, key: str) -> Optional[str]:
        """Return a persisted scratch value, or None when the key is unset."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_state WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_app_state(self, key: str, value: str) -> None:
        """Persist a scratch value; existing values are overwritten, never dropped."""
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO app_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )

    def record_token_usage(
        self,
        run_id: str,
        by_model: Dict[str, Dict[str, int]],
        *,
        mode: str = "daily_research",
    ) -> None:
        """Persist one run's per-model token usage.

        ``by_model`` follows TokenCounter.get_summary(): ``{model: {"prompt":
        int, "completion": int, "total": int}}``.  Recording again for the
        same run replaces its rows, keeping interrupted-then-retried runs
        from double counting.
        """
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM run_token_usage WHERE run_id = ?", (run_id,)
            )
            for model, usage in (by_model or {}).items():
                prompt = int(usage.get("prompt", 0) or 0)
                completion = int(usage.get("completion", 0) or 0)
                conn.execute(
                    """
                    INSERT INTO run_token_usage (
                        run_id, mode, model, prompt_tokens,
                        completion_tokens, total_tokens, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        mode,
                        str(model),
                        prompt,
                        completion,
                        prompt + completion,
                        now,
                    ),
                )

    def get_daily_token_totals(self, days: Optional[int] = None) -> list[Dict[str, Any]]:
        """Aggregate persisted token usage by calendar day (oldest first).

        ``days`` limits the window to the most recent N days; None returns
        the full history.  Each row: ``{"date", "prompt", "completion",
        "total", "runs"}``.
        """
        query = (
            "SELECT substr(recorded_at, 1, 10) AS day, "
            "SUM(prompt_tokens) AS prompt, "
            "SUM(completion_tokens) AS completion, "
            "SUM(total_tokens) AS total, "
            "COUNT(DISTINCT run_id) AS runs "
            "FROM run_token_usage"
        )
        params: list[Any] = []
        if days is not None and days > 0:
            cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
            query += " WHERE substr(recorded_at, 1, 10) >= ?"
            params.append(cutoff)
        query += " GROUP BY day ORDER BY day"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "date": row["day"],
                "prompt": row["prompt"] or 0,
                "completion": row["completion"] or 0,
                "total": row["total"] or 0,
                "runs": row["runs"] or 0,
            }
            for row in rows
        ]

    def get_token_usage_by_model(self, days: Optional[int] = None) -> list[Dict[str, Any]]:
        """Aggregate persisted token usage by model over the window."""
        query = (
            "SELECT model, SUM(prompt_tokens) AS prompt, "
            "SUM(completion_tokens) AS completion, "
            "SUM(total_tokens) AS total "
            "FROM run_token_usage"
        )
        params: list[Any] = []
        if days is not None and days > 0:
            cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
            query += " WHERE substr(recorded_at, 1, 10) >= ?"
            params.append(cutoff)
        query += " GROUP BY model ORDER BY total DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "model": row["model"],
                "prompt": row["prompt"] or 0,
                "completion": row["completion"] or 0,
                "total": row["total"] or 0,
            }
            for row in rows
        ]

    # ==================== Paper preferences ====================

    def set_paper_preference(
        self,
        source: str,
        paper_id: str,
        *,
        preference: str,
        title: str,
        canonical_id: Optional[str] = None,
        version: Optional[int] = None,
        authors: Optional[list[str]] = None,
        categories: Optional[list[str]] = None,
    ) -> None:
        """Upsert one reader preference; clearing writes 'none', never a delete."""
        if preference not in ("like", "dislike", "none"):
            raise ValueError(f"invalid preference: {preference!r}")
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO paper_preferences (
                    source, paper_id, canonical_id, version, preference,
                    title, authors_json, categories_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, paper_id) DO UPDATE SET
                    canonical_id = excluded.canonical_id,
                    version = excluded.version,
                    preference = excluded.preference,
                    title = excluded.title,
                    authors_json = excluded.authors_json,
                    categories_json = excluded.categories_json,
                    updated_at = excluded.updated_at
                """,
                (
                    source,
                    paper_id,
                    canonical_id,
                    version,
                    preference,
                    title,
                    json.dumps(list(authors or []), ensure_ascii=False),
                    json.dumps(list(categories or []), ensure_ascii=False),
                    now,
                    now,
                ),
            )

            # Explicit preferences are the strongest learning signal. The
            # paper's own stored metadata supplies the terms, and clearing a
            # preference rewrites its signal to zero instead of deleting it.
            keyword_terms: list[str] = []
            author_terms = [name for name in (authors or []) if isinstance(name, str) and name.strip()]
            row = conn.execute(
                "SELECT paper_json, score_json FROM daily_papers "
                "WHERE source = ? AND paper_id = ?",
                (source, paper_id),
            ).fetchone()
            if row is not None:
                try:
                    metadata = json.loads(row["paper_json"]) if row["paper_json"] else {}
                except (TypeError, ValueError, json.JSONDecodeError):
                    metadata = {}
                if isinstance(metadata, dict):
                    author_terms = author_terms or [
                        name
                        for name in (metadata.get("authors") or [])
                        if isinstance(name, str) and name.strip()
                    ]
                try:
                    score = json.loads(row["score_json"]) if row["score_json"] else {}
                except (TypeError, ValueError, json.JSONDecodeError):
                    score = {}
                if isinstance(score, dict):
                    keyword_terms = [
                        keyword
                        for keyword in (score.get("extracted_keywords") or [])
                        if isinstance(keyword, str) and keyword.strip()
                    ]
            signal = PREFERENCE_SIGNALS.get(preference)
            if signal is not None:
                self._upsert_learning_signals(
                    conn,
                    source,
                    paper_id,
                    keyword_terms,
                    "keyword",
                    "preference",
                    signal,
                    now,
                )
                self._upsert_learning_signals(
                    conn,
                    source,
                    paper_id,
                    author_terms,
                    "author",
                    "preference",
                    signal,
                    now,
                )

    @staticmethod
    def _preference_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        def parse(raw: object) -> list[str]:
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else []
            except (TypeError, ValueError, json.JSONDecodeError):
                return []
            return [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []

        return {
            "source": row["source"],
            "paper_id": row["paper_id"],
            "canonical_id": row["canonical_id"],
            "version": row["version"],
            "preference": row["preference"],
            "title": row["title"],
            "authors": parse(row["authors_json"]),
            "categories": parse(row["categories_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_paper_preference(self, source: str, paper_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM paper_preferences WHERE source = ? AND paper_id = ?",
                (source, paper_id),
            ).fetchone()
        return self._preference_row_to_dict(row) if row else None

    def get_preference_map(self, papers: list[Dict[str, Any]]) -> Dict[Tuple[str, str], str]:
        """Batch lookup of preference strings for (source, paper_id) pairs."""
        result: Dict[Tuple[str, str], str] = {}
        with self._connect() as conn:
            for paper in papers:
                source = paper.get("source")
                paper_id = paper.get("paper_id")
                if not isinstance(source, str) or not isinstance(paper_id, str):
                    continue
                row = conn.execute(
                    "SELECT preference FROM paper_preferences "
                    "WHERE source = ? AND paper_id = ?",
                    (source, paper_id),
                ).fetchone()
                if row and row["preference"] != "none":
                    result[(source, paper_id)] = row["preference"]
        return result

    def list_preferences(
        self, *, preference: Optional[str] = None, limit: int = 100
    ) -> list[Dict[str, Any]]:
        """List marked papers newest-first; 'none' rows are skipped by default."""
        query = "SELECT * FROM paper_preferences"
        params: list[Any] = []
        if preference in ("like", "dislike"):
            query += " WHERE preference = ?"
            params.append(preference)
        elif preference != "all":
            query += " WHERE preference != 'none'"
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._preference_row_to_dict(row) for row in rows]

    def get_preference_counts(self) -> Dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT preference, COUNT(*) AS n FROM paper_preferences "
                "GROUP BY preference"
            ).fetchall()
        counts = {"like": 0, "dislike": 0, "none": 0}
        for row in rows:
            if row["preference"] in counts:
                counts[row["preference"]] = row["n"]
        return counts

    @staticmethod
    def _upsert_learning_signals(
        conn,
        source: str,
        paper_id: str,
        terms: list[str],
        term_type: str,
        signal_kind: str,
        signal: float,
        now: str,
    ) -> None:
        for term in terms:
            conn.execute(
                """
                INSERT INTO preference_learning_signals(
                    source, paper_id, term, term_type, signal_kind,
                    signal, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, paper_id, term, term_type, signal_kind)
                DO UPDATE SET
                    signal = excluded.signal,
                    recorded_at = excluded.recorded_at
                """,
                (source, paper_id, term, term_type, signal_kind, signal, now),
            )

    def record_learning_signals(
        self,
        source: str,
        paper_id: str,
        terms: list[str],
        term_type: str,
        signal_kind: str,
        signal: float,
    ) -> None:
        """Upsert one paper's learning signal for each given term."""
        if term_type not in ("keyword", "author"):
            raise ValueError(f"invalid term_type: {term_type!r}")
        if signal_kind not in ("preference", "v1_pass"):
            raise ValueError(f"invalid signal_kind: {signal_kind!r}")
        if isinstance(signal, bool) or not isinstance(signal, (int, float)):
            raise ValueError("signal 必须是数字")
        cleaned = [
            term.strip() for term in terms if isinstance(term, str) and term.strip()
        ]
        if not cleaned:
            return
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            self._upsert_learning_signals(
                conn, source, paper_id, cleaned, term_type, signal_kind,
                float(signal), now,
            )

    def get_learned_preference_terms(
        self, term_type: Optional[str] = None, limit: int = 200
    ) -> list[Dict[str, Any]]:
        """Aggregate the learned keyword/author library with net weights.

        Terms whose signals cancel out to (near) zero are skipped: they carry
        no usable preference either way.
        """
        bounded_limit = max(1, min(int(limit), 1000))
        query = (
            "SELECT term, term_type, SUM(signal) AS weight, COUNT(*) AS signals "
            "FROM preference_learning_signals "
        )
        params: list[Any] = []
        if term_type in ("keyword", "author"):
            query += "WHERE term_type = ? "
            params.append(term_type)
        query += (
            "GROUP BY term, term_type "
            "HAVING ABS(SUM(signal)) > 1e-9 "
            "ORDER BY ABS(SUM(signal)) DESC, term ASC LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(query, [*params, bounded_limit]).fetchall()
        return [
            {
                "term": row["term"],
                "term_type": row["term_type"],
                "weight": float(row["weight"]),
                "signals": int(row["signals"]),
            }
            for row in rows
        ]

    def aggregate_liked_preferences(self) -> Dict[str, list[Dict[str, Any]]]:
        """Deterministic top authors/categories among liked papers.

        Pure SQLite + Python counting; no LLM involved by design — the goal is
        a faithful mirror of what the reader marked, not a model's guess.
        """
        rows = self.list_preferences(preference="like", limit=100000)
        author_counts: Dict[str, int] = {}
        category_counts: Dict[str, int] = {}
        for row in rows:
            for author in row["authors"]:
                key = author.strip()
                if key:
                    author_counts[key] = author_counts.get(key, 0) + 1
            for category in row["categories"]:
                key = category.strip()
                if key:
                    category_counts[key] = category_counts.get(key, 0) + 1

        def ranked(counts: Dict[str, int]) -> list[Dict[str, Any]]:
            return [
                {"name": name, "count": count}
                for name, count in sorted(
                    counts.items(), key=lambda item: (-item[1], item[0])
                )
            ]

        return {"authors": ranked(author_counts), "categories": ranked(category_counts)}

    def liked_paper_urls(self) -> Dict[Tuple[str, str], str]:
        """URL lookup for liked papers, taken from their stored metadata."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT p.source, p.paper_id, d.paper_json FROM paper_preferences p "
                "LEFT JOIN daily_papers d "
                "ON d.source = p.source AND d.paper_id = p.paper_id "
                "WHERE p.preference = 'like'"
            ).fetchall()
        urls: Dict[Tuple[str, str], str] = {}
        for row in rows:
            try:
                metadata = json.loads(row["paper_json"]) if row["paper_json"] else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
            url = metadata.get("url") if isinstance(metadata, dict) else None
            if isinstance(url, str) and url.strip():
                urls[(row["source"], row["paper_id"])] = url.strip()
        return urls

    def aggregate_liked_keywords(self, limit: int = 200) -> list[Dict[str, Any]]:
        """Count extracted keywords across currently liked papers.

        Mirrors aggregate_liked_preferences: pure SQL + Python counting over
        the reader's own marks — no model inference involved.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT d.score_json FROM paper_preferences p "
                "JOIN daily_papers d "
                "ON d.source = p.source AND d.paper_id = p.paper_id "
                "WHERE p.preference = 'like'"
            ).fetchall()
        counts: Dict[str, int] = {}
        for row in rows:
            try:
                score = json.loads(row["score_json"]) if row["score_json"] else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                score = {}
            if not isinstance(score, dict):
                continue
            for keyword in score.get("extracted_keywords") or []:
                if isinstance(keyword, str) and keyword.strip():
                    key = keyword.strip()
                    counts[key] = counts.get(key, 0) + 1
        ranked = [
            {"keyword": keyword, "count": count}
            for keyword, count in sorted(
                counts.items(), key=lambda item: (-item[1], item[0])
            )
        ]
        return ranked[: max(1, int(limit))]

    def count_pending_papers(self) -> Dict[str, int]:
        """Global durable-queue depth: uncompleted papers, split by retry need.

        Pure SQL with no paper-source imports so the thin WebUI image can
        surface the real backlog any time.
        """
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM daily_papers WHERE completed_at IS NULL"
            ).fetchone()["n"] or 0
            failed = conn.execute(
                "SELECT COUNT(*) AS n FROM daily_papers WHERE completed_at IS NULL "
                "AND (score_status = 'failed' OR translation_status = 'failed' "
                "OR analysis_status = 'failed')"
            ).fetchone()["n"] or 0
        return {"total": total, "failed_retry": failed, "fresh": total - failed}

    def list_delivered_papers(self, limit: int = 50) -> list[Dict[str, Any]]:
        """Recently completed papers (newest first) for preference marking."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT source, paper_id, canonical_id, version, paper_json, "
                "completed_at FROM daily_papers "
                "WHERE completed_at IS NOT NULL "
                "ORDER BY completed_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        papers = []
        for row in rows:
            try:
                metadata = json.loads(row["paper_json"]) if row["paper_json"] else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            papers.append(
                {
                    "source": row["source"],
                    "paper_id": row["paper_id"],
                    "canonical_id": row["canonical_id"],
                    "version": row["version"],
                    "title": str(metadata.get("title") or row["paper_id"]),
                    "authors": [
                        a for a in (metadata.get("authors") or []) if isinstance(a, str)
                    ],
                    "categories": [
                        c
                        for c in (metadata.get("categories") or [])
                        if isinstance(c, str)
                    ],
                    "completed_at": row["completed_at"],
                }
            )
        return papers

    @staticmethod
    def _like_pattern(query: str) -> str:
        """Escape LIKE wildcards so user input matches literally."""
        escaped = (
            query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        return f"%{escaped}%"

    def search_papers(
        self,
        *,
        query: str = "",
        source: Optional[str] = None,
        liked_only: bool = False,
        min_score: Optional[float] = None,
        completed_from: Optional[str] = None,
        completed_to: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Full-archive metadata search over completed papers.

        The query matches literally (LIKE wildcards escaped) against the
        paper JSON (title, authors, abstract) and the stored score JSON
        (TLDR, extracted keywords). Data is never deleted, so this is the
        primary way back into old reports as the archive grows.
        """
        conditions = ["dp.completed_at IS NOT NULL"]
        params: list[Any] = []

        stripped = (query or "").strip()
        if stripped:
            pattern = self._like_pattern(stripped)
            conditions.append(
                "(dp.paper_json LIKE ? ESCAPE '\\' OR dp.score_json LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern, pattern])
        normalized_source = (source or "").strip().lower()
        if normalized_source:
            conditions.append("dp.source = ?")
            params.append(normalized_source)
        if liked_only:
            conditions.append(
                "EXISTS (SELECT 1 FROM paper_preferences pp "
                "WHERE pp.source = dp.source "
                "AND pp.paper_id = dp.paper_id "
                "AND pp.preference = 'like')"
            )
        if min_score is not None:
            conditions.append(
                "json_extract(dp.score_json, '$.total_score') >= ?"
            )
            params.append(float(min_score))
        if completed_from:
            conditions.append("substr(dp.completed_at, 1, 10) >= ?")
            params.append(str(completed_from))
        if completed_to:
            conditions.append("substr(dp.completed_at, 1, 10) <= ?")
            params.append(str(completed_to))

        where_clause = " AND ".join(conditions)
        bounded_limit = max(1, min(int(limit), 200))
        bounded_offset = max(0, int(offset))

        with self._connect() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) FROM daily_papers dp WHERE {where_clause}", params
            ).fetchone()
            rows = conn.execute(
                f"""
                SELECT dp.source, dp.paper_id, dp.canonical_id, dp.version,
                       dp.completed_at, dp.paper_json, dp.score_json,
                       (SELECT pp.preference FROM paper_preferences pp
                        WHERE pp.source = dp.source
                          AND pp.paper_id = dp.paper_id) AS preference
                FROM daily_papers dp
                WHERE {where_clause}
                ORDER BY dp.completed_at DESC
                LIMIT ? OFFSET ?
                """,
                [*params, bounded_limit, bounded_offset],
            ).fetchall()

        items = []
        for row in rows:
            try:
                metadata = (
                    json.loads(row["paper_json"]) if row["paper_json"] else {}
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            try:
                score = json.loads(row["score_json"]) if row["score_json"] else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                score = {}
            if not isinstance(score, dict):
                score = {}
            items.append(
                {
                    "source": row["source"],
                    "paper_id": row["paper_id"],
                    "canonical_id": row["canonical_id"],
                    "version": row["version"],
                    "completed_at": row["completed_at"],
                    "title": str(metadata.get("title") or row["paper_id"]),
                    "authors": [
                        author
                        for author in (metadata.get("authors") or [])
                        if isinstance(author, str)
                    ],
                    "url": metadata.get("url"),
                    "pdf_url": metadata.get("pdf_url"),
                    "categories": [
                        category
                        for category in (metadata.get("categories") or [])
                        if isinstance(category, str)
                    ],
                    "published_date": metadata.get("published_date"),
                    "total_score": score.get("total_score"),
                    "is_qualified": score.get("is_qualified"),
                    "strategy_id": score.get("strategy_id"),
                    "tldr": score.get("tldr"),
                    "extracted_keywords": [
                        keyword
                        for keyword in (score.get("extracted_keywords") or [])
                        if isinstance(keyword, str)
                    ],
                    "preference": row["preference"],
                }
            )
        return {"total": int(total_row[0]), "items": items}

    def get_source_health(self, window: int = 20) -> Dict[str, Dict[str, Any]]:
        """Per-source scan health aggregated from the durable receipt log.

        For each source the summary covers its most recent ``window`` receipts:
        the newest status/timestamp, the success rate inside the window, the
        candidate count of the newest succeeded scan and the newest recorded
        error text. Sources without any receipt yet are omitted.
        """
        bounded_window = max(1, min(int(window), 100))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source, status, recorded_at, receipt_json
                FROM daily_scan_receipts
                ORDER BY recorded_at DESC
                LIMIT ?
                """,
                (bounded_window * 32,),
            ).fetchall()

        per_source: Dict[str, list[Dict[str, Any]]] = {}
        for row in rows:
            try:
                receipt = json.loads(row["receipt_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                receipt = {}
            if not isinstance(receipt, dict):
                receipt = {}
            per_source.setdefault(row["source"], []).append(
                {
                    "status": row["status"],
                    "recorded_at": row["recorded_at"],
                    "receipt": receipt,
                }
            )

        summaries: Dict[str, Dict[str, Any]] = {}
        for source, entries in per_source.items():
            window_entries = entries[:bounded_window]
            succeeded = sum(
                1 for entry in window_entries if entry["status"] == "succeeded"
            )
            newest = window_entries[0]
            new_candidates = None
            for entry in window_entries:
                if entry["status"] != "succeeded":
                    continue
                domain_receipts = entry["receipt"].get("domain_receipts")
                if isinstance(domain_receipts, list):
                    new_candidates = sum(
                        int(item.get("new_candidates") or 0)
                        for item in domain_receipts
                        if isinstance(item, dict)
                    )
                break
            last_error = None
            for entry in window_entries:
                if entry["status"] == "failed":
                    last_error = self._extract_receipt_error(entry["receipt"])
                    break
            summaries[source] = {
                "last_status": newest["status"],
                "last_scan_at": newest["recorded_at"],
                "scans_in_window": len(window_entries),
                "succeeded_in_window": succeeded,
                "success_rate": (
                    succeeded / len(window_entries) if window_entries else 0.0
                ),
                "last_new_candidates": new_candidates,
                "last_error": last_error,
            }
        return summaries

    @staticmethod
    def _extract_receipt_error(receipt: Dict[str, Any]) -> Optional[str]:
        error = receipt.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip()
        for item in receipt.get("domain_receipts") or []:
            if not isinstance(item, dict):
                continue
            domain_error = item.get("error")
            if isinstance(domain_error, str) and domain_error.strip():
                label = item.get("domain") or item.get("label") or ""
                return f"{label}: {domain_error.strip()}".lstrip(": ")
        return None

    def get_recent_runs(self, limit: int = 20) -> list[Dict[str, Any]]:
        """Return recent run summaries plus receipts for local observability."""
        max_rows = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, started_at, scan_started_at, scan_days,
                       scanned_sources_json, completed_at, status, total_papers,
                       error, report_paths_json
                FROM daily_runs
                ORDER BY started_at DESC, run_id DESC
                LIMIT ?
                """,
                (max_rows,),
            ).fetchall()
        runs = []
        for row in rows:
            try:
                sources = json.loads(row["scanned_sources_json"] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                sources = []
            runs.append(
                {
                    "run_id": row["run_id"],
                    "started_at": row["started_at"],
                    "scan_started_at": row["scan_started_at"],
                    "scan_days": row["scan_days"],
                    "scanned_sources": sources if isinstance(sources, list) else [],
                    "completed_at": row["completed_at"],
                    "status": row["status"],
                    "total_papers": int(row["total_papers"] or 0),
                    "error": row["error"],
                    "receipts": self.get_scan_receipts(row["run_id"]),
                }
            )
        return runs

    def set_run_total(self, run_id: str, total_papers: int):
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE daily_runs SET total_papers = ? WHERE run_id = ?",
                (total_papers, run_id),
            )

    def complete_run(self, run_id: str, report_paths: Optional[Dict[str, Any]] = None):
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            run = conn.execute(
                "SELECT status FROM daily_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"daily run does not exist: {run_id}")
            if run["status"] == "completed":
                return
            if run["status"] != "running":
                raise RuntimeError(
                    f"只能完成 running 运行: {run_id} ({run['status']})"
                )
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
                "SELECT run_id, status FROM daily_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"daily run does not exist: {run_id}")
            if run["status"] != "running":
                raise RuntimeError(
                    f"只能交付 running 运行: {run_id} ({run['status']})"
                )

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

                    existing_delivery = conn.execute(
                        """
                        SELECT run_id FROM paper_deliveries
                        WHERE source = ? AND canonical_id = ? AND version = ?
                        """,
                        (
                            source,
                            paper.canonical_id or paper.paper_id,
                            paper.version or 0,
                        ),
                    ).fetchone()
                    if existing_delivery is not None and existing_delivery["run_id"] != run_id:
                        raise RuntimeError(
                            "该论文版本已由另一日报交付，拒绝重复提交: "
                            f"{source}:{paper.canonical_id or paper.paper_id}v{paper.version or 0}"
                        )

                    conn.execute(
                        """
                        INSERT INTO paper_deliveries(
                            run_id, source, paper_id, canonical_id, version,
                            report_path, delivered_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(source, canonical_id, version) DO NOTHING
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
            run = conn.execute(
                "SELECT status FROM daily_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"daily run does not exist: {run_id}")
            # A completed report is authoritative.  A late provider or cleanup
            # exception must not reopen it as a failed/retryable run.  Repeated
            # failure calls are also idempotent and preserve the first error.
            if run["status"] in {"completed", "failed"}:
                return
            if run["status"] != "running":
                raise RuntimeError(
                    f"只能失败 running 运行: {run_id} ({run['status']})"
                )
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
        from sources.base_source import paper_identity

        canonical_id, version = paper_identity(source, paper_id)
        normalized_version = version if version is not None else 0
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM paper_deliveries
                WHERE source = ? AND canonical_id = ? AND version = ?
                LIMIT 1
                """,
                (source, canonical_id, normalized_version),
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

    def has_delivered_arxiv_canonical(self, canonical_id: str) -> bool:
        """Return whether any delivered arXiv version exists for a canonical ID.

        This intentionally answers a broader question than
        :meth:`is_paper_delivered`: a late-arriving supplemental mirror should
        not create a second report merely because its upstream feed omitted an
        arXiv ``vN`` suffix.  It is used only to suppress the mirror, never to
        suppress an arXiv revision itself.
        """
        value = str(canonical_id or "").strip()
        if not value:
            return False
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM paper_deliveries
                WHERE source = 'arxiv' AND canonical_id = ?
                LIMIT 1
                """,
                (value,),
            ).fetchone()
            if row is not None:
                return True
            # Backward-compatible fallback for databases before the delivery
            # ledger.  Exact canonical identity still prevents false matches.
            row = conn.execute(
                """
                SELECT 1 FROM daily_papers
                WHERE source = 'arxiv' AND canonical_id = ?
                  AND completed_at IS NOT NULL
                LIMIT 1
                """,
                (value,),
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

    @classmethod
    def restore_optional_enrichment_from_record(
        cls, paper: "PaperMetadata", record: Optional[sqlite3.Row]
    ) -> None:
        """Hydrate best-effort fields from an already loaded paper record.

        The daily worker needs these fields before it computes the stage input
        fingerprints.  Keeping this as a read-only helper avoids a preliminary
        ``upsert_paper_seen`` transaction solely to get the same hydration,
        while ``upsert_paper_seen`` still performs the restoration itself for
        all other callers.
        """
        if record is None:
            return
        try:
            persisted_paper_json = record["paper_json"]
        except (IndexError, KeyError, TypeError):
            return
        cls._restore_optional_enrichment(paper, persisted_paper_json)

    def register_paper_candidates(
        self,
        run_id: str,
        papers_by_source: Dict[str, list["PaperMetadata"]],
    ) -> int:
        """Durably register every newly discovered candidate before limiting work.

        A per-run processing limit must never truncate an upstream scan.  All
        exact source/version candidates enter ``daily_papers`` first; papers
        not selected in this run remain pending and are eligible next time even
        after the successful scan watermark advances.
        """
        registered = 0
        seen_identities = set()
        candidates = []
        for source, papers in papers_by_source.items():
            normalized_source = str(source or "").strip().lower()
            if not normalized_source:
                raise ValueError("candidate source must be a non-empty string")
            for paper in papers:
                if paper.source != normalized_source:
                    raise ValueError(
                        "candidate source mismatch: "
                        f"group={normalized_source}, metadata={paper.source}"
                    )
                identity = (
                    normalized_source,
                    paper.canonical_id or paper.paper_id,
                    paper.version or 0,
                )
                if identity in seen_identities:
                    continue
                seen_identities.add(identity)
                candidates.append((normalized_source, paper))

        with self._lock, self._connect() as conn:
            run = conn.execute(
                "SELECT status FROM daily_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"daily run does not exist: {run_id}")
            if run["status"] != "running":
                raise RuntimeError(f"只能向 running 运行登记候选论文: {run_id}")

            for normalized_source, paper in candidates:
                candidate_seen_at = datetime.now().isoformat()
                existing = conn.execute(
                    "SELECT paper_json FROM daily_papers WHERE source = ? AND paper_id = ?",
                    (normalized_source, paper.paper_id),
                ).fetchone()
                if existing is not None:
                    self._restore_optional_enrichment(paper, existing["paper_json"])
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
                        paper_json = excluded.paper_json
                    """,
                    (
                        normalized_source,
                        paper.paper_id,
                        paper.canonical_id or paper.paper_id,
                        paper.version or 0,
                        candidate_seen_at,
                        candidate_seen_at,
                        run_id,
                        json.dumps(paper.to_dict(), ensure_ascii=False),
                    ),
                )
                registered += 1
        return registered

    @staticmethod
    def _pending_row_sort_key(row: sqlite3.Row, paper: "PaperMetadata") -> tuple:
        failed_or_retried = bool(row["retry_count"]) or any(
            row[field] == "failed"
            for field in ("score_status", "translation_status", "analysis_status")
        )
        first_seen = DailyResearchStore._parse_checkpoint_timestamp(row["first_seen_at"])
        first_seen_key = first_seen.timestamp() if first_seen is not None else float("inf")
        published = paper.published_date
        if published.tzinfo is None:
            published = published.astimezone()
        published_key = published.timestamp()
        return (
            0 if failed_or_retried else 1,
            first_seen_key,
            published_key,
            row["source"],
            row["canonical_id"],
            int(row["version"] or 0),
            row["paper_id"],
        )

    def select_pending_papers(
        self, enabled_sources: list[str], limit: int = 0
    ) -> tuple[Dict[str, list["PaperMetadata"]], int]:
        """Return the deterministic pending queue and its total size.

        ``limit == 0`` means all pending papers.  Failed/retried records are
        attempted first, followed by older queued records and publication time.
        Only currently enabled report sources are selected; disabling a source
        preserves its backlog without processing it unexpectedly.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("daily paper limit must be a non-negative integer")
        sources = sorted(
            {str(source).strip().lower() for source in enabled_sources if str(source).strip()}
        )
        if not sources:
            return {}, 0
        placeholders = ", ".join("?" for _ in sources)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT daily_papers.*
                FROM daily_papers
                WHERE daily_papers.source IN ("""
                + placeholders
                + """)
                  AND daily_papers.completed_at IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM paper_deliveries
                      WHERE paper_deliveries.source = daily_papers.source
                        AND paper_deliveries.canonical_id = daily_papers.canonical_id
                        AND paper_deliveries.version = daily_papers.version
                  )
                """,
                sources,
            ).fetchall()

        pending = []
        identities = set()
        for row in rows:
            try:
                payload = json.loads(row["paper_json"])
                from sources.base_source import PaperMetadata

                paper = PaperMetadata.from_dict(payload)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    "SQLite 待处理论文元数据损坏，已停止运行以避免漏报: "
                    f"{row['source']}:{row['paper_id']}"
                ) from exc
            expected_identity = (
                row["source"],
                row["canonical_id"],
                int(row["version"] or 0),
            )
            actual_identity = (
                paper.source,
                paper.canonical_id or paper.paper_id,
                paper.version or 0,
            )
            if paper.paper_id != row["paper_id"] or actual_identity != expected_identity:
                raise RuntimeError(
                    "SQLite 待处理论文身份不一致，已停止运行以避免错误交付: "
                    f"{row['source']}:{row['paper_id']}"
                )
            if expected_identity in identities:
                raise RuntimeError(
                    "SQLite 待处理队列包含重复论文版本: "
                    f"{row['source']}:{row['canonical_id']}v{row['version']}"
                )
            identities.add(expected_identity)
            pending.append((row, paper))

        pending.sort(key=lambda item: self._pending_row_sort_key(*item))
        total_pending = len(pending)
        if limit:
            pending = pending[:limit]

        selected: Dict[str, list["PaperMetadata"]] = {}
        for row, paper in pending:
            selected.setdefault(row["source"], []).append(paper)
        return selected, total_pending

    def upsert_paper_seen(
        self,
        run_id: str,
        source: str,
        paper: "PaperMetadata",
        stage_fingerprints: Optional[Dict[str, str]] = None,
    ):
        """Persist fresh metadata and invalidate stale incomplete stage output.

        Delivered rows are immutable ledger entries and are filtered before
        this method in normal runs.  For incomplete rows, a changed score
        input invalidates score/translation/analysis; a changed translation
        input invalidates only translation; and a changed analysis input
        invalidates only deep analysis.  This lets restarts reuse work only
        when it was produced for the same paper/configuration/model inputs.
        """
        now = datetime.now().isoformat()
        fingerprints = stage_fingerprints or {}
        score_fingerprint = fingerprints.get("score")
        translation_fingerprint = fingerprints.get("translation")
        analysis_fingerprint = fingerprints.get("analysis")
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM daily_papers WHERE source = ? AND paper_id = ?",
                (source, paper.paper_id),
            ).fetchone()
            if existing is not None:
                self._restore_optional_enrichment(paper, existing["paper_json"])

            paper_json = json.dumps(paper.to_dict(), ensure_ascii=False)
            if existing is not None and existing["completed_at"] is None:
                score_changed = (
                    score_fingerprint is not None
                    and existing["score_input_fingerprint"] != score_fingerprint
                )
                translation_changed = (
                    translation_fingerprint is not None
                    and existing["translation_input_fingerprint"] != translation_fingerprint
                )
                analysis_changed = (
                    analysis_fingerprint is not None
                    and existing["analysis_input_fingerprint"] != analysis_fingerprint
                )
                if score_changed:
                    conn.execute(
                        """
                        UPDATE daily_papers
                        SET score_json = NULL, score_audit_json = NULL,
                            abstract_cn = NULL, analysis_json = NULL,
                            scored_at = NULL, translated_at = NULL, analyzed_at = NULL,
                            score_status = 'pending', translation_status = 'pending',
                            analysis_status = 'pending', last_error = NULL
                        WHERE source = ? AND paper_id = ?
                        """,
                        (source, paper.paper_id),
                    )
                else:
                    if translation_changed:
                        conn.execute(
                            """
                            UPDATE daily_papers
                            SET abstract_cn = NULL, translated_at = NULL,
                                translation_status = 'pending', last_error = NULL
                            WHERE source = ? AND paper_id = ?
                            """,
                            (source, paper.paper_id),
                        )
                    # Deep analysis consumes the translated/abstract content
                    # indirectly through the reporting configuration.  A
                    # changed translation stage must therefore not leave a
                    # stale deep-analysis cache attached to a new report.
                    if translation_changed or analysis_changed:
                        conn.execute(
                            """
                            UPDATE daily_papers
                            SET analysis_json = NULL, analyzed_at = NULL,
                                analysis_status = 'pending', last_error = NULL
                            WHERE source = ? AND paper_id = ?
                            """,
                            (source, paper.paper_id),
                        )

            conn.execute(
                """
                INSERT INTO daily_papers(
                    source, paper_id, canonical_id, version,
                    first_seen_at, last_seen_at, run_id, paper_json,
                    score_input_fingerprint, translation_input_fingerprint,
                    analysis_input_fingerprint
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, paper_id) DO UPDATE SET
                    canonical_id = excluded.canonical_id,
                    version = excluded.version,
                    last_seen_at = excluded.last_seen_at,
                    run_id = excluded.run_id,
                    paper_json = excluded.paper_json,
                    score_input_fingerprint = COALESCE(
                        excluded.score_input_fingerprint, daily_papers.score_input_fingerprint
                    ),
                    translation_input_fingerprint = COALESCE(
                        excluded.translation_input_fingerprint, daily_papers.translation_input_fingerprint
                    ),
                    analysis_input_fingerprint = COALESCE(
                        excluded.analysis_input_fingerprint, daily_papers.analysis_input_fingerprint
                    )
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
                    score_fingerprint,
                    translation_fingerprint,
                    analysis_fingerprint,
                ),
            )

    def update_scored_paper(
        self,
        run_id: str,
        source: str,
        scored: Dict[str, Any],
        stage_fingerprints: Optional[Dict[str, str]] = None,
        score_audit_metadata: Optional[Dict[str, Any]] = None,
    ):
        """Persist a complete score result for backward-compatible callers."""
        now = datetime.now().isoformat()
        paper = scored["paper_metadata"]
        score_response = scored["score_response"]
        fingerprints = stage_fingerprints or {}
        translation_done = bool(str(scored.get("abstract_cn", "")).strip())
        translation_status = "succeeded" if translation_done else "pending"
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_papers(
                    source, paper_id, canonical_id, version,
                    first_seen_at, last_seen_at, run_id, paper_json,
                    score_json, abstract_cn, scored_at, translated_at,
                    score_audit_json,
                    score_status, translation_status,
                    score_input_fingerprint, translation_input_fingerprint,
                    analysis_input_fingerprint, last_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(source, paper_id) DO UPDATE SET
                    canonical_id = excluded.canonical_id,
                    version = excluded.version,
                    last_seen_at = excluded.last_seen_at,
                    run_id = excluded.run_id,
                    paper_json = excluded.paper_json,
                    score_json = excluded.score_json,
                    score_audit_json = COALESCE(
                        excluded.score_audit_json, daily_papers.score_audit_json
                    ),
                    abstract_cn = excluded.abstract_cn,
                    scored_at = excluded.scored_at,
                    translated_at = excluded.translated_at,
                    score_status = excluded.score_status,
                    translation_status = excluded.translation_status,
                    score_input_fingerprint = COALESCE(
                        excluded.score_input_fingerprint, daily_papers.score_input_fingerprint
                    ),
                    translation_input_fingerprint = COALESCE(
                        excluded.translation_input_fingerprint, daily_papers.translation_input_fingerprint
                    ),
                    analysis_input_fingerprint = COALESCE(
                        excluded.analysis_input_fingerprint, daily_papers.analysis_input_fingerprint
                    ),
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
                    json.dumps(score_audit_metadata, ensure_ascii=False)
                    if score_audit_metadata is not None
                    else None,
                    "succeeded",
                    translation_status,
                    fingerprints.get("score"),
                    fingerprints.get("translation"),
                    fingerprints.get("analysis"),
                ),
            )

    def update_score(
        self,
        run_id: str,
        source: str,
        scored: Dict[str, Any],
        score_input_fingerprint: Optional[str] = None,
        score_audit_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist score/TLDR before attempting translation."""
        now = datetime.now().isoformat()
        paper = scored["paper_metadata"]
        score_response = scored["score_response"]
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, paper_json = ?, score_json = ?, score_audit_json = ?, scored_at = ?,
                    score_status = 'succeeded',
                    score_input_fingerprint = COALESCE(?, score_input_fingerprint),
                    last_error = NULL
                WHERE source = ? AND paper_id = ?
                """,
                (
                    run_id,
                    json.dumps(paper.to_dict(), ensure_ascii=False),
                    score_response.model_dump_json(),
                    json.dumps(score_audit_metadata, ensure_ascii=False)
                    if score_audit_metadata is not None
                    else None,
                    now,
                    score_input_fingerprint,
                    source,
                    scored["paper_id"],
                ),
            )

    def update_translation(
        self,
        run_id: str,
        source: str,
        paper_id: str,
        translation: str,
        translation_input_fingerprint: Optional[str] = None,
    ):
        """Persist a successful non-empty abstract translation."""
        if not translation or not translation.strip():
            raise ValueError("translation must be non-empty")
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, abstract_cn = ?, translated_at = ?,
                    translation_status = 'succeeded',
                    translation_input_fingerprint = COALESCE(
                        ?, translation_input_fingerprint
                    ),
                    last_error = NULL
                WHERE source = ? AND paper_id = ?
                """,
                (
                    run_id,
                    translation.strip(),
                    now,
                    translation_input_fingerprint,
                    source,
                    paper_id,
                ),
            )

    def mark_translation_not_required(
        self,
        run_id: str,
        source: str,
        paper_id: str,
        translation_input_fingerprint: Optional[str] = None,
    ):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, translation_status = 'not_required',
                    translation_input_fingerprint = COALESCE(
                        ?, translation_input_fingerprint
                    ),
                    last_error = NULL
                WHERE source = ? AND paper_id = ?
                """,
                (run_id, translation_input_fingerprint, source, paper_id),
            )

    def update_analysis(
        self,
        run_id: str,
        source: str,
        paper_id: str,
        analysis: "Stage2Response",
        analysis_input_fingerprint: Optional[str] = None,
    ):
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, analysis_json = ?, analyzed_at = ?,
                    analysis_status = 'succeeded',
                    analysis_input_fingerprint = COALESCE(?, analysis_input_fingerprint),
                    last_error = NULL
                WHERE source = ? AND paper_id = ?
                """,
                (
                    run_id,
                    self._analysis_json(analysis),
                    now,
                    analysis_input_fingerprint,
                    source,
                    paper_id,
                ),
            )

    def mark_analysis_not_required(
        self,
        run_id: str,
        source: str,
        paper_id: str,
        analysis_input_fingerprint: Optional[str] = None,
    ):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, analysis_status = 'not_required',
                    analysis_input_fingerprint = COALESCE(
                        ?, analysis_input_fingerprint
                    ),
                    last_error = NULL
                WHERE source = ? AND paper_id = ?
                """,
                (run_id, analysis_input_fingerprint, source, paper_id),
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
        raw_analysis = record["analysis_json"]
        try:
            payload = json.loads(raw_analysis)
            if not isinstance(payload, dict) or not payload:
                raise ValueError("深度分析缓存必须是非空 JSON 对象")

            # Validate known fields while preserving unknown template fields.
            # Future/custom report modules may add keys that Stage2Response does
            # not know yet, so returning model_dump() here would silently lose
            # them during retry hydration.  The shared validator additionally
            # rejects a nonempty metadata/error object that contains no
            # renderable enabled module; treating that as success used to hide
            # a failed deep-analysis call behind an empty report section.
            from agents.analysis_agent import validate_deep_analysis_payload
            from config import settings

            validate_deep_analysis_payload(
                payload,
                settings.load_report_template("deep_analysis_template.json"),
            )
        except Exception as exc:
            # A successful status with unreadable data is a recoverable cache
            # corruption, not a valid result.  Clear it and mark the stage
            # failed so the next run retries it with the same input fingerprint.
            try:
                source = record["source"]
                paper_id = record["paper_id"]
            except (IndexError, KeyError, TypeError):
                return None
            error = f"持久化深度分析缓存无效: {exc}"[:4000]
            now = datetime.now().isoformat()
            with self._lock, self._connect() as conn:
                conn.execute(
                    """
                    UPDATE daily_papers
                    SET analysis_json = NULL, analyzed_at = NULL,
                        analysis_status = 'failed', last_error = ?,
                        retry_count = retry_count + 1
                    WHERE source = ? AND paper_id = ?
                      AND analysis_status = 'succeeded'
                    """,
                    (error, source, paper_id),
                )
            return None
        return payload
