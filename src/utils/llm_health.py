"""Best-effort, privacy-safe observability for real LLM calls.

The health panel deliberately does *not* send a synthetic completion request:
it only summarizes the final outcome of calls the user has already asked the
application to make.  This module keeps the recording callback optional so
agents remain usable in isolated tools and tests without writing to a user's
database.
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Callable, Optional, Protocol


logger = logging.getLogger(__name__)


class _LLMHealthStore(Protocol):
    """The small store surface needed by a health recorder callback."""

    def record_llm_health_event(
        self,
        role: str,
        model: str,
        success: bool,
        error_summary: Optional[str] = None,
    ) -> None: ...


LLMHealthRecorder = Callable[[str, str, bool, Optional[BaseException]], None]


def safe_llm_error_summary(
    error: Optional[BaseException | str], *, limit: int = 360
) -> Optional[str]:
    """Return a compact error explanation without credentials or URL secrets.

    Provider SDKs can put useful DNS/HTTP details on a chained exception, but
    occasionally include an API key in a header or URL.  The result is safe to
    persist and display in the local WebUI; it is not intended to be a full
    traceback.
    """
    if error is None:
        return None

    details: list[str] = []
    if isinstance(error, BaseException):
        current: Optional[BaseException] = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen and len(details) < 4:
            seen.add(id(current))
            detail = str(current).strip()
            if detail and detail not in details:
                details.append(detail)
            current = current.__cause__ or current.__context__
    else:
        detail = str(error).strip()
        if detail:
            details.append(detail)

    rendered = " -> ".join(details)
    if not rendered:
        return None
    rendered = re.sub(r"\s+", " ", rendered)
    rendered = re.sub(
        r"(?i)((?:api[_-]?key|authorization|token|access[_-]?token|password|secret)"
        r"\s*(?:=|:)\s*)(?:bearer\s+)?[^,;\s]+",
        r"\1***",
        rendered,
    )
    rendered = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._-]+", r"\1***", rendered)
    rendered = re.sub(r"(?i)(?:sk|rk|pk)-[A-Za-z0-9_-]+", lambda m: m.group(0)[:3] + "***", rendered)
    rendered = re.sub(r"(https?://)[^/@\s]+@", r"\1***@", rendered)
    rendered = re.sub(
        r"(?i)([?&](?:api[_-]?key|token|access_token|password|secret)=)[^&#\s]+",
        r"\1***",
        rendered,
    )
    return rendered[: max(40, int(limit))]


def make_llm_health_recorder(store: _LLMHealthStore) -> LLMHealthRecorder:
    """Build a non-fatal recorder bound to one authoritative SQLite store."""

    def _record(
        role: str,
        model: str,
        success: bool,
        error: Optional[BaseException] = None,
    ) -> None:
        try:
            store.record_llm_health_event(
                role,
                model,
                success,
                safe_llm_error_summary(error),
            )
        except Exception:
            # Observability must never make a completed analysis/retry fail.
            logger.debug("LLM 健康事件写入失败", exc_info=True)

    return _record


def make_database_llm_health_recorder(db_path: Path) -> LLMHealthRecorder:
    """Build a recorder that opens SQLite only if a real call actually occurs.

    This is useful for maintenance/trend entry points where LLM work may be
    disabled or a request may finish before it reaches an LLM stage.  The
    lazy construction also keeps dry-run and unit-test paths read-only.
    """
    path = Path(db_path)
    store: Optional[_LLMHealthStore] = None
    store_lock = threading.Lock()

    def _record(
        role: str,
        model: str,
        success: bool,
        error: Optional[BaseException] = None,
    ) -> None:
        nonlocal store
        try:
            with store_lock:
                if store is None:
                    from utils.daily_research_store import DailyResearchStore

                    store = DailyResearchStore(path)
            store.record_llm_health_event(
                role,
                model,
                success,
                safe_llm_error_summary(error),
            )
        except Exception:
            logger.debug("LLM 健康事件写入失败", exc_info=True)

    return _record
