"""每日 0 点静默运行的关键词维护任务（标准化 + 趋势报告）。

关键词标准化需要批量调用 LLM，耗时且依赖中转供应商的可用性。它已从
每日研究主流程中拆出：cron 在深夜单独触发本模块，输出只进入
logs/keyword_*.log，失败不影响当天的日报交付，次日 0 点自动重试。
"""

import logging
import sys
from datetime import date
from pathlib import Path
from typing import Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings  # noqa: E402
from utils.run_lock import run_lock  # noqa: E402

logger = logging.getLogger(__name__)


def should_generate_report(frequency: str, today: date) -> bool:
    """按配置频率判断当天是否生成关键词趋势报告。"""
    frequency = (frequency or "").strip().lower()
    if frequency in ("always", "daily"):
        return True
    if frequency == "weekly":
        return today.weekday() == 0
    if frequency == "monthly":
        return today.day == 1
    return False


def run_keyword_maintenance(today: Optional[date] = None) -> int:
    """执行标准化与趋势报告，返回进程退出码（失败不抛出）。"""
    today = today or date.today()

    if not settings.KEYWORD_TRACKER_ENABLED:
        logger.info("关键词跟踪已禁用（keyword_tracker.enabled=false），跳过维护")
        return 0
    if not settings.KEYWORD_NORMALIZATION_ENABLED:
        logger.info("关键词标准化已禁用（normalization.enabled=false），跳过维护")
        return 0

    try:
        from keyword_tracker import KeywordTracker

        tracker = KeywordTracker()
    except Exception as exc:
        logger.error("关键词跟踪器初始化失败: %s", exc)
        return 1

    try:
        stats = tracker.run_daily_normalization()
        logger.info(
            "标准化完成: 处理 %s 个, 新增规范词 %s, 合并 %s",
            stats.get("processed", 0),
            stats.get("new_canonical", 0),
            stats.get("merged", 0),
        )
    except Exception as exc:
        # 标准化失败时同日不再生成趋势报告，避免基于旧规范词出报告；
        # 次日 0 点的下一次维护会自然重试。
        logger.error("关键词标准化失败: %s", exc)
        return 1

    if settings.KEYWORD_REPORT_ENABLED and should_generate_report(
        settings.KEYWORD_REPORT_FREQUENCY, today
    ):
        try:
            logger.info("生成关键词趋势报告...")
            top_keywords = tracker.get_top_keywords()
            trends = tracker.get_trends()
            bar_chart = tracker.generate_bar_chart()
            trend_chart = tracker.generate_trend_chart()

            from report.keyword_trend import KeywordTrendReporter

            trend_paths = KeywordTrendReporter().render(
                top_keywords=top_keywords,
                trends=trends,
                bar_chart=bar_chart,
                trend_chart=trend_chart,
                today=today,
                days=tracker.default_days,
            )
            logger.info("趋势报告已保存: %s", trend_paths.get("markdown", ""))
        except Exception as exc:
            logger.error("关键词趋势报告生成失败: %s", exc)
            return 1
    else:
        logger.info(
            "跳过趋势报告生成 (频率设置: %s)", settings.KEYWORD_REPORT_FREQUENCY
        )

    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    with run_lock("keyword_maintenance"):
        return run_keyword_maintenance()


if __name__ == "__main__":
    sys.exit(main())
