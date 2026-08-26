"""Durable queue runner for historical daily-report date ranges.

The WebUI sends one validated range request. This module expands it into one
SQLite queue row per calendar day, then runs those rows strictly in date order.
Completed days remain auditable; an interrupted worker returns the active day
to pending so the next range request can resume it safely.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Callable, Optional

from config import settings
from notifications import NotifierAgent, WorkflowResult
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
        try:
            batch_progress = store.backfill_batch_summary(job["batch_id"])
            batch_total = int(batch_progress.get("total", 0) or 0)
            # This row has already been claimed as running. Completed +
            # failed + running is the visible ordinal in this user request.
            batch_current = (
                int(batch_progress.get("completed", 0) or 0)
                + int(batch_progress.get("failed", 0) or 0)
                + int(batch_progress.get("running", 0) or 0)
            )
        except Exception as exc:
            batch_total = 0
            batch_current = processed
            logger.warning("过去日报队列无法读取批次进度: %s", exc)
        logger.info(
            "[Backfill] 批次 %s：处理第 %s/%s 天（本次第 %s 个任务）：%s",
            job["batch_id"],
            batch_current,
            batch_total or "?",
            processed,
            target_date.isoformat(),
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
            remaining = max(
                0, int(getattr(result, "deferred_paper_count", 0) or 0)
            )
            if remaining:
                store.requeue_backfill_job(
                    job["backfill_id"],
                    f"当日仍有 {remaining} 篇论文，已自动续跑下一批",
                )
                logger.info(
                    "[Backfill] 日期 %s 本批完成，仍有 %s 篇待处理；已自动续跑同一天",
                    target_date,
                    remaining,
                )
                continue
            store.complete_backfill_job(job["backfill_id"])
            logger.info(
                "[Backfill] 日期 %s 已完成（批次 %s 当前 %s/%s 天）",
                target_date,
                job["batch_id"],
                batch_current,
                batch_total or "?",
            )
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


def _notify_backfill_result(
    store: DailyResearchStore,
    request: dict[str, Any],
    batch: dict[str, Any],
    exit_code: int,
    error: str = "",
) -> None:
    """Send one durable summary for the whole user-selected date range."""
    if not settings.ENABLE_NOTIFICATIONS:
        return

    first_error = str(batch.get("first_error") or error or "")
    summary = {
        "日期范围": f"{request['date_from']} 至 {request['date_to']}",
        "排队天数": request.get("queued", 0),
        "已完成日期": batch.get("completed", 0),
        "失败日期": batch.get("failed", 0),
        "仍待处理": batch.get("pending", 0),
    }
    if batch.get("first_failed_date"):
        summary["首个失败日期"] = batch["first_failed_date"]
    issues = []
    if batch.get("failed"):
        issues.append(f"{batch['failed']} 个日期未生成完整报告，已保留失败状态供后续补跑")
    if batch.get("pending"):
        issues.append(f"{batch['pending']} 个日期仍在队列中，下一次启动会继续处理")
    result = WorkflowResult(
        workflow="过去日报补跑",
        run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        success=exit_code == 0,
        interrupted=exit_code == 130,
        summary=summary,
        issues=issues,
        error_message=first_error or None,
    )
    try:
        notifier = NotifierAgent()
        created = notifier.enqueue_workflow_result(store, request["batch_id"], result)
        delivery = notifier.deliver_pending_workflow_results(store)
        logger.info(
            "过去日报队列通知：新建 %s，发送 %s，待补发 %s",
            created,
            delivery["sent"],
            delivery["deferred"],
        )
    except Exception as exc:
        # Queue state is already durable and must remain unaffected by a
        # temporary webhook/email failure.
        logger.warning("过去日报队列通知写入/发送失败: %s", exc)


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
    exit_code = 1
    error = ""
    try:
        exit_code = drain_backfill_queue(store)
    except KeyboardInterrupt:
        exit_code = 130
        error = "用户中断；未完成日期已保留在队列中"
        logger.warning(error)
    except Exception as exc:
        error = str(exc)
        logger.exception("过去日报队列异常终止")
    finally:
        try:
            batch = store.backfill_batch_summary(request["batch_id"])
        except Exception as exc:
            batch = {}
            error = error or f"无法读取过去日报队列状态: {exc}"
        _notify_backfill_result(store, request, batch, exit_code, error)
    return exit_code
