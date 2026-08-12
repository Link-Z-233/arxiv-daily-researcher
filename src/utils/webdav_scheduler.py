"""Small cron entrypoint for WebDAV's independent scheduled-upload mode.

The main daily run must not be used as a proxy for a scheduled backup: a long
LLM run or an empty paper day should not suppress a configured WebDAV upload.
Docker's existing cron daemon invokes this module once a minute; the module
checks the user schedule, then records and delivers an idempotent outbox task.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional, Sequence

if TYPE_CHECKING:
    from utils.daily_research_store import DailyResearchStore

logger = logging.getLogger(__name__)

_SCHEDULED_TASK_PREFIX = "webdav_scheduled:"


def _scheduled_mode_is_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "WEBDAV_ENABLED", False)) and getattr(
        settings, "WEBDAV_SYNC_MODE", "manual"
    ) == "scheduled"


def scheduled_task_key(now: datetime) -> str:
    """Return one idempotency key for a local calendar minute."""
    return f"{_SCHEDULED_TASK_PREFIX}{now.strftime('%Y%m%d%H%M')}"


def _retry_delay_seconds(attempt_count: int, settings: Any) -> int:
    """Bound exponential retry delay using the existing global retry settings."""
    max_attempts = max(1, int(getattr(settings, "RETRY_MAX_ATTEMPTS", 3)))
    min_wait = max(1, int(getattr(settings, "RETRY_MIN_WAIT", 2)))
    max_wait = max(min_wait, int(getattr(settings, "RETRY_MAX_WAIT", 30)))
    retry_exponent = min(max(0, attempt_count - 1), max_attempts - 1)
    return min(min_wait * (2**retry_exponent), max_wait)


def _deliver_due_scheduled_syncs(
    store: "DailyResearchStore",
    *,
    log: logging.Logger,
    settings: Any,
    sync_callable=None,
    limit: int = 10,
) -> Dict[str, int]:
    """Deliver due scheduled uploads without altering paper/report state."""
    if not _scheduled_mode_is_enabled(settings):
        # Configuration may change after an earlier invocation queued a task.
        # Do not claim or mark a remote backup complete merely because it is no
        # longer configured; leave it pending until scheduled mode is enabled.
        return {"claimed": 0, "completed": 0, "deferred": 0}

    rows = store.claim_due_maintenance_tasks(prefix=_SCHEDULED_TASK_PREFIX, limit=limit)
    summary = {"claimed": len(rows), "completed": 0, "deferred": 0}
    deliver = sync_callable
    if deliver is None:
        from utils.webdav_sync import sync_scheduled

        deliver = sync_scheduled
    for row in rows:
        task_key = row["task_key"]
        try:
            result = deliver(log)
            if result is None:
                # The task can only be claimed when scheduled mode is enabled,
                # but config may have changed after queueing.  Preserve it for
                # the operator rather than falsely declaring a backup complete.
                raise RuntimeError("WebDAV 定时同步当前未启用")
        except Exception as exc:
            retry_after = _retry_delay_seconds(int(row["attempt_count"]), settings)
            store.reschedule_maintenance_task(task_key, str(exc), retry_after)
            log.error(
                "[WebDAV] 定时同步失败，已保留待补发 (%s, %ss 后重试): %s",
                task_key,
                retry_after,
                exc,
            )
            summary["deferred"] += 1
        else:
            store.mark_maintenance_task_completed(task_key)
            summary["completed"] += 1
    return summary


def run_scheduled_webdav_sync(
    now: Optional[datetime] = None,
    *,
    store: Optional["DailyResearchStore"] = None,
    log: Optional[logging.Logger] = None,
    settings_override: Any = None,
    sync_callable=None,
) -> Dict[str, Any]:
    """Queue any matching minute and attempt all due scheduled WebDAV uploads.

    A failed transfer is persisted and retried on every subsequent one-minute
    invocation.  A successfully delivered minute is immutable, so a Docker
    restart or cron's duplicate minute invocation cannot upload it twice.
    """
    from utils.webdav_sync import (
        cron_schedule_matches,
        validate_cron_schedule,
    )

    if settings_override is None:
        from config import settings as configured_settings

        active_settings = configured_settings
    else:
        active_settings = settings_override
    if store is None:
        from utils.daily_research_store import DailyResearchStore

    active_log = log or logger
    current = (now or datetime.now()).replace(second=0, microsecond=0)
    summary: Dict[str, Any] = {
        "enabled": False,
        "matched": False,
        "queued": False,
        "claimed": 0,
        "completed": 0,
        "deferred": 0,
    }

    if not _scheduled_mode_is_enabled(active_settings):
        return summary
    summary["enabled"] = True

    schedule = validate_cron_schedule(getattr(active_settings, "WEBDAV_CRON_SCHEDULE", ""))
    summary["matched"] = cron_schedule_matches(schedule, current)
    active_store = store or DailyResearchStore(active_settings.DAILY_RESEARCH_DB_PATH)

    if summary["matched"]:
        task_key = scheduled_task_key(current)
        summary["queued"] = active_store.enqueue_maintenance_task(
            task_key,
            {
                "task_type": "webdav_scheduled",
                "schedule": schedule,
                "scheduled_for": current.isoformat(),
            },
        )
        if summary["queued"]:
            active_log.info("[WebDAV] 已排入定时同步: %s", task_key)

    delivery = _deliver_due_scheduled_syncs(
        active_store,
        log=active_log,
        settings=active_settings,
        sync_callable=sync_callable,
    )
    summary.update(delivery)
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run one cron tick; return nonzero only for invalid settings/bugs."""
    del argv  # Kept for a conventional, testable Python entrypoint signature.
    try:
        summary = run_scheduled_webdav_sync()
    except Exception as exc:
        logger.error("[WebDAV] 定时同步调度失败: %s", exc)
        return 1

    if summary["claimed"]:
        logger.info(
            "[WebDAV] 定时同步调度完成: 完成 %s，待补发 %s",
            summary["completed"],
            summary["deferred"],
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
