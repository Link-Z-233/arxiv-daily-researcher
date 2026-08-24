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
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from utils.daily_research_store import DailyResearchStore
from utils.legacy_history import LEGACY_IMPORT_STATE_KEY, import_legacy_history
from utils.legacy_range_scan import scan_legacy_range
from utils.run_lock import (
    daily_workflow_gate,
    legacy_import_activity_gate,
    run_lock,
)

logger = logging.getLogger("LegacyImport")

def _scan_phase(store: DailyResearchStore, summary: dict) -> dict:
    """扫描旧历史涉及的时间段，寻找被漏掉的 arXiv 论文。"""
    try:
        from sources.arxiv_source import ArxivSource

        proxy_kwargs = settings.get_proxy_dict("arxiv")
    except Exception as exc:  # pragma: no cover - 环境缺依赖时跳过扫描
        logger.warning("时间段扫描初始化失败: %s", exc)
        summary["range_scan"] = {"skipped_reason": f"初始化失败: {exc}"}
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
    scan_summary = scan_legacy_range(
        store,
        history_dir=settings.HISTORY_DIR,
        fetch_between=lambda start, end: source.fetch_domain_papers_between(
            start, end, domains
        ),
        logger_override=logger,
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


def run_import() -> int:
    store = DailyResearchStore(settings.DAILY_RESEARCH_DB_PATH)
    run_id = store.start_run(0, run_kind="legacy_import")
    store.record_run_phase(run_id, "legacy_import")
    try:
        summary = import_legacy_history(
            store,
            history_dir=settings.HISTORY_DIR,
            reports_html_dir=settings.REPORTS_DIR / "daily_research" / "html",
            delivery_run_id=run_id,
            progress_logger=logger,
        )
        store.record_run_phase(run_id, "legacy_scan")
        summary = _scan_phase(store, summary)
    except Exception as exc:
        logger.error("旧历史导入失败: %s", exc, exc_info=True)
        store.fail_run(run_id, str(exc))
        return 1

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
    return 0


def _run_automatic_supplement() -> int:
    """Continue a successful import with one capped supplement-report run.

    The import itself first owns the exclusive legacy gate. It must release
    that gate before entering the normal daily-style pipeline, otherwise the
    shared side of the gate would deadlock itself. The result remains part of
    the same WebUI-triggered workflow and is written back to its summary.
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

    supplement["state"] = "running"
    _save_summary(store, summary)
    logger.info(
        "旧历史导入完成，自动开始补充报告：积压 %s 篇；本次上限由每日研究配置控制",
        pending_before,
    )

    try:
        from modes.daily_research import DailyResearchPipeline

        # The outer legacy-import mode lock remains held for this second phase
        # (see ``main``).  Acquire the daily gate first (the same order used
        # by normal daily-style work), then take the import gate exclusively.
        # This makes the automatic follow-up part of the same idle-time
        # workflow too: trend/WebDAV/keyword-maintenance work cannot write the
        # shared SQLite state while this supplement is selecting and settling
        # backlog entries.
        with daily_workflow_gate(
            logger=logger, wait_note="自动补充报告等待每日研究/过去日报完成"
        ):
            with legacy_import_activity_gate(
                exclusive=True,
                logger=logger,
                wait_note="自动补充报告等待其他任务空闲",
            ):
                result = DailyResearchPipeline().run(run_kind="supplement")
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
    supplement.update(
        {
            "state": (
                "completed"
                if getattr(result, "success", None) is True
                else "failed"
            ),
            "pending_after": pending_after,
            "processed": int(getattr(result, "total_papers_fetched", 0) or 0),
            "report_paths": dict(getattr(result, "report_paths", {}) or {}),
        }
    )
    if getattr(result, "interrupted", False):
        supplement["state"] = "interrupted"
        supplement["error"] = "补充报告被中断；积压论文会在下次读取旧历史时重试"
        _save_summary(store, summary)
        return 130
    if getattr(result, "success", None) is not True:
        supplement["error"] = str(
            getattr(result, "error_message", "补充报告未成功完成")
        )[:4000]
        _save_summary(store, summary)
        logger.error("自动补充报告未成功完成；积压仍保留供下次重试")
        return 1

    _save_summary(store, summary)
    logger.info(
        "自动补充报告完成：本次处理 %s 篇，剩余积压 %s 篇",
        supplement["processed"],
        pending_after,
    )
    return 0


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
            import_exit_code = run_import()
        if import_exit_code != 0:
            return import_exit_code
        # 补充报告是“读取旧历史”的第二阶段，不再由 WebUI 单独触发。外层
        # mode lock 覆盖整个工作流，面板能持续显示运行状态和实际日志。
        return _run_automatic_supplement()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    sys.exit(main())
