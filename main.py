"""
多数据源论文研究系统入口

运行模式（通过 --mode 参数选择）：
- daily_research（默认）：每日论文监控与研究
- trend_research：关键词驱动的研究趋势分析
"""

import sys
import argparse
from pathlib import Path
from datetime import date, timedelta
from typing import Any, Optional, Sequence

# 将 src 目录加入 Python 模块搜索路径
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from config import settings
from utils.logger import setup_logger, setup_run_log
from utils.run_lock import run_lock

logger = setup_logger("Main")


def parse_args(argv: Optional[Sequence[str]] = None):
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="ArXiv Daily Researcher — 多数据源论文研究系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
运行示例:
  python main.py                                        # 每日研究模式（默认）
  python main.py --mode trend_research --keywords "quantum error correction"
  python main.py --mode trend_research --keywords "quantum error correction" "fault tolerant" \\
                 --date-from 2024-01-01 --date-to 2024-12-31
        """,
    )
    parser.add_argument(
        "--mode",
        default="daily_research",
        choices=[
            "daily_research",
            "trend_research",
            "legacy_import",
            "supplement_run",
            "backfill_run",
        ],
        help="运行模式：daily_research（每日研究，默认）、trend_research（研究趋势分析）、legacy_import（旧版本历史导入）、supplement_run（补充报告）或 backfill_run（过去时间段每日报告）",
    )
    parser.add_argument(
        "--target-date",
        type=str,
        default=None,
        help="[backfill_run] 要补跑的过去日期，格式 YYYY-MM-DD（必须早于今天）",
    )
    parser.add_argument(
        "--keywords",
        nargs="+",
        help="[trend_research] 搜索关键词，多个用空格分隔",
    )
    parser.add_argument(
        "--date-from",
        type=str,
        default=None,
        help="[trend_research] 搜索起始日期，格式 YYYY-MM-DD",
    )
    parser.add_argument(
        "--date-to",
        type=str,
        default=None,
        help="[trend_research] 搜索截至日期，格式 YYYY-MM-DD（默认：今天）",
    )
    parser.add_argument(
        "--sort-order",
        type=str,
        choices=["ascending", "descending"],
        default=None,
        help="[trend_research] 时间排序方向：ascending（旧→新）或 descending（新→旧）",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=None,
        help="[trend_research] 最大论文数（安全上限，默认使用配置文件值）",
    )
    parser.add_argument(
        "--categories",
        nargs="+",
        default=None,
        help="[trend_research] 限制搜索的 ArXiv 分类，多个用空格分隔，如 quant-ph cond-mat.mes-hall；不指定则不限制分类",
    )
    parser.add_argument(
        "--analysis-prompt",
        default=None,
        help="[trend_research] 自定义深度分析提示词；不指定则使用配置文件或技能库内置指令",
    )
    return parser.parse_args(argv)


def _result_exit_code(result: Any) -> int:
    """Map an explicit pipeline result to a shell-compatible exit code.

    Both pipelines return a result object with a boolean ``success`` field.
    Treating a missing or malformed result as failure is intentional: a cron
    task must not look healthy merely because an implementation path forgot to
    return its outcome.
    """
    if getattr(result, "interrupted", False):
        logger.warning("任务已中断")
        return 130

    success = getattr(result, "success", None)
    if success is True:
        return 0
    if success is False:
        error = getattr(result, "error_message", None)
        if error:
            logger.error("任务失败: %s", error)
        else:
            logger.error("任务失败，但未提供错误信息")
        return 1

    logger.error("任务未返回有效运行结果，按失败处理: %r", result)
    return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run one selected pipeline and return its process exit code."""
    settings.ensure_directories()

    # 自动更新检查
    if settings.AUTO_UPDATE_ENABLED:
        try:
            from utils.updater import check_and_update

            check_and_update(logger)
        except Exception as e:
            logger.warning(f"自动更新检查失败: {e}")

    args = parse_args(argv)

    if args.mode == "trend_research":
        # 研究趋势分析模式
        log_file = setup_run_log("trend_research")
        logger.info(f"趋势研究日志文件: {log_file}")

        if not args.keywords:
            print("错误: trend_research 模式必须指定 --keywords 参数")
            return 1

        date_to = date.today()
        if args.date_to:
            date_to = date.fromisoformat(args.date_to)

        date_from = date_to - timedelta(days=settings.RESEARCH_DEFAULT_DATE_RANGE_DAYS)
        if args.date_from:
            date_from = date.fromisoformat(args.date_from)

        sort_order = args.sort_order or settings.RESEARCH_SORT_ORDER
        max_results = (
            args.max_results if args.max_results is not None else settings.RESEARCH_MAX_RESULTS
        )
        # 面板传入的自定义深度分析提示词优先于配置文件
        if args.analysis_prompt:
            settings.RESEARCH_ANALYSIS_PROMPT = args.analysis_prompt

        from modes.trend_research import TrendResearchPipeline

        with run_lock(
            "trend_research",
            keywords=args.keywords,
            date_from=date_from,
            date_to=date_to,
            categories=args.categories,
        ):
            result = TrendResearchPipeline(
                settings=settings,
                keywords=args.keywords,
                date_from=date_from,
                date_to=date_to,
                sort_order=sort_order,
                max_results=max_results,
                categories=args.categories,
            ).run()
        return _result_exit_code(result)

    if args.mode == "legacy_import":
        # 旧版本（v3.2）历史导入：面板触发，等待主流程空闲后运行。
        log_file = setup_run_log("legacy_import")
        logger.info(f"旧历史导入日志文件: {log_file}")

        from modes.legacy_import import main as legacy_import_main

        return legacy_import_main()

    if args.mode == "supplement_run":
        # 补充报告：重跑旧历史缺失/遗漏论文，产出一份补充报告。
        log_file = setup_run_log("supplement_run")
        logger.info(f"补充运行日志文件: {log_file}")

        from modes.daily_research import DailyResearchPipeline
        from utils.run_lock import AUX_JOB_MODES, wait_for_idle

        wait_for_idle(
            ("daily_research", "trend_research_*", *AUX_JOB_MODES),
            poll_seconds=30,
            timeout_seconds=12 * 3600,
            logger=logger,
            wait_note="补充运行等待其他任务空闲",
        )
        with run_lock("supplement_run"):
            result = DailyResearchPipeline().run(run_kind="supplement")
        return _result_exit_code(result)

    if args.mode == "backfill_run":
        # 过去时间段每日报告：为过去的某一天重跑当天的每日研究。
        log_file = setup_run_log("backfill_run")
        logger.info(f"过去日报补跑日志文件: {log_file}")

        if not args.target_date:
            logger.error("backfill_run 模式必须指定 --target-date 参数")
            return 1
        try:
            target_date = date.fromisoformat(args.target_date)
        except ValueError:
            logger.error("--target-date 格式无效（应为 YYYY-MM-DD）: %r", args.target_date)
            return 1
        if target_date >= date.today():
            logger.error("--target-date 必须早于今天: %s", target_date)
            return 1

        from modes.daily_research import DailyResearchPipeline
        from utils.run_lock import AUX_JOB_MODES, wait_for_idle

        wait_for_idle(
            ("daily_research", "trend_research_*", *AUX_JOB_MODES),
            poll_seconds=30,
            timeout_seconds=12 * 3600,
            logger=logger,
            wait_note="过去日报补跑等待其他任务空闲",
        )
        with run_lock("backfill_run"):
            result = DailyResearchPipeline().run(
                run_kind="backfill", target_date=target_date
            )
        return _result_exit_code(result)

    # 每日研究模式（默认）
    log_file = setup_run_log("daily_research")
    logger.info(f"每日研究日志文件: {log_file}")

    from modes.daily_research import DailyResearchPipeline
    from utils.run_lock import AUX_JOB_MODES, wait_for_idle

    # 面板后台作业（旧历史导入等）与每日研究共用一个 SQLite 库；daily
    # 先等它们收尾（有上限），再取自己的运行锁，避免与 cron 互相踩踏。
    wait_for_idle(
        AUX_JOB_MODES,
        poll_seconds=30,
        timeout_seconds=4 * 3600,
        logger=logger,
        wait_note="每日研究等待面板后台作业完成",
    )

    with run_lock("daily_research"):
        result = DailyResearchPipeline().run()
    return _result_exit_code(result)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.warning("任务被用户或停止信号中断")
        sys.exit(130)
