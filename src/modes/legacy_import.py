"""旧版本（v3.2）历史导入模式。

由面板「读取旧历史」按钮经触发队列启动：等待每日研究/趋势任务空闲后，
把旧 JSON 历史与 HTML 日报卡片合并进 SQLite（详见 utils/legacy_history），
随后扫描旧历史涉及的时间段寻找遗漏论文，并把结果汇总写入 app_state
供面板展示。数据缺失与遗漏论文进入补充运行积压表，等待补充报告流程。
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from notifications import NotifierAgent, WorkflowResult
from utils.daily_research_store import DailyResearchStore
from utils.legacy_history import LEGACY_IMPORT_STATE_KEY, import_legacy_history
from utils.legacy_range_scan import scan_legacy_range
from utils.run_lock import (
    daily_workflow_gate,
    legacy_import_activity_gate,
    run_lock,
)

logger = logging.getLogger("LegacyImport")


def _scan_phase(
    store: DailyResearchStore,
    summary: dict,
    *,
    progress_callback=None,
) -> dict:
    """扫描旧历史涉及的时间段，寻找被漏掉的 arXiv 论文。"""
    try:
        from sources.arxiv_source import ArxivSource

        proxy_kwargs = settings.get_proxy_dict("arxiv")
    except Exception as exc:  # pragma: no cover - 环境缺依赖时跳过扫描
        logger.warning("时间段扫描初始化失败: %s", exc)
        summary["range_scan"] = {"skipped_reason": f"初始化失败: {exc}"}
        if progress_callback is not None:
            progress_callback(
                phase="legacy_scan",
                detail="时间段扫描初始化失败，已保留已导入数据",
            )
        return summary

    source = ArxivSource(
        history_dir=settings.HISTORY_DIR,
        proxy_dict=proxy_kwargs,
        announcement_lookback_grace_days=int(
            getattr(settings, "ARXIV_ANNOUNCEMENT_LOOKBACK_GRACE_DAYS", 2)
        ),
        load_legacy_history=False,
    )
    domains = list(getattr(settings, "TARGET_DOMAINS", []) or [])
    try:
        scan_summary = scan_legacy_range(
            store,
            history_dir=settings.HISTORY_DIR,
            fetch_between=lambda start, end: source.fetch_domain_papers_between(
                start, end, domains
            ),
            logger_override=logger,
            progress_callback=progress_callback,
        )
    except Exception as exc:
        # Archive import has already committed by this point. Keep its data
        # and allow automatic supplements to run even if an unexpected scan
        # integration failure prevents part of the optional range check.
        logger.exception("旧历史时间段扫描异常，已保留导入结果与可重试积压")
        scan_summary = {
            "skipped_reason": f"扫描异常: {exc}",
            "errors": [str(exc)],
        }
        if progress_callback is not None:
            progress_callback(
                phase="legacy_scan",
                detail="时间段扫描异常，已记录并将在下次读取旧历史时重试",
            )
    summary["range_scan"] = scan_summary
    return summary


def _save_summary(store: DailyResearchStore, summary: dict) -> None:
    """Persist the user-visible workflow summary without hiding import data."""
    try:
        store.set_app_state(
            LEGACY_IMPORT_STATE_KEY, json.dumps(summary, ensure_ascii=False)
        )
    except Exception as exc:
        logger.warning("旧历史导入汇总写入失败: %s", exc)


def _load_summary(store: DailyResearchStore) -> dict:
    """Return the latest import summary, tolerating an older/malformed state."""
    try:
        raw = store.get_app_state(LEGACY_IMPORT_STATE_KEY)
        loaded = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        loaded = None
    return loaded if isinstance(loaded, dict) else {}


def _make_progress_callback(store: DailyResearchStore, run_id: str):
    """Build a throttled durable heartbeat for the status panel.

    Parser-level checkpoints can be frequent for a large archive. The full
    detail is always written to the run log; this heartbeat is intentionally
    limited to phase changes, completed units and one write per second so it
    cannot contend with the import's actual SQLite writes.
    """
    last_phase = None
    last_write_at = 0.0

    def report(*, phase: str, detail: str = "", current=None, total=None) -> None:
        nonlocal last_phase, last_write_at
        now = time.monotonic()
        complete = (
            isinstance(current, int)
            and isinstance(total, int)
            and total >= 0
            and current >= total
        )
        if phase == last_phase and not complete and now - last_write_at < 1.0:
            return
        try:
            store.record_run_phase(
                run_id,
                phase,
                detail=detail,
                current=current,
                total=total,
            )
            last_phase = phase
            last_write_at = now
        except Exception as exc:  # pragma: no cover - diagnostics are non-critical
            logger.debug("旧历史导入进度写入失败: %s", exc)

    return report


def run_import() -> tuple[int, str, dict]:
    """Run the import phase and keep enough state for one consolidated notice."""
    store = DailyResearchStore(settings.DAILY_RESEARCH_DB_PATH)
    run_id = store.start_run(0, run_kind="legacy_import")
    progress_callback = _make_progress_callback(store, run_id)
    progress_callback(phase="legacy_import", detail="开始读取旧版本历史")
    summary: dict = {}
    try:
        summary = import_legacy_history(
            store,
            history_dir=settings.HISTORY_DIR,
            reports_html_dir=settings.REPORTS_DIR / "daily_research" / "html",
            delivery_run_id=run_id,
            legacy_keywords_db_path=settings.KEYWORD_DB_PATH,
            progress_logger=logger,
            progress_callback=progress_callback,
        )
        progress_callback(phase="legacy_scan", detail="开始扫描旧历史涉及的时间段")
        summary = _scan_phase(
            store,
            summary,
            progress_callback=progress_callback,
        )
    except KeyboardInterrupt:
        # WebUI 的“停止运行”会向该子进程发送 SIGINT。导入/扫描已经
        # 写入的数据保持可恢复，但这条 daily_runs 记录必须离开 running，
        # 否则状态面板和后续诊断会永久误报一个不存在的任务。
        error = "用户中断旧历史导入；已写入的数据与补充积压会保留"
        logger.warning(error)
        try:
            store.fail_run(run_id, error)
        except Exception as finish_exc:
            logger.warning("旧历史导入中断状态写入失败: %s", finish_exc)
        summary["error"] = error
        _save_summary(store, summary)
        return 130, run_id, summary
    except Exception as exc:
        logger.error("旧历史导入失败: %s", exc, exc_info=True)
        error = str(exc)
        store.fail_run(run_id, error)
        summary["error"] = error
        _save_summary(store, summary)
        return 1, run_id, summary

    try:
        store.complete_run(run_id, {})
    except Exception as exc:
        logger.warning("旧历史导入运行收尾失败（数据已写入）: %s", exc)

    try:
        pending = store.supplement_backlog_summary().get("pending", 0)
    except Exception:
        pending = 0
    summary["supplement"] = {
        "state": "pending" if pending else "not_needed",
        "pending_before": pending,
    }
    _save_summary(store, summary)

    range_scan = summary.get("range_scan") or {}
    logger.info(
        "旧历史导入完成：历史文件 %s，报告 %s 份，卡片 %s 张，账本 %s 条，积压 %s 条；"
        "时间段扫描 %s~%s 共 %s 篇、遗漏 %s 篇",
        summary.get("history_files"),
        summary.get("reports_scanned"),
        summary.get("cards_found"),
        summary.get("delivered_ledger_rows"),
        summary.get("backlog_queued"),
        range_scan.get("range_start"),
        range_scan.get("range_end"),
        range_scan.get("papers_scanned"),
        range_scan.get("missed_found"),
    )
    return 0, run_id, summary


def _run_automatic_supplement() -> int:
    """Drain import backlog into capped supplement reports until it stalls.

    Every report still observes ``daily_research.max_papers_per_run``.  A
    single user click, however, must not silently abandon the second and later
    batches.  Failed/unfetchable rows deliberately stop the loop once a batch
    makes no progress, so they stay retryable on a later import instead of
    causing an infinite LLM/API retry loop.
    """
    store = DailyResearchStore(settings.DAILY_RESEARCH_DB_PATH)
    summary = _load_summary(store)
    try:
        pending_before = int(store.supplement_backlog_summary().get("pending", 0))
    except Exception as exc:
        logger.warning("无法读取补充积压，跳过自动补充报告: %s", exc)
        pending_before = 0

    supplement = summary.setdefault("supplement", {})
    supplement["pending_before"] = pending_before
    if pending_before <= 0:
        supplement.update({"state": "not_needed", "pending_after": 0})
        _save_summary(store, summary)
        logger.info("旧历史导入后没有缺失/遗漏论文，无需生成补充报告")
        return 0

    supplement.update({
        "state": "running",
        "processed": 0,
        "batches": [],
    })
    _save_summary(store, summary)
    logger.info(
        "旧历史导入完成，自动开始补充报告：积压 %s 篇；每份报告上限由每日研究配置控制",
        pending_before,
    )

    try:
        from modes.daily_research import DailyResearchPipeline

        batch_index = 0

        # The outer legacy-import mode lock remains held for this second phase
        # (see ``main``).  Acquire both gates once for every capped batch, in
        # the same order as normal daily work.  This preserves the importer as
        # one idle-time workflow and avoids another task claiming rows between
        # batches.
        with daily_workflow_gate(
            logger=logger, wait_note="自动补充报告等待每日研究/过去日报完成"
        ):
            with legacy_import_activity_gate(
                exclusive=True,
                logger=logger,
                wait_note="自动补充报告等待其他任务空闲",
            ):
                while True:
                    batch_pending_before = int(
                        store.supplement_backlog_summary().get("pending", 0)
                    )
                    if batch_pending_before <= 0:
                        supplement.update({
                            "state": "completed",
                            "pending_after": 0,
                        })
                        break

                    batch_index += 1
                    logger.info(
                        "[LegacySupplement] 开始第 %s 批：当前积压 %s 篇，每批上限 %s 篇",
                        batch_index,
                        batch_pending_before,
                        getattr(settings, "DAILY_MAX_PAPERS_PER_RUN", 0) or "不限",
                    )
                    result = DailyResearchPipeline().run(run_kind="supplement")
                    batch = {
                        "pending_before": batch_pending_before,
                        "processed": int(
                            getattr(result, "total_papers_fetched", 0) or 0
                        ),
                        "report_paths": dict(
                            getattr(result, "report_paths", {}) or {}
                        ),
                    }
                    supplement["batches"].append(batch)
                    supplement["processed"] += batch["processed"]

                    if getattr(result, "interrupted", False):
                        supplement.update(
                            {
                                "state": "interrupted",
                                "error": "补充报告被中断；积压论文会在下次读取旧历史时重试",
                            }
                        )
                        _save_summary(store, summary)
                        logger.warning(
                            "[LegacySupplement] 第 %s 批被中断；剩余积压保留供下次重试",
                            batch_index,
                        )
                        return 130
                    if getattr(result, "success", None) is not True:
                        supplement.update(
                            {
                                "state": "failed",
                                "error": str(
                                    getattr(result, "error_message", "补充报告未成功完成")
                                )[:4000],
                            }
                        )
                        _save_summary(store, summary)
                        logger.error(
                            "[LegacySupplement] 第 %s 批未成功完成：%s；积压仍保留供下次重试",
                            batch_index,
                            supplement["error"],
                        )
                        return 1

                    batch_pending_after = int(
                        store.supplement_backlog_summary().get("pending", 0)
                    )
                    batch["pending_after"] = batch_pending_after
                    supplement["pending_after"] = batch_pending_after
                    _save_summary(store, summary)
                    logger.info(
                        "[LegacySupplement] 第 %s 批完成：处理 %s 篇，剩余积压 %s 篇，报告 %s 份",
                        batch_index,
                        batch["processed"],
                        batch_pending_after,
                        len(batch["report_paths"]),
                    )

                    if batch_pending_after >= batch_pending_before:
                        # Most commonly a missing-data row could not be fetched
                        # this time.  It remains in failed/pending state and the
                        # next legacy-import click retries it without starving
                        # other batches or spinning forever.
                        supplement.update(
                            {
                                "state": "retry_pending",
                                "error": "本批没有完成可交付论文；剩余积压已保留，后续读取旧历史会重试",
                            }
                        )
                        _save_summary(store, summary)
                        logger.warning(
                            "自动补充报告本批未推进积压，停止本轮续跑；剩余 %s 篇可重试",
                            batch_pending_after,
                        )
                        break
    except KeyboardInterrupt:
        supplement.update(
            {
                "state": "interrupted",
                "error": "补充报告被中断；积压论文会在下次读取旧历史时重试",
            }
        )
        _save_summary(store, summary)
        return 130
    except Exception as exc:
        supplement.update(
            {
                "state": "failed",
                "error": str(exc)[:4000],
            }
        )
        _save_summary(store, summary)
        logger.exception("自动补充报告失败；导入数据与积压已保留，可重试")
        return 1

    try:
        pending_after = int(store.supplement_backlog_summary().get("pending", 0))
    except Exception:
        pending_after = pending_before
    supplement.setdefault("pending_after", pending_after)
    _save_summary(store, summary)
    logger.info(
        "自动补充报告收尾：共处理 %s 篇，生成 %s 份补充报告，剩余积压 %s 篇（%s）",
        supplement["processed"],
        len(supplement.get("batches") or []),
        pending_after,
        supplement.get("state"),
    )
    return 0


def _legacy_import_notification_summary(summary: dict) -> dict:
    """Build a concise, user-facing summary for the one import notification."""
    history_files = summary.get("history_files")
    if isinstance(history_files, dict):
        history_text = "，".join(
            f"{source}: {count}" for source, count in sorted(history_files.items())
        )
    else:
        history_text = None

    range_scan = summary.get("range_scan")
    supplement = summary.get("supplement")
    fields = {
        "旧历史文件": history_text,
        "扫描 HTML 报告": summary.get("reports_scanned"),
        "读取报告卡片": summary.get("cards_found"),
        "写入 SQLite": summary.get("imported"),
        "保留已有新数据": summary.get("skipped_existing_newer"),
        "补充积压入队": summary.get("backlog_queued"),
    }
    if isinstance(range_scan, dict):
        fields["漏扫时间段"] = (
            f"{range_scan.get('range_start') or '—'} 至 {range_scan.get('range_end') or '—'}；"
            f"扫描 {range_scan.get('papers_scanned') or 0} 篇，发现遗漏 {range_scan.get('missed_found') or 0} 篇"
        )
    if isinstance(supplement, dict):
        fields["自动补充报告"] = (
            f"{supplement.get('state') or 'unknown'}；"
            f"处理 {supplement.get('processed') or 0} 篇，剩余 {supplement.get('pending_after', supplement.get('pending_before', 0)) or 0} 篇"
        )
    return fields


def _legacy_import_issues(summary: dict) -> list[str]:
    """Surface non-terminal import degradation in the one workflow notice."""
    issues: list[str] = []
    raw_errors = summary.get("errors")
    if isinstance(raw_errors, list):
        issues.extend(str(error) for error in raw_errors if error)

    range_scan = summary.get("range_scan")
    if isinstance(range_scan, dict) and range_scan.get("skipped_reason"):
        issues.append(f"旧历史时间段扫描未完整执行：{range_scan['skipped_reason']}")
    if isinstance(range_scan, dict):
        raw_scan_errors = range_scan.get("errors")
        if isinstance(raw_scan_errors, list):
            issues.extend(
                f"旧历史时间段扫描：{error}"
                for error in raw_scan_errors
                if error
            )

    supplement = summary.get("supplement")
    if isinstance(supplement, dict):
        state = str(supplement.get("state") or "")
        if state in {"failed", "interrupted", "retry_pending"}:
            issues.append(
                str(supplement.get("error") or f"自动补充报告状态：{state}")
            )
        pending_after = supplement.get("pending_after")
        if isinstance(pending_after, int) and pending_after > 0:
            issues.append(f"自动补充报告仍有 {pending_after} 篇待重试")
    return issues


def _notify_legacy_import_result(run_id: str, summary: dict, exit_code: int) -> None:
    """Persist and send exactly one result notification for the whole workflow."""
    if not settings.ENABLE_NOTIFICATIONS:
        return

    supplement = summary.get("supplement")
    supplement_error = supplement.get("error") if isinstance(supplement, dict) else ""
    error = str(summary.get("error") or supplement_error or "")
    result = WorkflowResult(
        workflow="旧历史导入",
        run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        success=exit_code == 0,
        interrupted=exit_code == 130,
        summary=_legacy_import_notification_summary(summary),
        issues=_legacy_import_issues(summary),
        error_message=error or None,
    )
    try:
        store = DailyResearchStore(settings.DAILY_RESEARCH_DB_PATH)
        notifier = NotifierAgent()
        created = notifier.enqueue_workflow_result(store, run_id, result)
        delivery = notifier.deliver_pending_workflow_results(store)
        logger.info(
            "旧历史导入通知：新建 %s，发送 %s，待补发 %s",
            created,
            delivery["sent"],
            delivery["deferred"],
        )
    except Exception as exc:
        # The import result is already safely persisted; notification delivery
        # must never turn a completed import into a failed one.
        logger.warning("旧历史导入通知写入/发送失败: %s", exc)


def main() -> int:
    with run_lock("legacy_import"):
        # 先拿到本模式锁，重复点击会立刻以 skipped_busy 返回；随后由独占
        # gate 原子等待所有普通 worker 活动结束，避免“检查空闲后又有日报
        # 启动”的竞态。等待没有固定超时，队列中的用户请求会在空闲时自动
        # 执行而不会被悄悄丢弃。
        with legacy_import_activity_gate(
            exclusive=True,
            logger=logger,
            wait_note="旧历史导入等待每日研究及其他任务空闲",
        ):
            import_exit_code, run_id, summary = run_import()
        if import_exit_code != 0:
            _notify_legacy_import_result(run_id, summary, import_exit_code)
            return import_exit_code
        # 补充报告是“读取旧历史”的第二阶段，不再由 WebUI 单独触发。外层
        # mode lock 覆盖整个工作流，面板能持续显示运行状态和实际日志。
        supplement_exit_code = _run_automatic_supplement()
        try:
            summary = _load_summary(DailyResearchStore(settings.DAILY_RESEARCH_DB_PATH))
        except Exception:
            # The detailed workflow result remains available in logs even if a
            # non-critical summary reload happens to fail.
            pass
        _notify_legacy_import_result(run_id, summary, supplement_exit_code)
        return supplement_exit_code


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    sys.exit(main())
