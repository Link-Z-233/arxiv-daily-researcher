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
from utils.run_lock import run_lock, wait_for_idle

logger = logging.getLogger("LegacyImport")

# 导入前等待这些锁释放；趋势锁按参数哈希命名，用通配匹配。
BUSY_LOCK_PATTERNS = ("daily_research", "trend_research_*", "keyword_maintenance")
WAIT_TIMEOUT_SECONDS = 12 * 3600


def run_import() -> int:
    store = DailyResearchStore(settings.DAILY_RESEARCH_DB_PATH)
    run_id = store.start_run(0, run_kind="legacy_import")
    try:
        summary = import_legacy_history(
            store,
            history_dir=settings.HISTORY_DIR,
            reports_html_dir=settings.REPORTS_DIR / "daily_research" / "html",
            delivery_run_id=run_id,
            progress_logger=logger,
        )
    except Exception as exc:
        logger.error("旧历史导入失败: %s", exc, exc_info=True)
        store.fail_run(run_id, str(exc))
        return 1

    try:
        store.complete_run(run_id, {})
    except Exception as exc:
        logger.warning("旧历史导入运行收尾失败（数据已写入）: %s", exc)

    try:
        store.set_app_state(LEGACY_IMPORT_STATE_KEY, json.dumps(summary, ensure_ascii=False))
    except Exception as exc:
        logger.warning("旧历史导入汇总写入失败: %s", exc)

    logger.info(
        "旧历史导入完成：历史文件 %s，报告 %s 份，卡片 %s 张，账本 %s 条，积压 %s 条",
        summary.get("history_files"),
        summary.get("reports_scanned"),
        summary.get("cards_found"),
        summary.get("delivered_ledger_rows"),
        summary.get("backlog_queued"),
    )
    return 0


def main() -> int:
    idle = wait_for_idle(
        BUSY_LOCK_PATTERNS,
        poll_seconds=30,
        timeout_seconds=WAIT_TIMEOUT_SECONDS,
        logger=logger,
        wait_note="旧历史导入等待每日研究/趋势任务空闲",
    )
    if not idle:
        logger.error("旧历史导入等待空闲超时，本次退出（可稍后重新触发）")
        return 75
    with run_lock("legacy_import"):
        return run_import()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    sys.exit(main())
