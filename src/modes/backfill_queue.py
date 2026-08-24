"""Durable queue runner for historical daily-report date ranges.

The WebUI sends one validated range request. This module expands it into one
SQLite queue row per calendar day, then runs those rows strictly in date order.
Completed days remain auditable; an interrupted worker returns the active day
to pending so the next range request can resume it safely.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable, Optional

from config import settings
from utils.daily_research_store import DailyResearchStore

logger = logging.getLogger("BackfillQueue")


def _result_success(result: Any) -> bool:
    """Accept only an explicit successful pipeline result."""
    return getattr(result, "success", None) is True


def drain_backfill_queue(
    store: DailyResearchStore,
    *,
    pipeline_factory: Optional[Callable[[], Any]] = None,
) -> int:
    """Run every pending historical date sequentially.

    A failure is recorded against its individual day and does not discard the
    remaining dates. An interruption is different: that day is restored to
    ``pending`` and the trigger exits with code 130 so the worker/UI can show
    a resumable interruption rather than a completed queue.
    """
    if pipeline_factory is None:
        from modes.daily_research import DailyResearchPipeline

        pipeline_factory = DailyResearchPipeline

    restored = store.recover_interrupted_backfill_jobs()
    if restored:
        logger.info("过去日报队列恢复 %s 个被中断的日期", restored)

    processed = 0
    failed = 0
    while True:
        job = store.claim_next_backfill_job()
        if job is None:
            break

        target_date = date.fromisoformat(job["target_date"])
        processed += 1
        logger.info(
            "过去日报队列处理第 %s 天：%s（批次 %s）",
            processed,
            target_date.isoformat(),
            job["batch_id"],
        )
        try:
            result = pipeline_factory().run(
                run_kind="backfill", target_date=target_date
            )
        except KeyboardInterrupt:
            store.requeue_backfill_job(
                job["backfill_id"], "用户中断；该日期将在下次运行时重试"
            )
            logger.warning("过去日报队列被中断，已回退日期 %s", target_date)
            return 130
        except Exception as exc:
            failed += 1
            store.fail_backfill_job(job["backfill_id"], str(exc))
            logger.exception("过去日报 %s 失败，继续处理后续日期", target_date)
            continue

        if getattr(result, "interrupted", False):
            store.requeue_backfill_job(
                job["backfill_id"], "运行被中断；该日期将在下次运行时重试"
            )
            logger.warning("过去日报 %s 被中断，已回退队列", target_date)
            return 130
        if _result_success(result):
            store.complete_backfill_job(job["backfill_id"])
            logger.info("过去日报 %s 已完成", target_date)
            continue

        failed += 1
        store.fail_backfill_job(
            job["backfill_id"],
            str(getattr(result, "error_message", "过去日报运行失败")),
        )
        logger.error("过去日报 %s 未成功完成，已记录为失败", target_date)

    summary = store.backfill_queue_summary()
    logger.info(
        "过去日报队列收尾：本次处理 %s 天，失败 %s 天；待处理 %s，已完成 %s，失败累计 %s",
        processed,
        failed,
        summary["pending"],
        summary["completed"],
        summary["failed"],
    )
    return 0 if failed == 0 else 1


def enqueue_and_run_backfill_range(date_from: date | str, date_to: date | str) -> int:
    """Persist a requested range, then drain the durable queue in FIFO order."""
    store = DailyResearchStore(settings.DAILY_RESEARCH_DB_PATH)
    request = store.enqueue_backfill_range(date_from, date_to)
    logger.info(
        "过去日报队列已加入 %s 天：%s 至 %s（批次 %s）",
        request["queued"],
        request["date_from"],
        request["date_to"],
        request["batch_id"],
    )
    return drain_backfill_queue(store)
