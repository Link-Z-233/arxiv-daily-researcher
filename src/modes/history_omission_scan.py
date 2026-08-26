"""Find SQLite-covered historical omissions and build natural-week supplements.

The v3.2 HTML importer is the only component that reads archived reports.
Once a card has been written to SQLite, this workflow uses the delivery ledger,
paper metadata and supplement backlog exclusively.  Each missed paper is
handled with the normal daily-research pipeline, while report batches are
bounded by ``daily_research.max_papers_per_run`` and grouped by ISO calendar
week (Monday through Sunday).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

from config import settings
from notifications import NotifierAgent, WorkflowResult
from utils.daily_research_store import DailyResearchStore
from utils.legacy_range_scan import scan_legacy_range
from utils.token_counter import token_counter

logger = logging.getLogger("HistoryOmissionScan")

HISTORY_OMISSION_SCAN_STATE_KEY = "history_omission_scan_summary"


def _save_summary(store: DailyResearchStore, summary: Dict[str, Any]) -> None:
    try:
        store.set_app_state(
            HISTORY_OMISSION_SCAN_STATE_KEY,
            json.dumps(summary, ensure_ascii=False),
        )
    except Exception as exc:  # pragma: no cover - observability must not lose queue state
        logger.warning("历史遗漏扫描汇总写入失败: %s", exc)


def _notify_result(
    store: DailyResearchStore,
    run_id: str,
    summary: Dict[str, Any],
    exit_code: int,
) -> None:
    if not settings.ENABLE_NOTIFICATIONS:
        return
    scan = summary.get("scan") if isinstance(summary.get("scan"), dict) else {}
    weeks = summary.get("weeks") if isinstance(summary.get("weeks"), list) else []
    batches = sum(int(week.get("batches", 0) or 0) for week in weeks if isinstance(week, dict))
    completed_weeks = sum(
        1 for week in weeks if isinstance(week, dict) and week.get("state") == "completed"
    )
    issues = list(summary.get("issues") or [])
    if scan.get("failed_chunks"):
        issues.append(f"{scan['failed_chunks']} 个历史扫描分块失败，后续可再次执行本任务重试")
    result = WorkflowResult(
        workflow="历史遗漏扫描与补充报告",
        run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        success=exit_code == 0,
        interrupted=exit_code == 130,
        summary={
            "扫描范围": (
                f"{scan.get('range_start') or '—'} 至 {scan.get('range_end') or '—'}"
            ),
            "扫描论文": int(scan.get("papers_scanned", 0) or 0),
            "发现遗漏": int(scan.get("missed_found", 0) or 0),
            "自然周": len(weeks),
            "完成自然周": completed_weeks,
            "补充报告批次": batches,
            "仍待处理": int(summary.get("pending_after", 0) or 0),
        },
        issues=issues,
        error_message=str(summary.get("error") or "") or None,
    )
    try:
        notifier = NotifierAgent()
        created = notifier.enqueue_workflow_result(store, run_id, result)
        delivery = notifier.deliver_pending_workflow_results(store)
        logger.info(
            "历史遗漏扫描通知：新建 %s，发送 %s，待补发 %s",
            created,
            delivery["sent"],
            delivery["deferred"],
        )
    except Exception as exc:
        logger.warning("历史遗漏扫描通知写入/发送失败: %s", exc)


def _progress_callback(store: DailyResearchStore, run_id: str):
    def emit(*, phase: str, detail: str = "", current=None, total=None) -> None:
        try:
            store.record_run_phase(
                run_id,
                phase,
                detail=detail,
                current=current,
                total=total,
            )
        except Exception as exc:  # pragma: no cover - UI heartbeat is optional
            logger.debug("历史遗漏扫描进度写入失败: %s", exc)

    return emit


def _scan_sqlite_history(
    store: DailyResearchStore,
    *,
    progress_callback: Callable[..., None],
) -> Dict[str, Any]:
    """Fetch the covered arXiv range and persist only unknown identities."""
    from sources.arxiv_source import ArxivSource

    source = ArxivSource(
        history_dir=settings.HISTORY_DIR,
        proxy_dict=settings.get_proxy_dict("arxiv"),
        announcement_lookback_grace_days=int(
            getattr(settings, "ARXIV_ANNOUNCEMENT_LOOKBACK_GRACE_DAYS", 2)
        ),
        load_legacy_history=False,
    )
    domains = list(getattr(settings, "TARGET_DOMAINS", []) or [])
    return scan_legacy_range(
        store,
        fetch_between=lambda start, end: source.fetch_domain_papers_between(
            start, end, domains
        ),
        logger_override=logger,
        progress_callback=progress_callback,
    )


def run_history_omission_scan(
    *,
    store: Optional[DailyResearchStore] = None,
    notify: bool = True,
    pipeline_factory: Optional[Callable[[], Any]] = None,
) -> tuple[int, str, Dict[str, Any]]:
    """Scan SQLite delivery coverage and drain omission rows week by week.

    Callers that expose this as a user task must hold both the daily workflow
    gate and the legacy-import activity gate.  ``notify=False`` is used by a
    full legacy import, which sends one consolidated notification for its
    nested import, repair and omission phases.
    """
    store = store or DailyResearchStore(settings.DAILY_RESEARCH_DB_PATH)
    run_id = store.start_run(0, run_kind="history_omission_scan")
    progress = _progress_callback(store, run_id)
    summary: Dict[str, Any] = {
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "scan": {},
        "weeks": [],
        "pending_after": 0,
        "issues": [],
    }
    if pipeline_factory is None:
        from modes.daily_research import DailyResearchPipeline

        pipeline_factory = DailyResearchPipeline

    try:
        if settings.TOKEN_TRACKING_ENABLED:
            token_counter.reset()

        progress(phase="history_omission_scan", detail="根据 SQLite 交付账本确定历史扫描范围")
        logger.info("[HistoryOmission] 开始按 SQLite 历史范围扫描 arXiv 漏项")
        try:
            scan = _scan_sqlite_history(store, progress_callback=progress)
        except Exception as exc:
            # The existing backlog is still useful.  Keep the failure visible
            # and continue draining any previously found natural-week groups.
            logger.exception("[HistoryOmission] 历史范围扫描初始化或执行失败")
            scan = {"errors": [str(exc)], "failed_chunks": 1, "skipped_reason": str(exc)}
        summary["scan"] = scan
        for error in scan.get("errors", []) if isinstance(scan.get("errors"), list) else []:
            summary["issues"].append(f"历史范围扫描：{error}")
        if scan.get("skipped_reason"):
            logger.warning("[HistoryOmission] 扫描说明：%s", scan["skipped_reason"])

        groups = store.missed_scan_week_groups()
        total_pending = sum(groups.values())
        store.set_run_total(run_id, total_pending)
        if not groups:
            summary["pending_after"] = store.supplement_backlog_summary(
                reasons={"missed_scan"}
            )["pending"]
            summary["finished_at"] = datetime.now().isoformat()
            exit_code = 0 if not scan.get("failed_chunks") else 1
            if exit_code == 0:
                store.complete_run(run_id, {})
            else:
                summary.setdefault(
                    "error",
                    "历史遗漏扫描有分块未完成；已保留后续可重试的扫描与补充任务",
                )
                store.fail_run(run_id, summary["error"])
            _save_summary(store, summary)
            if notify:
                _notify_result(store, run_id, summary, exit_code)
            logger.info("[HistoryOmission] 没有可生成补充报告的历史遗漏论文")
            return exit_code, run_id, summary

        logger.info(
            "[HistoryOmission] 待补充遗漏论文 %s 篇，分布在 %s 个自然周",
            total_pending,
            len(groups),
        )
        completed_weeks = 0
        had_batch_failure = False
        for week_index, (week_start, expected_count) in enumerate(groups.items(), start=1):
            week_end = week_start + timedelta(days=6)
            week_summary: Dict[str, Any] = {
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "initial_pending": expected_count,
                "batches": 0,
                "processed": 0,
                "report_paths": [],
                "state": "running",
            }
            summary["weeks"].append(week_summary)
            progress(
                phase="history_omission_week",
                detail=f"处理第 {week_index}/{len(groups)} 个自然周：{week_start} 至 {week_end}",
                current=week_index - 1,
                total=len(groups),
            )
            logger.info(
                "[HistoryOmission] 自然周 %s/%s：%s 至 %s，初始积压 %s 篇",
                week_index,
                len(groups),
                week_start,
                week_end,
                expected_count,
            )

            while True:
                pending_before = int(
                    store.supplement_backlog_summary(
                        reasons={"missed_scan"},
                        published_from=week_start,
                        published_to=week_end,
                    )["pending"]
                    or 0
                )
                if pending_before <= 0:
                    week_summary["state"] = "completed"
                    completed_weeks += 1
                    break

                week_summary["batches"] += 1
                batch_index = week_summary["batches"]
                progress(
                    phase="history_omission_week",
                    detail=(
                        f"自然周 {week_start} 至 {week_end}：运行第 {batch_index} 份补充报告，"
                        f"待处理 {pending_before} 篇"
                    ),
                    current=week_index - 1,
                    total=len(groups),
                )
                # The report's date follows the real calendar week, while
                # its clock is the actual execution time as requested.
                report_stamp = datetime.combine(week_end, datetime.now().time())
                result = pipeline_factory().run(
                    run_kind="supplement",
                    supplement_reasons={"missed_scan"},
                    supplement_week_start=week_start,
                    supplement_week_end=week_end,
                    report_timestamp=report_stamp,
                )
                week_summary["processed"] += int(
                    getattr(result, "total_papers_fetched", 0) or 0
                )
                paths = getattr(result, "report_paths", {}) or {}
                if isinstance(paths, dict) and paths:
                    week_summary["report_paths"].append(
                        {key: str(value) for key, value in paths.items()}
                    )

                if getattr(result, "interrupted", False):
                    week_summary["state"] = "interrupted"
                    summary["error"] = "用户中断历史遗漏补充报告；未完成论文会在下次运行时继续"
                    summary["finished_at"] = datetime.now().isoformat()
                    store.fail_run(run_id, summary["error"])
                    _save_summary(store, summary)
                    if notify:
                        _notify_result(store, run_id, summary, 130)
                    return 130, run_id, summary

                pending_after = int(
                    store.supplement_backlog_summary(
                        reasons={"missed_scan"},
                        published_from=week_start,
                        published_to=week_end,
                    )["pending"]
                    or 0
                )
                week_summary["remaining"] = pending_after
                if getattr(result, "success", None) is not True:
                    had_batch_failure = True
                    week_summary["state"] = "failed"
                    detail = str(
                        getattr(result, "error_message", "补充报告未成功完成")
                        or "补充报告未成功完成"
                    )
                    summary["issues"].append(
                        f"自然周 {week_start} 至 {week_end} 第 {batch_index} 批失败：{detail[:1000]}"
                    )
                    logger.error("[HistoryOmission] %s", summary["issues"][-1])
                    break
                if pending_after >= pending_before:
                    # A report can complete with zero papers when every
                    # candidate is temporarily unfetchable. Do not spin; the
                    # durable failed/pending rows remain retryable.
                    had_batch_failure = True
                    week_summary["state"] = "retry_pending"
                    detail = (
                        f"自然周 {week_start} 至 {week_end} 第 {batch_index} 批没有推进积压；"
                        "已保留待重试数据"
                    )
                    summary["issues"].append(detail)
                    logger.warning("[HistoryOmission] %s", detail)
                    break
                logger.info(
                    "[HistoryOmission] 自然周 %s 至 %s 第 %s 批完成：处理 %s 篇，剩余 %s 篇",
                    week_start,
                    week_end,
                    batch_index,
                    getattr(result, "total_papers_fetched", 0),
                    pending_after,
                )

            progress(
                phase="history_omission_week",
                detail=f"自然周 {week_start} 至 {week_end}：{week_summary['state']}",
                current=week_index,
                total=len(groups),
            )
            _save_summary(store, summary)

        summary["pending_after"] = int(
            store.supplement_backlog_summary(reasons={"missed_scan"})["pending"] or 0
        )
        summary["finished_at"] = datetime.now().isoformat()
        exit_code = 0 if not (had_batch_failure or scan.get("failed_chunks")) else 1
        if exit_code == 0:
            store.complete_run(run_id, {})
        else:
            summary.setdefault(
                "error",
                "历史遗漏扫描或补充报告有步骤未完成；已保留遗漏论文供下次重试",
            )
            store.fail_run(run_id, summary["error"])
        _save_summary(store, summary)
        if notify:
            _notify_result(store, run_id, summary, exit_code)
        logger.info(
            "[HistoryOmission] 完成：扫描遗漏 %s 篇，完成自然周 %s/%s，剩余 %s 篇",
            scan.get("missed_found", 0),
            completed_weeks,
            len(groups),
            summary["pending_after"],
        )
        return exit_code, run_id, summary
    except KeyboardInterrupt:
        summary["error"] = "用户中断历史遗漏扫描；已入库的遗漏和未完成补充报告会在下次继续"
        summary["finished_at"] = datetime.now().isoformat()
        try:
            store.fail_run(run_id, summary["error"])
        except Exception:
            logger.warning("历史遗漏扫描中断状态写入失败", exc_info=True)
        _save_summary(store, summary)
        if notify:
            _notify_result(store, run_id, summary, 130)
        return 130, run_id, summary
    except Exception as exc:
        summary["error"] = str(exc)
        summary["finished_at"] = datetime.now().isoformat()
        logger.exception("历史遗漏扫描异常终止")
        try:
            store.fail_run(run_id, str(exc))
        except Exception:
            logger.warning("历史遗漏扫描失败状态写入失败", exc_info=True)
        _save_summary(store, summary)
        if notify:
            _notify_result(store, run_id, summary, 1)
        return 1, run_id, summary
    finally:
        try:
            usage = token_counter.get_summary().get("by_model") or {}
            if usage:
                store.record_token_usage(run_id, usage, mode="history_omission_scan")
        except Exception:
            logger.debug("历史遗漏扫描 Token 用量记录失败", exc_info=True)
